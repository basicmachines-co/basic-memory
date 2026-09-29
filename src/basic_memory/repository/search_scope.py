"""The project set a search statement is allowed to read.

Every search row, vector chunk, and temporal assertion carries a ``project_id``. A
statement binds that column to an explicit set before any ranking runs, so rows
outside the set never occupy a candidate window. Absence is not a value here: a scope
is always built from concrete IDs, and an empty scope compiles to a predicate that
admits nothing.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

# No project can match, and no bind needs to be sent to prove it.
_MATCHES_NOTHING = "1 = 0"


@dataclass(frozen=True, slots=True)
class ProjectScope:
    """Unique, positive project IDs a statement may read, in ascending order.

    Build one with ``ProjectScope.of`` or ``ProjectScope.single``; both canonicalize
    the input so two scopes over the same projects compare equal.
    """

    project_ids: tuple[int, ...]

    def __post_init__(self) -> None:
        for project_id in self.project_ids:
            # bool is an int subclass; ``True`` would silently read as project 1.
            if isinstance(project_id, bool) or not isinstance(project_id, int) or project_id <= 0:
                raise ValueError(f"Project IDs must be positive integers, got {project_id!r}")

    @classmethod
    def of(cls, project_ids: Iterable[int]) -> "ProjectScope":
        """Canonicalize any iterable of project IDs into a scope."""
        return cls(tuple(sorted(set(project_ids))))

    @classmethod
    def single(cls, project_id: int) -> "ProjectScope":
        """The scope every project-bound repository runs under."""
        return cls((project_id,))

    @property
    def is_empty(self) -> bool:
        return not self.project_ids

    def predicate(self, column: str, params: dict[str, Any]) -> str:
        """SQL restricting ``column`` to this scope, adding its binds to ``params``.

        The bind names are a function of the scope alone, so a statement that
        references the scope from several subqueries sends each ID once.
        """
        if self.is_empty:
            return _MATCHES_NOTHING
        placeholders: list[str] = []
        for index, project_id in enumerate(self.project_ids):
            name = f"scope_{index}"
            params[name] = project_id
            placeholders.append(f":{name}")
        return f"{column} IN ({', '.join(placeholders)})"
