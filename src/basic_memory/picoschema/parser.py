"""Picoschema parser for Basic Memory.

Parses Picoschema YAML dicts (from note frontmatter) into typed dataclass
representations. Picoschema is a compact schema notation from Google's Dotprompt
that fits naturally in YAML frontmatter.

Syntax reference:
  field: type, description          # required field
  field?: type, description         # optional field
  field(array): type                # array of values
  field(array, description): type   # array with description
  field?(enum): [val1, val2]        # enumeration
  field?(enum, description): [val1, val2]  # enum with description
  field?(object):                   # nested object
    sub_field: type
  EntityName as type (capitalized)  # entity reference
"""

import re
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
MODIFIER_TYPES = frozenset({"array", "enum", "object"})


# --- Field Name Parsing ---


def _parse_field_key_parts(key: str) -> tuple[str, bool, bool, bool, bool, str | None]:
    """Parse a Picoschema field key into its components.

    Returns (name, required, is_array, is_enum, is_object, description).
    The key format is: name[?][(array|enum|object[, description])]

    Examples:
        "name"           -> ("name", True, False, False, False, None)
        "role?"          -> ("role", False, False, False, False, None)
        "tags?(array)"   -> ("tags", False, True, False, False, None)
        "tags?(array, labels)" -> ("tags", False, True, False, False, "labels")
        "status?(enum)"  -> ("status", False, False, True, False, None)
        "metadata?(object)" -> ("metadata", False, False, False, True, None)
    """
    required = True
    is_array = False
    is_enum = False
    is_object = False
    description = None

    key, modifier, description = _split_modifier_suffix(key)

    if modifier == "array":
        is_array = True
    elif modifier == "enum":
        is_enum = True
    elif modifier == "object":
        is_object = True

    # Check for optional marker
    if key.endswith("?"):
        required = False
        key = key[:-1]

    return key.strip(), required, is_array, is_enum, is_object, description


def _parse_field_key(key: str) -> tuple[str, bool, bool, bool, bool]:
    """Parse a Picoschema field key, discarding any modifier description."""
    name, required, is_array, is_enum, is_object, _description = _parse_field_key_parts(key)
    return name, required, is_array, is_enum, is_object


def _split_modifier_suffix(key: str) -> tuple[str, str | None, str | None]:
    """Split a trailing picoschema modifier from a field key."""
    stripped_key = key.rstrip()
    if not stripped_key.endswith(")"):
        return key, None, None

    # Trigger: field names and modifier descriptions may both contain parentheses
    # Why: only the parenthesis paired with the final suffix can introduce a modifier
    # Outcome: preserves names like "risk(score)" and descriptions like "labels (freeform)"
    open_paren_index = -1
    depth = 0
    for index in range(len(stripped_key) - 1, -1, -1):
        char = stripped_key[index]
        if char == ")":
            depth += 1
        elif char == "(":
            depth -= 1
            if depth == 0:
                open_paren_index = index
                break

    if open_paren_index == -1:
        return key, None, None

    modifier_text = stripped_key[open_paren_index + 1 : -1].strip()
    modifier, separator, description = modifier_text.partition(",")
    modifier = modifier.strip()
    if modifier not in MODIFIER_TYPES:
        return key, None, None

    key_without_modifier = stripped_key[:open_paren_index].rstrip()
    parsed_description = description.strip() if separator else None
    return key_without_modifier, modifier, parsed_description or None


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


# --- Enum String Parsing ---


def _parse_enum_string(value: str) -> tuple[list[str], str | None]:
    """Parse a string-typed enum value into enum values and optional description.

    When picoschema enum values are quoted in YAML frontmatter (required when a
    description follows the list), YAML parses the whole thing as a string. This
    function extracts the enum values and description from that string.

    Examples:
        "[active, blocked, done], current state" -> (['active', 'blocked', 'done'], 'current state')
        "[active, blocked]"                      -> (['active', 'blocked'], None)
        "active"                                 -> (['active'], None)
    """
    # Match bracketed list with optional trailing description
    m = re.match(r"\[([^\]]+)\](?:\s*,\s*(.+))?", value)
    if m:
        items = [item.strip() for item in m.group(1).split(",")]
        description = m.group(2).strip() if m.group(2) else None
        return items, description
    # Plain string — single enum value
    return [value.strip()], None


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
        name, required, is_array, is_enum, is_object, key_description = _parse_field_key_parts(key)

        # --- Enum fields ---
        # Trigger: value is a list or a string containing bracketed enum values
        # Why: enums declare allowed values directly as a YAML list, or as a quoted
        #   string when a description follows (e.g., "[a, b], desc" must be quoted
        #   in YAML to avoid parse errors)
        # Outcome: SchemaField with is_enum=True and enum_values populated
        if is_enum:
            description = key_description
            if isinstance(value, list):
                enum_values = [str(v) for v in value]
            else:
                enum_values, value_description = _parse_enum_string(str(value))
                description = description or value_description
            fields.append(
                SchemaField(
                    name=name,
                    type="enum",
                    required=required,
                    is_enum=True,
                    enum_values=enum_values,
                    description=description,
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
                    description=key_description,
                    children=children,
                )
            )
            continue

        # --- Scalar and entity ref fields ---
        type_str, value_description = _parse_type_and_description(str(value))
        description = key_description or value_description
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
