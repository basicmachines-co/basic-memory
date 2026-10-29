"""The `bm find --meta` predicate grammar and the field projection helpers.

These are pure functions: a predicate string becomes the search API's
metadata_filters dict, and a projected field path walks typed frontmatter. The
refusals are exercised through the CLI in tests/cli/test_cli_posix_argument_errors.py
and the end-to-end matches in test-int/cli/test_posix_commands_integration.py; this
file pins the translations those rely on.
"""

import pytest

import basic_memory.mcp.metadata_find as metadata_find
from basic_memory.cli.commands.find_predicates import parse_meta_predicates
from basic_memory.repository.metadata_filters import (
    MetadataPath,
    ParsedMetadataFilter,
    parse_metadata_filters,
    parse_metadata_path,
)

# Each predicate string translates onto exactly one metadata_filters entry, in
# the grammar the search API's parse_metadata_filters already supports.
PREDICATE_GRAMMAR = [
    ("status=active", {"status": "active"}),
    ("status = active", {"status": "active"}),
    ("confidence>0.6", {"confidence": {"$gt": 0.6}}),
    ("confidence >= 0.6", {"confidence": {"$gte": 0.6}}),
    ("confidence<0.6", {"confidence": {"$lt": 0.6}}),
    ("confidence<=0.6", {"confidence": {"$lte": 0.6}}),
    ("priority in high,critical", {"priority": {"$in": ["high", "critical"]}}),
    ("priority in high, critical", {"priority": {"$in": ["high", "critical"]}}),
    ("tags has security,oauth", {"tags": ["security", "oauth"]}),
    ("score between 0.3,0.8", {"score": {"$between": [0.3, 0.8]}}),
    # Quoting is the documented escape for literal values, and it holds inside
    # a list: the comma it protects belongs to the value, not to the list.
    ('label in "a,b",c', {"label": {"$in": ["a,b", "c"]}}),
    # A backslash-escaped quote stays inside the value; it neither closes the
    # token nor leaves it looking unterminated.
    ('label in "a\\"b",c', {"label": {"$in": ['a"b', "c"]}}),
    ('note="say \\"hi\\""', {"note": 'say "hi"'}),
    ('tags has "red, green"', {"tags": ["red, green"]}),
    ('name in "quoted"', {"name": {"$in": ["quoted"]}}),
    # An unquoted value may not start with an operator character (a mis-spelled
    # operator is the far likelier reading), so quoting is how a value that
    # genuinely does start with one is expressed — scalars and list elements alike.
    ('range=">=5"', {"range": ">=5"}),
    ('range>"<=5"', {"range": {"$gt": "<=5"}}),
    ('bound in ">=5","<=9"', {"bound": {"$in": [">=5", "<=9"]}}),
    ('marks has "<a>","=b"', {"marks": ["<a>", "=b"]}),
    # Quoting is also the escape for the tokens the grammar refuses unquoted:
    # the non-finite number spellings and null outside equality.
    ('score="NaN"', {"score": "NaN"}),
    ('score>"Infinity"', {"score": {"$gt": "Infinity"}}),
    ('owner in "null","alice"', {"owner": {"$in": ["null", "alice"]}}),
    # Dot-paths address nested frontmatter and pass through verbatim.
    ("review.approved=true", {"review.approved": True}),
    # The one alias search carries, so both surfaces accept one spelling.
    ("note_type=spec", {"type": "spec"}),
]


@pytest.mark.parametrize(("predicate", "expected"), PREDICATE_GRAMMAR)
def test_parse_meta_predicate_grammar(predicate, expected):
    assert parse_meta_predicates([predicate]) == expected


@pytest.mark.parametrize(("predicate", "expected"), PREDICATE_GRAMMAR)
def test_parsed_predicates_are_valid_api_metadata_filters(predicate, expected):
    """Every predicate the parser accepts is a filter the search API accepts.

    parse_metadata_filters is the server-side authority; running the produced
    dict through it proves the CLI grammar is a strict subset rather than a
    parallel dialect that only fails at request time.
    """
    assert parse_metadata_filters(parse_meta_predicates([predicate]))


@pytest.mark.parametrize(
    ("predicate", "expected_value"),
    [
        ("done=true", True),
        ("done=false", False),
        ("owner=null", None),
        ("count=3", 3),
        ("ratio=1.5", 1.5),
        ("status=active", "active"),
        # A JSON-quoted token forces the literal string, escaping the inference.
        ('status="true"', "true"),
        # Non-scalar JSON is not a filter value; the raw text stays a string.
        ("shape=[1,2]", "[1,2]"),
    ],
)
def test_predicate_values_are_json_scalar_inferred(predicate, expected_value):
    """Values type-infer so a predicate string produces the same dict a JSON
    filters caller would pass."""
    key = predicate.split("=", 1)[0]

    assert parse_meta_predicates([predicate]) == {key: expected_value}


def test_parse_meta_predicates_and_together():
    assert parse_meta_predicates(["status=active", "confidence>0.6"]) == {
        "status": "active",
        "confidence": {"$gt": 0.6},
    }


@pytest.mark.parametrize(
    "key",
    ["review..approved", ".owner", "owner.", "review.approved", "status", "note-1_a.b"],
)
def test_one_path_grammar_governs_predicates_and_filters(key):
    """find's predicates and the search API accept exactly the same paths.

    parse_metadata_path owns the grammar, and both surfaces call it rather than
    keeping a copy, so find cannot build a filter the repository then rejects.
    """
    well_formed = parse_metadata_path(key) is not None

    if well_formed:
        assert parse_meta_predicates([f"{key}=x"]) == {key: "x"}
        assert parse_metadata_filters({key: "x"})
    else:
        with pytest.raises(ValueError, match="malformed predicate key"):
            parse_meta_predicates([f"{key}=x"])
        with pytest.raises(ValueError, match="Unsupported metadata filter key"):
            parse_metadata_filters({key: "x"})


@pytest.mark.parametrize(
    ("key", "expected_parts"),
    [
        ("status", ("status",)),
        ("review.approved", ("review", "approved")),
        ("a.b.c", ("a", "b", "c")),
        ("note-1_a.b", ("note-1_a", "b")),
        ("  padded.key  ", ("padded", "key")),
    ],
)
def test_parse_metadata_path_yields_the_segments(key, expected_parts):
    """The parse is what produces the segments a path walk consumes."""
    path = parse_metadata_path(key)

    assert path is not None
    assert path.parts == expected_parts
    assert path.key == key.strip()


@pytest.mark.parametrize(
    "key", ["review..approved", ".owner", "owner.", "", "   ", "..", "a..b.c", "bad key"]
)
def test_parse_metadata_path_refuses_everything_that_is_not_a_path(key):
    """No segments come back for a non-path, so nothing can walk one."""
    assert parse_metadata_path(key) is None


def test_null_equality_reaches_the_api_as_an_is_null_clause():
    """'owner=null' is the one null spelling the grammar keeps, and it is not equality.

    The API parser turns it into an IS NULL clause; an ordinary equality clause
    would be `= NULL`, which no row satisfies.
    """
    filters = parse_meta_predicates(["owner=null"])

    assert filters == {"owner": None}
    assert parse_metadata_filters(filters) == [ParsedMetadataFilter(["owner"], "is_null", None)]


@pytest.mark.parametrize(
    ("entity_metadata", "fields", "expected"),
    [
        ({"title": "Alpha"}, ["title"], {"title": "Alpha"}),
        ({"review": {"approved": True}}, ["review.approved"], {"review.approved": True}),
        # A missing key renders as null — the row is never dropped.
        ({"title": "Alpha"}, ["missing"], {"missing": None}),
        # A non-dict intermediate ends the walk at null rather than raising.
        ({"review": "yes"}, ["review.approved"], {"review.approved": None}),
        (None, ["title"], {"title": None}),
        ({"a": {"b": {"c": 1}}}, ["a.b.c", "a.b"], {"a.b.c": 1, "a.b": {"c": 1}}),
    ],
)
def test_project_metadata_fields(entity_metadata, fields, expected):
    # Projection takes parsed paths, not strings: the fixtures go through the
    # same parse find does, which is what makes an unwalkable path unreachable
    # here rather than a null that looks like data.
    assert metadata_find.project_metadata_fields(entity_metadata, _paths(fields)) == expected


def _paths(keys: list[str]) -> list[MetadataPath]:
    """Parse well-formed test paths, asserting the fixtures really are paths."""
    parsed = [parse_metadata_path(key) for key in keys]
    assert all(path is not None for path in parsed), keys
    return [path for path in parsed if path is not None]


def test_projection_metadata_preserves_indexed_defaults_without_frontmatter():
    normalized = {"title": "Plain Note", "type": "note"}

    assert metadata_find.projection_metadata("# Plain Note\n\nBody", normalized) == normalized


def test_projection_metadata_preserves_indexed_defaults_for_malformed_frontmatter():
    normalized = {"title": "Malformed Note", "type": "note"}
    malformed = "---\ntags: [unterminated\n---\n# Malformed Note"

    assert metadata_find.projection_metadata(malformed, normalized) == normalized
