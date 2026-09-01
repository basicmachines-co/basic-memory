"""How the agent observation contract meets temporal qualifiers (SPEC-82).

`DocumentAgentObservationV1` re-parses its own formatted markdown and requires the
parsed fields to match what it was given. Now that the parser peels a valid-time
qualifier off content, an untrusted agent that puts one inside `content` no longer
round-trips -- and is rejected.

That is the intended MVP behavior, not an oversight: the agent contract has no temporal
field, so the alternative would be an agent silently minting valid-time assertions
through a text channel. Rejection is loud, and this test pins it so that adding
`temporal` to the agent contract later is a deliberate decision rather than an accident.
"""

import pytest
from pydantic import ValidationError

from basic_memory.schemas.document import DocumentAgentObservationV1


def test_agent_observation_content_with_qualifier_is_rejected():
    """An agent cannot smuggle authored valid time through the content field."""
    with pytest.raises(ValidationError, match="must match parsed Markdown semantics"):
        DocumentAgentObservationV1(
            category="summary",
            content="@effective[2026-06-10,2026-07-27) The cache layer will use Redis.",
        )


def test_agent_observation_with_a_malformed_qualifier_is_accepted_as_plain_text():
    """A refused qualifier is never peeled, so the line still round-trips exactly.

    This is the other half of "never silently dropped": text that only looks like a
    qualifier stays content, and the agent contract keeps accepting it.
    """
    observation = DocumentAgentObservationV1(
        category="summary",
        content="@asserted[2026-06-10,) The cache layer will use Redis.",
    )

    assert observation.content.startswith("@asserted[2026-06-10,)")


def test_ordinary_agent_observations_are_unaffected():
    """Acceptance 1 at the agent boundary: undated content behaves as it always did."""
    observation = DocumentAgentObservationV1(
        category="summary",
        content="The cache layer will use Redis.",
        tags=("infra",),
        context="agreed",
    )

    assert observation.content == "The cache layer will use Redis."
    assert observation.tags == ("infra",)
    assert observation.context == "agreed"


def test_email_addresses_in_agent_content_are_not_qualifiers():
    """`@` is common prose, and the contract must not start rejecting it."""
    observation = DocumentAgentObservationV1(
        category="summary",
        content="Contact paul@basicmemory.com about the cutover.",
    )

    assert "paul@basicmemory.com" in observation.content
