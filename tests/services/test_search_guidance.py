"""Query guidance preserves explicit intent and never runs another retrieval."""

import pytest

from basic_memory.schemas.search import SearchQuery, SearchRetrievalMode
from basic_memory.services.search_guidance import unspaced_cjk_query_hint


@pytest.mark.parametrize("text", ["雾凇拼音", "  雾凇拼音  ", "日本語検索", "한국어검색"])
@pytest.mark.parametrize("mode", [SearchRetrievalMode.FTS, SearchRetrievalMode.HYBRID])
def test_plain_compounds_receive_guidance(text: str, mode: SearchRetrievalMode):
    assert unspaced_cjk_query_hint(SearchQuery(text=text, retrieval_mode=mode))


@pytest.mark.parametrize(
    "query",
    [
        SearchQuery(),
        SearchQuery(text="雾凇"),
        SearchQuery(text="雾凇 拼音"),
        SearchQuery(text='"雾凇拼音"'),
        SearchQuery(text="雾凇 AND 拼音"),
        SearchQuery(text="雾凇拼音*"),
        SearchQuery(text="雾凇拼音1"),
        SearchQuery(text="rime雾凇拼音"),
        SearchQuery(text="plainword"),
        SearchQuery(text="雾凇拼音", retrieval_mode=SearchRetrievalMode.VECTOR),
        SearchQuery(title="雾凇拼音"),
        SearchQuery(text="雾凇拼音", title="title"),
        SearchQuery(text="雾凇拼音", permalink="note"),
        SearchQuery(text="雾凇拼音", permalink_match="note*"),
    ],
)
def test_other_query_contracts_do_not_receive_compound_guidance(query: SearchQuery):
    assert unspaced_cjk_query_hint(query) is None
