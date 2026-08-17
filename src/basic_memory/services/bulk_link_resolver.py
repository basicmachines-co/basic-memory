"""Bulk exact link resolution for project relation repair."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
import uuid as uuid_mod

from sqlalchemy.ext.asyncio import AsyncSession

from basic_memory.config import BasicMemoryConfig
from basic_memory.models import Entity, Project
from basic_memory.repository.entity_repository import EntityRepository, file_path_alias
from basic_memory.repository.project_repository import ProjectRepository
from basic_memory.services.link_resolver import normalize_link_text
from basic_memory.utils import (
    build_permalink_resolution_candidates,
    generate_permalink,
    normalize_project_reference,
)
from basic_memory.workspace_context import current_workspace_permalink_context


# --- Target parsing ---


@dataclass(frozen=True, slots=True)
class RelationTargetReference:
    """One original relation target and its normalized routing form."""

    original: str
    identifier: str
    explicitly_qualified: bool

    @classmethod
    def parse(cls, link_text: str) -> "RelationTargetReference":
        """Normalize wikilink syntax once for the whole bulk-resolution pass."""
        clean_text, _ = normalize_link_text(link_text)
        return cls(
            original=link_text,
            identifier=normalize_project_reference(clean_text),
            explicitly_qualified="::" in clean_text,
        )

    def project_path(self) -> tuple[str | None, str]:
        """Return a possible project prefix and its remaining target path."""
        if "/" not in self.identifier:
            return None, self.identifier

        project_prefix, remainder = self.identifier.split("/", 1)
        if not project_prefix or not remainder:
            return None, self.identifier
        return project_prefix, remainder


# --- Snapshot indexes ---


@dataclass(frozen=True, slots=True)
class ProjectReferenceIndex:
    """Project registry snapshot with the regular resolver's lookup precedence."""

    projects_by_id: Mapping[int, Project]
    projects_by_name: Mapping[str, Project]
    projects_by_casefolded_name: Mapping[str, Project]
    projects_by_permalink: Mapping[str, Project]

    @classmethod
    def from_projects(cls, projects: Sequence[Project]) -> "ProjectReferenceIndex":
        """Index one stable project-registry read for repeated target routing."""
        return cls(
            projects_by_id={project.id: project for project in projects},
            projects_by_name={project.name: project for project in projects},
            projects_by_casefolded_name={project.name.casefold(): project for project in projects},
            projects_by_permalink={project.permalink: project for project in projects},
        )

    def find(self, identifier: str) -> Project | None:
        """Resolve a project name or permalink using the single-link precedence."""
        return (
            self.projects_by_name.get(identifier)
            or self.projects_by_casefolded_name.get(identifier.casefold())
            or self.projects_by_permalink.get(generate_permalink(identifier))
        )


@dataclass(frozen=True, slots=True)
class StrictProjectLinkMatch:
    """Exact project-local match, including ambiguity that blocks fallback routing."""

    entity: Entity | None
    ambiguous: bool = False


@dataclass(frozen=True, slots=True)
class ProjectEntityIdentityIndex:
    """Lightweight project entity identities used by one bulk-resolution pass."""

    project: Project
    by_external_id: Mapping[str, Entity]
    by_permalink: Mapping[str, Entity]
    by_title: Mapping[str, tuple[Entity, ...]]
    by_file_path: Mapping[str, Entity]
    by_file_path_alias: Mapping[str, Entity]

    @classmethod
    def from_entities(
        cls,
        project: Project,
        entities: Sequence[Entity],
    ) -> "ProjectEntityIdentityIndex":
        """Build deterministic exact-match indexes without graph relationships."""
        title_matches: dict[str, list[Entity]] = {}
        by_external_id: dict[str, Entity] = {}
        by_permalink: dict[str, Entity] = {}
        by_file_path: dict[str, Entity] = {}
        file_path_alias_matches: dict[str, list[Entity]] = {}

        for entity in entities:
            by_external_id[entity.external_id] = entity
            if entity.permalink is not None:
                by_permalink[entity.permalink] = entity
            title_matches.setdefault(entity.title, []).append(entity)
            by_file_path[entity.file_path] = entity
            file_path_alias_matches.setdefault(file_path_alias(entity.file_path), []).append(entity)

        return cls(
            project=project,
            by_external_id=by_external_id,
            by_permalink=by_permalink,
            by_title={
                title: tuple(
                    sorted(matches, key=lambda entity: (len(entity.file_path), entity.file_path))
                )
                for title, matches in title_matches.items()
            },
            by_file_path=by_file_path,
            by_file_path_alias={
                alias: matches[0]
                for alias, matches in file_path_alias_matches.items()
                if len(matches) == 1
            },
        )

    def resolve_strict(
        self,
        identifier: str,
        *,
        include_project_permalinks: bool,
        workspace_permalink: str | None,
    ) -> StrictProjectLinkMatch:
        """Apply exact permalink, title, and normalized path precedence in memory."""
        permalink_candidates = build_permalink_resolution_candidates(
            identifier,
            self.project.permalink,
            include_project=include_project_permalinks,
            workspace_permalink=workspace_permalink,
        )
        title_matches = self.by_title.get(identifier, ())
        ambiguous_title = len(title_matches) > 1
        exact_identifier = normalize_project_reference(identifier).strip("/")

        for candidate_permalink in permalink_candidates:
            entity = self.by_permalink.get(candidate_permalink)
            if entity is None:
                continue
            if ambiguous_title and candidate_permalink != exact_identifier:
                break
            return StrictProjectLinkMatch(entity)

        if len(title_matches) == 1:
            return StrictProjectLinkMatch(title_matches[0])

        normalized_path = Path(identifier).as_posix()
        path_match = self.by_file_path.get(normalized_path)
        if path_match is not None:
            return StrictProjectLinkMatch(path_match)

        path_with_md = normalized_path
        can_use_stem_path = "/" in normalized_path or not ambiguous_title
        has_markdown_extension = normalized_path.casefold().endswith(".md")
        if not has_markdown_extension and can_use_stem_path:
            path_with_md = f"{normalized_path}.md"
            path_match = self.by_file_path.get(path_with_md)
            if path_match is not None:
                return StrictProjectLinkMatch(path_match)

        if has_markdown_extension or can_use_stem_path:
            alias_match = self.by_file_path_alias.get(file_path_alias(path_with_md))
            if alias_match is not None:
                return StrictProjectLinkMatch(alias_match)

        return StrictProjectLinkMatch(entity=None, ambiguous=ambiguous_title)


@dataclass(frozen=True, slots=True)
class BulkLinkResolutionSnapshot:
    """All project and entity identity state used by one bulk-resolution pass."""

    current_project_id: int
    projects: ProjectReferenceIndex
    entity_indexes: Mapping[int, ProjectEntityIdentityIndex]
    include_project_permalinks: bool
    workspace_permalink: str | None

    def resolve(self, target: RelationTargetReference) -> Entity | None:
        """Resolve one parsed target without additional I/O."""
        current_index = self.entity_indexes[self.current_project_id]

        try:
            external_id = str(uuid_mod.UUID(target.identifier))
        except ValueError:
            external_id = None
        if external_id is not None:
            external_id_match = current_index.by_external_id.get(external_id)
            if external_id_match is not None:
                return external_id_match

        project_prefix, remainder = target.project_path()
        referenced_project = (
            self.projects.find(project_prefix) if project_prefix is not None else None
        )

        if target.explicitly_qualified:
            if referenced_project is None:
                return None
            return (
                self.entity_indexes[referenced_project.id]
                .resolve_strict(
                    remainder,
                    include_project_permalinks=self.include_project_permalinks,
                    workspace_permalink=self.workspace_permalink,
                )
                .entity
            )

        current_match = current_index.resolve_strict(
            target.identifier,
            include_project_permalinks=self.include_project_permalinks,
            workspace_permalink=self.workspace_permalink,
        )
        if current_match.entity is not None:
            return current_match.entity
        if current_match.ambiguous:
            return None

        if referenced_project is None or referenced_project.id == self.current_project_id:
            return None
        return (
            self.entity_indexes[referenced_project.id]
            .resolve_strict(
                remainder,
                include_project_permalinks=self.include_project_permalinks,
                workspace_permalink=self.workspace_permalink,
            )
            .entity
        )


# --- Snapshot loading ---


async def load_bulk_link_resolution_snapshot(
    targets: Sequence[RelationTargetReference],
    *,
    entity_repository: EntityRepository,
    project_repository: ProjectRepository,
    app_config: BasicMemoryConfig,
    session: AsyncSession,
) -> BulkLinkResolutionSnapshot:
    """Load every project entity index needed by one target batch."""
    current_project_id = entity_repository.project_id
    if current_project_id is None:  # pragma: no cover
        raise RuntimeError("Bulk link resolution requires a project-scoped repository")

    projects = list(await project_repository.find_all(session, use_load_options=False))
    project_index = ProjectReferenceIndex.from_projects(projects)
    if current_project_id not in project_index.projects_by_id:
        raise RuntimeError(f"Current project {current_project_id} does not exist")

    required_project_ids = {current_project_id}
    for target in targets:
        project_prefix, _ = target.project_path()
        referenced_project = (
            project_index.find(project_prefix) if project_prefix is not None else None
        )
        if referenced_project is not None:
            required_project_ids.add(referenced_project.id)

    entity_indexes: dict[int, ProjectEntityIdentityIndex] = {}
    for project_id in sorted(required_project_ids):
        project = project_index.projects_by_id[project_id]
        project_entity_repository = (
            entity_repository
            if project_id == current_project_id
            else EntityRepository(project_id=project_id)
        )
        entities = await project_entity_repository.find_all(session, use_load_options=False)
        entity_indexes[project_id] = ProjectEntityIdentityIndex.from_entities(project, entities)

    workspace_context = current_workspace_permalink_context()
    workspace_permalink = (
        workspace_context.workspace_slug
        if workspace_context and workspace_context.should_prefix_permalinks
        else None
    )
    return BulkLinkResolutionSnapshot(
        current_project_id=current_project_id,
        projects=project_index,
        entity_indexes=entity_indexes,
        include_project_permalinks=app_config.permalinks_include_project,
        workspace_permalink=workspace_permalink,
    )


# --- Resolver service ---


@dataclass(frozen=True, slots=True)
class BulkLinkResolver:
    """Resolve strict relation targets from one bounded identity snapshot."""

    entity_repository: EntityRepository
    app_config: BasicMemoryConfig
    project_repository: ProjectRepository = field(default_factory=ProjectRepository)

    async def resolve_relation_targets(
        self,
        link_texts: Sequence[str],
        *,
        session: AsyncSession,
    ) -> dict[str, Entity | None]:
        """Resolve unique relation targets with I/O bounded by referenced projects."""
        targets = tuple(
            RelationTargetReference.parse(link_text) for link_text in dict.fromkeys(link_texts)
        )
        if not targets:
            return {}

        snapshot = await load_bulk_link_resolution_snapshot(
            targets,
            entity_repository=self.entity_repository,
            project_repository=self.project_repository,
            app_config=self.app_config,
            session=session,
        )
        return {target.original: snapshot.resolve(target) for target in targets}
