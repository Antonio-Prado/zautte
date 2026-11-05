"""Transparency sections of a Nuvola portal (scripts/amt_sync.py)."""
from scripts import amt_sync as m

PORTAL = "https://example.nuvolapalitalsoft.it/"

PAGE = """<html><body><script>var u = "/wp-admin/admin-ajax.php";</script>
<div class="page-content"><h1>il Bilancio di previsione</h1>
<div class="amt-tiles-grid"><div class="amt-tile"><div class="amt-tile-body">
<a class="amt-tile-title" href="https://example.nuvolapalitalsoft.it/?page_id=36046&voceamt=459">Dati tabellari</a>
<p class="amt-tile-subtitle">7 elementi pubblicati</p></div></div></div>
<div class="italsoft-alert"><h2>Riferimenti normativi</h2><hr>D.Lgs. 33/2013</div>
<div class="italsoft-alert"><h2 title="Bilancio di previsione 2026/2028">Bilancio di previsione 2026/2028</h2><hr>
<p>Il PEG è nella sezione <a href="https://example.nuvolapalitalsoft.it/?page_id=36046&amp;voceamt=33">Performance</a></p>
<h4>Allegati</h4><ul>
<li><a href="https://example.nuvolapalitalsoft.it/?page_id=36046&event=vediAllegato&id=TOKEN1" title="Scarica"><img src="x.png"></a>
1) <a href="https://example.nuvolapalitalsoft.it/?page_id=36046&event=vediAllegato&id=TOKEN1">1_-_Bilancio.pdf</a>
<small><i>(impronta MD5: a7c4b9ca1ba8d806c03baa2969200c69)</i></small></li>
<li>2) <a href="https://example.nuvolapalitalsoft.it/?page_id=36046&event=vediAllegato&id=TOKEN2">2_-_Nota.pdf</a>
<small><i>(impronta MD5: ec1590e65e807df0ed62798a9304d9e1)</i></small></li></ul>
<span class="data-ultimo-aggiornamento">Periodo di pubblicazione dal 27/04/2026 al 31/12/2031</span></div>
</div></body></html>"""


def test_parse_page_tiles_documents_and_attachments():
    page = m.parse_page(PAGE, PORTAL + "?page_id=36046&voceamt=450")
    assert page["title"] == "il Bilancio di previsione"
    assert page["children"] == [(459, "Dati tabellari")]   # the link in a description is not a sub-section
    assert not page["lists"]                                # admin-ajax in a site script is not a search table
    (doc,) = page["docs"]                                   # the box without attachments is not a document
    assert doc["title"] == "Bilancio di previsione 2026/2028"
    assert doc["description"] == "Il PEG è nella sezione Performance"
    assert [a["name"] for a in doc["attachments"]] == ["1_-_Bilancio.pdf", "2_-_Nota.pdf"]
    assert doc["attachments"][0]["md5"] == "a7c4b9ca1ba8d806c03baa2969200c69"
    assert doc["published"] == "2026-04-27"


def test_digest_ignores_the_tokens():
    doc = m.parse_page(PAGE, PORTAL)["docs"][0]
    again = m.parse_page(PAGE.replace("TOKEN1", "OTHER"), PORTAL)["docs"][0]
    assert m.doc_digest(doc) == m.doc_digest(again)
    changed = m.parse_page(PAGE.replace("a7c4b9ca", "00000000"), PORTAL)["docs"][0]
    assert m.doc_digest(doc) != m.doc_digest(changed)


def test_walk_gives_every_document_a_stable_source_and_its_path():
    pages = {450: PAGE, 459: PAGE.replace('class="amt-tile-title"', 'class="x"').replace("il Bilancio di previsione", "Dati tabellari")}

    def fetch(url):
        return pages[int(url.rsplit("=", 1)[1])]
    docs, counts = m.walk(fetch, {"portal": PORTAL, "page_id": 36046}, 450, "Bilanci")
    assert counts == {"pages": 2, "failed": 0, "lists": 0, "rows": 0}
    assert docs[0]["source"] == PORTAL + "?page_id=36046&voceamt=450#bilancio-di-previsione-2026-2028"
    assert docs[0]["path"] == ["Bilanci", "il Bilancio di previsione"]
    assert docs[1]["path"] == ["Bilanci", "il Bilancio di previsione", "Dati tabellari"]


def test_attachments_share_the_text_budget(monkeypatch):
    doc = m.parse_page(PAGE, PORTAL)["docs"][0]
    monkeypatch.setattr(m, "_pdf_text", lambda data, limit: "x" * min(limit, len(data)))
    texts = m.read_attachments(lambda url: b"y" * 50000, doc, {"max_chars": 10000})
    assert [len(t) for _, t in texts] == [5000, 5000]   # the first one does not take everything


def test_replaced_sources_match_the_decoded_address_and_spare_this_job():
    sources = {
        "https://old.example.org/f//bilanci/Rendiconto%202025.pdf",
        "https://old.example.org/f//AMS_Spa_Bilancio_2019.pdf",
        PORTAL + "?page_id=36046&voceamt=450#rendiconto-2025",
    }
    assert m.replaced_sources(sources, ["/f//bilanci/", r"rendiconto 2025"]) == {
        "https://old.example.org/f//bilanci/Rendiconto%202025.pdf"}
    assert m.replaced_sources(sources, []) == set()


def test_section_limits_change_the_digest_only_when_set():
    doc = m.parse_page(PAGE, PORTAL)["docs"][0]
    assert m.section_limits({"voceamt": 56, "name": "Bilanci"}) == {}
    assert m.doc_digest(doc, {}) == m.doc_digest(doc)          # sections without limits: nothing re-read
    limits = m.section_limits({"voceamt": 56, "max_chars": "3000000"})
    assert limits == {"max_chars": 3000000}
    assert m.doc_digest(doc, limits) != m.doc_digest(doc)      # new limits: the documents are read again
    assert m.doc_digest(doc, limits) == m.doc_digest(doc, {"max_chars": 3000000})


def test_section_max_chars_keeps_more_text(monkeypatch):
    doc = m.parse_page(PAGE, PORTAL)["docs"][0]
    monkeypatch.setattr(m, "_pdf_text", lambda data, limit: "x" * min(limit, len(data)))
    config = {"max_chars": 10000}
    texts = m.read_attachments(lambda url: b"y" * 50000, doc, {**config, **m.section_limits({"max_chars": 200000})})
    assert [len(t) for _, t in texts] == [50000, 50000]


def test_signed_attachments_mark_the_digest():
    doc = m.parse_page(PAGE, PORTAL)["docs"][0]
    signed = {**doc, "attachments": [{**a, "name": a["name"] + ".p7m"} for a in doc["attachments"]]}
    assert m.doc_digest(signed) != m.doc_digest({**signed, "attachments": doc["attachments"]})
    assert m.doc_digest(doc) == m.doc_digest(doc, {})                  # plain PDFs: digest unchanged


def test_pdf_text_opens_a_signed_envelope(monkeypatch):
    import indexer.pdf_extractor as px
    from tests.unit.test_p7m import PDF, envelope
    monkeypatch.setattr(px, "extract_text_from_pdf", lambda path: path.read_bytes()[:8].decode())
    assert m._pdf_text(envelope(PDF), 100) == "%PDF-1.4"
    assert m._pdf_text(b"PK\x03\x04 a zip", 100) == ""


def test_office_attachments_mark_the_digest_and_are_read(monkeypatch):
    from tests.unit.test_office import DOCX
    doc = m.parse_page(PAGE, PORTAL)["docs"][0]
    office = {**doc, "attachments": [{**doc["attachments"][0], "name": "nota.docx"}]}
    assert m.doc_digest(office) != m.doc_digest({**office, "attachments": [{**doc["attachments"][0], "name": "nota.pdf"}]})
    texts = m.read_attachments(lambda url: DOCX, office, {"max_chars": 1000})
    assert texts == [("nota.docx", "Relazione annuale\nSecondo paragrafo")]


TABLE_PAGE = """<html><body><div class="page-content"><h1>Bandi di concorso</h1>
<script>var ajax = {}; ajax.url = 'x'; ajax.data = 'TOK123';</script>
<table class="italsoft-table" data-ajax><thead><tr><th>Descrizione</th><th>Numero</th><th>Data</th>
<th>Cod.Fiscale/P.IVA</th><th>Responsabile</th><th style="width: 200px;"></th></tr></thead><tbody></tbody></table>
</div></body></html>"""


def row(i):
    return [f'<a href="{PORTAL}?page_id=36046&amp;voceamt=544&amp;dettaglio={i}" title="t">Concorso n. {i}</a>',
            f"<div>{i}</div>", f"<div>0{i + 1}/03/2026</div>", "<div>RSSMRA80A01H501U</div>", "<div>Rossi</div>",
            '<a href="x">Vai al dettaglio</a>']


def test_parse_page_sees_a_search_table_with_its_headers_and_token():
    page = m.parse_page(TABLE_PAGE, PORTAL + "?page_id=36046&voceamt=544")
    assert page["lists"] and page["table"] == {"headers": ["Descrizione", "Numero", "Data", "Cod.Fiscale/P.IVA", "Responsabile", ""],
                                               "token": "TOK123"}
    assert m.parse_page(PAGE, PORTAL)["table"] is None


def test_parse_row_keeps_the_columns_but_the_personal_ones():
    headers = m.parse_page(TABLE_PAGE, PORTAL)["table"]["headers"]
    card = m.parse_row(row(7), headers, PORTAL, {"table_skip_columns": ["^Respons"]})
    assert card == {"title": "Concorso n. 7", "description": "Numero: 7; Data: 08/03/2026", "attachments": [],
                    "published": "2026-03-08", "detail": PORTAL + "?page_id=36046&voceamt=544&dettaglio=7"}
    assert "RSSMRA" not in m.parse_row(row(7), headers, PORTAL, {})["description"]   # even without config
    assert m.parse_row(["<div>no link</div>", "<div>1</div>"], headers, PORTAL, {}) is None


def test_table_rows_walks_the_pages(monkeypatch):
    monkeypatch.setattr(m, "_TABLE_PAGE", 2)
    calls = []

    def post(url, form):
        calls.append(url)
        assert form["data"] == "TOK123" and form["event"] == "tablePager"
        page = int(url.rsplit("page=", 1)[1].split("&")[0])
        return ["5", [row(i) for i in range(page * 2, min(page * 2 + 2, 5))]]
    table = m.parse_page(TABLE_PAGE, PORTAL)["table"]
    rows = m.table_rows(post, {"portal": PORTAL, "page_id": 36046}, 544, table, PORTAL)
    assert [r["title"] for r in rows] == [f"Concorso n. {i}" for i in range(5)]
    assert len(calls) == 3 and "voceamt=544&page=2&size=2" in calls[2]


def test_walk_reads_small_tables_in_full_and_big_ones_as_cards(monkeypatch):
    monkeypatch.setattr(m, "_TABLE_PAGE", 10)
    fetched = []

    def fetch(url):
        fetched.append(url)
        return PAGE if "dettaglio=" in url else TABLE_PAGE

    def post(url, form):
        return ["3", [row(i) for i in range(3)]]
    config = {"portal": PORTAL, "page_id": 36046, "max_table_details": 300}
    docs, counts = m.walk(fetch, config, 544, "Bandi di concorso", post=post)
    assert counts == {"pages": 1, "failed": 0, "lists": 1, "rows": 3}
    assert len(docs) == 3 and not docs[0]["card"] and docs[0]["row"]
    assert docs[0]["source"] == PORTAL + "?page_id=36046&voceamt=544&dettaglio=0"
    assert docs[0]["attachments"][0]["name"] == "1_-_Bilancio.pdf"              # from the detail page
    assert docs[0]["description"].startswith("Numero: 0; Data: 01/03/2026; Responsabile: Rossi. ")   # the card's fields first
    assert len(fetched) == 4                                                    # the page + 3 details

    known = {docs[0]["source"]: {"row": docs[0]["row"], "doc": {k: docs[0][k] for k in ("title", "description", "attachments", "published")}}}
    fetched.clear()
    again, _ = m.walk(fetch, config, 544, "Bandi di concorso", post=post, known=known)
    assert len(fetched) == 3 and again[0]["cached"]["title"] == "Concorso n. 0"   # the known detail is not fetched

    cards, counts = m.walk(fetch, {**config, "max_table_details": 2}, 544, "x", post=post)
    assert all(d["card"] and not d["attachments"] for d in cards) and counts["rows"] == 3
    assert m.doc_text(cards[1], "Ente", []) == ("Ente, Amministrazione trasparente, sezione x > Bandi di concorso: Concorso n. 1.\n"
                                               "Numero: 1; Data: 02/03/2026\nPubblicato dal 02/03/2026\n"
                                               f"Pagina del documento: {PORTAL}?page_id=36046&voceamt=544&dettaglio=1")


def test_walk_without_post_only_counts_the_tables():
    docs, counts = m.walk(lambda url: TABLE_PAGE, {"portal": PORTAL, "page_id": 36046}, 544, "x")
    assert docs == [] and counts["lists"] == 1 and counts["rows"] == 0


def test_a_table_that_fails_marks_the_section_as_not_read():
    def post(url, form):
        raise ValueError("boom")
    docs, counts = m.walk(lambda url: TABLE_PAGE, {"portal": PORTAL, "page_id": 36046}, 544, "x", post=post)
    assert docs == [] and counts["failed"] == 1
