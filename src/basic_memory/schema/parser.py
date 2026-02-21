"""Picoschema parser for Basic Memory.

Parses Picoschema YAML dicts (from note frontmatter) into typed dataclass
representations. Picoschema is a compact schema notation from Google's Dotprompt
that fits naturally in YAML frontmatter.

Syntax reference:
  field: type, description          # required field
  field?: type, description         # optional field
  field(array): type                # array of values
  field?(enum): [val1, val2]        # enumeration
  field?(object):                   # nested object
    sub_field: type
  EntityName as type (capitalized)  # entity reference
"""

from dataclasses import dataclass, field


# --- Data Model ---


@dataclass
class SchemaField:
    """A single field in a Picoschema definition.

    Maps to either an observation category or a relation type in Basic Memory notes.
    """

    name: str
    type: str  # string, integer, number, boolean, any, or EntityName
    required: bool  # True unless field name ends with ?
    is_array: bool = False  # True if (array) notation
    is_enum: bool = False  # True if (enum) notation
    enum_values: list[str] = field(default_factory=list)
    description: str | None = None  # Text after comma
    is_entity_ref: bool = False  # True if type is capitalized (entity reference)
    children: list["SchemaField"] = field(default_factory=list)  # For (object) types


@dataclass
class SchemaDefinition:
    """A complete schema definition parsed from a schema note's frontmatter.

    Combines the parsed fields with metadata about the schema itself.
    """

    entity: str  # The entity type this schema describes
    version: int  # Schema version
    fields: list[SchemaField]  # Parsed fields
    validation_mode: str  # "warn" | "strict" | "off"
    frontmatter_fields: list[SchemaField] = field(default_factory=list)  # From settings.frontmatter


# --- Built-in scalar types ---
# Types that are NOT entity references. Anything not in this set and starting
# with an uppercase letter is treated as an entity reference.

SCALAR_TYPES = frozenset({"string", "integer", "number", "boolean", "any"})


# --- Field Name Parsing ---


def _parse_field_key(key: str) -> tuple[str, bool, bool, bool, bool]:
    """Parse a Picoschema field key into its components.

    Returns (name, required, is_array, is_enum, is_object).
    The key format is: name[?][(array|enum|object)]

    Examples:
        "name"           -> ("name", True, False, False)
        "role?"          -> ("role", False, False, False)
        "tags?(array)"   -> ("tags", False, True, False)
        "status?(enum)"  -> ("status", False, False, True)
        "metadata?(object)" -> ("metadata", False, False, False) + children
    """
    required = True
    is_array = False
    is_enum = False
    is_object = False

    # Check for modifier suffix: (array), (enum), (object)
    if key.endswith("(array)"):
        is_array = True
        key = key[: -len("(array)")]
    elif key.endswith("(enum)"):
        is_enum = True
        key = key[: -len("(enum)")]
    elif key.endswith("(object)"):
        is_object = True
        key = key[: -len("(object)")]

    # Check for optional marker
    if key.endswith("?"):
        required = False
        key = key[:-1]

    return key, required, is_array, is_enum, is_object


def _parse_type_and_description(value: str) -> tuple[str, str | None]:
    """Parse a type string that may include a comma-separated description.

    Examples:
        "string"             -> ("string", None)
        "string, full name"  -> ("string", "full name")
        "Organization, employer" -> ("Organization", "employer")
    """
    if "," in value:
        type_str, desc = value.split(",", 1)
        return type_str.strip(), desc.strip()
    return value.strip(), None


def _is_entity_ref_type(type_str: str) -> bool:
    """Determine if a type string represents an entity reference.

    Entity references are capitalized type names that are not built-in scalar types.
    """
    if type_str in SCALAR_TYPES:
        return False
    # Capitalized first letter = entity reference
    return len(type_str) > 0 and type_str[0].isupper()


# --- Main Parser ---


def parse_picoschema(yaml_dict: dict) -> list[SchemaField]:
    """Parse a Picoschema YAML dict into a list of SchemaField objects.

    This is the core parser that converts YAML frontmatter schema definitions
    into structured SchemaField dataclasses.

    Args:
        yaml_dict: The schema dict from YAML frontmatter. Keys are field
            declarations (e.g., "name", "role?", "tags?(array)"), values are
            type declarations (e.g., "string", "string, description").

    Returns:
        List of SchemaField objects representing the schema.
    """
    fields: list[SchemaField] = []

    for key, value in yaml_dict.items():
        name, required, is_array, is_enum, is_object = _parse_field_key(key)

        # --- Enum fields ---
        # Trigger: value is a list (e.g., [active, inactive])
        # Why: enums declare allowed values directly as a YAML list
        # Outcome: SchemaField with is_enum=True and enum_values populated
        if is_enum:
            enum_values = value if isinstance(value, list) else [str(value)]
            fields.append(
                SchemaField(
                    name=name,
                    type="enum",
                    required=required,
                    is_enum=True,
                    enum_values=[str(v) for v in enum_values],
                )
            )
            continue

        # --- Object fields ---
        # Trigger: value is a dict (nested sub-fields)
        # Why: objects contain child fields parsed recursively
        # Outcome: SchemaField with children populated via recursive parse
        if is_object or (isinstance(value, dict) and not is_enum):
            children = parse_picoschema(value) if isinstance(value, dict) else []
            fields.append(
                SchemaField(
                    name=name,
                    type="object",
                    required=required,
                    children=children,
                )
            )
            continue

        # --- Scalar and entity ref fields ---
        type_str, description = _parse_type_and_description(str(value))
        is_entity_ref = _is_entity_ref_type(type_str)

        fields.append(
            SchemaField(
                name=name,
                type=type_str,
                required=required,
                is_array=is_array,
                description=description,
                is_entity_ref=is_entity_ref,
            )
        )

    return fields


def parse_schema_note(frontmatter: dict) -> SchemaDefinition:
    """Parse a full schema note's frontmatter into a SchemaDefinition.

    A schema note has type: schema and contains:
      - entity: the entity type this schema describes
      - version: schema version number
      - schema: the Picoschema dict
      - settings.validation: validation mode (warn/strict/off)

    Args:
        frontmatter: The complete YAML frontmatter dict from a schema note.

    Returns:
        A SchemaDefinition with parsed fields and metadata.

    Raises:
        ValueError: If required fields (entity, schema) are missing.
    """
    entity = frontmatter.get("entity")
    if not entity:
        raise ValueError("Schema note missing required 'entity' field in frontmatter")

    schema_dict = frontmatter.get("schema")
    if not schema_dict or not isinstance(schema_dict, dict):
        raise ValueError("Schema note missing required 'schema' dict in frontmatter")

    version = frontmatter.get("version", 1)
    settings = frontmatter.get("settings", {})
    validation_mode = settings.get("validation", "warn") if isinstance(settings, dict) else "warn"

    fields = parse_picoschema(schema_dict)

    # --- Frontmatter validation rules ---
    # Trigger: settings.frontmatter is a dict of Picoschema field declarations
    # Why: allows schema notes to validate frontmatter keys (tags, status, etc.)
    # Outcome: frontmatter_fields populated using same parser as schema fields
    frontmatter_dict = settings.get("frontmatter") if isinstance(settings, dict) else None
    frontmatter_fields = (
        parse_picoschema(frontmatter_dict) if isinstance(frontmatter_dict, dict) else []
    )

    return SchemaDefinition(
        entity=entity,
        version=version,
        fields=fields,
        validation_mode=validation_mode,
        frontmatter_fields=frontmatter_fields,
    )
