# SPEC-SCHEMA-IMPL: Schema System Implementation Plan

**Status:** Draft
**Created:** 2025-02-06
**Branch:** `feature/schema-system`
**Depends on:** [SPEC-SCHEMA](SPEC-SCHEMA.md)

## Overview

Implementation plan for the Basic Memory Schema System. The system is entirely programmatic —
no LLM agent runtime or API key required. The LLM already in the user's session (Claude Code,
Claude Desktop, etc.) provides the intelligence layer by reading schema notes via existing
MCP tools.

## Architecture

```
┌─────────────────────────────────────────────────┐
│                   Entry Points                   │
│  CLI (bm schema ...)  │  MCP (schema_validate)  │
└──────────┬────────────┴──────────┬──────────────┘
           │                       │
           ▼                       ▼
┌─────────────────────────────────────────────────┐
│              Schema Service Layer                │
│  resolve_schema · validate · infer · diff        │
└──────────┬────────────────────────┬──────────────┘
           │                       │
           ▼                       ▼
┌──────────────────────┐ ┌────────────────────────┐
│   Picoschema Parser  │ │   Note/Entity Access   │
│  YAML → SchemaModel  │ │  (existing repository) │
└──────────────────────┘ └────────────────────────┘
```

No new database tables. Schemas are notes with `type: schema` — they're already indexed.
Validation reads observations and relations from existing data.

## Components

### 1. Picoschema Parser

**Location:** `src/basic_memory/schema/parser.py`

Parses Picoschema YAML into an internal representation.

```python
@dataclass
class SchemaField:
    name: str
    type: str                    # string, integer, number, boolean, any, or EntityName
    required: bool               # True unless field name ends with ?
    is_array: bool               # True if (array) notation
    is_enum: bool                # True if (enum) notation
    enum_values: list[str]       # Populated for enums
    description: str | None      # Text after comma
    is_entity_ref: bool          # True if type is capitalized (entity reference)
    children: list[SchemaField]  # For (object) types


@dataclass
class SchemaDefinition:
    entity: str                  # The entity type this schema describes
    version: int                 # Schema version
    fields: list[SchemaField]    # Parsed fields
    validation_mode: str         # "warn" | "strict" | "off"
    frontmatter_fields: list[SchemaField]  # From settings.frontmatter (default: [])


def parse_picoschema(yaml_dict: dict) -> list[SchemaField]:
    """Parse a Picoschema YAML dict into a list of SchemaField objects."""


def parse_schema_note(frontmatter: dict) -> SchemaDefinition:
    """Parse a full schema note's frontmatter into a SchemaDefinition."""
```

**Input/Output:**
```yaml
# Input (YAML dict from frontmatter)
schema:
  name: string, full name
  role?: string, job title
  works_at?: Organization, employer
  expertise?(array): string, areas of knowledge
```

```python
# Output
[
    SchemaField(name="name", type="string", required=True, description="full name", ...),
    SchemaField(name="role", type="string", required=False, description="job title", ...),
    SchemaField(name="works_at", type="Organization", required=False, is_entity_ref=True, ...),
    SchemaField(name="expertise", type="string", required=False, is_array=True, ...),
]
```

### 2. Schema Resolver

**Location:** `src/basic_memory/schema/resolver.py`

Finds the applicable schema for a note using the resolution order.

```python
async def resolve_schema(
    note_frontmatter: dict,
    search_fn: Callable,          # injected search capability
) -> SchemaDefinition | None:
    """Resolve schema for a note.

    Resolution order:
    1. Inline schema (frontmatter['schema'] is a dict)
    2. Explicit reference (frontmatter['schema'] is a string)
    3. Implicit by type (frontmatter['type'] → schema note with matching entity)
    4. No schema (returns None)
    """
```

### 3. Schema Validator

**Location:** `src/basic_memory/schema/validator.py`

Validates a note's observations and relations against a resolved schema.

```python
@dataclass
class FieldResult:
    field: SchemaField
    status: str                  # "present" | "missing" | "type_mismatch"
    values: list[str]            # Matched observation values or relation targets
    message: str | None          # Human-readable detail


@dataclass
class ValidationResult:
    note_identifier: str
    schema_entity: str
    passed: bool                 # True if no errors (warnings are OK)
    field_results: list[FieldResult]
    unmatched_observations: dict[str, int]   # category → count
    unmatched_relations: list[str]           # relation types not in schema
    warnings: list[str]
    errors: list[str]


async def validate_note(
    note: Note,
    schema: SchemaDefinition,
    frontmatter: dict | None = None,
) -> ValidationResult:
    """Validate a note against a schema definition.

    Mapping rules:
    - field: string              → observation [field] exists
    - field?(array): type        → multiple [field] observations
    - field?: EntityType         → relation 'field [[...]]' exists
    - field?(enum): [v]          → observation [field] value ∈ enum values
    - settings.frontmatter field → frontmatter key presence/value
    """
```

### 4. Schema Inference Engine

**Location:** `src/basic_memory/schema/inference.py`

Analyzes notes of a given type and suggests a schema based on usage frequency.

```python
@dataclass
class FieldFrequency:
    name: str
    source: str                  # "observation" | "relation"
    count: int                   # notes containing this field
    total: int                   # total notes analyzed
    percentage: float
    sample_values: list[str]     # representative values
    is_array: bool               # True if typically appears multiple times per note
    target_type: str | None      # For relations, the most common target entity type


@dataclass
class InferenceResult:
    entity_type: str
    notes_analyzed: int
    field_frequencies: list[FieldFrequency]
    suggested_schema: dict       # Ready-to-use Picoschema YAML dict
    suggested_required: list[str]
    suggested_optional: list[str]
    excluded: list[str]          # Below threshold


async def infer_schema(
    entity_type: str,
    notes: list[Note],
    required_threshold: float = 0.95,   # 95%+ = required
    optional_threshold: float = 0.25,   # 25%+ = optional
) -> InferenceResult:
    """Analyze notes and suggest a Picoschema definition."""
```

### 5. Schema Diff

**Location:** `src/basic_memory/schema/diff.py`

Compares current note usage against an existing schema definition.

```python
@dataclass
class SchemaDrift:
    new_fields: list[FieldFrequency]       # Fields not in schema but common in notes
    dropped_fields: list[FieldFrequency]   # Fields in schema but rare in notes
    cardinality_changes: list[str]         # one → many or many → one
    type_mismatches: list[str]             # observation values don't match declared type


async def diff_schema(
    schema: SchemaDefinition,
    notes: list[Note],
) -> SchemaDrift:
    """Compare a schema against actual note usage to detect drift."""
```

## Entry Points

### CLI Commands

**Location:** `src/basic_memory/cli/schema.py`

```python
import typer

schema_app = typer.Typer(name="schema", help="Schema management commands")

@schema_app.command()
async def validate(
    target: str = typer.Argument(None, help="Note path or entity type"),
    strict: bool = typer.Option(False, help="Override to strict mode"),
):
    """Validate notes against their schemas."""

@schema_app.command()
async def infer(
    entity_type: str = typer.Argument(..., help="Entity type to analyze"),
    threshold: float = typer.Option(0.25, help="Minimum frequency for optional fields"),
    save: bool = typer.Option(False, help="Save to schema/ directory"),
):
    """Infer schema from existing notes of a type."""

@schema_app.command()
async def diff(
    entity_type: str = typer.Argument(..., help="Entity type to diff"),
):
    """Show drift between schema and actual usage."""
```

Registered as subcommand: `bm schema validate`, `bm schema infer`, `bm schema diff`.

### MCP Tools

**Location:** `src/basic_memory/mcp/tools/schema.py`

```python
@mcp_tool
async def schema_validate(
    entity_type: str | None = None,
    identifier: str | None = None,
    project: str | None = None,
) -> str:
    """Validate notes against their resolved schema."""

@mcp_tool
async def schema_infer(
    entity_type: str,
    threshold: float = 0.25,
    project: str | None = None,
) -> str:
    """Analyze existing notes and suggest a schema definition."""
```

### API Endpoints

**Location:** `src/basic_memory/api/schema_router.py`

```python
router = APIRouter(prefix="/schema", tags=["schema"])

@router.post("/validate")
async def validate_schema(...) -> ValidationReport: ...

@router.post("/infer")
async def infer_schema(...) -> InferenceResult: ...

@router.get("/diff/{entity_type}")
async def diff_schema(...) -> SchemaDrift: ...
```

MCP tools call these endpoints via the typed client pattern (consistent with existing
architecture).

## Implementation Phases

### Phase 1: Parser + Resolver

Build the foundation — can parse Picoschema and find schemas for notes.

**Deliverables:**
- `schema/parser.py` — Picoschema YAML → `SchemaDefinition`
- `schema/resolver.py` — Resolution order (inline → explicit ref → implicit by type → none)
- Unit tests for all Picoschema syntax variations
- Unit tests for resolution order

**No external dependencies.** Pure Python parsing of YAML dicts. Can develop and test
in isolation.

### Phase 2: Validator

Connect schemas to notes and produce validation results.

**Deliverables:**
- `schema/validator.py` — Validate note observations/relations against schema fields
- API endpoint: `POST /schema/validate`
- MCP tool: `schema_validate`
- CLI command: `bm schema validate`
- Integration tests with real notes and schemas

**Depends on:** Phase 1 (parser + resolver)

### Phase 3: Inference

Analyze existing notes to suggest schemas.

**Deliverables:**
- `schema/inference.py` — Frequency analysis across notes of a type
- API endpoint: `POST /schema/infer`
- MCP tool: `schema_infer`
- CLI command: `bm schema infer`
- Option to save inferred schema as a note via `write_note`

**Depends on:** Phase 1 (parser for output format)

### Phase 4: Diff

Compare schemas against current usage.

**Deliverables:**
- `schema/diff.py` — Drift detection between schema and actual notes
- API endpoint: `GET /schema/diff/{entity_type}`
- CLI command: `bm schema diff`

**Depends on:** Phase 1 (parser), Phase 3 (inference, for frequency analysis)

## Testing Strategy

- **Unit tests** (`tests/schema/`): Parser edge cases, resolution logic, validation mapping,
  inference thresholds
- **Integration tests** (`test-int/schema/`): End-to-end with real markdown files, schema notes
  on disk, CLI invocation
- Coverage target: 100% (consistent with project standard)

## What This Does NOT Include

- No new database tables or migrations
- No new markdown syntax (schemas validate existing observations/relations)
- No LLM agent runtime or API key management
- No hook integration (deferred)
- No schema composition/inheritance (deferred)
- No OWL/RDF export (deferred)
- No built-in templates (deferred)
