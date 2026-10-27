"""Validated records at the memory-JSON import boundary."""

from typing import Annotated, Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    StringConstraints,
)


type RecordText = Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]


class MemoryJsonEntity(BaseModel):
    model_config = ConfigDict(strict=True)

    type: Literal["entity"]
    name: RecordText = Field(validation_alias=AliasChoices("name", "entityName", "id"))
    entity_type: RecordText = Field(default="entity", validation_alias="entityType")
    observations: list[str] = Field(default_factory=list)


class MemoryJsonRelation(BaseModel):
    model_config = ConfigDict(strict=True)

    type: Literal["relation"]
    source: RecordText = Field(validation_alias=AliasChoices("from", "from_id"))
    target: RecordText = Field(validation_alias=AliasChoices("to", "to_id"))
    relation_type: RecordText = Field(
        validation_alias=AliasChoices("relationType", "relation_type")
    )


type MemoryJsonRecord = Annotated[
    MemoryJsonEntity | MemoryJsonRelation, Field(discriminator="type")
]

_RECORDS = TypeAdapter(list[MemoryJsonRecord])


class MemoryJsonInputError(ValueError):
    """An input rejection whose message never includes uploaded record contents."""


def parse_memory_json_records(source: object) -> list[MemoryJsonRecord]:
    """Validate the complete record stream before any folders or notes are written."""
    try:
        return _RECORDS.validate_python(source)
    except ValidationError as error:
        first = error.errors(include_input=False, include_url=False)[0]
        location = first["loc"]
        position = location[0] if location and isinstance(location[0], int) else None
        label = f"record {position + 1}" if position is not None else "input"
        match first["type"]:
            case "union_tag_not_found":
                reason = "missing type; expected entity or relation"
            case "union_tag_invalid":
                reason = "unsupported type; expected entity or relation"
            case "model_type" | "model_attributes_type":
                reason = "expected a JSON object record"
            case "list_type":
                reason = "expected an array of records or observations"
            case _:
                field = next(
                    (
                        part
                        for part in location
                        if isinstance(part, str) and part not in {"entity", "relation"}
                    ),
                    "record",
                )
                reason = f"invalid {field}; check the required entity or relation fields"
        raise MemoryJsonInputError(f"Invalid memory-JSON {label}: {reason}.") from None
