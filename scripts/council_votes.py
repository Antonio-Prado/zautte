"""
How the council voted in its latest session, from the resolutions already in the index.

"How did the last votes in the council go?" has its answer in the text of the resolutions: each
one says who was present and how many voted for, against or abstained, often with the names. Once
the resolutions are indexed in full those lines are in the vector store, but a question about
"the last votes" names no resolution and the search does not bring them up: the assistant answered
that it had no access to the results. This job reads the metadata of the index (no page is
downloaded), keeps the passages of the configured sources where a vote is recorded, takes the
resolutions of the latest session that has any, and writes them as one known fact in
KNOWN_FACTS_DYNAMIC_FILE (same format as config/known_facts.json): for each resolution its number,
date, subject, page and the lines of the vote as they are written. The API rereads that file when
it changes: no restart.

Configuration: the "council_votes" key of config/crawl_extra.json (without it the job does nothing)
    {"source_contains": ["voceamt=49"],        # the passages whose source has one of these are read
     "page_url": "https://…/?voceamt=49",      # where the resolutions are listed: the source of the fact
     "vote_pattern": "Consiglieri\\s*presenti",  # regex of the line that opens the record of a vote (default)
     "result_pattern": "vot\\w*\\s*favorevol|…",  # regex a record must hold to be a vote (default below): the
                                               # same opening line also notes who came in during a debate
     "end_pattern": "\\bDELIBERA\\b",          # regex of what follows it (default); the excerpt stops there
     "sessions": 1,                            # how many of the latest sessions with votes (default 1)
     "keywords": [...], "require_any": [...]}  # optional: when the fact is injected
The session of a resolution is the date its row gives in the list («Numero: 129; Data: 06/11/2026»,
written by scripts/amt_sync.py in the first passage of every document).

Run (daily from cron, as the sync account, through scripts/council_votes.sh, after the nightly
sync of the transparency portal):
    venv/bin/python -m scripts.council_votes [--dry-run]
Exit codes: 0 written, unchanged or not configured, 1 the index cannot be read, 2 no vote found
(the previous fact is kept).
"""

import argparse
import json
import logging
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.privdrop import drop_to_sync_user

drop_to_sync_user()   # as root: become the sync account before config.settings reads .env

from config.settings import COUNCIL_VOTES, KNOWN_FACTS_DYNAMIC_FILE, SITE_NAME, now_local
from indexer.vector_store import METADATA_FILE
from scripts.council_sessions import upsert_fact

log = logging.getLogger("council_votes")

FACT_ID = "council-votes"
VOTE_PATTERN = r"Consiglieri\s*presenti"
# «Voti favorevoli n. 16», «Voti contrari n. 3», «Astenuti n. 5», «unanimi», «ha riportato voti n. 15»; not
# «i pareri FAVOREVOLI di regolarità tecnica». The spaces are optional: some PDFs are read with the words joined.
RESULT_PATTERN = r"vot\w*\s*favorevol|contrar\w*\s*n|astenut\w*\s*n|unanim|riportato\s*voti"
END_PATTERN = r"\bDELIBERA\b"
LEAD_CHARS = 400          # text kept before the vote: what is being voted («pone in votazione l'emendamento…»)
MAX_EXCERPT = 1800        # characters of one vote
MAX_VOTES_PER_ACT = 4     # the item, its amendments, the immediate enforceability
MAX_TEXT = 60000
DEFAULT_KEYWORDS = [
    "votazione", "votazioni", "voti", "voto", "votato", "votata", "votate", "votati", "votò", "favorevoli",
    "favorevole", "contrari", "contrario", "astenuti", "astenuto", "astensione", "astensioni", "unanimità",
    "maggioranza", "minoranza", "opposizione",
]
DEFAULT_REQUIRE_ANY = [
    "consiglio", "consiliare", "consiliari", "consigliere", "consiglieri", "seduta", "sedute", "delibera",
    "delibere", "deliberazione", "deliberazioni", "maggioranza", "minoranza", "opposizione",
]
_ROW_RE = re.compile(r"\bNumero:\s*([\w/.-]+);\s*Data:\s*(\d{2})/(\d{2})/(\d{4})")
_PAGE_MARK_RE = re.compile(r"\n?\[Pagina \d+\]\n?")


def _body(chunk: dict) -> str:
    """The text of a passage without the title every passage starts with."""
    text = str(chunk.get("text", ""))
    return text.split("\n\n", 1)[1] if "\n\n" in text else text


def _joined(text: str, following: str) -> str:
    """`following` without the lines it repeats from the end of `text` (consecutive passages overlap)."""
    for size in range(min(len(text), len(following), 600), 20, -1):
        if text.endswith(following[:size]):
            return following[size:]
    return following


def vote_excerpts(text: str, vote_re: re.Pattern, end_re: re.Pattern, following: str = "",
                  result_re: re.Pattern | None = None) -> list[str]:
    """The records of the votes that open in `text`: from a few lines before the opening line (what
    is put to the vote) to what follows the record, cut at whole lines. A record that the passage
    breaks off goes on in `following`, the text of the next passage. With `result_re`, only the
    records that hold a result after the opening line: «Consiglieri presenti n. 23» alone is the
    note of who came into the room during a debate, not a vote."""
    whole = text + ("\n" + _joined(text, following) if following else "")
    out, pos = [], 0
    while (m := vote_re.search(whole, pos)) and m.start() < len(text):
        start = max(pos, whole.rfind("\n", 0, max(0, m.start() - LEAD_CHARS)) + 1)
        end_m = end_re.search(whole, m.end())
        limit = m.start() + MAX_EXCERPT
        if end_m and end_m.start() <= limit:
            end = end_m.start()
        elif len(whole) <= limit:
            end = len(whole)
        else:
            end = max(whole.rfind("\n", m.end(), limit), m.end())
        excerpt = _PAGE_MARK_RE.sub("\n", whole[start:end]).strip()
        if excerpt and (result_re is None or result_re.search(whole, m.end(), end)):
            out.append(excerpt)
        pos = max(end, m.end())
    return out


def read_acts(chunks, source_contains: list[str], vote_re: re.Pattern, end_re: re.Pattern,
              result_re: re.Pattern | None = None) -> list[dict]:
    """The documents with at least a recorded vote: number, date, title, page, votes in page order."""
    passages: dict[str, dict[int, dict]] = {}
    for c in chunks:
        if not isinstance(c, dict):
            continue
        source = str(c.get("source", ""))
        if any(s in source for s in source_contains):
            passages.setdefault(source, {})[int(c.get("chunk_index") or 0)] = c
    out = []
    for source, by_index in passages.items():
        first = by_index.get(0)
        row = _ROW_RE.search(str(first.get("text", ""))) if first else None
        if not row:
            continue   # not a row of the list: no number, no session
        try:
            day = date(int(row[4]), int(row[3]), int(row[2]))
        except ValueError:
            continue   # «Data: 31/02/2026»
        votes, seen = [], set()
        for index in sorted(by_index):
            following = _body(by_index[index + 1]) if index + 1 in by_index else ""
            for excerpt in vote_excerpts(_body(by_index[index]), vote_re, end_re, following, result_re):
                opening = vote_re.search(excerpt)
                key = " ".join(excerpt[opening.start():opening.start() + 200].split()) if opening else excerpt[:200]
                if key not in seen:      # the same vote in the lines two passages share
                    seen.add(key)
                    votes.append(excerpt)
        if votes:
            out.append({"source": source, "title": " ".join(str(first.get("title", "")).split()),
                        "number": row[1], "date": day, "votes": votes[:MAX_VOTES_PER_ACT]})
    return out


def latest_sessions(acts: list[dict], sessions: int = 1) -> list[tuple[date, list[dict]]]:
    """The acts of the latest `sessions` dates, each date with its acts by number."""
    by_date: dict[date, list[dict]] = {}
    for act in acts:
        by_date.setdefault(act["date"], []).append(act)

    def number(act: dict):
        digits = re.match(r"\d+", act["number"])
        return (int(digits[0]) if digits else 0, act["number"])

    return [(day, sorted(by_date[day], key=number)) for day in sorted(by_date, reverse=True)[:max(1, sessions)]]


def build_fact(sessions: list[tuple[date, list[dict]]], today: date, page_url: str, site_name: str,
               keywords: list[str] | None = None, require_any: list[str] | None = None) -> dict:
    """The known fact: every resolution of the session with the record of its votes."""
    days = ", ".join(f"{day:%d/%m/%Y}" for day, _ in sessions)
    lines = [
        (f"Esito delle votazioni del Consiglio comunale nelle deliberazioni dell'ultima seduta di cui sono "
         f"pubblicati i testi: seduta del {days}. Elenco costruito automaticamente il {today:%d/%m/%Y} dai testi "
         "delle deliberazioni pubblicate; per ogni deliberazione sono riportate le righe del voto così come "
         "sono scritte nell'atto."),
        ("Come usarlo: per «le ultime votazioni», «come si è votato», «chi ha votato contro» riporta i numeri e "
         "i nomi come sono scritti qui, deliberazione per deliberazione, indicando numero, data e pagina. "
         "Una deliberazione può avere più votazioni (emendamenti, il punto, l'immediata eseguibilità): "
         "distinguile. Non sommare, non fare medie e non dedurre schieramenti che il testo non dice. Se il "
         "calendario delle sedute indica una seduta più recente di questa, le sue deliberazioni non sono "
         "ancora state pubblicate: dillo. Sulla solidità della maggioranza e su ogni altra valutazione "
         "politica non esprimerti: riporta i numeri."),
        "",
    ]
    for day, acts in sessions:
        lines.append(f"SEDUTA DEL {day:%d/%m/%Y}: {len(acts)} deliberazioni con il voto registrato")
        lines.append("")
        for act in acts:
            lines.append(f"DELIBERAZIONE n. {act['number']} del {day:%d/%m/%Y}: {act['title']}")
            lines.append(f"Pagina: {act['source']}")
            for n, excerpt in enumerate(act["votes"], 1):
                lines.append(f"Votazione {n}:" if len(act["votes"]) > 1 else "Votazione:")
                lines.append(excerpt)
            lines.append("")
    text = "\n".join(lines).rstrip()
    if len(text) > MAX_TEXT:
        text = text[:text.rfind("\n", 0, MAX_TEXT)] + "\n[…] (le altre deliberazioni della seduta sono nelle loro pagine)"
    return {
        "id": FACT_ID,
        "keywords": keywords or DEFAULT_KEYWORDS,
        "require_any": require_any or DEFAULT_REQUIRE_ANY,
        "title": ("Esito delle votazioni del Consiglio comunale nell'ultima seduta con le deliberazioni pubblicate "
                  f"(aggiornato automaticamente) - {site_name}"),
        "source": page_url,
        "category": "amministrazione",
        "updated": today.isoformat(),
        "text": text + "\n",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Votes of the council's latest session (index) → dynamic known fact")
    ap.add_argument("--dry-run", action="store_true", help="print the fact, write nothing")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    contains = COUNCIL_VOTES.get("source_contains") or []
    contains = [s for s in ([contains] if isinstance(contains, str) else contains) if s]
    if not contains:
        log.info("no council_votes.source_contains in config/crawl_extra.json: nothing to do")
        return 0
    try:
        vote_re = re.compile(COUNCIL_VOTES.get("vote_pattern") or VOTE_PATTERN, re.IGNORECASE)
        end_re = re.compile(COUNCIL_VOTES.get("end_pattern") or END_PATTERN)
        result_re = re.compile(COUNCIL_VOTES.get("result_pattern") or RESULT_PATTERN, re.IGNORECASE)
        chunks = json.loads(METADATA_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError, re.error) as exc:
        log.error("the index or the patterns cannot be read: %s", exc)
        return 1
    acts = read_acts(chunks, contains, vote_re, end_re, result_re)
    del chunks
    if not acts:
        log.error("no recorded vote in the passages of %s: the previous fact is kept", ", ".join(contains))
        return 2
    sessions = latest_sessions(acts, int(COUNCIL_VOTES.get("sessions") or 1))
    fact = build_fact(sessions, now_local().date(), str(COUNCIL_VOTES.get("page_url") or ""), SITE_NAME,
                      COUNCIL_VOTES.get("keywords"), COUNCIL_VOTES.get("require_any"))
    if args.dry_run:
        print(fact["text"])
    written = upsert_fact(KNOWN_FACTS_DYNAMIC_FILE, fact, dry_run=args.dry_run)
    log.info("%d acts with a vote, latest session %s with %d (%d characters); %s", len(acts),
             sessions[0][0].isoformat(), len(sessions[0][1]), len(fact["text"]),
             "written" if written and not args.dry_run else "unchanged" if not written else "dry run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
