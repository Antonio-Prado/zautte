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
    found = rn.detail(lambda url: DETAIL_PAGE, call)
    assert found["title"] == "Avviso di mobilità"
    assert found["links"] == ["https://portale.inpa.gov.it/ui/public-area/concoursedetail/abc", "https://example.org/x"]
    # the attachments are kept apart, to read the notice: name, address, fingerprint
    assert [(a["name"], a["md5"]) for a in found["attachments"]] == [("Avviso.pdf", "0123456789abcdef0123456789abcdef")]
    assert "event=vediAllegato" in found["attachments"][0]["url"]
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
    assert "VALIDITÀ IN CORSO OGGI" in text and "BANDI ANNUNCIATI" in text and "FINITA NEGLI ULTIMI 90 GIORNI" in text
    assert "- Mobilità istruttore (validità indicata nella tabella: dal 27/10/2026 al 26/11/2026). Pagina del bando" in text
    assert "Collegamenti: https://portale.inpa.gov.it/ui/public-area/concoursedetail/abc" in text
    assert "- Senza date \u201cquote\u201d (validità non indicata nella tabella)" in text
    assert "- Concorso agenti (validità indicata nella tabella: dal 01/12/2026 al 31/12/2026)" in text
    assert "- Selezione dirigente (validità finita il 30/09/2026)" in text
    assert "Il testo dell'avviso viene prima delle date della tabella" in text
    assert "Vecchio concorso" not in text
    assert text.index("Scade oggi") < text.index("Mobilità istruttore") < text.index("Senza date")
    # a question about working for the administration: a strong word is needed
    assert "lavorare" in fact["keywords"] and "lavorare" in fact["require_any"]
    assert "lavoro" in fact["keywords"] and "lavoro" not in fact["require_any"]


def test_fact_without_open_calls_says_so():
    c = rn.classify([], TODAY)
    text = rn.build_fact(c, TODAY, SECTION, "Comune di Esempio")["text"]
    assert "Nessuno: al 06/11/2026 non c'è alcun bando" in text
    assert "BANDI ANNUNCIATI" not in text and "FINITA NEGLI ULTIMI" not in text


NOTICE = """COMUNE DI ESEMPIO
AVVISO PUBBLICO DI MOBILITÀ ESTERNA

                 REQUISITI DI AMMISSIONE A PENA DI ESCLUSIONE
I soggetti interessati devono essere in possesso dei seguenti requisiti:
1. essere dipendenti in servizio a tempo pieno ed indeterminato presso una delle
pubbliche Amministrazioni di cui all'art. 1, comma 2, del D.Lgs. n. 165/2001;
2. aver superato il periodo di prova.

La domanda si presenta sul Portale inPA. Il candidato riceverà una e-mail di conferma.
Sarà possibile modificare la domanda entro e non oltre la scadenza del termine di
presentazione della domanda: ore 23:59 del 30° giorno successivo alla pubblicazione
del presente avviso, mediante la stessa procedura telematica. Il termine è perentorio.
"""
FORM = "DICHIARAZIONE DEI SERVIZI PRESTATI\nIl sottoscritto dichiara di aver prestato servizio presso ..."


def test_notice_passages_quote_the_deadline_and_the_requirements_as_written():
    p = rn.notice_passages(NOTICE)
    # from the start of the sentence, on one line, where the page broke it
    assert p["deadline"].startswith("Sarà possibile modificare la domanda entro e non oltre la scadenza del termine di "
                                    "presentazione della domanda: ore 23:59 del 30° giorno successivo alla pubblicazione")
    assert p["requirements"].startswith("REQUISITI DI AMMISSIONE A PENA DI ESCLUSIONE I soggetti interessati")
    assert "essere dipendenti in servizio a tempo pieno ed indeterminato presso una delle pubbliche" in p["requirements"]
    assert rn.notice_passages(FORM) == {} and rn.notice_passages("") == {}
    long = rn.notice_passages(NOTICE + "REQUISITI " * 400)
    assert len(long["deadline"]) <= rn.DEADLINE_CHARS + 4 and long["deadline"].endswith("[…]")


def test_notice_deadline_is_counted_from_the_publication_only_when_the_notice_says_so():
    call = {"start": date(2026, 9, 24), "end": date(2031, 12, 31), "deadline": rn.notice_passages(NOTICE)["deadline"]}
    assert rn.notice_deadline(call) == date(2026, 10, 24)
    assert rn.notice_deadline({**call, "deadline": "entro il 30° giorno successivo alla data di pubblicazione"}) \
        == date(2026, 10, 24)
    assert rn.notice_deadline({**call, "start": None}) is None                      # the table has no start
    assert rn.notice_deadline({**call, "deadline": "entro e non oltre il 15 ottobre 2026"}) is None
    assert rn.notice_deadline({"start": date(2026, 9, 24)}) is None                 # no notice read


def test_read_notice_reads_each_attachment_once_and_stops_at_the_notice():
    texts = {"u1": FORM, "u2": NOTICE, "u3": "mai letto"}
    fetched = []

    def get_bytes(url):
        fetched.append(url)
        return url.encode()

    def pdf_text(data, max_chars):
        return texts[data.decode()]

    call = {"detail": SECTION + "&dettaglio=1",
            "attachments": [{"name": "Modulo.pdf", "url": "u1", "md5": "m1"},
                            {"name": "Avviso.pdf", "url": "u2", "md5": "m2"},
                            {"name": "Elenco ammessi.pdf", "url": "u3", "md5": "m3"}]}
    cache = {}
    found = rn.read_notice(get_bytes, call, cache, pdf_text)
    assert found["notice"] == "Avviso.pdf" and "30° giorno" in found["deadline"] and "dipendenti" in found["requirements"]
    assert fetched == ["u1", "u2"]                       # the third one is not needed
    assert cache["m1"] == {} and "deadline" in cache["m2"] and "m3" not in cache
    # the next run downloads nothing: what each attachment says is remembered by its fingerprint
    assert rn.read_notice(get_bytes, call, cache, pdf_text) == found and fetched == ["u1", "u2"]
    # an attachment that changes has another fingerprint and is read again

    def broken(url):
        raise ValueError("boom")
    changed = {**call, "attachments": [{"name": "Avviso.pdf", "url": "u2", "md5": "m9"}]}
    assert rn.read_notice(broken, changed, cache, pdf_text) == {} and "m9" not in cache
    assert rn.read_notice(get_bytes, {"detail": "x"}, cache, pdf_text) == {}


def test_cache_of_the_notices_is_written_and_read_back(tmp_path):
    path = tmp_path / "facts" / "cache.json"
    assert rn.load_cache(path) == {}
    rn.save_cache({"m1": {}, "m2": {"deadline": "entro…"}}, path)
    assert rn.load_cache(path) == {"m1": {}, "m2": {"deadline": "entro…"}}
    path.write_text("[1, 2]", encoding="utf-8")
    assert rn.load_cache(path) == {}                     # not what the job wrote: started again


def test_fact_puts_the_text_of_the_notice_before_the_dates_of_the_table():
    rows = [row(1, "Selezione dirigente tecnico", "24/09/2026", "31/12/2031"),
            row(2, "Mobilità istruttore", "27/10/2026", "26/11/2026"),
            row(3, "Concorso senza avviso letto", "01/11/2026", "30/11/2026")]
    headers = parse_page(TABLE_PAGE, SECTION)["table"]["headers"]
    from scripts.amt_sync import parse_row
    today = date(2026, 11, 23)
    c = rn.classify([parse_row(cells, headers, SECTION, {}) for cells in rows], today)
    by_title = {x["title"]: x for x in c["open"]}
    passages = rn.notice_passages(NOTICE)
    by_title["Selezione dirigente tecnico"].update(
        {**passages, "notice": "AVVISO PUBBLICO.pdf",
         "attachments": [{"name": "AVVISO PUBBLICO.pdf"}, {"name": "ELENCO CANDIDATI AMMESSI.pdf"}]})
    by_title["Mobilità istruttore"].update({**passages, "notice": "Avviso.pdf", "attachments": [{"name": "Avviso.pdf"}]})
    by_title["Concorso senza avviso letto"].update({"attachments": [{"name": "Bando scansionato.pdf"}]})
    text = rn.build_fact(c, today, SECTION, "Comune di Esempio")["text"]
    # the table says 2031; the notice says thirty days from the publication: that is the deadline, and it has passed
    assert "(validità indicata nella tabella: dal 24/09/2026 al 31/12/2031)" in text
    assert "Allegati pubblicati sulla pagina: AVVISO PUBBLICO.pdf; ELENCO CANDIDATI AMMESSI.pdf." in text
    assert "Termine per le domande, dal testo dell'avviso («AVVISO PUBBLICO.pdf»): «Sarà possibile" in text
    assert ("pubblicazione il 24/09/2026 (inizio validità della tabella) più 30 giorni = 24/10/2026: "
            "termine GIÀ PASSATO") in text
    assert "più 30 giorni = 26/11/2026: termine non ancora passato." in text
    assert text.count("Chi può partecipare, dal testo dell'avviso: «REQUISITI DI AMMISSIONE") == 2
    # an attachment that could not be read: the fact says where the deadline is, and invents none
    assert "Il termine per le domande non è stato letto in automatico dagli allegati" in text
