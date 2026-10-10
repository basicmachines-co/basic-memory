"""The write_note similar-notes advisory against the real default embedding model (#1718).

The advisory asks "might this be the same note?". On bge-small-en-v1.5 the nearest
neighbor of a note on an unrelated topic scores up to ~0.67 while a rewrite of an
existing note scores 0.77 and up. These tests run the exact query write_note sends and
pin both sides: a rewrite still finds its original, and an unrelated note finds nothing.
"""

from __future__ import annotations

import pytest

from basic_memory import db
from basic_memory.config import DatabaseBackend
from basic_memory.mcp.tools.write_note import similar_notes_query

from semantic.conftest import (
    SearchCombo,
    _create_fastembed_provider,
    create_search_service,
    skip_if_needed,
)

COMBO = SearchCombo("sqlite-fastembed", DatabaseBackend.SQLITE, "fastembed", 384)

EXISTING_NOTES = {
    "notes/coffee-brewing": (
        "Coffee Brewing Methods",
        "Pour over gives clarity and a clean cup. French press makes a heavier body. "
        "Water just off the boil, around 205F, extracts well. Use a 1:16 ratio of coffee "
        "to water.",
    ),
    "notes/sourdough": (
        "Sourdough Starter Care",
        "Feed the starter flour and water every day at room temperature. It is ready when "
        "it doubles within six hours and smells tangy. Keep it in the fridge between bakes.",
    ),
    "notes/bike-repair": (
        "Fixing a Flat Bicycle Tire",
        "Remove the wheel, lever the tire off the rim, and find the puncture by inflating "
        "the tube in water. Patch it or replace the tube, then reseat the tire and inflate "
        "to the rated pressure.",
    ),
    "notes/standup": (
        "Team Standup 2026-10-06",
        "Alice finished the billing page. Bob is blocked on the staging database "
        "credentials. Action item: Carol to rotate the staging password by Thursday.",
    ),
    "notes/guitar": (
        "Learning Guitar Chords",
        "Practice the G, C and D open chords until changes are smooth. Use a metronome at "
        "sixty beats per minute. Calluses form after about two weeks of daily practice.",
    ),
    "notes/houseplants": (
        "Houseplant Watering Schedule",
        "Water the fern when the topsoil is dry. Succulents need water only every two "
        "weeks. Repot the orchid in bark after it finishes blooming.",
    ),
}


def _note(title: str, body: str) -> str:
    return f"# {title}\n\n{body}\n"


@pytest.fixture
async def advisory_search(sqlite_engine_factory, tmp_path):
    skip_if_needed(COMBO)
    search_service = await create_search_service(
        sqlite_engine_factory, COMBO, tmp_path, embedding_provider=_create_fastembed_provider()
    )
    for permalink, (title, body) in EXISTING_NOTES.items():
        async with db.scoped_session(search_service.session_maker) as session:
            entity = await search_service.entity_repository.create(
                session,
                {
                    "title": title,
                    "note_type": "note",
                    "entity_metadata": {},
                    "content_type": "text/markdown",
                    "permalink": permalink,
                    "file_path": f"{permalink}.md",
                },
            )
        await search_service.index_entity_data(entity, content=_note(title, body))
        await search_service.sync_entity_vectors(entity.id)
    return search_service


@pytest.mark.asyncio
@pytest.mark.semantic
@pytest.mark.parametrize(
    ("title", "body", "original"),
    [
        (
            "How I Brew Coffee",
            "My coffee routine: pour-over for a clean, clear cup, sometimes a French press "
            "when I want more body. Water around 205 degrees, about one part coffee to "
            "sixteen parts water.",
            "notes/coffee-brewing",
        ),
        (
            "Repairing a Punctured Bike Tube",
            "Take the wheel off, pry the tire from the rim with levers, and locate the hole "
            "by submerging the inflated tube. Patch or swap the tube, refit the tire and "
            "pump to the pressure on the sidewall.",
            "notes/bike-repair",
        ),
    ],
)
async def test_rewrite_of_an_existing_note_is_still_suggested(
    advisory_search, title, body, original
):
    results = await advisory_search.search(similar_notes_query(title, _note(title, body)), limit=4)

    assert results, "a rewrite of an existing note must clear the advisory floor"
    assert results[0].permalink == original


@pytest.mark.asyncio
@pytest.mark.semantic
@pytest.mark.parametrize(
    ("title", "body"),
    [
        (
            "Kubernetes Autoscaling",
            "The horizontal pod autoscaler scales replicas on CPU utilization. Set resource "
            "requests on every container or the HPA cannot compute utilization.",
        ),
        (
            "Test Session Notes",
            "Ran the acceptance suite on main. Checksum-guarded edits refused stale "
            "revisions. Diagnostics listed environment overrides.",
        ),
        (
            "Jazz Harmony",
            "The ii-V-I progression is the backbone of jazz standards. Tritone substitution "
            "swaps the V chord for a dominant a tritone away.",
        ),
    ],
)
async def test_note_on_an_unrelated_topic_gets_no_suggestions(advisory_search, title, body):
    results = await advisory_search.search(similar_notes_query(title, _note(title, body)), limit=4)

    assert [row.permalink for row in results] == []
