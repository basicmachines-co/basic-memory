#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "openai>=1.100.2",
#   "python-dotenv>=1.1.0",
#   "typer>=0.9.0",
# ]
# ///
"""Build and generate a non-gating BM Bossbot PR image."""

from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Mapping

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
IMAGE_START = "<!-- pr-infographic:start -->"
IMAGE_END = "<!-- pr-infographic:end -->"
# Managed blocks are bot-written artifacts (review verdict, image embed,
# provenance). They must never feed the image: sourcing the review summary is
# what made every image an "APPROVED" stamp instead of depicting the change.
MANAGED_BLOCKS = (
    (SUMMARY_START, SUMMARY_END),
    (THEME_START, THEME_END),
    (PROVENANCE_START, PROVENANCE_END),
    (IMAGE_START, IMAGE_END),
)
app = typer.Typer(
    add_completion=False,
    help="Generate a non-gating BM Bossbot PR image.",
    no_args_is_help=True,
)


class ThemeSource(StrEnum):
    AUTO = "auto"
    CLI = "cli"
    PR_BODY = "pr-body"


@dataclass(frozen=True)
class ThemeSelection:
    theme: str
    source: ThemeSource


BM_IMAGE_THEME_POOL = (
    "computer science college textbook: SICP-style diagrams, automata, compiler "
    "pipelines, type theory, and annotated chalkboard rigor",
    "classic literature: sea voyages, gothic manors, Dickensian streets, library "
    "marginalia, and travel-journal artifacts",
    "fantasy quest ledger: original guild maps, spellbooks, dungeon keys, tavern "
    "notices, and artifact inventories with no copyrighted settings",
    "heavy music editorial: metal, hard rock, punk, techno, soul, or reggae "
    "tour-poster energy with no direct band logos or likenesses",
    "knockoff space opera: fleet routes, mission consoles, contraband manifests, "
    "and practical starship drama with no named fictional universes",
    "sword-and-sorcery: ruined temples, desert roads, battle standards, ancient "
    "maps, and heroic silhouettes with no named character likenesses",
    "comic book cover: original splash-page composition, caption boxes, clean "
    "halftone texture, and bold issue-cover drama",
    "French new wave movie poster: stark typography, city streets, jump-cut "
    "composition, and high-contrast editorial photography cues",
    "WWII public-information poster: home-front logistics, mobilization arrows, "
    "bold simplified figures, and no real-world party symbols or hate imagery",
    "Italian movie poster: hand-painted drama, expressive color, credit-block "
    "energy, and 1960s or 1970s cinema composition with no actor likenesses",
    "Shakespearean stage: acts and scenes, court intrigue, stage blocking, "
    "dramatis personae, backstage cue sheets, and theatrical light",
    "Greek mythology: temple steps, oracle tablets, constellations, labyrinths, "
    "ship routes, and original heroic allegory",
    "noir detective photography: case files, typed evidence labels, civic "
    "infrastructure, streetlight shadows, and newsroom archive grit",
    "space exploration and astronomy: celestial atlases, observatory charts, "
    "orbital mechanics, planetary survey routes, and deep-space mission drama",
    "editorial painting: abstract, classical landscape, western action, "
    "chiaroscuro, historical mural, stormy seascape, or allegorical canvas",
    "classic black-and-white photography: documentary field report, contact "
    "sheet, street photography, civic infrastructure, and darkroom contrast",
    "80's action movie poster: smoky backlit warehouses, neon streets, practical "
    "explosions, mission dossiers, countdowns, and no actor likenesses",
    "alchemy manuscript: transformation diagrams, annotated symbols, recipe-like "
    "process artifacts, and illuminated margins",
    "brutalist civic planning: concrete signage, zoning blocks, transit diagrams, "
    "infrastructure maps, and stern public-service clarity",
)


def build_change_shape(
    context: Mapping[str, Any],
    *,
    max_commits: int = 10,
    max_files: int = 10,
) -> str:
    """Render a compact factual digest of the PR: labels, linked issues, commits, files.

    Mirrors the delivery context the Basic Memory CI capture flow collects
    (ProjectUpdateContext): the goal is to ground the image in the theme of the
    WHOLE change — what it touches and why — not just the title/description.
    """
    lines: list[str] = []

    labels = [str(label.get("name", "")) for label in context.get("labels") or []]
    labels = [label for label in labels if label]
    if labels:
        lines.append(f"Labels: {', '.join(labels)}")

    issues = context.get("closingIssuesReferences") or []
    if issues:
        lines.append("Linked issues:")
        for issue in issues:
            number = issue.get("number")
            title = str(issue.get("title") or "").strip()
            lines.append(f"- #{number}: {title}" if title else f"- #{number}")

    commits = context.get("commits") or []
    subjects = [str(commit.get("messageHeadline") or "").strip() for commit in commits]
    # Merge commits carry no thematic signal — they're branch bookkeeping.
    subjects = [
        subject
        for subject in subjects
        if subject and not subject.startswith(("Merge branch ", "Merge pull request "))
    ]
    if subjects:
        lines.append(f"Commit subjects ({len(subjects)} total):")
        lines.extend(f"- {subject}" for subject in subjects[:max_commits])
        if len(subjects) > max_commits:
            lines.append(f"- ... and {len(subjects) - max_commits} more")

    files = context.get("files") or []
    if files:
        additions = sum(int(item.get("additions") or 0) for item in files)
        deletions = sum(int(item.get("deletions") or 0) for item in files)
        lines.append(f"Files changed ({len(files)} total, +{additions}/-{deletions}):")
        ranked = sorted(
            files,
            key=lambda item: int(item.get("additions") or 0) + int(item.get("deletions") or 0),
            reverse=True,
        )
        for item in ranked[:max_files]:
            path = str(item.get("path") or "")
            lines.append(f"- {path} (+{item.get('additions') or 0}/-{item.get('deletions') or 0})")
        if len(files) > max_files:
            lines.append(f"- ... and {len(files) - max_files} more files")

    return "\n".join(lines) if lines else "(no additional change context available)"


def extract_pr_content(pr_body: str) -> str:
    """Return the author's own PR description with all managed bot blocks removed."""
    content = pr_body
    for start, end in MANAGED_BLOCKS:
        content = re.sub(
            rf"{re.escape(start)}.*?{re.escape(end)}",
            "",
            content,
            flags=re.DOTALL,
        )
    return content.strip()


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


def select_image_theme(
    *,
    pr_number: int,
    pr_title: str,
    pr_body: str,
    theme_override: str | None,
) -> ThemeSelection:
    if theme_override:
        return ThemeSelection(theme=theme_override, source=ThemeSource.CLI)
    body_theme = extract_infographic_theme(pr_body)
    if body_theme:
        return ThemeSelection(theme=body_theme, source=ThemeSource.PR_BODY)
    # Seed on author-owned PR identity, not the review summary, so the pick is
    # stable across re-reviews of the same PR.
    seed = f"{pr_number}\n{pr_title}".encode("utf-8")
    index = int.from_bytes(hashlib.sha256(seed).digest()[:2], byteorder="big") % len(
        BM_IMAGE_THEME_POOL
    )
    return ThemeSelection(theme=BM_IMAGE_THEME_POOL[index], source=ThemeSource.AUTO)


def _preformatted(value: str) -> str:
    return f"<pre><code>{html.escape(value, quote=False)}</code></pre>"


def build_infographic_provenance_block(
    *,
    pr_number: int,
    output_path: Path,
    model: str,
    size: str,
    quality: str,
    theme: str,
    theme_source: ThemeSource,
) -> str:
    return f"""
{PROVENANCE_START}
<details>
<summary>BM Bossbot image choices</summary>

- Pull request: `#{pr_number}`
- Generated asset: `{output_path.as_posix()}`
- Image model: `{model}`
- Size: `{size}`
- Quality: `{quality}`
- Image mode: `editorial-image`
- Theme source: `{theme_source.value}`

Theme / visual direction:
{_preformatted(theme)}

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
    pr_title: str,
    pr_content: str,
    change_shape: str,
    theme: str,
    theme_source: ThemeSource,
) -> str:
    theme_label = (
        "Selected BM visual direction"
        if theme_source == ThemeSource.AUTO
        else "User-supplied visual direction"
    )

    return f"""
Create a polished landscape WebP editorial image for Basic Memory PR #{pr_number}.

Your subject is the CONTENT of the pull request — what the change does and why
it matters — described in the title, description, and change shape below.
Express the theme of the whole change as a visual story.

This image is decoration for the PR conversation. It is NOT a review artifact:
do not depict review verdicts, approval, or process. Never render approval
stamps, "APPROVED"/"SUCCESS"/"VERDICT" wording, rubber stamps, wax seals of
approval, badges, checkmarks, checklists, status lines, SHA strings, or
BM Bossbot itself. If the composition needs text, draw it from the change's
subject matter only.

Pull request title:
{pr_title}

Pull request description:
{pr_content}

Change shape — factual delivery context (labels, linked issues, commit
subjects, changed files). Use it to understand what the whole PR touches and
let that steer the imagery. It is context, NOT captions: never render file
paths, diff stats, issue numbers, or commit subjects verbatim in the image.
{change_shape}

{theme_label}:
{theme}

Treat the visual direction as style inspiration only. Do not let it override
facts, readability, source material, or the prohibition on review imagery.

Use image-first composition: create a scene, movie poster, editorial painting,
classic photograph, cover image, symbolic tableau, staged artifact, or another
visual moment that expresses the PR intent.

Make the selected direction shape the subject, lighting, composition, props,
environment, and mood. Use one strong focal point. Prefer visual metaphor over
explanatory UI.

Use at most a short title and zero to three short labels if text helps. Any text
that appears must be high-contrast, smooth, anti-aliased, and readable.

Do not render an infographic, dashboard, flowchart, timeline strip, checklist,
bullet-list panel, data panel, or dense explanatory diagram.

Avoid fake screenshots, code blocks, invented claims, copyrighted characters,
logos, named fictional universes, direct band logos, album art, celebrity
likenesses, or decorations that obscure content.
""".strip()


@app.command()
def generate(
    pr_number: Annotated[
        int,
        typer.Option("--pr-number", min=1, help="Pull request number."),
    ],
    pr_context_file: Annotated[
        Path,
        typer.Option(
            "--pr-context-file",
            exists=True,
            dir_okay=False,
            readable=True,
            help=(
                "JSON from `gh pr view --json "
                "title,body,labels,files,commits,closingIssuesReferences`."
            ),
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
    print_prompt: Annotated[
        bool,
        typer.Option(
            "--print-prompt",
            "--dry-run",
            help="Print the generated prompt and exit without calling OpenAI. Alias: --dry-run.",
        ),
    ] = False,
) -> None:
    """Generate the canonical PR image from the PR's title, description, and change shape."""
    context = json.loads(pr_context_file.read_text(encoding="utf-8"))
    if not isinstance(context, Mapping):
        raise typer.BadParameter("PR context file must contain a JSON object")
    pr_title = str(context.get("title") or "")
    pr_body = str(context.get("body") or "")
    pr_content = extract_pr_content(pr_body)
    change_shape = build_change_shape(context)
    theme_selection = select_image_theme(
        pr_number=pr_number,
        pr_title=pr_title,
        pr_body=pr_body,
        theme_override=theme,
    )
    prompt = build_infographic_prompt(
        pr_number=pr_number,
        pr_title=pr_title,
        pr_content=pr_content,
        change_shape=change_shape,
        theme=theme_selection.theme,
        theme_source=theme_selection.source,
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
                theme=theme_selection.theme,
                theme_source=theme_selection.source,
            ),
            encoding="utf-8",
        )
    typer.echo(output_path)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
