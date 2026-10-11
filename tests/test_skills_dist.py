"""Tests for scripts/validate_skills.py and scripts/build_skills_dist.py.

The release archives these scripts build are linked from the Basic Memory web
app by asset name, so the names, the zip layout, and what each zip holds are a
contract worth pinning down.
"""

import importlib.util
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
SKILLS_ROOT = REPO_ROOT / "skills"

# scripts/ is not a package, and build_skills_dist imports its sibling
# validate_skills by bare name, so put scripts/ on the path and load the files
# directly (matching tests/test_man_pages.py).
sys.path.insert(0, str(SCRIPTS_DIR))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered first: dataclasses resolve their module through sys.modules, and
    # build_skills_dist's `from validate_skills import ...` then reuses this copy.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


validate_skills = _load("validate_skills")
build_skills_dist = _load("build_skills_dist")

COMBINED_EXCLUDED = {"memory-defrag", "memory-reflect", "memory-literary-analysis"}
INTERNAL = "memory-ci-capture"


def _write_skill(root: Path, dir_name: str, /, body: str = "# Skill\n", **frontmatter: str) -> Path:
    """Write `<root>/<dir_name>/SKILL.md`; keyword args add or override frontmatter."""
    skill_dir = root / dir_name
    skill_dir.mkdir(parents=True)
    fields = {"name": dir_name, "description": f'"Do {dir_name} things. Use when testing."'}
    fields.update(frontmatter)
    header = "\n".join(f"{key}: {value}" for key, value in fields.items())
    (skill_dir / "SKILL.md").write_text(f"---\n{header}\n---\n\n{body}")
    return skill_dir


def _names(archive: Path) -> list[str]:
    with zipfile.ZipFile(archive) as zf:
        return zf.namelist()


def _read(archive: Path, member: str) -> str:
    with zipfile.ZipFile(archive) as zf:
        return zf.read(member).decode()


@pytest.fixture(scope="module")
def repo_dist(tmp_path_factory: pytest.TempPathFactory) -> Path:
    dist = tmp_path_factory.mktemp("dist")
    build_skills_dist.build_all(SKILLS_ROOT, dist)
    return dist


def _distributed() -> list[str]:
    return sorted(validate_skills.distributed_skill_names(SKILLS_ROOT))


# --- Validator: the repo itself ---


def test_repo_skills_and_lists_validate() -> None:
    validate_skills.validate_skills(SKILLS_ROOT)
    validate_skills.validate_skill_lists(REPO_ROOT)


def test_ci_capture_is_internal_and_others_are_not() -> None:
    assert validate_skills.is_internal_skill(SKILLS_ROOT / INTERNAL / "SKILL.md")
    assert INTERNAL not in _distributed()
    assert "memory-onboarding" in _distributed()


# --- Validator: frontmatter rules ---


@pytest.mark.parametrize(
    "name",
    [
        "Memory-Notes",
        "memory_notes",
        "memory--notes",
        "-memory",
        "memory-",
        "claude-notes",
        "anthropic-x",
        "a" * 65,
    ],
)
def test_validator_rejects_bad_names(tmp_path: Path, name: str) -> None:
    skill_file = _write_skill(tmp_path, "skill", name=name) / "SKILL.md"
    with pytest.raises(SystemExit):
        validate_skills.validate_name(skill_file, name)


@pytest.mark.parametrize(
    ("description", "message"),
    [
        (f'"{"x" * 201}"', "limit is 200"),
        ('"Use <when> testing."', "'<' or '>'"),
        ('"Arrow -> here."', "'<' or '>'"),
        ("|", "block scalar"),
        (">-", "block scalar"),
        ('""', "missing description"),
    ],
)
def test_validator_rejects_bad_descriptions(tmp_path: Path, description: str, message: str) -> None:
    _write_skill(tmp_path, "memory-x", description=description)
    with pytest.raises(SystemExit, match=re.escape(message)):
        validate_skills.validate_skills(tmp_path)


def test_validator_accepts_200_character_description(tmp_path: Path) -> None:
    _write_skill(tmp_path, "memory-x", description=f'"{"x" * 200}"')
    validate_skills.validate_skills(tmp_path)


def test_validator_rejects_mismatched_name(tmp_path: Path) -> None:
    _write_skill(tmp_path, "memory-x", name="memory-y")
    with pytest.raises(SystemExit, match="does not match directory"):
        validate_skills.validate_skills(tmp_path)


def test_validator_checks_linked_files(tmp_path: Path) -> None:
    body = (
        "See [the guide](references/guide.md#intro) and `references/extra.md`.\n"
        "Skip [web](https://example.com), [anchor](#top), [site](/logo.svg).\n"
    )
    skill_dir = _write_skill(tmp_path, "memory-x", body=body)
    (skill_dir / "references").mkdir()
    (skill_dir / "references/guide.md").write_text("guide")
    with pytest.raises(SystemExit, match="references/extra.md"):
        validate_skills.validate_skills(tmp_path)

    (skill_dir / "references/extra.md").write_text("extra")
    validate_skills.validate_skills(tmp_path)


def test_is_internal_reads_nested_metadata(tmp_path: Path) -> None:
    internal = _write_skill(tmp_path, "memory-a", metadata="\n  owner: ci\n  internal: true")
    not_internal = _write_skill(tmp_path, "memory-b", metadata="\n  internal: false")
    top_level = _write_skill(tmp_path, "memory-c", internal="true")
    assert validate_skills.is_internal_skill(internal / "SKILL.md")
    assert not validate_skills.is_internal_skill(not_internal / "SKILL.md")
    assert not validate_skills.is_internal_skill(top_level / "SKILL.md")


# --- Validator: skill lists ---


def _list_repo(tmp_path: Path, skills: list[str], *, internal: str | None = None) -> Path:
    """A minimal repo: skill dirs plus the four hand-kept lists naming `skills`."""
    root = tmp_path / "repo"
    for name in skills:
        _write_skill(root / "skills", name)
    if internal:
        _write_skill(root / "skills", internal, metadata="\n  internal: true")
    table = "".join(f"| **{name}** | x | y |\n" for name in skills)
    (root / "skills/README.md").write_text(table)
    claude_names = skills + ([internal] if internal else [])
    tree = "".join(f"{name}/SKILL.md  # x\n" for name in claude_names)
    (root / "skills/CLAUDE.md").write_text(tree)
    openclaw = root / "integrations/openclaw"
    openclaw.mkdir(parents=True)
    manifest = {"skills": [f"skills/{name}" for name in skills]}
    (openclaw / "openclaw.plugin.json").write_text(json.dumps(manifest))
    (openclaw / "README.md").write_text("".join(f"- **{name}** — x\n" for name in skills))
    return root


def test_skill_lists_pass_when_in_sync(tmp_path: Path) -> None:
    root = _list_repo(tmp_path, ["memory-a", "memory-b"], internal="memory-ci")
    validate_skills.validate_skill_lists(root)


def test_skill_lists_flag_missing_and_unknown(tmp_path: Path) -> None:
    root = _list_repo(tmp_path, ["memory-a", "memory-b"])
    readme = root / "skills/README.md"
    readme.write_text("| **memory-a** | x | y |\n")
    with pytest.raises(SystemExit, match=r"skills/README.md: missing skills \['memory-b'\]"):
        validate_skills.validate_skill_lists(root)

    readme.write_text("| **memory-a** | x |\n| **memory-b** | x |\n| **memory-gone** | x |\n")
    with pytest.raises(SystemExit, match="memory-gone"):
        validate_skills.validate_skill_lists(root)


def test_user_facing_lists_reject_internal_skills(tmp_path: Path) -> None:
    root = _list_repo(tmp_path, ["memory-a"], internal="memory-ci")
    readme = root / "skills/README.md"
    readme.write_text(readme.read_text() + "| **memory-ci** | x | y |\n")
    with pytest.raises(SystemExit, match="internal skills"):
        validate_skills.validate_skill_lists(root)


def test_memory_quest_may_be_absent_from_openclaw_readme_only(tmp_path: Path) -> None:
    # PR #1677 adds memory-quest everywhere except the OpenClaw README.
    root = _list_repo(tmp_path, ["memory-a", "memory-quest"])
    openclaw_readme = root / "integrations/openclaw/README.md"
    openclaw_readme.write_text("- **memory-a** — x\n")
    validate_skills.validate_skill_lists(root)

    (root / "skills/CLAUDE.md").write_text("memory-a/SKILL.md\n")
    with pytest.raises(SystemExit, match="skills/CLAUDE.md: missing"):
        validate_skills.validate_skill_lists(root)


def test_repo_lists_pass_once_memory_quest_lands(tmp_path: Path) -> None:
    """Apply what PR #1677 adds to a copy of this repo's lists; the check must still pass."""
    root = tmp_path / "repo"
    shutil.copytree(SKILLS_ROOT, root / "skills")
    openclaw = root / "integrations/openclaw"
    openclaw.mkdir(parents=True)
    for name in ("openclaw.plugin.json", "README.md"):
        shutil.copy(REPO_ROOT / "integrations/openclaw" / name, openclaw / name)

    _write_skill(root / "skills", "memory-quest")
    readme = root / "skills/README.md"
    readme.write_text(readme.read_text() + "\n| **memory-quest** | x | y |\n")
    claude = root / "skills/CLAUDE.md"
    claude.write_text(claude.read_text() + "\nmemory-quest/SKILL.md  # x\n")
    manifest_path = openclaw / "openclaw.plugin.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["skills"].append("skills/memory-quest")
    manifest_path.write_text(json.dumps(manifest))

    validate_skills.validate_skill_lists(root)


# --- Build: asset names ---


def test_asset_names_cover_every_skill_in_both_flavors(repo_dist: Path) -> None:
    plain = {path.name for path in (repo_dist / "skills").glob("*.zip")}
    chatgpt = {path.name for path in (repo_dist / "skills-openai").glob("*.zip")}
    skills = _distributed()

    assert plain == {f"{name}.zip" for name in skills} | {
        "basic-memory.zip",
        "basic-memory-skills.zip",
    }
    assert chatgpt == {f"{name}-chatgpt.zip" for name in skills} | {
        "basic-memory-chatgpt.zip",
        "basic-memory-skills-chatgpt.zip",
    }
    # One release holds both flavors, so no basename may repeat.
    assert not plain & chatgpt
    # Linked from the web app and earlier docs.
    assert "memory-onboarding.zip" in plain
    assert f"{INTERNAL}.zip" not in plain
    assert not (repo_dist / "skills" / ".stage").exists()


def test_duplicate_asset_names_fail_the_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clashing = build_skills_dist.Flavor(dist_subdir="skills-openai", suffix="", openai=True)
    monkeypatch.setattr(build_skills_dist, "FLAVORS", (build_skills_dist.PLAIN, clashing))
    with pytest.raises(SystemExit, match="duplicate archive names"):
        build_skills_dist.build_all(SKILLS_ROOT, tmp_path)


# --- Build: individual skill zips ---


@pytest.mark.parametrize("flavor", ["skills", "skills-openai"])
def test_each_zip_holds_one_skill_folder(repo_dist: Path, flavor: str) -> None:
    suffix = "-chatgpt" if flavor == "skills-openai" else ""
    for name in _distributed():
        members = _names(repo_dist / flavor / f"{name}{suffix}.zip")
        assert f"{name}/SKILL.md" in members
        assert all(member.startswith(f"{name}/") for member in members)
        assert not any("/evals/" in member for member in members)
        has_openai = f"{name}/agents/openai.yaml" in members
        assert has_openai == (flavor == "skills-openai")


def test_references_ship_with_their_skill(repo_dist: Path) -> None:
    members = _names(repo_dist / "skills/memory-comark.zip")
    assert "memory-comark/references/components.md" in members
    assert not any(
        "evals" in member for member in _names(repo_dist / "skills/memory-onboarding.zip")
    )


def test_bundle_holds_every_individual_skill(repo_dist: Path) -> None:
    members = _names(repo_dist / "skills/basic-memory-skills.zip")
    roots = {member.split("/", 1)[0] for member in members}
    assert roots == set(_distributed())


def test_generated_openai_yaml(repo_dist: Path) -> None:
    text = _read(
        repo_dist / "skills-openai/memory-notes-chatgpt.zip", "memory-notes/agents/openai.yaml"
    )
    assert 'display_name: "Memory Notes"' in text
    assert 'brand_color: "#2563EB"' in text


# --- Build: the combined basic-memory skill ---


def _extract(archive: Path, dest: Path) -> Path:
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(dest)
    return dest / "basic-memory"


def _routing_targets(skill_md: str) -> list[str]:
    return re.findall(r"\| `(references/[^`]+\.md)` \|", skill_md)


def _assert_links_resolve(skill_root: Path) -> None:
    """Markdown links resolve from their file; backticked references/ paths from the root."""
    for reference in (skill_root / "references").glob("*.md"):
        text = reference.read_text()
        for target in validate_skills.MARKDOWN_LINK.findall(text):
            if target.startswith(("#", "/", "http")):
                continue
            assert (reference.parent / target.split("#", 1)[0]).is_file(), (reference, target)
        for target in validate_skills.CODE_RESOURCE_PATH.findall(text):
            assert (skill_root / target).is_file(), (reference, target)


@pytest.mark.parametrize(
    "asset", ["skills/basic-memory.zip", "skills-openai/basic-memory-chatgpt.zip"]
)
def test_combined_skill_layout_and_routing(repo_dist: Path, tmp_path: Path, asset: str) -> None:
    members = _names(repo_dist / asset)
    assert "basic-memory/SKILL.md" in members
    assert all(member.startswith("basic-memory/") for member in members)

    skill_root = _extract(repo_dist / asset, tmp_path)
    skill_file = skill_root / "SKILL.md"
    frontmatter = validate_skills.parse_frontmatter(skill_file)
    assert frontmatter["name"] == "basic-memory"
    validate_skills.validate_name(skill_file, frontmatter["name"])
    validate_skills.validate_description(skill_file, frontmatter.get("description"))
    assert build_skills_dist.ROUTING_TABLE_MARKER not in skill_file.read_text()

    expected = sorted(set(_distributed()) - COMBINED_EXCLUDED)
    targets = _routing_targets(skill_file.read_text())
    assert targets == [f"references/{name}.md" for name in expected]
    for target in targets:
        assert (skill_root / target).is_file()

    for name in COMBINED_EXCLUDED | {INTERNAL}:
        assert not (skill_root / "references" / f"{name}.md").exists()
        assert not (skill_root / "references" / name).exists()
    assert not any("/evals/" in member for member in members)
    expected_agents = ["basic-memory/agents/openai.yaml"] if "openai" in asset else []
    assert [member for member in members if "/agents/" in member] == expected_agents
    _assert_links_resolve(skill_root)


def test_combined_references_rewrite_resource_links(repo_dist: Path, tmp_path: Path) -> None:
    skill_root = _extract(repo_dist / "skills/basic-memory.zip", tmp_path)
    comark = (skill_root / "references/memory-comark.md").read_text()
    assert not comark.startswith("---")
    assert "](memory-comark/references/components.md)" in comark
    assert (skill_root / "references/memory-comark/references/components.md").is_file()
    onboarding = (skill_root / "references/memory-onboarding.md").read_text()
    assert "`references/memory-onboarding/references/assistant-setup.md`" in onboarding
    assert "`references/assistant-setup.md`" not in onboarding


def test_combined_openai_yaml(repo_dist: Path) -> None:
    plain = _names(repo_dist / "skills/basic-memory.zip")
    assert "basic-memory/agents/openai.yaml" not in plain
    text = _read(
        repo_dist / "skills-openai/basic-memory-chatgpt.zip", "basic-memory/agents/openai.yaml"
    )
    assert 'display_name: "Basic Memory"' in text
    assert f'short_description: "{build_skills_dist.COMBINED_SHORT_DESCRIPTION}"' in text
    assert 'brand_color: "#2563EB"' in text
    assert "allow_implicit_invocation" not in text


def test_build_picks_up_new_skills_without_code_changes(tmp_path: Path) -> None:
    """A memory-quest dir like PR #1677's joins every archive on its own."""
    skills_root = tmp_path / "skills"
    shutil.copytree(SKILLS_ROOT / "memory-notes", skills_root / "memory-notes")
    shutil.copytree(SKILLS_ROOT / "memory-reflect", skills_root / "memory-reflect")
    shutil.copytree(SKILLS_ROOT / INTERNAL, skills_root / INTERNAL)
    quest = _write_skill(
        skills_root,
        "memory-quest",
        body="Finish the quest. See [notes](references/codes.md).\n",
        description='"Finish Memory Quest Mission 8. Use when asked to verify the assistant."',
    )
    (quest / "references").mkdir()
    (quest / "references/codes.md").write_text("codes")
    (quest / "agents").mkdir()
    (quest / "agents/openai.yaml").write_text('interface:\n  display_name: "Memory Quest"\n')
    (quest / "evals").mkdir()
    (quest / "evals/evals.json").write_text("[]")

    dist = tmp_path / "dist"
    names = {path.name for path in build_skills_dist.build_all(skills_root, dist)}
    assert {"memory-quest.zip", "memory-quest-chatgpt.zip"} <= names
    assert f"{INTERNAL}.zip" not in names

    plain = _names(dist / "skills/memory-quest.zip")
    assert "memory-quest/references/codes.md" in plain
    assert "memory-quest/agents/openai.yaml" not in plain
    assert not any("evals" in member for member in plain)
    hand_written = _read(
        dist / "skills-openai/memory-quest-chatgpt.zip", "memory-quest/agents/openai.yaml"
    )
    assert hand_written == 'interface:\n  display_name: "Memory Quest"\n'

    skill_root = _extract(dist / "skills/basic-memory.zip", tmp_path / "out")
    skill_md = (skill_root / "SKILL.md").read_text()
    assert _routing_targets(skill_md) == [
        "references/memory-notes.md",
        "references/memory-quest.md",
    ]
    assert build_skills_dist.ROUTING_INTENTS["memory-quest"] in skill_md
    assert (
        "](memory-quest/references/codes.md)"
        in (skill_root / "references/memory-quest.md").read_text()
    )
    assert not (skill_root / "references/memory-quest/agents").exists()
    assert not (skill_root / "references/memory-quest/evals").exists()
    _assert_links_resolve(skill_root)


def test_combined_template_needs_routing_marker(tmp_path: Path) -> None:
    template = tmp_path / "template.md"
    template.write_text("---\nname: basic-memory\ndescription: x\n---\n")
    with pytest.raises(SystemExit, match="routing-table"):
        build_skills_dist.build_all(SKILLS_ROOT, tmp_path / "dist", template)


def test_template_is_invisible_to_skill_discovery() -> None:
    # The `skills` CLI and plugin loaders look for files named SKILL.md.
    assert build_skills_dist.COMBINED_TEMPLATE.name != "SKILL.md"
    assert not list((SCRIPTS_DIR / "skill-bundle").rglob("SKILL.md"))


@pytest.mark.parametrize(
    ("members", "message"),
    [
        (["SKILL.md"], "expected skill folders"),
        (["memory-x/notes.md"], "expected skill folders"),
        (["memory-x/SKILL.md", "stray.md"], "expected skill folders"),
        (["memory-x/SKILL.md", "memory-x/evals/evals.json"], "test fixtures"),
    ],
)
def test_verify_layout_rejects_bad_archives(
    tmp_path: Path, members: list[str], message: str
) -> None:
    archive = tmp_path / "memory-x.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for member in members:
            zf.writestr(member, "x")
    with pytest.raises(SystemExit, match=message):
        build_skills_dist.verify_layout(archive, {"memory-x"})
