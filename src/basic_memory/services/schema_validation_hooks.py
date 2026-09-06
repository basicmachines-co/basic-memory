"""Overridable seam for observing authoritative schema validation.

Validation is the only place that knows whether a note actually satisfies its
schema: nothing on the write path runs it, and no validation state is stored on
the entity. A deployment that needs to react to a validation result -- a hosted
one recording that a user structured a note successfully, say -- would otherwise
have to re-run validation itself and own a copy of this module's schema
resolution, which would then drift from the answer the API returns.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ValidatedNoteOutcome:
    """One note's validation result, reduced to identity and pass state.

    Deliberately narrower than `NoteValidationResponse`: it carries no field
    names, values, warnings or error text, so an observer cannot come to depend
    on note content, and the note is named by its external id rather than by a
    title. What is left is what an observer can legitimately act on -- which
    schema, and whether the note satisfied it.
    """

    note_external_id: str
    schema_entity: str
    passed: bool


class SchemaValidationObserver:
    """Observes completed schema validations. A no-op in core."""

    async def on_notes_validated(
        self,
        *,
        project_external_id: str,
        outcomes: Sequence[ValidatedNoteOutcome],
    ) -> None:
        """React to a finished validation, after its report is complete.

        Called once per request with every note the request actually validated,
        which is not every note it looked at: entities whose frontmatter
        resolves to no schema are skipped, exactly as they are skipped in the
        report.

        Unlike `NoteContentMutationService.on_accepted_mutation`, this runs
        outside any transaction and has nothing to make atomic -- validation
        reads. An implementation is therefore responsible for its own durability
        and its own failures: raising here fails the caller's validation
        request, which is virtually never the right trade for bookkeeping that
        the user did not ask for.
        """
        return None
