"""The votes of the council's latest session, taken from the resolutions already in the index."""
import json
import re
from datetime import date

from scripts import council_votes as cv

VOTE_RE = re.compile(cv.VOTE_PATTERN, re.IGNORECASE)
END_RE = re.compile(cv.END_PATTERN)
RESULT_RE = re.compile(cv.RESULT_PATTERN, re.IGNORECASE)
PAGE = "https://portal.example.org/?page_id=1&voceamt=49&dettaglio="

VOTE = """UDITA la dichiarazione di voto del Consigliere Rossi;
Il Presidente del Consiglio pone in votazione il punto iscritto all'ordine del giorno.
Con votazione resa mediante strumentazione informatica;
Consiglieri presenti n. 24;
Consiglieri assenti n. 1 (Anna Bianchi);
– Voti favorevoli n.16 (Mario Rossi, Lucia Verdi);
– Voti contrari n. 3 (Paolo Neri, Carla Gialli, Ugo Blu);
– Astenuti n. 5 (Sara Viola);
DELIBERA
[Pagina 8]
1. di approvare la premessa;"""


def _first(n, number, day, title="BUDGET CHANGES"):
    return {"source": f"{PAGE}{n}", "title": title, "chunk_index": 0,
            "text": f"{title}\n\nComune, sezione Provvedimenti: {title}.\nNumero: {number}; Data: {day}; "
                    "Servizio: AREA RISORSE; Tipo Documento: Delibere."}


def _passage(n, index, body, title="BUDGET CHANGES"):
    return {"source": f"{PAGE}{n}", "title": title, "chunk_index": index, "text": f"{title}\n\n{body}"}


def test_the_record_of_a_vote_is_kept_as_written_with_what_was_put_to_the_vote():
    (excerpt,) = cv.vote_excerpts(VOTE, VOTE_RE, END_RE)
    assert excerpt.startswith("UDITA la dichiarazione di voto")
    assert "pone in votazione il punto" in excerpt and "Consiglieri presenti n. 24;" in excerpt
    assert "– Voti contrari n. 3 (Paolo Neri, Carla Gialli, Ugo Blu);" in excerpt
    assert excerpt.endswith("– Astenuti n. 5 (Sara Viola);")           # stops before «DELIBERA»


def test_words_run_together_by_the_pdf_reader_are_still_recognised():
    text = "Convotazioneresamediantestrumentazioneinformatica;\nConsiglieripresentin.22;\nVoti favorevoli n. 11\nDELIBERA"
    (excerpt,) = cv.vote_excerpts(text, VOTE_RE, END_RE)
    assert "Consiglieripresentin.22" in excerpt and "Voti favorevoli n. 11" in excerpt


def test_a_vote_broken_off_by_the_end_of_a_passage_goes_on_in_the_next_one():
    head, tail = VOTE[:VOTE.index("– Voti contrari")], VOTE[VOTE.index("– Voti favorevoli"):]   # they overlap
    (excerpt,) = cv.vote_excerpts(head, VOTE_RE, END_RE, tail)
    assert excerpt.count("Voti favorevoli n.16") == 1 and excerpt.endswith("– Astenuti n. 5 (Sara Viola);")
    assert cv.vote_excerpts(tail, VOTE_RE, END_RE) == []                 # no vote opens in the second passage


def test_several_votes_of_one_resolution_and_a_long_record_cut_at_a_line():
    two = VOTE.replace("il punto iscritto", "l'emendamento n. 1") + "\n" + VOTE
    assert len(cv.vote_excerpts(two, VOTE_RE, END_RE)) == 2
    endless = "Consiglieri presenti n. 24;\n" + "\n".join(f"riga {i} " + "x" * 60 for i in range(200))
    (excerpt,) = cv.vote_excerpts(endless, VOTE_RE, END_RE)
    assert len(excerpt) <= cv.MAX_EXCERPT and excerpt.splitlines()[-1].startswith("riga ")
    assert excerpt.splitlines()[-1].endswith("x" * 60)


def test_who_came_into_the_room_during_a_debate_is_not_a_vote():
    debate = ("Entra in aula il Consigliere comunale Ugo Blu;\nConsiglieri presenti n. 23\nIndi;\n"
              "IL CONSIGLIO COMUNALE\nUDITO il contenuto dell'interrogazione;\nDA' ATTO\ndell'esito della discussione.")
    assert cv.vote_excerpts(debate, VOTE_RE, END_RE, "", RESULT_RE) == []
    assert len(cv.vote_excerpts(debate + "\n" + VOTE, VOTE_RE, END_RE, "", RESULT_RE)) == 1
    assert len(cv.vote_excerpts(VOTE, VOTE_RE, END_RE, "", RESULT_RE)) == 1
    opinions = "Consiglieri presenti n. 24;\nVISTI i pareri FAVOREVOLI di regolarità tecnica e contabile;\nRITENUTO"
    assert cv.vote_excerpts(opinions, VOTE_RE, END_RE, "", RESULT_RE) == []
    for result in ("Votifavorevoliunanimi (22/22);", "Consiglieriastenutin.8(Ugo Blu)", "Voti contrari n. 11",
                   "ha riportato voti n. 15 la Consigliera Verdi"):
        assert len(cv.vote_excerpts(f"Consiglieripresentin.22\n{result}", VOTE_RE, END_RE, "", RESULT_RE)) == 1


def _chunks():
    return [
        _first(1, "129", "06/11/2026"), _passage(1, 1, "premessa"), _passage(1, 2, VOTE),
        _first(2, "130", "06/11/2026", "UNIVERSITY CONSORTIUM"), _passage(2, 1, VOTE, "UNIVERSITY CONSORTIUM"),
        _first(3, "95", "26/09/2026", "OLD SESSION"), _passage(3, 1, VOTE, "OLD SESSION"),
        _first(4, "150", "16/10/2026", "A RESOLUTION OF THE EXECUTIVE"),            # no council vote in it
        _passage(4, 1, "LA GIUNTA COMUNALE con votazione unanime DELIBERA", "A RESOLUTION OF THE EXECUTIVE"),
        {"source": "https://portal.example.org/?voceamt=207&dettaglio=9", "chunk_index": 0, "title": "ORDER",
         "text": "ORDER\n\nNumero: 1; Data: 27/11/2026; " + VOTE},                    # another section
        _passage(5, 1, VOTE, "NO FIRST PASSAGE"),                                     # no row: no session
        "not a passage",
    ]


def test_only_the_configured_sources_with_a_vote_and_a_row_are_acts():
    acts = cv.read_acts(_chunks(), ["voceamt=49"], VOTE_RE, END_RE)
    assert sorted((a["number"], a["date"], len(a["votes"])) for a in acts) == [
        ("129", date(2026, 11, 6), 1), ("130", date(2026, 11, 6), 1), ("95", date(2026, 9, 26), 1)]
    doubled = [*_chunks(), _passage(1, 3, VOTE)]                           # the same vote in two passages
    (act,) = [a for a in cv.read_acts(doubled, ["voceamt=49"], VOTE_RE, END_RE) if a["number"] == "129"]
    assert len(act["votes"]) == 1


def test_the_fact_holds_the_latest_session_with_its_resolutions_by_number():
    acts = cv.read_acts(_chunks(), ["voceamt=49"], VOTE_RE, END_RE)
    sessions = cv.latest_sessions(acts)
    assert [(day, [a["number"] for a in group]) for day, group in sessions] == [(date(2026, 11, 6), ["129", "130"])]
    assert [day for day, _ in cv.latest_sessions(acts, 2)] == [date(2026, 11, 6), date(2026, 9, 26)]
    fact = cv.build_fact(sessions, date(2026, 11, 28), "https://portal.example.org/?voceamt=49", "Comune di Esempio")
    text = fact["text"]
    assert fact["id"] == "council-votes" and fact["source"] == "https://portal.example.org/?voceamt=49"
    assert "seduta del 06/11/2026" in text and "SEDUTA DEL 06/11/2026: 2 deliberazioni" in text
    assert f"DELIBERAZIONE n. 129 del 06/11/2026: BUDGET CHANGES\nPagina: {PAGE}1\nVotazione:\n" in text
    assert text.index("n. 129") < text.index("n. 130") and "OLD SESSION" not in text
    assert "– Voti contrari n. 3 (Paolo Neri, Carla Gialli, Ugo Blu);" in text
    assert "votazioni" in fact["keywords"] and "consiglio" in fact["require_any"]


def test_the_api_adds_the_fact_to_a_question_on_the_votes_of_the_council(tmp_path, monkeypatch):
    from api import rag
    from scripts.council_sessions import upsert_fact
    acts = cv.read_acts(_chunks(), ["voceamt=49"], VOTE_RE, END_RE)
    fact = cv.build_fact(cv.latest_sessions(acts), date(2026, 11, 28), "https://portal.example.org/v", "X")
    path = tmp_path / "facts" / "known_facts_dynamic.json"
    assert upsert_fact(path, fact) and json.loads(path.read_text(encoding="utf-8"))[0]["id"] == "council-votes"
    monkeypatch.setattr(rag, "KNOWN_FACTS_DYNAMIC_FILE", path)
    monkeypatch.setitem(rag._DYNAMIC_FACTS, "checked", float("-inf"))
    monkeypatch.setitem(rag._DYNAMIC_FACTS, "mtime", None)
    monkeypatch.setitem(rag._DYNAMIC_FACTS, "facts", [])

    def added(question):
        return any(c["title"].startswith("Esito delle votazioni") for c in rag._inject_known_facts(question))

    assert added("Come sono andate le ultime votazioni in consiglio comunale? Dai dati la maggioranza è solida?")
    assert added("chi ha votato contro la delibera sul bilancio?")
    assert not added("quando è il prossimo consiglio comunale?") and not added("come si vota alle elezioni?")
