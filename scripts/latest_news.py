"""
The latest news, kept up to date by cron.

"What is the latest news?" has no answer in a search by similarity: no article says «latest»,
and the answer was a list of the sections of the site with "I have no real-time access to the
articles". This job reads the WordPress bulletin of scripts/news_sync.py (the "news" key of
config/crawl_extra.json) and writes the most recent articles as one known fact in
KNOWN_FACTS_DYNAMIC_FILE (same format as config/known_facts.json): date, title, the beginning
of the text and the link of each, newest first, and how to answer. The API rereads that file
when it changes: no restart.

Configuration: the "latest_news" key of config/crawl_extra.json (the source comes from "news")
    {"count": 10,            # articles listed (default)
     "days_back": 30,        # articles of the last N days are read (default)
     "max_text": 300,        # characters of the beginning of each article (default)
     "keywords": [...], "require_any": [...]}   # optional: when the fact is injected
Run (every 15 minutes from cron, as the sync account, through scripts/latest_news.sh):
    venv/bin/python -m scripts.latest_news [--dry-run]
Exit codes: 0 written or unchanged, 1 fetch or configuration error (the previous file is kept).
"""

import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.privdrop import drop_to_sync_user

drop_to_sync_user()   # as root: become the sync account before config.settings reads .env

import httpx

from config.settings import (
    KNOWN_FACTS_DYNAMIC_FILE,
    LATEST_NEWS,
    NEWS_SOURCES,
    SITE_NAME,
    now_local,
)
from scripts.council_sessions import MONTHS, WEEKDAYS, upsert_fact
from scripts.news_sync import _HEADERS
from scripts.weather_alerts import _cut, read_posts

log = logging.getLogger("latest_news")

FACT_ID = "news-latest"
COUNT = 10
DAYS_BACK = 30
MAX_TEXT = 300

# «le ultime notizie», «novità di oggi», «cosa è successo questa settimana»: a word about news and a
# word about now. «notizie sulla TARI» has no word about now: the search finds the articles on the subject.
DEFAULT_KEYWORDS = sorted({"notizie", "notizia", "news", "novità", "novita", "comunicati", "comunicato", "bollettino",
                           "aggiornamenti", "nuovo", "successo", "succede", "accaduto", "accade"})
DEFAULT_REQUIRE_ANY = sorted({"ultime", "ultima", "ultimi", "ultimo", "recenti", "recente", "oggi", "ieri", "settimana",
                              "giorni", "nuove", "nuovi", "nuovo", "novità", "novita", "successo", "succede",
                              "accaduto", "accade", "latest", "recent", "today"})


def _day(d: date) -> str:
    return f"{WEEKDAYS[d.weekday()]} {d.day} {MONTHS[d.month - 1]} {d.year}"


def latest(items: list[dict], count: int = COUNT) -> list[dict]:
    """The `count` most recent articles, newest first; an article without a readable date is left out."""
    dated = []
    for item in items:
        try:
            dated.append({**item, "published": date.fromisoformat(item["date"][:10])})
        except ValueError:
            continue
    dated.sort(key=lambda i: i["published"], reverse=True)   # stable: the source order within a day is kept
    return dated[:count]


def build_fact(news: list[dict], today: date, site_name: str, news_url: str, max_text: int = MAX_TEXT,
               keywords: list[str] | None = None, require_any: list[str] | None = None) -> dict:
    """The known fact: the latest articles, newest first, and how to answer."""
    lines = [(f"Ultime notizie pubblicate dal {site_name}: elenco letto automaticamente {_day(today)} dal bollettino "
              f"dell'ente ({news_url}). Sono le {len(news)} più recenti, dalla più nuova."), ""]
    for n in news:
        line = f"- {_day(n['published'])}: {n['title']}."
        if max_text and n.get("text"):
            line += f" {_cut(n['text'], max_text)}"
        lines.append(f"{line} Link: {n['url']}")
    if not news:
        lines.append("- Nessuna notizia pubblicata nel periodo letto.")
    lines += ["", ("Come rispondere: a chi chiede le ultime notizie, le novità o che cosa è successo di recente, elenca le più "
                   "recenti di questo elenco con la data, il titolo e il link (bastano le prime cinque o sei, una riga "
                   "ciascuna) e indica dove si leggono tutte. Non dire che non hai accesso alle notizie o agli articoli: "
                   "sono questi. Non presentare come di oggi una notizia di un altro giorno: scrivi la sua data. Per una "
                   "notizia su un argomento preciso usa i contenuti trovati su quell'argomento, non questo elenco."),
              f"Tutte le notizie: {news_url}"]
    return {
        "id": FACT_ID,
        "keywords": keywords or DEFAULT_KEYWORDS,
        "require_any": require_any or DEFAULT_REQUIRE_ANY,
        "title": f"Ultime notizie (aggiornato automaticamente) - {site_name}",
        "source": news_url,
        "category": "notizia",
        "updated": today.isoformat(),
        "text": "\n".join(lines).rstrip() + "\n",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="The latest news of the bulletin → dynamic known fact")
    ap.add_argument("--dry-run", action="store_true", help="print the fact, write nothing")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    api = str(NEWS_SOURCES.get("wordpress_api", "")).strip()
    if not LATEST_NEWS or not api:
        log.error("no latest_news key (or no news.wordpress_api) in config/crawl_extra.json: nothing to do")
        return 1
    today = now_local().date()
    since = today - timedelta(days=int(LATEST_NEWS.get("days_back") or DAYS_BACK))
    try:
        with httpx.Client(headers=_HEADERS, follow_redirects=False, timeout=60.0, trust_env=False) as client:
            items = read_posts(client, api, since)
    except Exception as exc:  # noqa: BLE001 - any network/policy/format error: keep the previous file
        log.error("could not read %s: %s", api, exc)
        return 1
    news = latest(items, int(LATEST_NEWS.get("count") or COUNT))
    fact = build_fact(news, today, SITE_NAME, api.split("/wp-json")[0] + "/", int(LATEST_NEWS.get("max_text") or MAX_TEXT),
                      LATEST_NEWS.get("keywords"), LATEST_NEWS.get("require_any"))
    if args.dry_run:
        print(fact["text"])
    written = upsert_fact(KNOWN_FACTS_DYNAMIC_FILE, fact, dry_run=args.dry_run)
    log.info("%d articles read, %d listed (%d characters); %s", len(items), len(news), len(fact["text"]),
             "written" if written and not args.dry_run else "unchanged" if not written else "dry run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
