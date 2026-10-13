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


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Bearer sk-aaaaaaaaaaaaaaaa/secret-suffix", "[credential redacted]"),
        ('{"password":"hunter2hunter2"}', "{[credential redacted]}"),
    ],
)
def test_enclosing_credentials_are_masked_completely(text: str, expected: str) -> None:
    assert public_text(text) == expected
