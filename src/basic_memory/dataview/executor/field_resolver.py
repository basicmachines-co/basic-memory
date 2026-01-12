"""
Field resolver for Dataview queries.

Resolves field references like 'status', 'file.name', 'file.link' from note data.
"""

from typing import Any


class FieldResolver:
    """Resolves field values from note data."""

    # Special fields that map to file metadata
    FILE_FIELDS = {
        "file.name": lambda note: note.get("title", ""),
        "file.link": lambda note: f"[[{note.get('title', '')}]]",
        "file.path": lambda note: note.get("path", ""),
        "file.folder": lambda note: note.get("folder", ""),
        "file.size": lambda note: note.get("size", 0),
        "file.ctime": lambda note: note.get("created_at", ""),
        "file.mtime": lambda note: note.get("updated_at", ""),
    }

    @classmethod
    def resolve_field(cls, note: dict[str, Any], field_name: str) -> Any:
        """
        Resolve a field value from a note.

        Args:
            note: Note data dictionary
            field_name: Field name to resolve (e.g., 'status', 'file.name')

        Returns:
            Field value or None if not found
        """
        # Handle special file.* fields
        if field_name in cls.FILE_FIELDS:
            return cls.FILE_FIELDS[field_name](note)

        # Handle frontmatter fields
        frontmatter = note.get("frontmatter", {})
        if field_name in frontmatter:
            return frontmatter[field_name]

        # Handle direct note fields
        if field_name in note:
            return note[field_name]

        # Field not found
        return None

    @classmethod
    def has_field(cls, note: dict[str, Any], field_name: str) -> bool:
        """Check if a note has a specific field."""
        if field_name in cls.FILE_FIELDS:
            return True

        frontmatter = note.get("frontmatter", {})
        return field_name in frontmatter or field_name in note
