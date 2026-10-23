"""What a bm_* tool reports after it changes a note.

bm_write, bm_edit and bm_move report the same fields, so a caller reads the new
path and checksum from one shape whichever tool made the change.
"""

from dataclasses import asdict, dataclass
from typing import Any, Literal

from basic_memory.mcp.note_targets import PathQualifier
from basic_memory.schemas.v2.entity import EntityResponseV2

type NoteChangeAction = Literal["created", "updated", "edited", "moved"]


@dataclass(frozen=True, slots=True)
class NoteChange:
    """One changed note: where it is now and the checksum to pass back.

    Paths are qualified the way the request addressed its project, so each one
    can be passed straight to another bm_* tool.
    """

    action: NoteChangeAction
    title: str
    permalink: str | None
    file_path: str
    # The accepted revision's checksum, which bm_write and bm_edit take back as
    # expected_checksum. file_checksum lags behind materialization.
    checksum: str | None
    observations: int
    relations: int
    unresolved_relations: int
    # The path the caller asked for, when the note landed in an existing folder's
    # spelling instead.
    requested_file_path: str | None = None
    # Where a moved note was.
    previous_file_path: str | None = None

    @classmethod
    def of(
        cls,
        note: EntityResponseV2,
        *,
        action: NoteChangeAction,
        paths: PathQualifier,
        requested_file_path: str | None = None,
        previous_file_path: str | None = None,
    ) -> "NoteChange":
        """Describe `note` as the API returned it after the change."""
        # A requested path equal to the written one adds nothing.
        requested = requested_file_path if requested_file_path != note.file_path else None
        return cls(
            action=action,
            title=note.title,
            permalink=note.permalink,
            file_path=paths.path(note.file_path),
            checksum=note.db_checksum,
            observations=len(note.observations),
            relations=len(note.relations),
            unresolved_relations=sum(1 for relation in note.relations if not relation.to_id),
            requested_file_path=None if requested is None else paths.path(requested),
            previous_file_path=(
                None if previous_file_path is None else paths.path(previous_file_path)
            ),
        )

    def as_json(self) -> dict[str, Any]:
        """The JSON result; the two situational paths appear only when they apply."""
        situational = {"requested_file_path", "previous_file_path"}
        return {
            key: value
            for key, value in asdict(self).items()
            if value is not None or key not in situational
        }

    def text_lines(self, *, detail: str | None = None) -> list[str]:
        """The text result: what changed and where, then identity and graph counts."""
        match self.action:
            case "moved":
                headline = f"Moved {self.previous_file_path} to {self.file_path}"
            case "created" | "updated" | "edited":
                headline = f"{self.action.capitalize()} {self.file_path}"
        if detail is not None:
            headline += f" ({detail})"
        lines = [headline]
        if self.requested_file_path is not None:
            lines.append(f"requested: {self.requested_file_path}")
        relations = f"relations: {self.relations}"
        if self.unresolved_relations:
            relations += f" ({self.unresolved_relations} not yet resolved)"
        lines += [
            f"title: {self.title}",
            f"permalink: {self.permalink}",
            f"checksum: {self.checksum}",
            f"observations: {self.observations}, {relations}",
        ]
        return lines
