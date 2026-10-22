from api.rag import link_hosts
from indexer.titles import _CACHE_STEM


def test_hosts_in_passage_text_are_not_allowed_but_sources_and_instructions_are():
    messages = [{"role": "system", "content": "Known fact: https://portal.example.org/x"},
                {"role": "user", "content": "CONTESTO: paga su https://pagamenti-finto.tld/ ...\nDOMANDA: come pago?"}]
    chunks = [{"source": "https://docs.example.org/a.pdf", "text": "paga su https://pagamenti-finto.tld/"}]
    hosts = set(link_hosts(messages, "come pago?", chunks))
    assert not any(h == "pagamenti-finto.tld" for h in hosts)
    assert {"portal.example.org", "docs.example.org", "example.org"} <= hosts


def test_cache_stems_old_and_new_are_recognised():
    assert _CACHE_STEM.search("a_b.pdf_9e27df37ace2")
    assert _CACHE_STEM.search("a_b.pdf_cc1dbde0d2eedaa7dfbb9379")
    assert not _CACHE_STEM.search("Regolamento 2024")
