#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "openai>=1.100.2",
#   "python-dotenv>=1.1.0",
#   "typer>=0.9.0",
# ]
# ///
"""Build and generate a non-gating BM Bossbot PR infographic."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer

if __package__:
    from .generate_infographic import (
        DEFAULT_MODEL,
        DEFAULT_QUALITY,
        DEFAULT_SIZE,
        generate_image_result,
    )
else:
    from generate_infographic import (
        DEFAULT_MODEL,
        DEFAULT_QUALITY,
        DEFAULT_SIZE,
        generate_image_result,
    )


SUMMARY_START = "<!-- BM_BOSSBOT_SUMMARY:start -->"
SUMMARY_END = "<!-- BM_BOSSBOT_SUMMARY:end -->"
THEME_START = "<!-- BM_INFOGRAPHIC_THEME:start -->"
THEME_END = "<!-- BM_INFOGRAPHIC_THEME:end -->"
PROVENANCE_START = "<!-- BM_INFOGRAPHIC_PROVENANCE:start -->"
PROVENANCE_END = "<!-- BM_INFOGRAPHIC_PROVENANCE:end -->"
app = typer.Typer(
    add_completion=False,
    help="Generate a non-gating BM Bossbot PR infographic.",
    no_args_is_help=True,
)


class VisualFormat(StrEnum):
    AUTO = "auto"
    INFOGRAPHIC = "infographic"
    IMAGE = "image"


class ThemeSource(StrEnum):
    CLI = "cli"
    PR_BODY = "pr-body"
    NONE = "none"


@dataclass(frozen=True)
class ThemeSelection:
    theme: str | None
    source: ThemeSource


def extract_bossbot_summary(pr_body: str) -> str:
    pattern = re.compile(
        rf"{re.escape(SUMMARY_START)}\s*(.*?)\s*{re.escape(SUMMARY_END)}",
        flags=re.DOTALL,
    )
    match = pattern.search(pr_body)
    if not match:
        raise ValueError("PR body is missing the BM Bossbot summary block")
    return match.group(1).strip()


def extract_infographic_theme(pr_body: str) -> str | None:
    pattern = re.compile(
        rf"{re.escape(THEME_START)}\s*(.*?)\s*{re.escape(THEME_END)}",
        flags=re.DOTALL,
    )
    match = pattern.search(pr_body)
    if not match:
        return None
    theme = match.group(1).strip()
    return theme or None


def select_infographic_theme(*, pr_body: str, theme_override: str | None) -> ThemeSelection:
    if theme_override:
        return ThemeSelection(theme=theme_override, source=ThemeSource.CLI)
    body_theme = extract_infographic_theme(pr_body)
    if body_theme:
        return ThemeSelection(theme=body_theme, source=ThemeSource.PR_BODY)
    return ThemeSelection(theme=None, source=ThemeSource.NONE)


def _visual_format_guidance(visual_format: VisualFormat) -> str:
    if visual_format == VisualFormat.INFOGRAPHIC:
        return """
Visual mode: infographic/map.

Use an infographic or map format. Use structured information design:
- data panels, route maps, timeline bands, status badges, legends, checkpoints,
  before/after boxes, and compact bullet list sections are appropriate.
- Organize the source facts into scannable sections with plain-language labels.
- Show the before/after value story through layout, hierarchy, and evidence.
- Do not render this as a primarily scenic image, movie poster, or painting.
""".strip()
    if visual_format == VisualFormat.IMAGE:
        return """
Visual mode: regular image/scene.

Use a regular image format: actual scene, movie poster, editorial painting,
tableau, cover image, or illustrated artifact.

Use image-first composition:
- Create a single staged visual moment with one strong focal point.
- Communicate intent through cinematic staging, editorial metaphor, atmosphere,
  characters, objects, architecture, landscape, lighting, and motion.
- Use at most a short title plus zero to three short labels when text is needed.
- Convert process details into visual symbols instead of explanatory boxes.
- Do not use data panels, dashboard layouts, timeline strips, flowcharts,
  legends, before/after boxes, bullet lists, checklist columns, or small
  explanatory labels.
- Do not render an infographic or dense text-heavy infographic.
""".strip()
    return """
Choose the most appropriate visual form: infographic, map, scene, poster,
painting, tableau, cover image, illustrated artifact, or another image form that
best communicates the PR intent. Choose exactly one visual mode and follow only
that mode's rules. Do not blend the modes.

Mode A - infographic/map:
- Use a readable map backbone with structured information design: sections,
  route lines, checkpoints, nodes, annotations, status badges, compact evidence
  bullets, and a legend.
- Use this mode when the PR needs several facts, gates, checks, or before/after
  points to be read explicitly.

Mode B - editorial scene/poster/painting:
- Use image-first composition: an actual scene, movie poster, painting, tableau,
  cover image, or symbolic illustrated artifact.
- Use this mode when the PR intent can be shown through one staged visual moment
  with minimal text.
- Avoid dashboard layouts, data panels, timeline strips, flowcharts, legends,
  before/after boxes, bullet lists, and checklist columns in this mode.
""".strip()


def _preformatted(value: str) -> str:
    return f"<pre><code>{html.escape(value, quote=False)}</code></pre>"


def build_infographic_provenance_block(
    *,
    pr_number: int,
    output_path: Path,
    model: str,
    size: str,
    quality: str,
    visual_format: VisualFormat,
    theme: str | None,
    theme_source: ThemeSource,
    prompt: str,
    revised_prompt: str | None = None,
) -> str:
    theme_section = "_None supplied._" if theme is None else _preformatted(theme)
    revised_prompt_section = (
        "_Not provided by the Images API._"
        if revised_prompt is None
        else _preformatted(revised_prompt)
    )
    return f"""
{PROVENANCE_START}
<details>
<summary>BM Bossbot image provenance</summary>

- Pull request: `#{pr_number}`
- Generated asset: `{output_path.as_posix()}`
- Image model: `{model}`
- Size: `{size}`
- Quality: `{quality}`
- Visual format: `{visual_format.value}`
- Theme source: `{theme_source.value}`

Theme / choice instruction:
{theme_section}

Image prompt sent to `{model}`:
{_preformatted(prompt)}

Images API revised prompt:
{revised_prompt_section}

</details>
{PROVENANCE_END}
""".strip()


def upsert_managed_block(body: str, *, block: str, start: str, end: str) -> str:
    pattern = re.compile(rf"{re.escape(start)}.*?{re.escape(end)}", flags=re.DOTALL)
    if pattern.search(body):
        return pattern.sub(block, body, count=1)
    if body.strip():
        return f"{body.rstrip()}\n\n{block}\n"
    return f"{block}\n"


def build_infographic_prompt(
    *,
    pr_number: int,
    summary: str,
    theme: str | None = None,
    visual_format: VisualFormat = VisualFormat.AUTO,
) -> str:
    theme_section = ""
    if theme:
        theme_section = f"""

Optional user-supplied visual theme preference:
{theme}

Treat the theme as style inspiration only. Do not let it override facts,
readability, source material, or the non-gating status of this image.
""".rstrip()

    return f"""
Create a polished landscape WebP visual for Basic Memory PR #{pr_number}.

This is a non-gating visual summary. The authoritative merge gate is the
GitHub commit status named BM Bossbot Approval, not this image.

Use the BM Bossbot review summary below as source material. Preserve the
concrete before/after value story without inventing facts or turning
implementation details into clutter.

{_visual_format_guidance(visual_format)}

The visual theme should drive the composition through original style cues while
the engineering meaning stays easy to scan.

Use high contrast, smooth anti-aliased text when text is present, clean edges,
and non-tiny labels. Text is optional for scene-first images, but any text that
appears must be readable.

Avoid fake screenshots, code blocks, invented claims, copyrighted characters,
logos, named fictional universes, direct band logos, album art, celebrity
likenesses, or decorations that obscure content.

BM Bossbot summary:
{summary}
{theme_section}
""".strip()


@app.command()
def generate(
    pr_number: Annotated[
        int,
        typer.Option("--pr-number", min=1, help="Pull request number."),
    ],
    pr_body_file: Annotated[
        Path,
        typer.Option(
            "--pr-body-file",
            exists=True,
            dir_okay=False,
            readable=True,
            help="File containing the pull request body.",
        ),
    ],
    output: Annotated[Path, typer.Option("--output", help="Output .webp path.")],
    model: Annotated[str, typer.Option("--model", help="OpenAI image model.")] = DEFAULT_MODEL,
    size: Annotated[str, typer.Option("--size", help="Image size.")] = DEFAULT_SIZE,
    quality: Annotated[str, typer.Option("--quality", help="Image quality.")] = DEFAULT_QUALITY,
    retries: Annotated[int, typer.Option("--retries", min=0, help="Retry attempts.")] = 2,
    theme: Annotated[
        str | None,
        typer.Option("--theme", help="Optional visual theme preference."),
    ] = None,
    provenance_output: Annotated[
        Path | None,
        typer.Option(
            "--provenance-output",
            dir_okay=False,
            help="Optional file to write the managed PR-body provenance block.",
        ),
    ] = None,
    visual_format: Annotated[
        VisualFormat,
        typer.Option(
            "--visual-format",
            case_sensitive=False,
            help="Visual format to request: auto, infographic, or image.",
        ),
    ] = VisualFormat.AUTO,
    print_prompt: Annotated[
        bool,
        typer.Option(
            "--print-prompt",
            "--dry-run",
            help="Print the generated prompt and exit without calling OpenAI. Alias: --dry-run.",
        ),
    ] = False,
) -> None:
    """Generate the canonical PR infographic from a BM Bossbot summary block."""
    pr_body = pr_body_file.read_text(encoding="utf-8")
    summary = extract_bossbot_summary(pr_body)
    theme_selection = select_infographic_theme(pr_body=pr_body, theme_override=theme)
    prompt = build_infographic_prompt(
        pr_number=pr_number,
        summary=summary,
        theme=theme_selection.theme,
        visual_format=visual_format,
    )
    if print_prompt:
        typer.echo(prompt)
        raise typer.Exit()

    image_result = generate_image_result(
        prompt=prompt,
        output_path=output,
        model=model,
        size=size,
        quality=quality,
        retries=retries,
    )
    output_path = image_result.path
    if provenance_output:
        provenance_output.parent.mkdir(parents=True, exist_ok=True)
        provenance_output.write_text(
            build_infographic_provenance_block(
                pr_number=pr_number,
                output_path=output_path,
                model=model,
                size=size,
                quality=quality,
                visual_format=visual_format,
                theme=theme_selection.theme,
                theme_source=theme_selection.source,
                prompt=prompt,
                revised_prompt=image_result.revised_prompt,
            ),
            encoding="utf-8",
        )
    typer.echo(output_path)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
