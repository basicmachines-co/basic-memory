"""Argument refusals of the POSIX verbs, through the `bm` CLI.

Every case here is refused before the command routes or opens a client, so the
tests run the real command with no project data behind it. A refusal is exit 1
with the reason on stderr; behavior that needs notes lives in
test-int/cli/test_posix_commands_integration.py.
"""

import pytest
from typer.testing import CliRunner

from basic_memory.cli.main import app as cli_app

runner = CliRunner()


def assert_refused(args: list[str], message: str) -> None:
    result = runner.invoke(cli_app, args)

    assert result.exit_code == 1, result.output
    assert message in result.stderr


# --- cat ---


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--section", "A", "--lines", "2-"], "cannot be combined with start_line/end_line"),
        (["--section", "A", "--lines", "1-3"], "cannot be combined with start_line/end_line"),
        (["--max-tokens", "0"], "max_tokens must be >= 1"),
        (["--max-tokens", "-5"], "max_tokens must be >= 1"),
        # A line range with max_tokens is document-absolute (frontmatter included);
        # --no-frontmatter ranges are body-relative, so mixing the two is refused.
        (
            ["--max-tokens", "5", "--lines", "2-", "--no-frontmatter"],
            "requires include_frontmatter=True",
        ),
        (
            ["--max-tokens", "5", "--lines", "1-3", "--no-frontmatter"],
            "requires include_frontmatter=True",
        ),
        (["--lines", "0"], "start_line must be >= 1"),
        (["--lines", "0-4"], "start_line must be >= 1"),
        (["--lines", "2-1"], "end_line must be >= start_line"),
        (["--lines", "abc"], '--lines must be "N-M"'),
    ],
)
def test_cat_refuses_bad_slice_arguments(args, message):
    assert_refused(["cat", "anything", *args], message)


# --- grep ---


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ([""], "pattern must not be empty"),
        (["   "], "pattern must not be empty"),
        (["ok", "--page", "0"], "page must be >= 1"),
        (["ok", "--page-size", "0"], "page_size must be >= 1"),
        (["ok", "--max-matches", "3"], "max_matches requires context_lines"),
        (["ok", "-C", "1", "--semantic"], "context_lines cannot be combined with semantic"),
        (["ok", "-C", "1", "--page-size", "101"], "line scanning page_size must be <= 100"),
    ],
)
def test_grep_refuses_bad_arguments(args, message):
    assert_refused(["grep", *args], message)


# --- ls ---


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--page", "0"], "page must be >= 1"),
        (["--page-size", "0"], "page_size must be >= 1"),
        (["--page-size", "201"], "page_size must be <= 200"),
    ],
)
def test_ls_refuses_bad_pagination(args, message):
    assert_refused(["ls", *args], message)


# --- find (directory listing) ---


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--page", "0"], "page must be >= 1"),
        (["--page-size", "0"], "page_size must be >= 1"),
        (["--page-size", "201"], "page_size must be <= 200"),
    ],
)
def test_find_refuses_bad_pagination(args, message):
    assert_refused(["find", *args], message)


# --- find --meta: combination rules ---


@pytest.mark.parametrize(
    ("args", "message"),
    [
        # The search API has no filename-glob facility, so a --name pattern has
        # no faithful translation; refuse rather than silently ignore it.
        (["--name", "*.md", "--meta", "status=active"], "'name' cannot combine with 'meta'"),
        # The file-path scope is whole-subtree; a depth bound is inexpressible.
        (["--depth", "3", "--meta", "status=active"], "'depth' cannot combine with 'meta'"),
        (["--fields", "title"], "'fields' requires 'meta' predicates"),
        (["--meta", "status=active", "--fields", ""], "must be non-empty"),
        (["--meta", "status=active", "--fields", "  "], "must be non-empty"),
        (["--meta", "status=active", "--fields", "title,"], "must be non-empty"),
        # A malformed field path walked an empty segment to null for every hit,
        # which reads exactly like a field the notes do not carry.
        (["--meta", "status=active", "--fields", "review..approved"], "malformed field path"),
        (["--meta", "status=active", "--fields", ".owner"], "malformed field path"),
        (["--meta", "status=active", "--fields", "owner."], "malformed field path"),
        (["--meta", "status=active", "--fields", "title,a..b"], "malformed field path"),
        # The metadata arm bounds pagination exactly as the listing arm does,
        # before routing rather than as a transport error from the search API.
        (["--meta", "status=active", "--page", "0"], "page must be >= 1"),
        (["--meta", "status=active", "--page-size", "0"], "page_size must be >= 1"),
        (["--meta", "status=active", "--page-size", "201"], "page_size must be <= 200"),
    ],
)
def test_find_meta_combination_rules_refuse(args, message):
    assert_refused(["find", *args], message)


# --- find --meta: predicate grammar refusals ---
# Each refusal names find's grammar before any request is built.


@pytest.mark.parametrize(
    ("predicates", "message"),
    [
        # The API has no $ne, so != is deliberately absent from the grammar.
        (["status!=active"], "unsupported predicate operator in 'status!=active'"),
        (["status ~= active"], "unsupported predicate operator"),
        (["priority gte 3"], "unsupported predicate operator"),
        (["priority in"], "unsupported predicate operator"),
        (["nothing"], "unsupported predicate operator"),
        ([""], "unsupported predicate operator"),
        (["status matches active"], "supported: = > >= < <= in has between"),
        (["score between 0.3"], "'between' needs exactly min,max"),
        (["score between 0.1,0.2,0.3"], "'between' needs exactly min,max"),
        (["priority in high,,low"], "empty list element"),
        (["status="], "has no value"),
        (["status=active", "status=draft"], "duplicate predicate key 'status'"),
        (["score>0.3", "score<0.8"], "use 'between' for ranges"),
        # The alias collapses onto the same key, so the collision is still caught.
        (["note_type=note", "type=spec"], "duplicate predicate key 'type'"),
    ],
)
def test_find_meta_refuses_unsupported_predicates(predicates, message):
    meta_args = [arg for predicate in predicates for arg in ("--meta", predicate)]
    assert_refused(["find", *meta_args], message)


@pytest.mark.parametrize(
    "predicate",
    [
        # Symbol operators: the regex matches the longest SUPPORTED spelling, so
        # the second operator character used to land at the head of the value.
        "status==active",
        "status=>active",
        "status=<active",
        "count>>3",
        "count>=>3",
        "count<<1",
        "count<=<1",
        # Word operators fold the same way: everything after them is the value.
        "priority in >high",
        "tags has =security",
        "score between >0.3,0.8",
        "score between 0.3,<0.8",
        # Unquoted, a value starting with an operator character reads as a typo.
        "range=>=5",
    ],
)
def test_find_meta_malformed_operators_refuse_instead_of_folding_into_the_value(predicate):
    """REGRESSION: a mis-spelled multi-character operator is a refusal, not a value.

    'status==active' used to parse as {"status": "=active"} and 'count>>3' as
    {"count": {"$gt": ">3"}}: filters for text no note carries.
    """
    assert_refused(["find", "--meta", predicate], "unsupported predicate operator")


def test_find_meta_malformed_operator_refusal_teaches_the_grammar_and_the_escape():
    result = runner.invoke(cli_app, ["find", "--meta", "status==active"])

    assert result.exit_code == 1
    assert "unsupported predicate operator in 'status==active'" in result.stderr
    assert "supported: = > >= < <= in has between" in result.stderr
    assert 'quote the value as "=active"' in result.stderr


@pytest.mark.parametrize(
    "predicate",
    [
        # A doubled, leading or trailing dot is not a dot path.
        "review..approved=true",
        ".owner=null",
        "owner.=x",
        "status.=active",
        "a..b>1",
        ".score>=0.5",
        "trailing.<9",
        # Word operators capture the key the same way, so they fold identically.
        "review..approved in a,b",
        ".tags has security",
        "owner. between 1,2",
    ],
)
def test_find_meta_malformed_keys_refuse_before_transport(predicate):
    """REGRESSION: a key that is not a dot path is refused by find, not by the API.

    The search API would refuse these with "Unsupported metadata filter key",
    which names neither find nor the shape a key must have.
    """
    assert_refused(["find", "--meta", predicate], "malformed predicate key")


def test_find_meta_malformed_key_refusal_names_the_key_and_the_grammar():
    result = runner.invoke(cli_app, ["find", "--meta", "review..approved=true"])

    assert result.exit_code == 1
    assert "malformed predicate key 'review..approved' in 'review..approved=true'" in result.stderr
    assert "dot-separated names of letters, digits, '_' or '-'" in result.stderr
    assert "review.approved" in result.stderr


@pytest.mark.parametrize(
    "predicate",
    [
        # Python's JSON reader accepts these three spellings as an extension...
        "score=NaN",
        "score=Infinity",
        "score=-Infinity",
        # ...and silently overflows an oversized exponent to infinity.
        "score=1e999",
        "score=-1e999",
        # Every operator reads its values through the same scalar reader.
        "score>NaN",
        "score<=Infinity",
        "score in 0.5,NaN",
        "marks has NaN",
        "score between NaN,0.8",
        "score between 0.3,1e999",
    ],
)
def test_find_meta_non_finite_numbers_refuse_instead_of_failing_at_transport(predicate):
    """REGRESSION: a non-finite number died in the request encoder, not the grammar."""
    assert_refused(["find", "--meta", predicate], "non-finite number")


def test_find_meta_non_finite_refusal_names_the_predicate_and_the_escape():
    result = runner.invoke(cli_app, ["find", "--meta", "score=NaN"])

    assert result.exit_code == 1
    assert "predicate 'score=NaN' has a non-finite number 'NaN'" in result.stderr
    assert "predicate values must be finite numbers" in result.stderr
    assert 'quote the value as "NaN"' in result.stderr


@pytest.mark.parametrize(
    "predicate",
    [
        'status="active',
        'status = "active',
        'confidence>"0.6',
        # A quote that opens, closes, and opens again is still unterminated.
        'name="a"b"c',
        # The list operators reach the same check through their split elements.
        'priority in "high,low',
        'tags has red,"green',
        'score between "0.3,0.8',
    ],
)
def test_find_meta_dangling_quote_refuses_for_every_operator(predicate):
    """REGRESSION: the scalar path used to keep the dangling quote as the value."""
    assert_refused(["find", "--meta", predicate], "unterminated quoted value")


def test_find_meta_unterminated_quote_refusal_teaches_both_uses_of_quoting():
    result = runner.invoke(cli_app, ["find", "--meta", 'status="active'])

    assert result.exit_code == 1
    assert "predicate 'status=\"active' has an unterminated quoted value" in result.stderr
    assert 'status="active"' in result.stderr
    assert 'label in "a,b",c' in result.stderr


@pytest.mark.parametrize(
    "predicate",
    [
        "score>null",
        "score>=null",
        "score<null",
        "score<=null",
        "priority in null,high",
        "tags has null",
        "score between null,0.8",
    ],
)
def test_find_meta_null_refuses_outside_equality(predicate):
    """A SQL comparison with NULL is never true, so only '=' may carry null."""
    assert_refused(["find", "--meta", predicate], "null matches only as equality")
