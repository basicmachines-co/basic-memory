"""Basic Memory's shared note contract, independent of Tau lifecycle machinery."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

ProjectRef = Annotated[str, Field(min_length=1, pattern=r"^\S(?:.*\S)?$")]


class Destination(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    project: ProjectRef | None = None
    read_projects: list[ProjectRef] = Field(default_factory=list, max_length=6)
    checkpoint_folder: str | None = None
    placement_conventions: str = (
        "Put durable decisions in decisions/, tasks in tasks/, and other notes with their topic. "
        "Search and update existing notes before creating new ones."
    )


class GeneralProfile(Destination):
    kind: Literal["general"] = "general"


class CodingProfile(Destination):
    kind: Literal["coding"] = "coding"
    root: Path
    repository: ProjectRef

    @field_validator("root")
    @classmethod
    def absolute_root(cls, root: Path) -> Path:
        root = root.expanduser()
        if not root.is_absolute():
            raise ValueError("repository root must be an absolute path")
        return root.resolve()


type SessionProfile = GeneralProfile | CodingProfile


class PullRequest(BaseModel):
    number: int
    title: str
    url: str
    state: Literal["OPEN", "CLOSED", "MERGED"]
    baseRefName: str
    headRefName: str


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str


async def command(cwd: Path, *args: str) -> CommandResult:
    """Run a bounded metadata read; cancellation retires its subprocess inline."""
    process = await asyncio.create_subprocess_exec(
        *args, cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
    )
    try:
        async with asyncio.timeout(5):
            stdout, _stderr = await process.communicate()
    finally:
        if process.returncode is None:
            process.kill()
            await process.communicate()
    assert process.returncode is not None
    return CommandResult(process.returncode, stdout.decode().strip())


@dataclass(frozen=True, slots=True)
class CodingContext:
    repository: str
    repo_root: Path
    branch: str
    git_sha: str
    pull_request: PullRequest | None

    def metadata(self) -> dict[str, JsonValue]:
        values: dict[str, JsonValue] = {
            "repository": self.repository,
            "repo_root": self.repo_root.as_posix(),
            "branch": self.branch,
            "git_sha": self.git_sha,
        }
        if self.pull_request is not None:
            pr = self.pull_request
            values.update(
                pull_request_number=str(pr.number),
                pull_request_title=pr.title,
                pull_request_url=pr.url,
                pull_request_state=pr.state.lower(),
                pull_request_base=pr.baseRefName,
                pull_request_head=pr.headRefName,
            )
        return values


async def validate_checkout(profile: CodingProfile, cwd: Path) -> None:
    """Do not let a nested checkout inherit another repository's memory mapping."""
    root = await command(cwd, "git", "rev-parse", "--show-toplevel")
    if root.returncode or not root.stdout:
        raise ValueError("coding setup requires an initialized Git checkout")
    if Path(root.stdout).resolve() != profile.root:
        raise ValueError("Git root differs from the approved repository profile; rerun setup")


async def coding_context(profile: CodingProfile, cwd: Path) -> CodingContext:
    """Read required Git identity; PR presence is optional, never model-invented."""
    await validate_checkout(profile, cwd)
    branch = await command(cwd, "git", "rev-parse", "--abbrev-ref", "HEAD")
    sha = await command(cwd, "git", "rev-parse", "HEAD")
    if any(result.returncode or not result.stdout for result in (branch, sha)):
        raise ValueError("coding setup requires an initialized Git checkout")
    try:
        result = await command(
            cwd, "gh", "pr", "view", "--json", "number,title,url,state,baseRefName,headRefName"
        )
    except (FileNotFoundError, TimeoutError):
        # GitHub is optional: offline/local coding still has complete Git identity.
        pull_request = None
    else:
        pull_request = (
            PullRequest.model_validate_json(result.stdout) if result.returncode == 0 else None
        )
    return CodingContext(profile.repository, profile.root, branch.stdout, sha.stdout, pull_request)


def checkpoint_directory(profile: SessionProfile) -> str:
    """Resolve session placement within the explicitly chosen memory project."""
    if profile.checkpoint_folder is not None:
        return profile.checkpoint_folder
    # Stable repository identity groups worktrees together, unlike checkout basenames.
    if isinstance(profile, CodingProfile):
        return f"tau/{profile.repository.rsplit('/', 1)[-1]}"
    return "tau/checkpoints"


def placement(profile: SessionProfile) -> str:
    """Trusted user policy, kept distinct from recalled graph content."""
    return (
        "Basic Memory placement policy (user configuration):\n"
        f"Write destination: {json.dumps(profile.project)}. "
        f"Read-only sources: {json.dumps(profile.read_projects)}.\n"
        f"Checkpoints: {checkpoint_directory(profile)}/. {profile.placement_conventions}\n"
        "Shared recall never authorizes writes to those projects. Decisions/tasks are durable "
        "knowledge, not lifecycle telemetry. Use schemas, categorized observations, and verified "
        "relations; don't create a separate note for every conversational statement.\n"
    )
