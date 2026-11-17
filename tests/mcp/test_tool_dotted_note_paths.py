"""A note named `X.ai.md` beside `X.md`: reads and writes by path reach the right file.

`.ai` is an extension `mimetypes` knows, so a resolver that slugged the path twice read
`entities/X.ai.md` as `entities/x`, the sibling's permalink. A read returned the sibling,
and an edit changed it. These run the real ASGI stack through the shared `client` fixture.
"""

from pathlib import Path
from typing import Any

import pytest

from basic_memory.mcp.tools import bm_edit, bm_read, bm_write

PLAIN = "entities/Claude.md"
DOTTED = "entities/Claude.ai.md"


async def _seed(project: str) -> None:
    await bm_write(identifier=PLAIN, content="# Claude\n\nThe model", project=project)
    await bm_write(identifier=DOTTED, content="# Claude.ai\n\nThe website", project=project)


def _bytes(project_path: str, file_path: str) -> bytes:
    return (Path(project_path) / file_path).read_bytes()


async def _read_json(identifier: str, project: str) -> dict[str, Any]:
    result = await bm_read(identifier, output_format="json", project=project)
    assert isinstance(result, dict)
    return result


@pytest.mark.asyncio
async def test_read_by_dotted_path_returns_that_note(client, test_project):
    await _seed(test_project.name)

    dotted = await _read_json(DOTTED, test_project.name)
    plain = await _read_json(PLAIN, test_project.name)

    assert dotted["file_path"] == DOTTED
    assert dotted["content"].rstrip().endswith("The website")
    assert plain["file_path"] == PLAIN
    assert plain["content"].rstrip().endswith("The model")


@pytest.mark.asyncio
async def test_edit_by_dotted_path_leaves_the_sibling_untouched(client, test_project):
    await _seed(test_project.name)
    sibling_before = _bytes(test_project.path, PLAIN)

    await bm_edit(
        identifier=DOTTED,
        command="find_replace",
        find_text="The website",
        content="The web app",
        project=test_project.name,
    )

    assert "The web app" in _bytes(test_project.path, DOTTED).decode()
    assert _bytes(test_project.path, PLAIN) == sibling_before


@pytest.mark.asyncio
async def test_overwrite_by_dotted_path_leaves_the_sibling_untouched(client, test_project):
    await _seed(test_project.name)
    sibling_before = _bytes(test_project.path, PLAIN)

    await bm_write(
        identifier=DOTTED,
        content="# Claude.ai\n\nReplaced",
        overwrite=True,
        project=test_project.name,
    )

    assert _bytes(test_project.path, DOTTED).decode().rstrip().endswith("Replaced")
    assert _bytes(test_project.path, PLAIN) == sibling_before
    dotted = await _read_json(DOTTED, test_project.name)
    assert dotted["content"].rstrip().endswith("Replaced")
