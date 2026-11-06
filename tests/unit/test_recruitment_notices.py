"""The calls-for-jobs job: the rows of the «Bandi di concorso» table become one known fact with the calls
open today, the ones announced and the ones just closed, which the API picks up without a restart."""
from datetime import date

from scripts import recruitment_notices as rn
from scripts.amt_sync import parse_page

PORTAL = "https://example.nuvolapalitalsoft.it/"
SECTION = PORTAL + "?page_id=36046&voceamt=544"
TODAY = date(2026, 11, 6)

TABLE_PAGE = """<html><body><div class="page-content"><h1>Bandi di concorso</h1>
<script>var ajax = {}; ajax.url = 'x'; ajax.data = 'TOK123';</script>
<table class="italsoft-table" data-ajax><thead><tr><th>Descrizione</th><th>Inizio validità</th>
<th>Fine validità</th><th>Tipo Documento</th><th style="width: 200px;"></th></tr></thead><tbody></tbody></table>
</div></body></html>"""
DETAIL_PAGE = """<html><body><div class="page-content"><h1>Bandi di concorso</h1>
<div class="italsoft-alert"><h2>Avviso di mobilità</h2><p>Bandi di concorso 27/10/2026 26/11/2026 Collegamenti
https://portale.inpa.gov.it/ui/public-area/concoursedetail/abc. Altro https://example.org/x,</p>
<ul><li><a href="?page_id=36046&amp;event=vediAllegato&amp;id=T1">Avviso.pdf</a> MD5: 0123456789abcdef0123456789abcdef</li></ul>
</div></div></body></html>"""
NO_TABLE_PAGE = "<html><body><div class='page-content'><h1>Bandi di concorso</h1></div></body></html>"


def row(i, title, start, end):
    return [f'<a href="{PORTAL}?page_id=36046&amp;voceamt=544&amp;dettaglio={i}" title="t">{title}</a>',
            f"<div>{start}</div>", f"<div>{end}</div>", "<div>Bandi di concorso</div>", '<a href="x">Vai</a>']


ROWS = [row(1, "Mobilità istruttore", "27/10/2026", "26/11/2026"),
        row(2, "Concorso agenti", "01/12/2026", "31/12/2026"),
        row(3, "Selezione dirigente", "01/09/2026", "30/09/2026"),
        row(4, "Vecchio concorso", "01/01/2025", "31/01/2025"),
        row(5, "Senza date %u201Cquote%u201D", "", ""),
        row(6, "Scade oggi", "01/10/2026", "06/11/2026")]


def calls():
    headers = parse_page(TABLE_PAGE, SECTION)["table"]["headers"]
    from scripts.amt_sync import parse_row
    return [parse_row(cells, headers, SECTION, {}) for cells in ROWS]


def test_classify_by_validity_dates():
    c = rn.classify(calls(), TODAY, closed_days=90)
    assert [x["title"] for x in c["open"]] == ["Scade oggi", "Mobilità istruttore", "Senza date \u201cquote\u201d"]
    assert [x["title"] for x in c["upcoming"]] == ["Concorso agenti"]
    assert [x["title"] for x in c["closed"]] == ["Selezione dirigente"]     # the 2025 one is older than 90 days
    assert c["open"][1]["end"] == date(2026, 11, 26) and c["open"][2]["start"] is None


def test_read_calls_pages_the_table_and_sees_a_missing_one():
    posted = []

    def post(url, form):
        posted.append((url, form))
        return [len(ROWS), ROWS]

    rows = rn.read_calls(lambda url: TABLE_PAGE, post, {"portal": PORTAL, "page_id": 36046}, 544)
    assert [r["title"] for r in rows] == [r[0].split(">")[1].split("<")[0] for r in ROWS]
    assert posted[0][1]["data"] == "TOK123" and "voceamt=544" in posted[0][0]
    assert rn.read_calls(lambda url: NO_TABLE_PAGE, post, {"portal": PORTAL, "page_id": 36046}, 544) is None


def test_detail_page_gives_the_full_title_and_the_links_not_the_attachments():
    call = {"detail": SECTION + "&dettaglio=1", "title": "Avviso di mobi..."}
    assert rn.detail(lambda url: DETAIL_PAGE, call) == {
        "title": "Avviso di mobilità",
        "links": ["https://portale.inpa.gov.it/ui/public-area/concoursedetail/abc", "https://example.org/x"]}
    # a shorter title on the page (another document) does not replace the one of the table
    assert "title" not in rn.detail(lambda url: DETAIL_PAGE, {**call, "title": "Avviso di mobilità esterna 2026"})

    def broken(url):
        raise ValueError("boom")
    assert rn.detail(broken, call) == {}
    assert rn.detail(lambda url: NO_TABLE_PAGE, call) == {}


def test_fact_lists_open_announced_and_closed_calls():
    c = rn.classify(calls(), TODAY)
    c["open"][1]["links"] = ["https://portale.inpa.gov.it/ui/public-area/concoursedetail/abc"]
    fact = rn.build_fact(c, TODAY, SECTION, "Comune di Esempio")
    text = fact["text"]
    assert fact["id"] == "recruitment-open" and fact["source"] == SECTION and fact["updated"] == "2026-11-06"
    assert "letto automaticamente il 6 novembre 2026" in text
    assert "BANDI APERTI OGGI" in text and "BANDI ANNUNCIATI" in text and "CHIUSI NEGLI ULTIMI 90 GIORNI" in text
    assert "- Mobilità istruttore (domande dal 27/10/2026 al 26/11/2026). Pagina del bando" in text
    assert "Collegamenti: https://portale.inpa.gov.it/ui/public-area/concoursedetail/abc" in text
    assert "- Senza date \u201cquote\u201d (periodo delle domande non indicato)" in text
    assert "- Concorso agenti (domande dal 01/12/2026 al 31/12/2026)" in text
    assert "- Selezione dirigente (scaduto il 30/09/2026)" in text
    assert "Vecchio concorso" not in text
    assert text.index("Scade oggi") < text.index("Mobilità istruttore") < text.index("Senza date")
    # a question about working for the administration: a strong word is needed
    assert "lavorare" in fact["keywords"] and "lavorare" in fact["require_any"]
    assert "lavoro" in fact["keywords"] and "lavoro" not in fact["require_any"]


def test_fact_without_open_calls_says_so():
    c = rn.classify([], TODAY)
    text = rn.build_fact(c, TODAY, SECTION, "Comune di Esempio")["text"]
    assert "Nessuno: al 06/11/2026 non c'è alcun bando" in text
    assert "BANDI ANNUNCIATI" not in text and "CHIUSI" not in text
