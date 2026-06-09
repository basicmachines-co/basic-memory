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
    assert "--pr-body-file" in help_text
    assert "--output" in help_text
    assert "--theme" in help_text
    assert "--visual-format" in help_text
    assert "--provenance-output" in help_text
    assert "--print-prompt" in help_text
    assert "--dry-run" in help_text


def test_extract_bossbot_summary_from_pr_body() -> None:
    body = "\n".join(
        [
            "Before",
            "<!-- BM_BOSSBOT_SUMMARY:start -->",
            "Reviewed SHA: abc123",
            "Verdict: approve",
            "<!-- BM_BOSSBOT_SUMMARY:end -->",
            "After",
        ]
    )

    summary = generate_pr_infographic.extract_bossbot_summary(body)

    assert summary == "Reviewed SHA: abc123\nVerdict: approve"


def test_extract_bossbot_summary_requires_managed_block() -> None:
    with pytest.raises(ValueError, match="BM Bossbot summary block"):
        generate_pr_infographic.extract_bossbot_summary("No managed summary")


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


def test_select_infographic_theme_reports_source() -> None:
    body = "\n".join(
        [
            "<!-- BM_INFOGRAPHIC_THEME:start -->",
            "paintings: Rembrandt-inspired merge gate",
            "<!-- BM_INFOGRAPHIC_THEME:end -->",
        ]
    )

    from_body = generate_pr_infographic.select_infographic_theme(
        pr_body=body,
        theme_override=None,
    )
    from_cli = generate_pr_infographic.select_infographic_theme(
        pr_body=body,
        theme_override="80's action movies",
    )
    from_none = generate_pr_infographic.select_infographic_theme(
        pr_body="No theme",
        theme_override=None,
    )

    assert from_body.theme == "paintings: Rembrandt-inspired merge gate"
    assert from_body.source == generate_pr_infographic.ThemeSource.PR_BODY
    assert from_cli.theme == "80's action movies"
    assert from_cli.source == generate_pr_infographic.ThemeSource.CLI
    assert from_none.theme is None
    assert from_none.source == generate_pr_infographic.ThemeSource.NONE


def test_build_infographic_prompt_uses_summary_without_making_gate_claims() -> None:
    prompt = generate_pr_infographic.build_infographic_prompt(
        pr_number=42,
        summary="Verdict: approve\nSummary: Adds a merge gate.",
        theme="WWII propaganda posters with home-front logistics routes",
        visual_format=generate_pr_infographic.VisualFormat.AUTO,
    )

    assert "PR #42" in prompt
    assert "Adds a merge gate" in prompt
    assert "WWII propaganda posters" in prompt
    assert "style inspiration only" in prompt
    assert "choose the most appropriate visual form" in prompt.lower()
    assert "Choose exactly one visual mode" in prompt
    assert "Do not blend the modes" in prompt
    assert "scene" in prompt
    assert "poster" in prompt
    assert "tableau" in prompt
    assert "map backbone" in prompt
    assert "before/after value story" in prompt
    assert "copyrighted characters" in prompt
    assert "restrained" not in prompt
    assert "non-gating" in prompt
    assert "BM Bossbot Approval" in prompt


def test_build_infographic_provenance_block_includes_choices_and_prompt() -> None:
    prompt = "Create <gate> & keep `sha` exact."
    block = generate_pr_infographic.build_infographic_provenance_block(
        pr_number=42,
        output_path=Path("docs/assets/infographics/pr-42.webp"),
        model="gpt-image-2",
        size="1536x1024",
        quality="high",
        visual_format=generate_pr_infographic.VisualFormat.IMAGE,
        theme="classic black-and-white photography",
        theme_source=generate_pr_infographic.ThemeSource.CLI,
        prompt=prompt,
        revised_prompt="A black-and-white editorial photo of a guarded merge gate.",
    )

    assert generate_pr_infographic.PROVENANCE_START in block
    assert generate_pr_infographic.PROVENANCE_END in block
    assert "BM Bossbot image provenance" in block
    assert "Generated asset: `docs/assets/infographics/pr-42.webp`" in block
    assert "Image model: `gpt-image-2`" in block
    assert "Size: `1536x1024`" in block
    assert "Quality: `high`" in block
    assert "Visual format: `image`" in block
    assert "Theme source: `cli`" in block
    assert "classic black-and-white photography" in block
    assert "Image prompt sent to `gpt-image-2`" in block
    assert "Create &lt;gate&gt; &amp; keep `sha` exact." in block
    assert "Images API revised prompt" in block
    assert "black-and-white editorial photo" in block


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


def test_build_infographic_prompt_can_force_infographic_format() -> None:
    prompt = generate_pr_infographic.build_infographic_prompt(
        pr_number=42,
        summary="Verdict: approve\nSummary: Adds a merge gate.",
        visual_format=generate_pr_infographic.VisualFormat.INFOGRAPHIC,
    )

    assert "Use an infographic or map format." in prompt
    assert "Use structured information design" in prompt
    assert "data panels" in prompt
    assert "timeline" in prompt
    assert "bullet list" in prompt
    assert "primarily scenic image" in prompt


def test_build_infographic_prompt_can_force_regular_image_format() -> None:
    prompt = generate_pr_infographic.build_infographic_prompt(
        pr_number=42,
        summary="Verdict: approve\nSummary: Adds a merge gate.",
        visual_format=generate_pr_infographic.VisualFormat.IMAGE,
    )

    assert "Use a regular image format" in prompt
    assert "Use image-first composition" in prompt
    assert "actual scene" in prompt
    assert "movie poster" in prompt
    assert "painting" in prompt
    assert "editorial" in prompt
    assert "single staged visual moment" in prompt
    assert "scene" in prompt
    assert "poster" in prompt
    assert "tableau" in prompt
    assert "cover image" in prompt
    assert "illustrated" in prompt
    assert "at most a short title" in prompt
    assert "Do not use data panels" in prompt
    assert "dashboard" in prompt
    assert "timeline strips" in prompt
    assert "bullet lists" in prompt
    assert "Do not render an infographic" in prompt
    assert "dense text-heavy infographic" in prompt


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
                "<!-- BM_BOSSBOT_SUMMARY:start -->",
                "Verdict: approve",
                "Summary: Adds a merge gate.",
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
            "--pr-body-file",
            str(body_file),
            "--output",
            str(output),
            "--visual-format",
            "image",
            flag,
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Create a polished landscape WebP visual for Basic Memory PR #42" in result.output
    assert "Adds a merge gate" in result.output
    assert "space exploration and astronomy" in result.output
    assert "Use a regular image format" in result.output
    assert "BM Bossbot Approval" in result.output
    assert not output.exists()


def test_generate_pr_infographic_writes_provenance_after_image_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body_file = tmp_path / "pr-body.md"
    body_file.write_text(
        "\n".join(
            [
                "<!-- BM_BOSSBOT_SUMMARY:start -->",
                "Verdict: approve",
                "Summary: Adds a merge gate.",
                "<!-- BM_BOSSBOT_SUMMARY:end -->",
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
            "--pr-body-file",
            str(body_file),
            "--output",
            str(output),
            "--visual-format",
            "image",
            "--provenance-output",
            str(provenance),
        ],
    )

    assert result.exit_code == 0, result.output
    assert output.exists()
    text = provenance.read_text(encoding="utf-8")
    assert "Generated asset:" in text
    assert "Visual format: `image`" in text
    assert "Theme source: `pr-body`" in text
    assert "paintings: Rembrandt-inspired merge gate" in text
    assert "Image prompt sent to `gpt-image-2`" in text
    assert "Images API revised prompt" in text
    assert "robot guarding a merge gate" in text
    assert "Adds a merge gate" in text


def test_validate_output_path_must_stay_under_docs_assets_infographics(tmp_path: Path) -> None:
    good = tmp_path / "docs/assets/infographics/pr-42.webp"
    bad = tmp_path / "docs/assets/pr-42.webp"

    assert generate_infographic.validate_output_path(good, repo_root=tmp_path) == good
    with pytest.raises(ValueError, match="docs/assets/infographics"):
        generate_infographic.validate_output_path(bad, repo_root=tmp_path)
