"""Unit tests for knowledge model properties."""

from types import SimpleNamespace

from basic_memory.models.knowledge import Relation


def _entity(permalink: str | None, file_path: str) -> SimpleNamespace:
    return SimpleNamespace(permalink=permalink, file_path=file_path)


def _relation(**kwargs) -> Relation:
    """Build a Relation with required fields; relationship attrs set to None by default."""
    defaults = dict(
        id=1,
        project_id=1,
        from_id=42,
        to_id=None,
        to_name="Target",
        relation_type="implements",
        context=None,
    )
    defaults.update(kwargs)
    r = Relation(**defaults)
    # Simulate unloaded lazy relationships (no active session)
    r.from_entity = None
    r.to_entity = None
    return r


def test_relation_permalink_with_both_entities():
    """Normal case: both from_entity and to_entity are loaded."""
    r = _relation()
    r.from_entity = _entity(permalink="specs/source", file_path="specs/source.md")
    r.to_entity = _entity(permalink="features/target", file_path="features/target.md")

    assert r.permalink == "specs/source/implements/features/target"


def test_relation_permalink_no_to_entity():
    """to_entity is None — falls back to to_name."""
    r = _relation()
    r.from_entity = _entity(permalink="specs/source", file_path="specs/source.md")

    assert r.permalink == "specs/source/implements/target"


def test_relation_permalink_from_entity_none():
    """from_entity is None (race condition: entity deleted mid-resolution).

    Should fall back to str(from_id) rather than raising AttributeError.
    This is the guard added to fix the concurrent-watcher crash described in issue #758.
    """
    r = _relation(from_id=42)
    # from_entity stays None

    result = r.permalink
    assert "42" in result
    assert "implements" in result


def test_relation_permalink_from_entity_none_with_to_entity():
    """from_entity is None but to_entity is loaded — both guards compose correctly."""
    r = _relation(from_id=7)
    r.to_entity = _entity(permalink="features/resolved", file_path="features/resolved.md")

    result = r.permalink
    assert "7" in result
    assert "implements" in result
    assert "features/resolved" in result
