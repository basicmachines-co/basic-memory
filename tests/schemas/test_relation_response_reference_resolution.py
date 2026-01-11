from basic_memory.schemas.response import RelationResponse


def test_relation_response_resolves_from_to_from_dict_fallbacks():
    data = {
        "permalink": "rel/1",
        "relation_type": "relates_to",
        "context": "ctx",
        "to_name": None,
        "from_entity": {"permalink": None, "file_path": "From.md"},
        "to_entity": {"permalink": None, "file_path": "To.md", "title": "To Title"},
    }

    rel = RelationResponse.model_validate(data)
    assert rel.from_id == "From.md"
    assert rel.to_id == "To.md"
    assert rel.to_name == "To Title"


def test_relation_response_resolves_from_to_from_orm_like_object_fallbacks():
    class EntityLike:
        def __init__(self, permalink, file_path, title=None):
            self.permalink = permalink
            self.file_path = file_path
            self.title = title

    class RelationLike:
        def __init__(self):
            self.permalink = "rel/2"
            self.relation_type = "relates_to"
            self.context = "ctx"
            self.to_name = None
            self.from_entity = EntityLike(permalink=None, file_path="From2.md")
            self.to_entity = EntityLike(permalink=None, file_path="To2.md", title="To2 Title")

    rel = RelationResponse.model_validate(RelationLike())
    assert rel.from_id == "From2.md"
    assert rel.to_id == "To2.md"
    assert rel.to_name == "To2 Title"
