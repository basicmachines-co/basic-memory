"""Real CLI note-type predicates must accept authored and displayed spellings."""

import json

from typer.testing import CliRunner

from basic_memory.cli.main import app
import pytest

pytestmark = pytest.mark.usefixtures("app")

runner = CliRunner()


def test_find_chapter_type_round_trip(app_config, test_project, config_manager):
    write = runner.invoke(
        app,
        [
            "tool",
            "write-note",
            "--title",
            "Chapter One",
            "--folder",
            "chapters",
            "--content",
            "---\ntype: Chapter\nchapter_number: 1\n---\nChapter body.",
        ],
    )
    assert write.exit_code == 0, write.output
    permalink = json.loads(write.stdout)["permalink"]

    authored = runner.invoke(app, ["find", "--meta", "note_type=Chapter", "--json"])
    assert authored.exit_code == 0, authored.output
    rows = json.loads(authored.stdout)["results"]
    assert [row["permalink"] for row in rows] == [permalink]
    displayed_type = rows[0]["metadata"]["note_type"]
    assert displayed_type == "chapter"

    repeated = runner.invoke(app, ["find", "--meta", f"note_type={displayed_type}", "--json"])
    assert repeated.exit_code == 0, repeated.output
    assert [row["permalink"] for row in json.loads(repeated.stdout)["results"]] == [permalink]


def test_find_multiword_type_round_trip(app_config, test_project, config_manager):
    write = runner.invoke(
        app,
        [
            "tool",
            "write-note",
            "--title",
            "Metaphor",
            "--folder",
            "devices",
            "--content",
            "---\ntype: LiteraryDevice\n---\nA comparison.",
        ],
    )
    assert write.exit_code == 0, write.output
    permalink = json.loads(write.stdout)["permalink"]

    authored = runner.invoke(app, ["find", "--meta", "type=LiteraryDevice", "--json"])
    assert authored.exit_code == 0, authored.output
    rows = json.loads(authored.stdout)["results"]
    assert [row["permalink"] for row in rows] == [permalink]
    displayed_type = rows[0]["metadata"]["note_type"]
    assert displayed_type == "literary_device"

    repeated = runner.invoke(app, ["find", "--meta", f"note_type={displayed_type}", "--json"])
    assert repeated.exit_code == 0, repeated.output
    assert [row["permalink"] for row in json.loads(repeated.stdout)["results"]] == [permalink]


def test_find_type_membership_preserves_scope_and_other_predicates(
    app_config, test_project, config_manager
):
    chapter = runner.invoke(
        app,
        [
            "tool",
            "write-note",
            "--title",
            "Chapter One",
            "--folder",
            "book",
            "--content",
            "---\ntype: Chapter\nstatus: Active\nchapter_number: 1\n---\nBody.",
        ],
    )
    assert chapter.exit_code == 0, chapter.output
    device = runner.invoke(
        app,
        [
            "tool",
            "write-note",
            "--title",
            "Metaphor",
            "--folder",
            "book",
            "--content",
            "---\ntype: LiteraryDevice\nstatus: inactive\n---\nBody.",
        ],
    )
    assert device.exit_code == 0, device.output
    outside = runner.invoke(
        app,
        [
            "tool",
            "write-note",
            "--title",
            "Other Chapter",
            "--folder",
            "other",
            "--content",
            "---\ntype: Chapter\nstatus: Active\n---\nBody.",
        ],
    )
    assert outside.exit_code == 0, outside.output

    result = runner.invoke(
        app,
        [
            "find",
            "book",
            "--meta",
            "note_type in chapter,LiteraryDevice",
            "--meta",
            "status=Active",
            "--fields",
            "chapter_number",
            "--plain",
        ],
    )
    assert result.exit_code == 0, result.output
    assert 'book/Chapter One.md\t{"chapter_number":1}' in result.stdout
    assert "Metaphor" not in result.stdout
    assert "Other Chapter" not in result.stdout

    case_sensitive = runner.invoke(
        app, ["find", "book", "--meta", "note_type=Chapter", "--meta", "status=active", "--json"]
    )
    assert case_sensitive.exit_code == 0, case_sensitive.output
    assert json.loads(case_sensitive.stdout)["results"] == []
