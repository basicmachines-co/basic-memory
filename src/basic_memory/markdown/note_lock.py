"""API write protection carried by canonical Markdown, not indexed metadata."""

from basic_memory.file_utils import ParseError, has_frontmatter, parse_frontmatter


LOCKED_NOTE_MESSAGE = (
    "This note is locked (locked: true). Basic Memory cannot edit, overwrite, delete, "
    "or unlock it. Remove locked: true by editing the Markdown file directly to unlock it."
)


def note_is_locked(markdown_content: str) -> bool:
    """Only the YAML boolean true opts a note into API write protection."""
    if not has_frontmatter(markdown_content):
        return False
    try:
        metadata = parse_frontmatter(markdown_content)
    except ParseError:
        # Offline malformed notes remain supported resources, not valid lock declarations.
        # API edits cannot reach this escape hatch: the accepted locked predecessor is
        # checked before any proposed replacement can introduce malformed frontmatter.
        return False
    return metadata.get("locked") is True
