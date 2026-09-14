"""Local project snapshot and staged publication of a validated OKF bundle."""

from pathlib import Path, PurePosixPath
import shutil
from tempfile import TemporaryDirectory
from uuid import uuid4

from basic_memory.config import APP_DATABASE_NAME, BasicMemoryConfig, DatabaseBackend, ProjectMode
from basic_memory.okf.render import ExportFile, ExportSnapshot, RecordedChange, render_bundle
from basic_memory.okf.validation import CheckReport, check_bundle, parse_document


async def recorded_history(
    config: BasicMemoryConfig, project_name: str, root: Path
) -> tuple[RecordedChange, ...]:
    """Read available journal evidence without indexing or repairing source files."""
    from basic_memory import db
    from basic_memory.repository.project_repository import ProjectRepository
    from basic_memory.utils import ensure_timezone_aware
    from sqlalchemy import inspect

    # database_path creates an empty DB as a side effect; a read-only export must
    # distinguish absent history before opening the existing database.
    database_path = config.data_dir_path / APP_DATABASE_NAME
    if config.database_backend == DatabaseBackend.SQLITE and not database_path.exists():
        return ()
    engine, session_maker = await db.get_or_create_db(
        db_path=database_path,
        db_type=db.DatabaseType.FILESYSTEM,
        config=config,
        ensure_migrations=False,
    )
    # An older database has no accepted-change journal. Export remains read-only
    # and reports no recorded history instead of migrating or querying missing columns.
    async with engine.connect() as connection:
        has_journal = await connection.run_sync(
            lambda sync: (
                inspect(sync).has_table("accepted_project_note_change")
                and any(
                    column["name"] == "partition_position"
                    for column in inspect(sync).get_columns("project")
                )
            )
        )
    if not has_journal:
        return ()
    repository = ProjectRepository()
    async with db.scoped_session(session_maker) as session:
        project = await repository.get_by_name(session, project_name)
        if project is None:
            return ()
        if Path(project.path).resolve() != root:
            raise ValueError("Configured project path differs from the recorded project path")
        changes = await repository.list_accepted_note_changes(
            session, project.id, through_position=project.partition_position
        )
        return tuple(
            RecordedChange(
                change.partition_position,
                change.file_path,
                change.operation,
                ensure_timezone_aware(change.accepted_at),
            )
            for change in changes
            if change.source != "wiki_projector"
        )


def snapshot_files(root: Path) -> tuple[ExportFile, ...]:
    from basic_memory.index.local_project import scan_local_project_index_files
    from basic_memory.runtime.storage import runtime_file_path_is_markdown_note

    scan = scan_local_project_index_files(root)
    if scan.unreadable_directories:
        raise OSError("Incomplete project scan: " + ", ".join(scan.unreadable_directories))
    files = []
    for path in scan.file_paths:
        name = PurePosixPath(path).name
        if name.casefold() in {"index.md", "log.md"} and name not in {"index.md", "log.md"}:
            raise ValueError(f"{path}: reserved filename casing collides with generated OKF files")
        if runtime_file_path_is_markdown_note(path) and PurePosixPath(path).suffix != ".md":
            raise ValueError(
                f"{path}: OKF concepts require a lowercase .md suffix; rename it first"
            )
        content = (root / path).read_bytes()
        if PurePosixPath(path).name in {"index.md", "log.md"}:
            document = parse_document(content.decode("utf-8"))
            bm = document.metadata.get("bm")
            # Never silently discard user-authored concepts at reserved names.
            if not (isinstance(bm, dict) and bm.get("profile") == "wiki/1") and set(
                document.metadata
            ) != {"okf_version"}:
                raise ValueError(
                    f"{path}: reserved OKF filename contains a concept; rename it first"
                )
            continue
        files.append(ExportFile(path, content))
    return tuple(files)


async def export_project(
    config: BasicMemoryConfig, project: str, destination: Path, *, replace: bool = False
) -> CheckReport:
    entry = config.projects.get(project)
    if entry is None or entry.mode != ProjectMode.LOCAL:
        raise ValueError(
            "Export requires a configured local project; pull cloud files locally first"
        )
    if not Path(entry.path).expanduser().is_absolute():
        raise ValueError("Local project path must be absolute")
    root = Path(entry.path).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"Project directory does not exist: {root}")
    destination = destination.expanduser().absolute()
    resolved = destination.resolve()
    if resolved.is_relative_to(root) or root.is_relative_to(resolved):
        raise ValueError("Destination must be outside, and must not contain, the source project")
    if destination.is_symlink():
        raise ValueError("Destination must not be a symlink")
    if destination.exists() and (not replace or not destination.is_dir()):
        raise ValueError("Destination exists; use --replace to replace a directory bundle")
    history = await recorded_history(config, project, root)
    files = snapshot_files(root)
    snapshot = ExportSnapshot(project, files, history)
    rendered = render_bundle(snapshot)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # A sibling staging directory keeps rename on the same filesystem. On a failed
    # replacement, restore the previous bundle before propagating the I/O failure.
    with TemporaryDirectory(prefix=".bm-okf-", dir=destination.parent) as temporary:
        staging = Path(temporary) / "bundle"
        staging.mkdir()
        for file in rendered:
            target = staging / file.path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(file.content)
        report = check_bundle(staging)
        if not report.success:
            return report
        if (
            snapshot_files(root) != files
            or await recorded_history(config, project, root) != history
        ):
            raise ValueError("Project changed during export; retry with unchanged source files")
        # Keep rollback bytes outside automatic staging cleanup, including when
        # restoring the destination itself fails.
        backup = destination.with_name(f".{destination.name}.bm-okf-backup-{uuid4().hex}")
        if destination.is_symlink():
            raise ValueError("Destination became a symlink during export")
        if destination.exists():
            if not replace:
                raise ValueError("Destination appeared during export; refusing to replace it")
            destination.rename(backup)
        try:
            staging.rename(destination)
        except OSError:
            if backup.exists():
                backup.rename(destination)
            raise
        if backup.exists():
            shutil.rmtree(backup)
    return report
