"""Database management commands."""

# PEP 563 lazy annotations let signatures reference IndexProgress without importing
# the indexing stack at module load; reset/reindex import their heavy database and
# indexing dependencies at call time so CLI startup stays fast (#886).
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

import psutil
import typer
from loguru import logger
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

from basic_memory.cli.app import app
from basic_memory.cli.commands.command_utils import run_with_cleanup
from basic_memory.config import ConfigManager, ProjectMode

console = Console()


def _is_basic_memory_mcp(cmdline: list[str]) -> bool:
    """Heuristic: does this argv represent a `basic-memory mcp` server?

    The MCP server can be launched any of:
      basic-memory mcp
      bm mcp                                  # entrypoint alias from pyproject.toml
      python -m basic_memory.cli.main mcp     # module form
      uv run basic-memory mcp / uv run bm mcp # uv wrappers
      /abs/path/to/{bm,basic-memory}[.exe] mcp

    A reliable match needs both signals:
      1. "mcp" appears as an exact argv token (not "mcp-foo").
      2. Some argv token names the basic-memory entrypoint — either by
         hyphen/underscore form, or as a `bm` script (covers `/usr/local/bin/bm`,
         `bm.exe`, etc. via Path.stem).
    """
    if "mcp" not in cmdline:
        return False
    for arg in cmdline:
        if "basic-memory" in arg or "basic_memory" in arg:
            return True
        # Try both POSIX and Windows path interpretations so a test on
        # macOS still recognizes `C:\\...\\bm.exe`, and a real Windows
        # run still recognizes `/usr/local/bin/bm`. Path() alone uses
        # the host OS, which gives wrong stems for foreign separators.
        if PurePosixPath(arg).stem == "bm" or PureWindowsPath(arg).stem == "bm":
            return True
    return False


def _find_live_mcp_processes() -> list[tuple[int, str]]:
    """Return (pid, joined_cmdline) for live `basic-memory mcp` processes.

    Why this exists (issue #765):
        On POSIX, `Path.unlink()` removes the directory entry but the inode
        survives as long as any process holds the file open. A `bm reset`
        run while Claude Desktop (or another MCP client) is alive will
        therefore "succeed" — but the still-running MCP keeps reading the
        old, now-invisible memory.db inode and returns phantom rows. On
        Windows the OS naturally raises PermissionError on `unlink()`, so
        the bug is POSIX-specific. We detect proactively to give the same
        error experience on every platform before doing damage.

    The current process is excluded so this can be called from inside a
    `bm reset` invocation. NoSuchProcess / AccessDenied are swallowed
    because process tables race with the scan and we don't want a
    transient permission error to mask a real zombie.
    """
    me = os.getpid()
    matches: list[tuple[int, str]] = []
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            pid = proc.info.get("pid")
            if pid is None or pid == me:
                continue
            cmdline = proc.info.get("cmdline") or []
            if not cmdline:
                continue
            if _is_basic_memory_mcp(cmdline):
                matches.append((pid, " ".join(cmdline)))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return matches


def _abort_if_mcp_processes_alive() -> None:
    """Refuse `bm reset` while basic-memory MCP processes are still running.

    See _find_live_mcp_processes for the underlying POSIX-vs-Windows
    rationale. Prints a per-PID list and platform-appropriate cleanup
    instructions, then exits non-zero so destructive work never starts.
    """
    zombies = _find_live_mcp_processes()
    if not zombies:
        return

    console.print("[red]Refusing to reset:[/red] basic-memory MCP processes are still running.")
    console.print(
        "[yellow]On macOS/Linux these would keep reading the deleted memory.db inode "
        "and return phantom search results (see #765).[/yellow]"
    )
    for pid, cmd in zombies:
        console.print(f"  PID {pid}: {cmd}")
    console.print("\n[bold]How to clean up:[/bold]")
    console.print("  1. Quit Claude Desktop and any other MCP clients.")
    if os.name == "nt":
        console.print(
            "  2. Verify nothing remains: "
            "[green]Get-CimInstance Win32_Process | "
            "Where-Object {$_.CommandLine -like '*basic-memory*mcp*'}[/green]"
        )
    else:
        console.print("  2. Verify nothing remains: [green]pgrep -fa 'basic-memory mcp'[/green]")
    console.print("  3. Re-run [green]bm reset[/green].")
    raise typer.Exit(1)


@dataclass(slots=True)
class EmbeddingProgress:
    """Typed CLI progress payload for embedding backfills."""

    entity_id: int
    completed: int
    total: int


async def _reindex_projects(app_config):
    """Reindex all projects in a single async context.

    This ensures all database operations use the same event loop,
    and proper cleanup happens when the function completes.
    """
    # Deferred: SQLAlchemy, repositories, and the indexing stack load only when a
    # reindex actually runs, not on every CLI start (#886).
    from basic_memory import db
    from basic_memory.repository import ProjectRepository
    from basic_memory.services.initialization import reconcile_projects_with_config
    from basic_memory.index.local_project import (
        LocalProjectIndexRuntimeFactory,
        run_local_project_index_for_project,
    )

    try:
        await reconcile_projects_with_config(app_config)

        # Get database session (migrations already run if needed)
        _, session_maker = await db.get_or_create_db(
            db_path=app_config.database_path,
            db_type=db.DatabaseType.FILESYSTEM,
        )
        project_repository = ProjectRepository()
        async with db.scoped_session(session_maker) as session:
            projects = await project_repository.get_active_projects(session)

        for project in projects:
            console.print(f"  Indexing [cyan]{project.name}[/cyan]...")
            logger.info(f"Starting project index for project: {project.name}")
            result = await run_local_project_index_for_project(
                project,
                runtime_factory=LocalProjectIndexRuntimeFactory(),
                force_full=True,
            )
            logger.info(
                "Project index completed",
                project_name=project.name,
                total_files=result.total_files,
                enqueued_files=result.enqueued_files,
                enqueued_batches=result.enqueued_batches,
                deleted_files=result.deleted_files,
            )
    finally:
        # Clean up database connections before event loop closes
        await db.shutdown_db()


@app.command()
def reset(
    reindex: bool = typer.Option(False, "--reindex", help="Rebuild db index from filesystem"),
    force: bool = typer.Option(
        False,
        "--force",
        help=(
            "Skip the pre-flight check that refuses to reset while "
            "basic-memory MCP processes are running. Use only in "
            "automated workflows where you've already ensured no MCP "
            "clients are attached to the database."
        ),
    ),
):  # pragma: no cover
    """Reset database (drop all tables and recreate)."""
    # Deferred: SQLAlchemy and the db module load only when a reset actually
    # runs, not on every CLI start (#886).
    from sqlalchemy.exc import OperationalError

    from basic_memory import db

    console.print(
        "[yellow]Note:[/yellow] This only deletes the index database. "
        "Your markdown note files will not be affected.\n"
        "Use [green]bm reset --reindex[/green] to automatically rebuild the index afterward."
    )
    if typer.confirm("Reset the database index?"):
        # Pre-flight: refuse to proceed if MCP processes still hold the DB
        # file open. POSIX would silently let us unlink the inode while
        # they keep reading it; Windows would error here anyway. See
        # _find_live_mcp_processes for the full story. --force is the
        # documented escape hatch for scripted/CI runs.
        if not force:
            _abort_if_mcp_processes_alive()

        logger.info("Resetting database...")
        config_manager = ConfigManager()
        app_config = config_manager.config
        # Get database path
        db_path = app_config.app_database_path

        # Delete the database file and WAL files if they exist
        for suffix in ["", "-shm", "-wal"]:
            path = db_path.parent / f"{db_path.name}{suffix}"
            if path.exists():
                try:
                    path.unlink()
                    logger.info(f"Deleted: {path}")
                except OSError as e:
                    console.print(
                        f"[red]Error:[/red] Cannot delete {path.name}: {e}\n"
                        "The database may be in use by another process (e.g., MCP server).\n"
                        "Please close Claude Desktop or any other Basic Memory clients and try again."
                    )
                    raise typer.Exit(1)

        # Create a new empty database (preserves project configuration)
        try:
            run_with_cleanup(db.run_migrations(app_config))
        except OperationalError as e:
            if "disk I/O error" in str(e) or "database is locked" in str(e):
                console.print(
                    "[red]Error:[/red] Cannot access database. "
                    "It may be in use by another process (e.g., MCP server).\n"
                    "Please close Claude Desktop or any other Basic Memory clients and try again."
                )
                raise typer.Exit(1)
            raise
        console.print("[green]Database reset complete[/green]")

        if reindex:
            projects = list(app_config.projects)
            if not projects:
                console.print("[yellow]No projects configured. Skipping reindex.[/yellow]")
            else:
                console.print(f"Rebuilding search index for {len(projects)} project(s)...")
                # Note: _reindex_projects has its own cleanup, but run_with_cleanup
                # ensures db.shutdown_db() is called even if _reindex_projects changes
                run_with_cleanup(_reindex_projects(app_config))
                console.print("[green]Reindex complete[/green]")


@app.command()
def reindex(
    embeddings: bool = typer.Option(
        False, "--embeddings", "-e", help="Rebuild vector embeddings (requires semantic search)"
    ),
    search: bool = typer.Option(False, "--search", "-s", help="Rebuild full-text search index"),
    full: bool = typer.Option(
        False,
        "--full",
        help="Request a full project-index run instead of the default project-index pass",
    ),
    project: str = typer.Option(
        None, "--project", "-p", help="Reindex a specific project (default: all)"
    ),
):  # pragma: no cover
    """Rebuild search indexes and/or vector embeddings without dropping the database.

    By default runs the project-index coordinator + embeddings (if semantic search is enabled).
    Use --full to request a full project-index run and re-embed all eligible notes.
    Use --search or --embeddings to rebuild only one side.

    Examples:
        bm reindex                  # Project index + embeddings
        bm reindex --full           # Full project index + full re-embed
        bm reindex --embeddings     # Only rebuild vector embeddings
        bm reindex --search         # Only run project index
        bm reindex --full --search  # Full project index only
        bm reindex --full --embeddings  # Full re-embed only
        bm reindex -p claw --full   # Full reindex for only the 'claw' project
    """
    # If neither flag is set, do both
    if not embeddings and not search:
        embeddings = True
        search = True

    config_manager = ConfigManager()
    app_config = config_manager.config

    if embeddings and not app_config.semantic_search_enabled:
        console.print(
            "[yellow]Semantic search is not enabled.[/yellow] "
            "Set [cyan]semantic_search_enabled: true[/cyan] in config to use embeddings."
        )
        embeddings = False
        if not search:
            raise typer.Exit(0)

    run_with_cleanup(
        _reindex(app_config, search=search, embeddings=embeddings, full=full, project=project)
    )


async def _reindex(
    app_config,
    *,
    search: bool,
    embeddings: bool,
    full: bool,
    project: str | None,
):
    """Run reindex operations."""
    # Deferred: SQLAlchemy, repositories, and the indexing stack load only when a
    # reindex actually runs, not on every CLI start (#886).
    from basic_memory import db
    from basic_memory.index.local_project import (
        LocalProjectIndexRuntimeFactory,
        run_local_project_index_for_project,
    )
    from basic_memory.repository import EntityRepository, ProjectRepository
    from basic_memory.repository.search_repository import create_search_repository
    from basic_memory.services.initialization import (
        reconcile_projects_with_config,
        recover_project_materializations,
    )
    from basic_memory.services.search_service import SearchService
    from basic_memory.services.file_service import FileService
    from basic_memory.markdown.markdown_processor import MarkdownProcessor
    from basic_memory.markdown.entity_parser import EntityParser

    try:
        await reconcile_projects_with_config(app_config)

        _, session_maker = await db.get_or_create_db(
            db_path=app_config.database_path,
            db_type=db.DatabaseType.FILESYSTEM,
        )
        project_repository = ProjectRepository()
        async with db.scoped_session(session_maker) as session:
            projects = await project_repository.get_active_projects(session)

        if project:
            projects = [p for p in projects if p.name == project]
            if not projects:
                # Check if it's a cloud-only project — those can't be reindexed locally
                project_mode = app_config.get_project_mode(project)
                if project_mode == ProjectMode.CLOUD:
                    console.print(
                        f"[yellow]Project '{project}' is a cloud project.[/yellow]\n"
                        "Reindexing is a local operation — cloud projects are "
                        "indexed on the server."
                    )
                else:
                    console.print(f"[red]Project '{project}' not found.[/red]")
                raise typer.Exit(1)

        for proj in projects:
            console.print(f"\n[bold]Project: [cyan]{proj.name}[/cyan][/bold]")

            if search:
                # Trigger: the project-index scan below reconciles deletes against
                # the filesystem, and a crash can leave an accepted note stuck
                # mid-materialization with its markdown file never written.
                # Why: scanning first would treat the missing file as a delete and
                # destroy the entity plus its accepted content — data loss of an
                # acknowledged write.
                # Outcome: run the same per-project recovery sweep as startup so
                # stuck materializations are re-driven to disk before the scan.
                await recover_project_materializations(proj, session_maker)

                search_mode_label = "full project index" if full else "project index"
                console.print(
                    f"  Rebuilding full-text search index ([cyan]{search_mode_label}[/cyan])..."
                )
                result = await run_local_project_index_for_project(
                    proj,
                    runtime_factory=LocalProjectIndexRuntimeFactory(),
                    force_full=full,
                    # The full-text search rebuild must never embed: the explicit
                    # embeddings phase below owns vector (re)builds. Passing the CLI
                    # flag here would double-embed on a full reindex — the inline
                    # project-index sync, then reindex_vectors discarding and
                    # rebuilding it — so callers pay the embedding cost twice.
                    embeddings=False,
                )
                console.print(
                    "  [dim]project index: "
                    f"{result.total_files} observed, "
                    f"{result.enqueued_files} indexed, "
                    f"{result.deleted_files} deleted, "
                    f"{result.enqueued_batches} batches[/dim]"
                )

                console.print("  [green]done[/green] Full-text search index rebuilt")

            if embeddings:
                embedding_mode_label = "full rebuild" if full else "incremental sync"
                console.print(
                    f"  Building vector embeddings ([cyan]{embedding_mode_label}[/cyan])..."
                )
                entity_repository = EntityRepository(project_id=proj.id)
                search_repository = create_search_repository(
                    session_maker, project_id=proj.id, app_config=app_config
                )
                project_path = Path(proj.path)
                entity_parser = EntityParser(project_path)
                markdown_processor = MarkdownProcessor(entity_parser, app_config=app_config)
                file_service = FileService(project_path, markdown_processor, app_config=app_config)
                search_service = SearchService(
                    search_repository,
                    entity_repository,
                    file_service,
                    session_maker=session_maker,
                )

                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    BarColumn(),
                    TaskProgressColumn(),
                    console=console,
                ) as progress:
                    task = progress.add_task("  Embedding entities...", total=None)

                    def on_progress(entity_id, index, total):
                        embedding_progress = EmbeddingProgress(
                            entity_id=entity_id,
                            completed=index,
                            total=total,
                        )
                        # Trigger: repository progress now reports terminal entity completion.
                        # Why: operators need to see finished embedding work rather than
                        # entities merely entering prepare.
                        # Outcome: the CLI bar advances steadily with real completed work.
                        progress.update(
                            task,
                            total=embedding_progress.total,
                            completed=embedding_progress.completed,
                        )

                    stats = await search_service.reindex_vectors(
                        progress_callback=on_progress,
                        force_full=full,
                    )
                    progress.update(task, completed=stats["total_entities"])

                console.print(
                    f"  [green]done[/green] Embeddings complete: "
                    f"{stats['embedded']} entities embedded, "
                    f"{stats['skipped']} skipped, "
                    f"{stats['errors']} errors"
                )

        console.print("\n[green]Reindex complete![/green]")
    finally:
        await db.shutdown_db()
