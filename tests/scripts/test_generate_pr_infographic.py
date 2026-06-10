from pathlib import Path

import pytest
from click import unstyle
from typer.testing import CliRunner

from scripts import generate_infographic, generate_pr_infographic


def test_infographic_scripts_are_uv_typer_entrypoints() -> None:
    for module in (generate_infographic, generate_pr_infographic):
        source = module.__file__
        assert source is not None
        text = Path(source).read_text(encoding="utf-8")

        assert text.startswith("#!/usr/bin/env -S uv run --script\n")
        assert "# /// script" in text
        assert "typer" in text
        assert hasattr(module, "app")


def test_generate_pr_infographic_cli_help_exposes_useful_options() -> None:
    result = CliRunner().invoke(generate_pr_infographic.app, ["--help"])
    help_text = unstyle(result.output)

    assert result.exit_code == 0
    assert "--pr-number" in help_text
    assert "--pr-title" in help_text
    assert "--pr-body-file" in help_text
    assert "--output" in help_text
    assert "--theme" in help_text
    assert "--provenance-output" in help_text
    assert "--print-prompt" in help_text
    assert "--dry-run" in help_text


def test_extract_pr_content_strips_managed_bot_blocks() -> None:
    body = "\n".join(
        [
            "## Summary",
            "Adds per-workspace rclone remotes for Team push/pull.",
            "<!-- BM_BOSSBOT_SUMMARY:start -->",
            "Reviewed SHA: abc123",
            "Verdict: approve",
            "<!-- BM_BOSSBOT_SUMMARY:end -->",
            "<!-- pr-infographic:start -->",
            "![BM Bossbot image for PR #42](https://example.test/img.webp)",
            "<!-- pr-infographic:end -->",
            "<!-- BM_INFOGRAPHIC_PROVENANCE:start -->",
            "provenance details",
            "<!-- BM_INFOGRAPHIC_PROVENANCE:end -->",
            "## Test plan",
            "pytest passes.",
        ]
    )

    content = generate_pr_infographic.extract_pr_content(body)

    assert "Adds per-workspace rclone remotes" in content
    assert "pytest passes." in content
    assert "Verdict: approve" not in content
    assert "Reviewed SHA" not in content
    assert "provenance details" not in content
    assert "BM Bossbot image for PR" not in content


def test_extract_pr_content_handles_body_without_managed_blocks() -> None:
    assert generate_pr_infographic.extract_pr_content("Plain description") == "Plain description"


def test_extract_infographic_theme_from_pr_body() -> None:
    body = "\n".join(
        [
            "Before",
            "<!-- BM_INFOGRAPHIC_THEME:start -->",
            "Italian movie poster with a release-route map",
            "<!-- BM_INFOGRAPHIC_THEME:end -->",
            "After",
        ]
    )

    theme = generate_pr_infographic.extract_infographic_theme(body)

    assert theme == "Italian movie poster with a release-route map"


def test_extract_infographic_theme_is_optional() -> None:
    assert generate_pr_infographic.extract_infographic_theme("No theme") is None


def test_select_image_theme_reports_source() -> None:
    body = "\n".join(
        [
            "<!-- BM_INFOGRAPHIC_THEME:start -->",
            "paintings: Rembrandt-inspired merge gate",
            "<!-- BM_INFOGRAPHIC_THEME:end -->",
        ]
    )

    from_body = generate_pr_infographic.select_image_theme(
        pr_number=42,
        pr_title="feat(ci): add a merge gate",
        pr_body=body,
        theme_override=None,
    )
    from_cli = generate_pr_infographic.select_image_theme(
        pr_number=42,
        pr_title="feat(ci): add a merge gate",
        pr_body=body,
        theme_override="80's action movies",
    )
    from_auto = generate_pr_infographic.select_image_theme(
        pr_number=42,
        pr_title="feat(ci): add a merge gate",
        pr_body="No theme",
        theme_override=None,
    )

    assert from_body.theme == "paintings: Rembrandt-inspired merge gate"
    assert from_body.source == generate_pr_infographic.ThemeSource.PR_BODY
    assert from_cli.theme == "80's action movies"
    assert from_cli.source == generate_pr_infographic.ThemeSource.CLI
    assert from_auto.theme in generate_pr_infographic.BM_IMAGE_THEME_POOL
    assert from_auto.source == generate_pr_infographic.ThemeSource.AUTO


def test_build_infographic_prompt_depicts_pr_content_not_review_outcome() -> None:
    prompt = generate_pr_infographic.build_infographic_prompt(
        pr_number=42,
        pr_title="feat(sync): stream large files during cloud sync",
        pr_content="Streams PDFs in chunks instead of loading them fully into memory.",
        theme="WWII propaganda posters with home-front logistics routes",
        theme_source=generate_pr_infographic.ThemeSource.CLI,
    )

    assert "PR #42" in prompt
    assert "stream large files during cloud sync" in prompt
    assert "Streams PDFs in chunks" in prompt
    assert "WWII propaganda posters" in prompt
    assert "User-supplied visual direction" in prompt
    assert "style inspiration only" in prompt
    assert "polished landscape WebP editorial image" in prompt
    assert "image-first composition" in prompt
    assert "symbolic tableau" in prompt
    assert "Do not render an infographic" in prompt
    assert "dashboard" in prompt
    assert "flowchart" in prompt
    assert "copyrighted characters" in prompt
    # The subject is the change itself; review-process imagery is banned.
    assert "CONTENT of the pull request" in prompt
    assert "do not depict review verdicts" in prompt
    assert "approval" in prompt.lower()
    assert "stamps" in prompt
    assert "checkmarks" in prompt
    # The old prompt fed the review summary and named the approval status,
    # which produced literal "BOSSBOT APPROVED" stamp images.
    assert "BM Bossbot summary:" not in prompt
    assert "BM Bossbot Approval" not in prompt


def test_build_infographic_provenance_block_includes_image_choices_without_prompt() -> None:
    block = generate_pr_infographic.build_infographic_provenance_block(
        pr_number=42,
        output_path=Path("docs/assets/infographics/pr-42.webp"),
        model="gpt-image-2",
        size="1536x1024",
        quality="high",
        theme="classic black-and-white photography",
        theme_source=generate_pr_infographic.ThemeSource.CLI,
    )

    assert generate_pr_infographic.PROVENANCE_START in block
    assert generate_pr_infographic.PROVENANCE_END in block
    assert "BM Bossbot image choices" in block
    assert "Generated asset: `docs/assets/infographics/pr-42.webp`" in block
    assert "Image model: `gpt-image-2`" in block
    assert "Size: `1536x1024`" in block
    assert "Quality: `high`" in block
    assert "Image mode: `editorial-image`" in block
    assert "Theme source: `cli`" in block
    assert "classic black-and-white photography" in block
    assert "Image prompt sent to" not in block
    assert "Images API revised prompt" not in block


def test_upsert_managed_block_appends_and_replaces() -> None:
    first = "\n".join(
        [
            generate_pr_infographic.PROVENANCE_START,
            "first",
            generate_pr_infographic.PROVENANCE_END,
        ]
    )
    second = "\n".join(
        [
            generate_pr_infographic.PROVENANCE_START,
            "second",
            generate_pr_infographic.PROVENANCE_END,
        ]
    )

    appended = generate_pr_infographic.upsert_managed_block(
        "Existing body",
        block=first,
        start=generate_pr_infographic.PROVENANCE_START,
        end=generate_pr_infographic.PROVENANCE_END,
    )
    replaced = generate_pr_infographic.upsert_managed_block(
        appended,
        block=second,
        start=generate_pr_infographic.PROVENANCE_START,
        end=generate_pr_infographic.PROVENANCE_END,
    )

    assert appended == f"Existing body\n\n{first}\n"
    assert "first" not in replaced
    assert "second" in replaced
    assert replaced.count(generate_pr_infographic.PROVENANCE_START) == 1


def test_build_infographic_prompt_uses_auto_theme_as_visual_direction() -> None:
    theme = generate_pr_infographic.select_image_theme(
        pr_number=42,
        pr_title="feat(ci): add a merge gate",
        pr_body="No theme",
        theme_override=None,
    )
    prompt = generate_pr_infographic.build_infographic_prompt(
        pr_number=42,
        pr_title="feat(ci): add a merge gate",
        pr_content="Adds a deterministic merge gate for pull requests.",
        theme=theme.theme,
        theme_source=theme.source,
    )

    assert "Selected BM visual direction" in prompt
    assert theme.theme in prompt
    assert "Use image-first composition" in prompt
    assert "movie poster" in prompt
    assert "painting" in prompt
    assert "classic photograph" in prompt
    assert "scene" in prompt
    assert "poster" in prompt
    assert "cover image" in prompt
    assert "symbolic tableau" in prompt
    assert "Use at most a short title" in prompt
    assert "Do not render an infographic" in prompt
    assert "dashboard" in prompt
    assert "flowchart" in prompt
    assert "bullet-list panel" in prompt


@pytest.mark.parametrize("flag", ["--print-prompt", "--dry-run"])
def test_generate_pr_infographic_can_print_prompt_without_image_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    flag: str,
) -> None:
    body_file = tmp_path / "pr-body.md"
    body_file.write_text(
        "\n".join(
            [
                "Adds a deterministic merge gate for pull requests.",
                "<!-- BM_BOSSBOT_SUMMARY:start -->",
                "Verdict: approve",
                "Summary: review artifact that must not reach the image.",
                "<!-- BM_BOSSBOT_SUMMARY:end -->",
                "<!-- BM_INFOGRAPHIC_THEME:start -->",
                "space exploration and astronomy",
                "<!-- BM_INFOGRAPHIC_THEME:end -->",
            ]
        ),
        encoding="utf-8",
    )

    def fail_generate_image_result(**_: object) -> generate_infographic.GeneratedImage:
        raise AssertionError("print-prompt mode must not call image generation")

    monkeypatch.setattr(
        generate_pr_infographic, "generate_image_result", fail_generate_image_result
    )
    output = tmp_path / "docs/assets/infographics/pr-42.webp"

    result = CliRunner().invoke(
        generate_pr_infographic.app,
        [
            "--pr-number",
            "42",
            "--pr-title",
            "feat(ci): add a merge gate",
            "--pr-body-file",
            str(body_file),
            "--output",
            str(output),
            flag,
        ],
    )

    assert result.exit_code == 0, result.output
    assert (
        "Create a polished landscape WebP editorial image for Basic Memory PR #42" in result.output
    )
    assert "feat(ci): add a merge gate" in result.output
    assert "Adds a deterministic merge gate" in result.output
    assert "space exploration and astronomy" in result.output
    assert "image-first composition" in result.output
    assert "Do not render an infographic" in result.output
    assert "Verdict: approve" not in result.output
    assert "must not reach the image" not in result.output
    assert not output.exists()


def test_generate_pr_infographic_writes_provenance_after_image_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body_file = tmp_path / "pr-body.md"
    body_file.write_text(
        "\n".join(
            [
                "Adds a merge gate.",
                "<!-- BM_INFOGRAPHIC_THEME:start -->",
                "paintings: Rembrandt-inspired merge gate",
                "<!-- BM_INFOGRAPHIC_THEME:end -->",
            ]
        ),
        encoding="utf-8",
    )

    def fake_generate_image_result(**kwargs: object) -> generate_infographic.GeneratedImage:
        output_path = kwargs["output_path"]
        assert isinstance(output_path, Path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"fake-webp")
        return generate_infographic.GeneratedImage(
            path=output_path,
            revised_prompt="A Rembrandt-inspired painting of a robot guarding a merge gate.",
        )

    monkeypatch.setattr(
        generate_pr_infographic, "generate_image_result", fake_generate_image_result
    )
    output = tmp_path / "docs/assets/infographics/pr-42.webp"
    provenance = tmp_path / "provenance.md"

    result = CliRunner().invoke(
        generate_pr_infographic.app,
        [
            "--pr-number",
            "42",
            "--pr-title",
            "feat(ci): add a merge gate",
            "--pr-body-file",
            str(body_file),
            "--output",
            str(output),
            "--provenance-output",
            str(provenance),
        ],
    )

    assert result.exit_code == 0, result.output
    assert output.exists()
    text = provenance.read_text(encoding="utf-8")
    assert "Generated asset:" in text
    assert "Image mode: `editorial-image`" in text
    assert "Theme source: `pr-body`" in text
    assert "paintings: Rembrandt-inspired merge gate" in text
    assert "Image prompt sent to" not in text
    assert "Images API revised prompt" not in text
    assert "robot guarding a merge gate" not in text
    assert "Adds a merge gate" not in text


def test_validate_output_path_must_stay_under_docs_assets_infographics(tmp_path: Path) -> None:
    good = tmp_path / "docs/assets/infographics/pr-42.webp"
    bad = tmp_path / "docs/assets/pr-42.webp"

    assert generate_infographic.validate_output_path(good, repo_root=tmp_path) == good
    with pytest.raises(ValueError, match="docs/assets/infographics"):
        generate_infographic.validate_output_path(bad, repo_root=tmp_path)
