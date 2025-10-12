"""
Performance benchmark tests for sync operations.

These tests measure baseline performance for indexing operations to track
improvements from optimizations. Tests are marked with @pytest.mark.benchmark
and can be run separately.

Usage:
    # Run all benchmarks
    pytest test-int/test_sync_performance_benchmark.py -v

    # Run specific benchmark
    pytest test-int/test_sync_performance_benchmark.py::test_benchmark_sync_100_files -v
"""

import asyncio
import time
from pathlib import Path
from textwrap import dedent

import pytest

from basic_memory.config import BasicMemoryConfig, ProjectConfig
from basic_memory.sync.sync_service import get_sync_service


async def create_benchmark_file(path: Path, file_num: int, total_files: int) -> None:
    """Create a realistic test markdown file with observations and relations.

    Args:
        path: Path to create the file at
        file_num: Current file number (for unique content)
        total_files: Total number of files being created (for relation targets)
    """
    # Create realistic content with varying complexity
    has_relations = file_num < (total_files - 1)  # Most files have relations
    num_observations = min(3 + (file_num % 5), 10)  # 3-10 observations per file

    # Generate relation targets (some will be forward references)
    relations = []
    if has_relations:
        # Reference 1-3 other files
        num_relations = min(1 + (file_num % 3), 3)
        for i in range(num_relations):
            target_num = (file_num + i + 1) % total_files
            relations.append(f"- relates_to [[test-file-{target_num:04d}]]")

    content = dedent(f"""
        ---
        type: note
        tags: [benchmark, test, category-{file_num % 10}]
        ---
        # Test File {file_num:04d}

        This is benchmark test file {file_num} of {total_files}.
        It contains realistic markdown content to simulate actual usage.

        ## Observations
        {chr(10).join([f"- [category-{i % 5}] Observation {i} for file {file_num} with some content #tag{i}" for i in range(num_observations)])}

        ## Relations
        {chr(10).join(relations) if relations else "- No relations for this file"}

        ## Additional Content

        This section contains additional prose to simulate real documents.
        Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod
        tempor incididunt ut labore et dolore magna aliqua.

        ### Subsection

        More content here to make the file realistic. This helps test the
        full indexing pipeline including content extraction and search indexing.
    """).strip()

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


async def generate_benchmark_files(project_dir: Path, num_files: int) -> None:
    """Generate benchmark test files.

    Args:
        project_dir: Directory to create files in
        num_files: Number of files to generate
    """
    print(f"\nGenerating {num_files} test files...")
    start = time.time()

    # Create files in batches for faster generation
    batch_size = 100
    for batch_start in range(0, num_files, batch_size):
        batch_end = min(batch_start + batch_size, num_files)
        tasks = [
            create_benchmark_file(
                project_dir / f"category-{i % 10}" / f"test-file-{i:04d}.md", i, num_files
            )
            for i in range(batch_start, batch_end)
        ]
        await asyncio.gather(*tasks)
        print(f"  Created files {batch_start}-{batch_end} ({batch_end}/{num_files})")

    duration = time.time() - start
    print(f"  File generation completed in {duration:.2f}s ({num_files / duration:.1f} files/sec)")


def get_db_size(db_path: Path) -> tuple[int, str]:
    """Get database file size.

    Returns:
        Tuple of (size_bytes, formatted_size)
    """
    if not db_path.exists():
        return 0, "0 B"

    size_bytes = db_path.stat().st_size

    # Format size
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024.0:
            return size_bytes, f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0

    return int(size_bytes * 1024**4), f"{size_bytes:.2f} TB"


async def run_sync_benchmark(
    project_config: ProjectConfig, app_config: BasicMemoryConfig, num_files: int, test_name: str
) -> dict:
    """Run a sync benchmark and collect metrics.

    Args:
        project_config: Project configuration
        app_config: App configuration
        num_files: Number of files to benchmark
        test_name: Name of the test for reporting

    Returns:
        Dictionary with benchmark results
    """
    project_dir = project_config.home
    db_path = app_config.database_path

    print(f"\n{'=' * 70}")
    print(f"BENCHMARK: {test_name}")
    print(f"{'=' * 70}")

    # Generate test files
    await generate_benchmark_files(project_dir, num_files)

    # Get initial DB size
    initial_db_size, initial_db_formatted = get_db_size(db_path)
    print(f"\nInitial database size: {initial_db_formatted}")

    # Create sync service
    from basic_memory.repository import ProjectRepository
    from basic_memory import db

    _, session_maker = await db.get_or_create_db(
        db_path=app_config.database_path,
        db_type=db.DatabaseType.FILESYSTEM,
    )
    project_repository = ProjectRepository(session_maker)

    # Get or create project
    projects = await project_repository.find_all()
    if projects:
        project = projects[0]
    else:
        project = await project_repository.create(
            {
                "name": project_config.name,
                "path": str(project_config.home),
                "is_active": True,
                "is_default": True,
            }
        )

    sync_service = await get_sync_service(project)

    # Initialize search index (required for FTS5 table)
    await sync_service.search_service.init_search_index()

    # Run sync and measure time
    print(f"\nStarting sync of {num_files} files...")
    sync_start = time.time()

    report = await sync_service.sync(project_dir, project_name=project.name)

    sync_duration = time.time() - sync_start

    # Get final DB size
    final_db_size, final_db_formatted = get_db_size(db_path)
    db_growth = final_db_size - initial_db_size
    db_growth_formatted = f"{db_growth / 1024 / 1024:.2f} MB"

    # Calculate metrics
    files_per_sec = num_files / sync_duration if sync_duration > 0 else 0
    ms_per_file = (sync_duration * 1000) / num_files if num_files > 0 else 0

    # Print results
    print(f"\n{'-' * 70}")
    print("RESULTS:")
    print(f"{'-' * 70}")
    print(f"Files processed:      {num_files}")
    print(f"  New:                {len(report.new)}")
    print(f"  Modified:           {len(report.modified)}")
    print(f"  Deleted:            {len(report.deleted)}")
    print(f"  Moved:              {len(report.moves)}")
    print("\nPerformance:")
    print(f"  Total time:         {sync_duration:.2f}s")
    print(f"  Files/sec:          {files_per_sec:.1f}")
    print(f"  ms/file:            {ms_per_file:.1f}")
    print("\nDatabase:")
    print(f"  Initial size:       {initial_db_formatted}")
    print(f"  Final size:         {final_db_formatted}")
    print(f"  Growth:             {db_growth_formatted}")
    print(f"  Growth per file:    {(db_growth / num_files / 1024):.2f} KB")
    print(f"{'=' * 70}\n")

    return {
        "test_name": test_name,
        "num_files": num_files,
        "sync_duration_sec": sync_duration,
        "files_per_sec": files_per_sec,
        "ms_per_file": ms_per_file,
        "new_files": len(report.new),
        "modified_files": len(report.modified),
        "deleted_files": len(report.deleted),
        "moved_files": len(report.moves),
        "initial_db_size": initial_db_size,
        "final_db_size": final_db_size,
        "db_growth_bytes": db_growth,
        "db_growth_per_file_bytes": db_growth / num_files if num_files > 0 else 0,
    }


@pytest.mark.benchmark
@pytest.mark.asyncio
async def test_benchmark_sync_100_files(app_config, project_config, config_manager):
    """Benchmark: Sync 100 files (small repository)."""
    results = await run_sync_benchmark(
        project_config, app_config, num_files=100, test_name="Sync 100 files (small repository)"
    )

    # Basic assertions to ensure sync worked
    # Note: May be slightly more than 100 due to OS-generated files (.DS_Store, etc.)
    assert results["new_files"] >= 100
    assert results["sync_duration_sec"] > 0
    assert results["files_per_sec"] > 0


@pytest.mark.benchmark
@pytest.mark.asyncio
async def test_benchmark_sync_500_files(app_config, project_config, config_manager):
    """Benchmark: Sync 500 files (medium repository)."""
    results = await run_sync_benchmark(
        project_config, app_config, num_files=500, test_name="Sync 500 files (medium repository)"
    )

    # Basic assertions
    # Note: May be slightly more than 500 due to OS-generated files
    assert results["new_files"] >= 500
    assert results["sync_duration_sec"] > 0
    assert results["files_per_sec"] > 0


@pytest.mark.benchmark
@pytest.mark.asyncio
@pytest.mark.slow
async def test_benchmark_sync_1000_files(app_config, project_config, config_manager):
    """Benchmark: Sync 1000 files (large repository).

    This test is marked as 'slow' and can be skipped in regular test runs:
        pytest -m "not slow"
    """
    results = await run_sync_benchmark(
        project_config, app_config, num_files=1000, test_name="Sync 1000 files (large repository)"
    )

    # Basic assertions
    # Note: May be slightly more than 1000 due to OS-generated files
    assert results["new_files"] >= 1000
    assert results["sync_duration_sec"] > 0
    assert results["files_per_sec"] > 0


@pytest.mark.benchmark
@pytest.mark.asyncio
async def test_benchmark_resync_no_changes(app_config, project_config, config_manager):
    """Benchmark: Re-sync with no changes (should be fast).

    This tests the performance of scanning files when nothing has changed,
    which is important for cloud restarts.
    """
    project_dir = project_config.home
    num_files = 100

    # First sync
    print(f"\nFirst sync of {num_files} files...")
    await generate_benchmark_files(project_dir, num_files)

    from basic_memory.repository import ProjectRepository
    from basic_memory import db

    _, session_maker = await db.get_or_create_db(
        db_path=app_config.database_path,
        db_type=db.DatabaseType.FILESYSTEM,
    )
    project_repository = ProjectRepository(session_maker)
    projects = await project_repository.find_all()
    if projects:
        project = projects[0]
    else:
        project = await project_repository.create(
            {
                "name": project_config.name,
                "path": str(project_config.home),
                "is_active": True,
                "is_default": True,
            }
        )

    sync_service = await get_sync_service(project)

    # Initialize search index
    await sync_service.search_service.init_search_index()

    await sync_service.sync(project_dir, project_name=project.name)

    # Second sync (no changes)
    print("\nRe-sync with no changes...")
    resync_start = time.time()
    report = await sync_service.sync(project_dir, project_name=project.name)
    resync_duration = time.time() - resync_start

    print(f"\n{'-' * 70}")
    print("RE-SYNC RESULTS (no changes):")
    print(f"{'-' * 70}")
    print(f"Files scanned:        {num_files}")
    print(f"Changes detected:     {report.total}")
    print(f"  New:                {len(report.new)}")
    print(f"  Modified:           {len(report.modified)}")
    print(f"  Deleted:            {len(report.deleted)}")
    print(f"  Moved:              {len(report.moves)}")
    print(f"Duration:             {resync_duration:.2f}s")
    print(f"Files/sec:            {num_files / resync_duration:.1f}")

    # Debug: Show what changed
    if report.total > 0:
        print("\n⚠️  UNEXPECTED CHANGES DETECTED:")
        if report.new:
            print(f"  New files ({len(report.new)}): {list(report.new)[:5]}")
        if report.modified:
            print(f"  Modified files ({len(report.modified)}): {list(report.modified)[:5]}")
        if report.deleted:
            print(f"  Deleted files ({len(report.deleted)}): {list(report.deleted)[:5]}")
        if report.moves:
            print(f"  Moved files ({len(report.moves)}): {dict(list(report.moves.items())[:5])}")

    print(f"{'=' * 70}\n")

    # Should be no changes
    assert report.total == 0, (
        f"Expected no changes but got {report.total}: new={len(report.new)}, modified={len(report.modified)}, deleted={len(report.deleted)}, moves={len(report.moves)}"
    )
    assert len(report.new) == 0
    assert len(report.modified) == 0
    assert len(report.deleted) == 0
