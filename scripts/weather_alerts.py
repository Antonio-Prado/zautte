"""
Weather and civil protection alerts in force today, kept up to date by cron.

"Are there any alerts for today?" needs today's date and the day each alert is about. The news
sync indexes every alert the organisation publishes as a passage, but a search by similarity
finds the alerts that look like the question (years of them, all alike), not the one for today:
the answer was "no alert is in force" on a day with a wind alert published the afternoon
before. This job reads the news sources of scripts/news_sync.py (the WordPress bulletin and the
public preview of the Telegram channel, the "news" key of config/crawl_extra.json), keeps the
items of the last `days_back` days whose title says they are an alert, works out the days each
one is about from its title («per venerdì 13 novembre», «da giovedì 27 a sabato 29 agosto»,
«fino a domenica 19 luglio») and writes them as one known fact in KNOWN_FACTS_DYNAMIC_FILE (same
format as config/known_facts.json): the alerts for today with their text and link, the ones
for the next days, the ones of the past days (so that an old alert is not presented as current),
and how to answer, above all when there is none: the list holds what the organisation has
published, not what the civil protection authority has issued, so "none published for today"
must never become "no alert". The API rereads that file when it changes: no restart.

Configuration: the "alerts" key of config/crawl_extra.json (the sources come from "news")
    {"authority": "Protezione Civile della Regione …",      # who issues the alerts (named in the answer)
     "authority_url": "https://…",     # optional: where the official bulletins are
     "days_back": 10,                  # news of the last N days are read (default)
     "max_text": 1500,                 # characters of the text of a current alert (default)
     "title_pattern": "…",             # optional: regular expression that makes a title an alert
     "keywords": [...], "require_any": [...]}   # optional: when the fact is injected
Run (every 15 minutes from cron, as the sync account, through scripts/weather_alerts.sh):
    venv/bin/python -m scripts.weather_alerts [--dry-run]
Exit codes: 0 written or unchanged, 1 fetch or configuration error (the previous file is kept).
"""

import argparse
import logging
import re
import sys
from datetime import date, timedelta
from itertools import pairwise
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.privdrop import drop_to_sync_user

drop_to_sync_user()   # as root: become the sync account before config.settings reads .env

import httpx
from bs4 import BeautifulSoup

from config.settings import (
    KNOWN_FACTS_DYNAMIC_FILE,
    NEWS_SOURCES,
    SITE_NAME,
    WEATHER_ALERTS,
    now_local,
)
from scripts.council_sessions import MONTHS, WEEKDAYS, upsert_fact
from scripts.news_sync import (
    _HEADERS,
    _attachment_hosts,
    _get,
    _own_page,
    _telegram_preview_url,
    html_to_text,
    parse_telegram_preview,
    telegram_announces_site_page,
)

log = logging.getLogger("weather_alerts")

FACT_ID = "alerts-today"
DAYS_BACK = 10
MAX_TEXT = 1500
MAX_RANGE_DAYS = 31        # «da … a …» longer than this is a misreading, not an alert
# A title that makes a news item an alert. «allertamento» (the alerting system, a drill) is not one.
DEFAULT_TITLE_PATTERN = (r"\b(allerta|allerte|allarme meteo|maltempo|mareggiat[ae]|nubifrag\w+|"
                         r"ondat[ae] di calore|bollettino (?:del )?calore)\b")

_MONTH = "|".join(MONTHS)
_WEEKDAY = "(?:" + "|".join(WEEKDAYS) + r"|lunedi|martedi|mercoledi|giovedi|venerdi)"
_DAY = r"(\d{1,2})\s?°?"
_DATE_RE = re.compile(rf"\b{_DAY}\s+({_MONTH})\b", re.IGNORECASE)
# «18 e sabato 19 settembre», «18, 19 e 20 settembre»: a day number that shares the month of the date after it
_BARE_DAY_RE = re.compile(rf"\b{_DAY}\s*(?:,|\s+ed?)\s+(?:{_WEEKDAY}\s+)?$", re.IGNORECASE)
# «da giovedì 27 a sabato 29 agosto», «dal 30 dicembre al 2 gennaio»
_RANGE_RE = re.compile(rf"\bda(?:l|ll['’])?\s*(?:{_WEEKDAY}\s+)?{_DAY}(?:\s+({_MONTH}))?\s+"
                       rf"(?:fino\s+)?al?\s+(?:{_WEEKDAY}\s+)?{_DAY}\s+({_MONTH})\b", re.IGNORECASE)
# «fino a domenica 19 luglio», «prorogata fino a tutto lunedì 2 novembre»: from the day of publication
_UNTIL_RE = re.compile(rf"\bfino\s+a(?:l|lla|ll['’])?\s*(?:tutt[ao]\s+)?(?:(?:la\s+)?giornata\s+di\s+)?"
                       rf"(?:{_WEEKDAY}\s+)?{_DAY}\s+({_MONTH})\b", re.IGNORECASE)
_TODAY_RE = re.compile(r"\b(?:per|di|da)\s+oggi\b|\boggi\s+" + _WEEKDAY, re.IGNORECASE)
_TOMORROW_RE = re.compile(r"\b(?:per|di|da)\s+domani\b|\bdomani\s+" + _WEEKDAY, re.IGNORECASE)

# Words of a question about alerts and bad weather: one is enough.
DEFAULT_KEYWORDS = sorted({
    "allerta", "allerte", "allertamento", "allarme", "allarmi", "meteo", "meteorologica", "meteorologiche",
    "maltempo", "mareggiata", "mareggiate", "burrasca", "tempesta", "temporale", "temporali", "nubifragio",
    "nubifragi", "vento", "raffiche", "pioggia", "piogge", "piove", "neve", "nevicata", "nevicate", "ghiaccio",
    "grandine", "grandinata", "calore", "idrogeologica", "idrogeologico", "idraulica", "idraulico", "esondazione",
    "alluvione", "previsioni", "alert", "alerts", "weather", "storm", "wind",
})


def _day(d: date, year: bool = True) -> str:
    out = f"{WEEKDAYS[d.weekday()]} {d.day} {MONTHS[d.month - 1]}"
    return f"{out} {d.year}" if year else out


def _dated(day: str, month: str, published: date) -> date | None:
    """The date «day month» nearest to the day of publication: an alert is about the days right
    after it, so «2 gennaio» published on 30 December is in the following year."""
    number = MONTHS.index(month.lower()) + 1
    best = None
    for year in (published.year - 1, published.year, published.year + 1):
        try:
            d = date(year, number, int(day))
        except ValueError:
            continue
        if best is None or abs((d - published).days) < abs((best - published).days):
            best = d
    return best


def alert_days(text: str, published: date) -> list[date]:
    """The days an alert is about, from what its title says: single days («venerdì 13 novembre»,
    «18 e sabato 19 settembre»), a period («da giovedì 27 a sabato 29 agosto»), a period that
    starts when it is published («fino a domenica 19 luglio»), «oggi» and «domani». [] when the
    text names no day."""
    days: set[date] = set()

    def span(first: date | None, last: date | None) -> None:
        if first and last and first <= last and (last - first).days <= MAX_RANGE_DAYS:
            days.update(first + timedelta(days=i) for i in range((last - first).days + 1))

    for m in _RANGE_RE.finditer(text):
        span(_dated(m[1], m[2] or m[4], published), _dated(m[3], m[4], published))
    in_range = [m.span() for m in _RANGE_RE.finditer(text)]
    for m in _UNTIL_RE.finditer(text):
        if not any(a <= m.start() < b for a, b in in_range):
            span(published, _dated(m[1], m[2], published))
    for m in _DATE_RE.finditer(text):
        d = _dated(m[1], m[2], published)
        if d is None:
            continue
        days.add(d)
        head = text[:m.start()]
        while (b := _BARE_DAY_RE.search(head)):
            if (other := _dated(b[1], m[2], published)):
                days.add(other)
            head = head[:b.start()]
    if not days:
        if _TODAY_RE.search(text):
            days.add(published)
        if _TOMORROW_RE.search(text):
            days.add(published + timedelta(days=1))
    return sorted(days)


def _when(days: list[date]) -> str:
    """«venerdì 13 novembre 2026», «da giovedì 27 a sabato 29 agosto 2026», «venerdì 18 e domenica 20 …»"""
    if len(days) == 1:
        return _day(days[0])
    if all((b - a).days == 1 for a, b in pairwise(days)):
        return f"da {_day(days[0], year=False)} a {_day(days[-1])}"
    return ", ".join(_day(d, year=False) for d in days[:-1]) + f" e {_day(days[-1])}"


def to_alert(item: dict, pattern: re.Pattern) -> dict | None:
    """A news item ({title, text, url, date, channel}) as an alert with the days it is about, or
    None when its title does not say it is one. The days come from the title, then from the
    beginning of the text; an alert that names no day counts for the day it was published."""
    if not pattern.search(item["title"]):
        return None
    try:
        published = date.fromisoformat(item["date"][:10])
    except ValueError:
        return None
    days = alert_days(item["title"], published) or alert_days(item["text"][:400], published)
    return {**item, "published": published, "days": days or [published], "undated": not days}


def classify(alerts: list[dict], today: date) -> dict[str, list[dict]]:
    """The alerts by the days they are about: today (also when they go on), the next days, the past days."""
    out: dict[str, list[dict]] = {"today": [], "next": [], "past": []}
    for a in alerts:
        if today in a["days"]:
            out["today"].append(a)
        elif a["days"][-1] > today:
            out["next"].append(a)
        else:
            out["past"].append(a)
    out["today"].sort(key=lambda a: a["published"], reverse=True)
    out["next"].sort(key=lambda a: (a["days"][0], a["published"]))
    out["past"].sort(key=lambda a: (a["days"][-1], a["published"]), reverse=True)
    return out


def _cut(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit + 1]
    return (cut[:cut.rfind(" ")] if " " in cut else cut[:limit]).rstrip(" ,;:") + "…"


def _entry(a: dict, max_text: int) -> str:
    where = "sul canale Telegram" if a.get("channel") == "telegram" else "sul sito"
    valid = ("giorno non indicato nel titolo: vale la data di pubblicazione" if a["undated"]
             else f"riguarda: {_when(a['days'])}")
    line = f"- {a['title']} ({valid}; pubblicata {where} il {a['published']:%d/%m/%Y}). Link: {a['url']}"
    if max_text and a.get("text"):
        line += f"\n  Testo dell'avviso: {_cut(a['text'], max_text)}"
    return line


def build_fact(alerts: dict[str, list[dict]], today: date, site_name: str, days_back: int = DAYS_BACK,
               authority: str = "", authority_url: str = "", max_text: int = MAX_TEXT,
               sources: list[str] | None = None, keywords: list[str] | None = None,
               require_any: list[str] | None = None, news_url: str = "") -> dict:
    """The known fact: the alerts for today, for the next days, of the past days, and how to answer."""
    authority = authority or "protezione civile regionale"
    official = authority + (f" ({authority_url})" if authority_url else "")
    lines = [(f"Allerte meteo e di protezione civile pubblicate dal {site_name}: elenco letto automaticamente "
              f"{_day(today)} dalle notizie e dagli avvisi dell'ente" + (f" ({', '.join(sources)})" if sources else "")
              + f". Le allerte non sono emesse dall'ente ma dall'autorità competente ({authority}): l'ente le riporta, "
              "e può pubblicarle con ritardo o non pubblicarle affatto."), ""]
    lines.append(f"ALLERTE CHE RIGUARDANO OGGI, {_day(today).upper()}:")
    if alerts["today"]:
        lines += [_entry(a, max_text) for a in alerts["today"]]
    else:
        lines.append(f"- Tra gli avvisi pubblicati dall'ente negli ultimi {days_back} giorni NON ce n'è nessuno che "
                     "riguardi oggi. Questo NON significa che oggi non ci siano allerte: significa soltanto che l'ente "
                     "non ne ha pubblicata una.")
    lines.append("")
    if alerts["next"]:
        lines.append("ALLERTE PER I PROSSIMI GIORNI:")
        lines += [_entry(a, max_text) for a in alerts["next"]]
        lines.append("")
    if alerts["past"]:
        lines.append("ALLERTE DEI GIORNI SCORSI (NON riguardano oggi, non presentarle come attuali):")
        lines += [_entry(a, 0) for a in alerts["past"]]
        lines.append("")
    lines.append(
        "Come rispondere: a chi chiede se ci sono allerte, se c'è maltempo o che tempo è previsto, riporta le allerte "
        "che riguardano oggi (e, se servono, quelle per i prossimi giorni) con il colore, i fenomeni previsti, il "
        "giorno e il link. Se per oggi non ce n'è nessuna in questo elenco, di' che tra gli avvisi pubblicati dall'ente "
        "non risulta un'allerta per oggi e aggiungi SEMPRE che questo non esclude che ne sia stata emessa una: non "
        "scrivere mai che «non ci sono allerte», che «non risultano allerte attive» o che la situazione è tranquilla. "
        f"In ogni caso indica che le allerte ufficiali e aggiornate sono quelle dell'autorità competente, {official}, "
        "e che per un'emergenza si chiama il 112. Questo assistente non fa previsioni del tempo e non valuta se sia sicuro fare qualcosa "
        "(uscire in mare, mettersi in viaggio, tenere un evento all'aperto): chi deve decidere consulta i bollettini "
        "ufficiali e le autorità competenti.")
    return {
        "id": FACT_ID,
        "keywords": keywords or DEFAULT_KEYWORDS,
        "require_any": require_any or keywords or DEFAULT_KEYWORDS,
        "title": f"Allerte meteo e di protezione civile che riguardano oggi (aggiornato automaticamente) - {site_name}",
        "source": (alerts["today"] or alerts["next"] or [{"url": news_url or authority_url}])[0]["url"],
        "category": "notizia",
        "updated": today.isoformat(),
        "text": "\n".join(lines).rstrip() + "\n",
    }


def read_posts(client: httpx.Client, api: str, since: date) -> list[dict]:
    """The articles of the WordPress bulletin published from `since` on (newest first, at most
    100: alerts are a few a week). Raises RuntimeError when the API does not answer."""
    resp = _get(client, f"{api.rstrip('/')}/posts", params={
        "per_page": 100, "orderby": "date", "order": "desc", "status": "publish",
        "after": f"{since.isoformat()}T00:00:00", "_fields": "date,link,title,content"})
    if resp is None:
        raise RuntimeError(f"{api}: no answer")
    try:
        batch = resp.json()
    except ValueError as e:
        raise RuntimeError(f"{api}: not JSON") from e
    if not isinstance(batch, list):
        raise RuntimeError(f"{api}: unexpected answer")  # noqa: TRY004 - a bad answer of the source, handled as a failed read
    items = []
    for p in batch:
        try:
            if not _own_page(str(p["link"])):
                continue
            items.append({"url": p["link"], "date": p["date"][:10], "channel": "site",
                          "title": BeautifulSoup(p["title"]["rendered"], "lxml").get_text(" ", strip=True),
                          "text": html_to_text(p["content"]["rendered"])})
        except (KeyError, TypeError):
            continue
    return items


def telegram_items(html: str, since: date) -> list[dict]:
    """The messages of the channel preview that are notices of their own (an alert extended to the
    next day is often posted only there): those that link to a page of the site are announcements
    of an article already read from the bulletin."""
    _, posts = parse_telegram_preview(html)
    hosts = _attachment_hosts()
    items = []
    for post in posts:
        if not post["date"] or post["date"] < since.isoformat() or telegram_announces_site_page(post, hosts):
            continue
        first = re.sub(r"^[^\w«\"']+", "", post["text"].splitlines()[0]).strip()
        # the title is what says the message is an alert: its first line, or its beginning when that is a label
        title = first if len(first) >= 25 else " ".join(post["text"].split())[:200]
        items.append({"url": post["url"], "date": post["date"], "channel": "telegram",
                      "title": title, "text": post["text"]})
    return items


def main() -> int:
    ap = argparse.ArgumentParser(description="Weather and civil protection alerts for today → dynamic known fact")
    ap.add_argument("--dry-run", action="store_true", help="print the fact, write nothing")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    api = str(NEWS_SOURCES.get("wordpress_api", "")).strip()
    preview = _telegram_preview_url()
    if not WEATHER_ALERTS or not (api or preview):
        log.error("no alerts key (or no news.wordpress_api / news.telegram) in config/crawl_extra.json: nothing to do")
        return 1
    try:
        pattern = re.compile(str(WEATHER_ALERTS.get("title_pattern") or DEFAULT_TITLE_PATTERN), re.IGNORECASE)
    except re.error as e:
        log.error("alerts.title_pattern is not a regular expression: %s", e)
        return 1
    days_back = int(WEATHER_ALERTS.get("days_back") or DAYS_BACK)
    today = now_local().date()
    since = today - timedelta(days=days_back)
    items: list[dict] = []
    sources: list[str] = []
    try:
        with httpx.Client(headers=_HEADERS, follow_redirects=False, timeout=60.0, trust_env=False) as client:
            if api:
                items += read_posts(client, api, since)
                sources.append("bollettino")
            if preview:
                resp = _get(client, preview)
                if resp is not None:
                    items += telegram_items(resp.text, since)
                    sources.append("canale Telegram")
                elif not api:
                    raise RuntimeError(f"{preview}: no answer")
                else:
                    log.warning("%s: not read, alerts from the bulletin only", preview)
    except Exception as exc:  # noqa: BLE001 - any network/policy/format error: keep the previous file
        log.error("could not read the news sources: %s", exc)
        return 1
    alerts = classify([a for a in (to_alert(i, pattern) for i in items) if a], today)
    fact = build_fact(alerts, today, SITE_NAME, days_back, str(WEATHER_ALERTS.get("authority") or ""),
                      str(WEATHER_ALERTS.get("authority_url") or ""), int(WEATHER_ALERTS.get("max_text") or MAX_TEXT),
                      sources, WEATHER_ALERTS.get("keywords"), WEATHER_ALERTS.get("require_any"),
                      news_url=api.split("/wp-json")[0] + "/" if api else preview)
    if args.dry_run:
        print(fact["text"])
    written = upsert_fact(KNOWN_FACTS_DYNAMIC_FILE, fact, dry_run=args.dry_run)
    log.info("%d news items read: %d alerts for today, %d for the next days, %d past (%d characters); %s",
             len(items), len(alerts["today"]), len(alerts["next"]), len(alerts["past"]), len(fact["text"]),
             "written" if written and not args.dry_run else "unchanged" if not written else "dry run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
