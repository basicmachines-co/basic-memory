"""`bm schema` against a real project: each command calls bm_schema end to end."""

import json
from typing import Any

from typer.testing import CliRunner, Result

from basic_memory.cli.main import app as cli_app
from basic_memory.models import Project


PERSON_SCHEMA = """\
---
title: Person
type: schema
entity: person
version: 1
schema:
  name: string, full name
  role?: string, job title
  email?: string, contact address
settings:
  validation: warn
---

# Person
"""


def person_note(name: str, observations: str) -> str:
    return f"---\ntitle: {name}\ntype: person\n---\n\n# {name}\n\n## Observations\n{observations}"


def write_note(project: Project, identifier: str, content: str) -> None:
    """Write a note through `bm tool write`, so it is indexed before the next command."""
    result = CliRunner().invoke(
        cli_app,
        ["tool", "write", "--identifier", identifier, "--project", project.name, "--json"],
        input=content,
    )
    assert result.exit_code == 0, result.output


def bm_schema(*arguments: str) -> Result:
    return CliRunner().invoke(cli_app, ["schema", *arguments])


def bm_schema_json(*arguments: str) -> dict[str, Any]:
    result = bm_schema(*arguments, "--json")
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def write_people(project: Project) -> None:
    """Alice matches the schema; Bob has no name, repeats role, and adds a hobby."""
    write_note(project, "schemas/Person.md", PERSON_SCHEMA)
    write_note(project, "people/Alice.md", person_note("Alice", "- [name] Alice\n- [role] Eng\n"))
    write_note(
        project,
        "people/Bob.md",
        person_note("Bob", "- [role] Eng\n- [role] Ops\n- [hobby] Chess\n- [hobby] Go\n"),
    )


# --- validate ---


def test_validate_renders_table_and_json(app, test_project, config_manager) -> None:
    write_people(test_project)

    result = bm_schema("validate", "person")
    assert result.exit_code == 0, result.output
    assert "Schema Validation: person" in result.stdout
    assert "Alice" in result.stdout
    assert "Bob" in result.stdout
    assert "pass" in result.stdout
    assert "warn" in result.stdout
    assert "2/2 valid" in result.stdout

    data = bm_schema_json("validate", "person", "--project", test_project.name)
    assert data["note_type"] == "person"
    assert data["total_notes"] == 2
    assert {row["note_identifier"] for row in data["results"]} == {"Alice", "Bob"}


def test_validate_target_with_slash_is_one_note(app, test_project, config_manager) -> None:
    write_people(test_project)

    data = bm_schema_json("validate", "people/Alice.md")

    assert data["total_notes"] == 1
    assert [row["note_identifier"] for row in data["results"]] == ["Alice"]


def test_validate_strict_exits_on_errors(app, test_project, config_manager) -> None:
    write_note(test_project, "schemas/Person.md", PERSON_SCHEMA.replace("warn", "strict"))
    write_note(test_project, "people/Bob.md", person_note("Bob", "- [role] Eng\n"))

    rich = bm_schema("validate", "person", "--strict")
    assert rich.exit_code == 1
    assert "fail" in rich.stdout

    # JSON stays parseable on stdout even when --strict fails the command.
    result = bm_schema("validate", "person", "--json", "--strict")
    assert result.exit_code == 1
    assert json.loads(result.stdout)["error_count"] == 1


def test_validate_nothing_to_report_prints_message(app, test_project, config_manager) -> None:
    rich = bm_schema("validate", "person")
    assert rich.exit_code == 0, rich.output
    assert "No notes found of type 'person'" in rich.stdout

    data = bm_schema_json("validate")
    assert data == {"error": "No schemas defined in this project"}


def test_validate_invalid_schema_fails(app, test_project, config_manager) -> None:
    write_note(test_project, "schemas/Person.md", PERSON_SCHEMA.replace("warn", "bogus"))
    write_note(test_project, "people/Alice.md", person_note("Alice", "- [name] Alice\n"))

    result = bm_schema("validate", "person")

    assert result.exit_code == 1
    assert "Error during schema validate" in result.stderr
    assert "Invalid settings.validation value 'bogus'" in result.stderr


# --- infer ---


def test_infer_renders_table_and_json(app, test_project, config_manager) -> None:
    write_people(test_project)

    result = bm_schema("infer", "person")
    assert result.exit_code == 0, result.output
    assert "Analyzing 2 notes with type: person" in result.stdout
    assert "Field Frequencies" in result.stdout
    assert "required" in result.stdout
    assert "optional" in result.stdout
    assert "Suggested schema" in result.stdout

    data = bm_schema_json("infer", "person")
    assert data["note_type"] == "person"
    assert data["notes_analyzed"] == 2
    assert "role" in data["suggested_required"]


def test_infer_threshold_and_save(app, test_project, config_manager) -> None:
    write_people(test_project)
    # A third person keeps hobby at 1 note in 3, between the two thresholds below.
    write_note(test_project, "people/Cy.md", person_note("Cy", "- [name] Cy\n- [role] Eng\n"))

    assert "hobby" in bm_schema_json("infer", "person")["suggested_optional"]
    assert "hobby" in bm_schema_json("infer", "person", "--threshold", "0.5")["excluded"]

    saved = bm_schema("infer", "person", "--threshold", "0.5", "--save")
    assert saved.exit_code == 0, saved.output
    assert "excluded" in saved.stdout
    assert "--save not yet implemented" in saved.stdout


def test_infer_without_notes_prints_message(app, test_project, config_manager) -> None:
    rich = bm_schema("infer", "person")
    assert rich.exit_code == 0, rich.output
    assert "No notes found of type 'person'" in rich.stdout

    assert bm_schema_json("infer", "person") == {"error": "No notes found of type 'person'"}


# --- diff ---


def test_diff_renders_drift_and_json(app, test_project, config_manager) -> None:
    write_people(test_project)
    # A second note repeating role makes multi-value role usage the majority.
    write_note(test_project, "people/Cy.md", person_note("Cy", "- [role] Eng\n- [role] Ops\n"))

    result = bm_schema("diff", "person")
    assert result.exit_code == 0, result.output
    assert "Schema drift detected for person" in result.stdout
    assert "+ hobby" in result.stdout
    assert "- email" in result.stdout
    assert "~ role: schema declares single-value but usage is typically array" in result.stdout

    data = bm_schema_json("diff", "person")
    assert data["note_type"] == "person"
    assert [field["name"] for field in data["new_fields"]] == ["hobby"]
    assert [field["name"] for field in data["dropped_fields"]] == ["email"]


def test_diff_no_drift(app, test_project, config_manager) -> None:
    write_note(test_project, "schemas/Person.md", PERSON_SCHEMA)
    write_note(
        test_project,
        "people/Alice.md",
        person_note("Alice", "- [name] Alice\n- [role] Eng\n- [email] a@example.com\n"),
    )

    result = bm_schema("diff", "person")

    assert result.exit_code == 0, result.output
    assert "No drift detected for person schema." in result.stdout


def test_diff_without_schema_prints_message(app, test_project, config_manager) -> None:
    write_note(test_project, "people/Alice.md", person_note("Alice", "- [name] Alice\n"))

    rich = bm_schema("diff", "person")
    assert rich.exit_code == 0, rich.output
    assert "No schema found for type 'person'" in rich.stdout

    assert bm_schema_json("diff", "person") == {"error": "No schema found for type 'person'"}


# --- project resolution ---


def test_project_passes_through_like_bm_tool(app, test_project, config_manager) -> None:
    """--project goes to bm_schema unchanged, so a project id routes as it does in bm tool."""
    write_people(test_project)

    data = bm_schema_json("validate", "person", "--project", test_project.external_id)

    assert data["total_notes"] == 2


def test_unknown_project_fails(app, test_project, config_manager) -> None:
    result = bm_schema("validate", "--project", "missing")

    assert result.exit_code == 1
    assert "Error during schema validate" in result.stderr
    assert "missing" in result.stderr
