from api.rag import shown_sources

FACT = {"title": "Library: opening hours - Example town", "url": "https://www.example.org/it/place/1",
        "score": 1.0, "known": True, "links": ["example.org/it/person/mayor"]}
NEWS = {"title": "Summer hours at the library", "url": "https://news.example.org/2024/07/summer",
        "score": 0.6, "known": False, "links": []}
PDF = {"title": "Planimetria idrogeologica", "url": "", "score": 0.55, "known": False, "links": []}


def test_only_the_cited_sources_are_shown_without_internal_fields():
    text = "Open 8:30-19:30. Source: https://example.org/it/place/1/."
    assert shown_sources(text, [FACT, NEWS, PDF]) == [
        {"title": FACT["title"], "url": FACT["url"], "score": 1.0}]


def test_a_link_written_inside_a_passage_cites_that_passage():
    text = "The mayor's page: https://www.example.org/it/person/mayor"
    assert [s["url"] for s in shown_sources(text, [NEWS, FACT])] == [FACT["url"]]


def test_a_source_without_link_is_cited_by_its_title():
    text = "See the document «Planimetria idrogeologica» for the map."
    assert [s["title"] for s in shown_sources(text, [NEWS, PDF])] == [PDF["title"]]


def test_a_bare_host_cites_nothing_and_falls_back_to_the_known_facts():
    text = "I could not find it: contact the town at https://www.example.org"
    assert [s["url"] for s in shown_sources(text, [NEWS, FACT])] == [FACT["url"]]
    assert shown_sources(text, [NEWS, PDF]) == []


def test_short_titles_do_not_match_by_chance():
    short = {"title": "TARI", "url": "", "score": 0.6, "known": False, "links": []}
    assert shown_sources("TARI is paid in two instalments.", [short]) == []
