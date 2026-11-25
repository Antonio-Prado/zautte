"""scripts/news_sync.py: the short paragraphs of an article stay with the one before, so that the
hour, the subject and the closing line of a convocation end up in the same passage."""

from scripts import news_sync

CONVOCATION = (
    "<p>E’ stato convocato il Consiglio Comunale in seduta aperta, nella consueta sala delle adunanze consiliari "
    "di viale De Gasperi, per il giorno 30 novembre 2026 alle ore 18:00 per la trattazione del seguente argomento:</p>"
    "<p>Analisi dei danni conseguenti all’evento straordinario di grandine del 21 luglio 2026 e stato delle procedure.</p>"
    "<p>Chiunque potrà partecipare ed intervenire.</p>"
    "<p>La seduta potrà comunque essere seguita in diretta streaming collegandosi a questa pagina: "
    "https://video.example.org/</p>")
LONG_A = "Primo paragrafo lungo. " + "Il testo prosegue con molte informazioni sul provvedimento. " * 6
LONG_B = "Secondo paragrafo lungo. " + "Altre informazioni, in un capoverso che sta bene da solo. " * 6


def _post(html: str) -> dict:
    return {"url": "https://bum.example.org/2026/11/24/consiglio-aperto/", "title": "Consiglio comunale aperto",
            "html": html, "date": "2026-11-24", "modified": "2026-11-24T10:00:00", "categories": []}


def test_a_convocation_in_short_paragraphs_is_one_passage():
    doc = news_sync.post_doc(_post(CONVOCATION), {})
    assert len(doc["chunks"]) == 1
    text = doc["chunks"][0]["text"]
    for piece in ("Notizia del 24 novembre 2026", "alle ore 18:00", "Analisi dei danni",
                  "Chiunque potrà partecipare ed intervenire.", "diretta streaming"):
        assert piece in text


def test_long_paragraphs_stay_apart_and_a_short_one_joins_the_one_before():
    text = news_sync.join_short_paragraphs(f"{LONG_A}\n\n{LONG_B}\n\nPer informazioni: 0735 000000.")
    blocks = text.split("\n\n")
    assert len(blocks) == 2
    assert blocks[0] == LONG_A.strip()
    assert blocks[1].endswith("capoverso che sta bene da solo.\nPer informazioni: 0735 000000.")


def test_a_block_never_grows_over_the_limit():
    items = "\n\n".join(f"Voce numero {i} di un elenco molto lungo di voci brevi." for i in range(60))
    blocks = news_sync.join_short_paragraphs(items).split("\n\n")
    assert len(blocks) > 1
    assert all(len(b) <= news_sync.JOINED_PARAGRAPH_MAX for b in blocks)
    assert sum(b.count("Voce numero") for b in blocks) == 60


def test_a_colon_pulls_the_next_paragraph_even_when_both_are_long():
    intro = LONG_A.strip()[:-1] + ":"
    blocks = news_sync.join_short_paragraphs(f"{intro}\n\n{LONG_B}").split("\n\n")
    assert len(blocks) == 1
