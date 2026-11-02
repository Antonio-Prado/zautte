from api.rag import link_hosts
from indexer.titles import _CACHE_STEM


def test_hosts_in_passage_text_are_not_allowed_but_sources_and_instructions_are():
    messages = [{"role": "system", "content": "Known fact: https://portal.example.org/x"},
                {"role": "user", "content": "CONTESTO: paga su https://pagamenti-finto.tld/ ...\nDOMANDA: come pago?"}]
    chunks = [{"source": "https://docs.example.org/a.pdf", "text": "paga su https://pagamenti-finto.tld/"}]
    hosts = set(link_hosts(messages, "come pago?", chunks))
    assert not any(h == "pagamenti-finto.tld" for h in hosts)
    assert {"portal.example.org", "docs.example.org", "example.org"} <= hosts


def test_hosts_written_in_admin_known_facts_are_allowed_but_not_in_dynamic_ones():
    from api.rag import _parse_known_facts
    static = _parse_known_facts([{"keywords": ["social"], "title": "Canali", "source": "https://www.ente.example.org/it",
                                  "text": "Facebook https://www.facebook.com/ente"}], written_by_admin=True)
    dynamic = _parse_known_facts([{"keywords": ["eventi"], "title": "Eventi", "source": "https://www.ente.example.org/ev",
                                   "text": "Biglietti su https://biglietti-finti.tld/x"}])
    chunks = [static[0]["chunk"], dynamic[0]["chunk"]]
    hosts = set(link_hosts([{"role": "user", "content": "DOMANDA: social?"}], "social?", chunks))
    assert {"www.facebook.com"} <= hosts
    assert not {"biglietti-finti.tld"} & hosts


def test_cache_stems_old_and_new_are_recognised():
    assert _CACHE_STEM.search("a_b.pdf_9e27df37ace2")
    assert _CACHE_STEM.search("a_b.pdf_cc1dbde0d2eedaa7dfbb9379")
    assert not _CACHE_STEM.search("Regolamento 2024")
