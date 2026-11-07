"""The figures of the transparency portal as dynamic known facts (scripts/transparency_facts.py)."""
from datetime import date

from scripts import transparency_facts as tf

SECTION = "https://example.nuvolapalitalsoft.it/?page_id=36046&voceamt=173"
TODAY = date(2026, 11, 7)
SITE = "Comune di Esempio"

PAY_TEXT = """ORGANI DI INDIRIZZO POLITICO – Compensi Anno 2025
n.\t\tConsigliere Comunale\tIndennità di Carica\tIndennità di Fine Mandato\tGettoni di Presenza\tImporti di viaggi di servizio e missioni pagati con fondi pubblici\tTOTALE GENERALE (elargito dal Comune di Esempio) al 31/12/2025
1\tRossi Mario\tSindaco\t€ 51.272,31\t€ 17.957,32\t///\t€ 1.310,64\t€ 70.540,27
2\tBianchi Anna\tVice-Sindaco\t€ 15.939,00\t///\t///\t€ 35,60\t€ 15.974,60
\tTotale\t\t€ 67.211,31
15\tVerdi Luca\tConsigliere\t///\t///\t€ 1.200,00\t€ 0,00\t€ 1.200,00
"""

INDICATOR_TEXT = """[Pagina 1]
Comune di Esempio Elaborazione del 14-01-2026 ora 10:18:10
INDICATORE DI TEMPESTIVITA' DEI PAGAMENTI
( Data Mandato: dal 01-01-2025 al 31-12-2025)
Numero Elementi per il calcolo 6.386
Importo Totale dei Pagamenti (A) 44.048.515,35
Coefficiente dei Pagamenti (Importo * GG Ritardo) (B) -491.913.506,78
INDICATORE DI TEMPESTIVITA' DEI PAGAMENTI (B/A) -11,17
"""


def _doc(title, description="", attachments=(), published=""):
    return {"title": title, "description": description, "published": published,
            "attachments": [{"name": n, "url": f"https://example.org/{n}", "md5": ""} for n in attachments]}


def test_latest_doc_by_year_in_title():
    docs = [_doc("Compensi anno 2023"), _doc("Compensi anno 2025", published="2026-06-05"),
            _doc("Compensi anno 2024"), _doc("Curriculum", published="2026-09-01")]
    assert tf.latest_doc(docs)["title"] == "Compensi anno 2025"
    assert tf.latest_doc(docs, r"curric")["title"] == "Curriculum"
    assert tf.latest_doc([], r"x") is None
    assert tf.doc_year(_doc("Piano 2022/2024")) == 2024


def test_pay_lines_from_the_table():
    lines = tf.pay_lines(PAY_TEXT)
    assert lines[0] == ("- Rossi Mario, Sindaco: Indennità di Carica € 51.272,31; Indennità di Fine Mandato € 17.957,32; "
                        "Importi di viaggi di servizio e missioni pagati con fondi pubblici € 1.310,64; "
                        "TOTALE GENERALE € 70.540,27")
    assert lines[1].startswith("- Bianchi Anna, Vice-Sindaco: Indennità di Carica € 15.939,00;")
    assert lines[2] == "- Verdi Luca, Consigliere: Gettoni di Presenza € 1.200,00; Importi di viaggi di servizio e missioni pagati con fondi pubblici € 0,00; TOTALE GENERALE € 1.200,00"
    assert len(lines) == 3            # the «Totale» row and the header are not people
    assert tf.pay_lines("niente tabella") == []


def test_build_pay_fact():
    doc = _doc("Compensi degli Amministratori comunali anno 2025", attachments=["Organi 2025.ods"])
    fact = tf.build_pay(doc, tf.pay_lines(PAY_TEXT), 2025, SECTION, TODAY, SITE, {})
    assert fact["id"] == "amt-pay" and fact["source"] == SECTION and fact["updated"] == "2026-11-07"
    assert "Rossi Mario, Sindaco: Indennità di Carica € 51.272,31" in fact["text"]
    assert "7 novembre 2026" in fact["text"]
    assert "guadagna" in fact["require_any"] and "sindaco" in fact["keywords"]
    assert fact["title"].startswith("Compensi del sindaco")


def test_payment_values_and_fact():
    values = tf.payment_values([("Indicatore - 2025.pdf", INDICATOR_TEXT),
                                ("Indicatore - primo trimestre.pdf", INDICATOR_TEXT.replace("-11,17", "2,5")),
                                ("Altro.pdf", "nessun indicatore")])
    assert values == {"Indicatore - 2025.pdf": -11.17, "Indicatore - primo trimestre.pdf": 2.5}
    fact = tf.build_payments(_doc("2025"), values, 2025, (2024, -8.0), SECTION, TODAY, SITE, {})
    assert fact["id"] == "amt-payments"
    assert "- Indicatore annuale 2025: 11,17 giorni in anticipo rispetto alla scadenza (valore -11,17)." in fact["text"]
    assert "- Primo trimestre 2025: 2,50 giorni di ritardo medio (valore 2,5)." in fact["text"]
    assert "- Anno precedente (2024): 8,00 giorni in anticipo" in fact["text"]


def test_staff_total_and_fact():
    desc = ("Dotazione organica al 31 dicembre 2025. Al 31 dicembre 2025 il totale del personale effettivo in "
            "servizio era 321 (tra dipendenti e dirigenti).")
    assert tf.staff_total(desc) == (321, "31 dicembre 2025")
    assert tf.staff_total("nessun numero") is None
    doc = _doc("Dotazione organica al 31 dicembre 2025", desc, ["Conto annuale.pdf"])
    account = ("Riepilogo triennale\nPersonale a tempo indeterminato al 31.12 (Tab. 1)   Spese per retribuzioni "
               "lorde (Tab. 12+13)\n2023 2024 2025\nDIRIGENTI 6,75 6,92 624.624 702.326 622.866\nTOTALE 9.000.000")
    fact = tf.build_staff(doc, 321, "31 dicembre 2025", SECTION, TODAY, SITE, {"title": "Mio titolo"},
                          tf.spending_extract(account))
    assert fact["id"] == "amt-staff" and fact["title"] == "Mio titolo"
    assert "al 31 dicembre 2025 il totale del personale effettivo in servizio era di 321 persone" in fact["text"]
    assert "Conto annuale.pdf" in fact["text"]
    assert "DIRIGENTI 6,75 6,92 624.624 702.326 622.866" in fact["text"] and "Spesa per il personale" in fact["text"]
    assert tf.spending_extract("niente tabella") == ""
    assert "speso" in fact["require_any"] and "personale" in fact["keywords"] and "costa" not in fact["keywords"]


def test_build_rpct_fact():
    doc = _doc("Dott.ssa Maria Verdi", "Art. 1, c. 8, legge n. 190/2012 Dott.ssa Maria Verdi telefono 0735/000000 "
               "email verdi@example.org", ["Decreto di nomina.pdf"])
    fact = tf.build_rpct(doc, SECTION, TODAY, SITE, {})
    assert fact["id"] == "amt-rpct"
    assert "(RPCT) del Comune di Esempio: Dott.ssa Maria Verdi" in fact["text"]
    assert "telefono 0735/000000" in fact["text"] and "Decreto di nomina.pdf" in fact["text"]
    assert "anticorruzione" in fact["require_any"]


def test_read_subject_pay_reads_the_ods_first():
    page = """<html><body><div class="page-content"><h1>Titolari</h1>
    <div class="italsoft-alert"><h2>Compensi degli Amministratori comunali anno 2024</h2><p>vecchio</p>
    <ul><li><a href="?page_id=36046&amp;event=vediAllegato&amp;id=A">Organi 2024(.ods).ods</a></li></ul></div>
    <div class="italsoft-alert"><h2>Compensi degli Amministratori comunali anno 2025</h2><p>nuovo</p>
    <ul><li><a href="?page_id=36046&amp;event=vediAllegato&amp;id=B">Organi 2025(.xls).xls</a></li>
    <li><a href="?page_id=36046&amp;event=vediAllegato&amp;id=C">Organi 2025(.ods).ods</a></li></ul></div>
    </div></body></html>"""
    asked = []

    def fetch(url):
        asked.append(url)
        return page

    def get_bytes(url):
        asked.append(url)
        return b"ods-bytes"

    import scripts.transparency_facts as mod
    original = mod.extract
    mod.extract = lambda name, data, max_chars, pdf_text: PAY_TEXT if name.endswith(".ods") else ""
    try:
        config = {"portal": "https://example.nuvolapalitalsoft.it/", "page_id": 36046}
        fact = tf.read_subject("pay", {"voceamt": 173}, config, fetch, get_bytes, TODAY, SITE)
    finally:
        mod.extract = original
    assert fact and "Rossi Mario, Sindaco" in fact["text"] and "anno 2025" in fact["text"]
    assert asked[1].endswith("id=C")          # the .ods of 2025, not the .xls nor the 2024 file
