"""Race regressions for portable note-file concurrency guards."""

from basic_memory.runtime.note_file_guards import read_runtime_file_checksum
from basic_memory.runtime.storage import RuntimeFileChecksum, RuntimeFilePath


class _DisappearingChecksumReader:
    """Model an object deleted after the existence probe succeeds."""

    async def exists(self, path: RuntimeFilePath) -> bool:
        del path
        return True

    async def compute_checksum(self, path: RuntimeFilePath) -> RuntimeFileChecksum:
        raise FileNotFoundError(path)


async def test_checksum_read_treats_post_probe_deletion_as_absent() -> None:
    """A disappearing object should not poison a durable materialization retry."""
    checksum = await read_runtime_file_checksum(
        _DisappearingChecksumReader(),
        "notes/disappeared.md",
    )

    assert checksum is None
