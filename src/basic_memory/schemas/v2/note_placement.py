"""Where a missing note goes: the wire form of services.note_placement (#1775)."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field


class NotePlacementRequest(BaseModel):
    """Ask where a note that no identifier reading resolves to would be created."""

    identifier: str = Field(
        ...,
        description="The note identifier as the caller wrote it: path, permalink, or memory:// URL",
        min_length=1,
    )


class NoteCreatePlacementResponse(BaseModel):
    """Create the missing note at ``directory``/``title``."""

    kind: Literal["create"] = "create"
    readings: list[str] = Field(
        description="Every project-relative path the identifier names, the literal one first"
    )
    directory: str = Field(description="Project-relative folder; '' is the project root")
    title: str


class AmbiguousNotePlacementResponse(BaseModel):
    """Both readings name folders storage already holds, so nothing says which one."""

    kind: Literal["ambiguous"] = "ambiguous"
    readings: list[str] = Field(
        description="Every project-relative path the identifier names, the literal one first"
    )
    literal: str = Field(description="The path as written, inside a folder named like the project")
    below_prefix: str = Field(description="The path below the project's routing prefix")


class OutsideProjectPlacementResponse(BaseModel):
    """A reading escapes the project, so no folder was probed and nothing is created."""

    kind: Literal["outside_project"] = "outside_project"
    readings: list[str] = Field(
        description="Every project-relative path the identifier names, the literal one first"
    )
    path: str = Field(description="The reading that escapes the project")


type NotePlacementResponse = Annotated[
    NoteCreatePlacementResponse | AmbiguousNotePlacementResponse | OutsideProjectPlacementResponse,
    Field(discriminator="kind"),
]
