import pytest
from tau.privacy import public_text


@pytest.mark.parametrize(
    "assignment",
    [
        'PASSWORD="multiple secret words"',
        "API_KEY='multiple secret words'",
        r'ACCESS_TOKEN="secret with \"escaped quotes\" inside"',
    ],
)
def test_quoted_credentials_are_masked_completely(assignment: str) -> None:
    assert public_text(f"Before {assignment}; after") == "Before [credential redacted]; after"
