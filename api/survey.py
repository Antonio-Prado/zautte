"""
Satisfaction survey for the logged-in users (e.g. the volunteers of a pilot).

The questions live in config/survey.json (an installation file, like known_facts.json; the
repository has config/survey.example.json). The page /widget/survey.html reads them from
`GET /survey` and asks `GET /survey/me` whether this account may still answer; it sends the
answers with `POST /survey`. All three need the user's token: without a login nothing of the
survey is shown, and the page sends the visitor to the login.

Answers are anonymous:
- data/survey.jsonl holds one row per questionnaire with the survey id and the answers only:
  no account, no name, no time;
- data/survey_respondents.json holds, per survey, the SORTED set of HMAC(AUTH_SECRET, survey id +
  account id): it stops a second questionnaire from the same account without saying who answered
  what (the set has no order, so it cannot be lined up with the rows of the answers). Whoever holds
  AUTH_SECRET can check whether a given account has answered, never what it answered.
Free texts go through the same personal-data filter as the feedback comments.
Answers of the test accounts (TEST_ACCOUNTS) and of the administrators are marked `test`, kept out
of the results and of the respondents set, so a test can be repeated; both are always eligible, so
an administrator sees the notice and the page as the respondents do.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import hmac
import json
import logging
import random
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Security
from pydantic import BaseModel, Field

from api import auth
from api.auth import require_user
from api.filelock import file_lock
from api.limiter import limiter
from api.pii import contains_personal, redact
from api.safefile import dump_line, read_json_bounded, require_regular, write_text_atomic
from config.settings import DATA_DIR, TEST_ACCOUNTS, now_local

log = logging.getLogger(__name__)

CONFIG_FILE = Path(__file__).parent.parent / "config" / "survey.json"
ANSWERS_FILE = DATA_DIR / "survey.jsonl"
RESPONDENTS_FILE = DATA_DIR / "survey_respondents.json"

_MAX_QUESTIONS = 40
_TEXT_DEFAULT_MAX = 1000
_TEXT_HARD_MAX = 3000

# Texts of the page that do not depend on the questions; the configuration may replace each one.
DEFAULT_LABELS = {
    "submit": "Send",
    "sending": "Sending…",
    "required": "Please answer the questions marked as required.",
    "optional": "optional",
    "login": "Log in to the assistant with your credentials, then open this page again.",
    "login_link": "Log in",
    "closed": "The questionnaire is closed. Thank you.",
    "already": "You have already answered this questionnaire. Thank you!",
    "not_eligible": "This questionnaire is not meant for your account.",
    "error": "Sending failed, please try again.",
    "thanks": "Thank you for your answers.",
    "anonymous": "Answers are anonymous: they are saved without your name, account or time.",
    "banner": "Tell us how it went: answer the short survey",
    "dismiss": "Hide this notice",
}

router = APIRouter(prefix="/survey", tags=["survey"])


# ---------------------------------------------------------------------------
# Definition
# ---------------------------------------------------------------------------
def _clean_question(q: Any) -> dict | None:
    if not isinstance(q, dict):
        return None
    qid, text, kind = str(q.get("id") or "").strip(), str(q.get("text") or "").strip(), q.get("type")
    if not qid or not text or kind not in ("scale", "choice", "text") or len(qid) > 40:
        return None
    out: dict = {"id": qid, "type": kind, "text": text, "required": bool(q.get("required", kind != "text"))}
    if q.get("help"):
        out["help"] = str(q["help"])
    if kind == "scale":
        lo, hi = int(q.get("min", 1)), int(q.get("max", 5))
        if not (0 <= lo < hi <= 10):
            return None
        out.update({"min": lo, "max": hi, "low": str(q.get("low") or ""), "high": str(q.get("high") or "")})
    elif kind == "choice":
        opts = [str(o).strip() for o in q.get("options") or [] if str(o).strip()]
        if len(opts) < 2 or len(set(opts)) != len(opts):
            return None
        out.update({"options": opts, "multiple": bool(q.get("multiple"))})
    else:
        out["max_length"] = max(1, min(int(q.get("max_length", _TEXT_DEFAULT_MAX)), _TEXT_HARD_MAX))
    return out


def _past(closes: str) -> bool:
    """True once the closing day (YYYY-MM-DD, last day included, local time) is over."""
    if not closes:
        return False
    try:
        day = dt.date.fromisoformat(closes)
    except ValueError:
        log.warning("survey.json: closes %r is not YYYY-MM-DD, survey kept closed", closes)
        return True
    return now_local().date() > day


def load_definition() -> dict | None:
    """The survey of config/survey.json, checked and completed, or None when there is none."""
    # written by the administrator like known_facts.json, and like it may be a link into the
    # installation's configuration repository: read it plainly, bounded in size
    try:
        if CONFIG_FILE.stat().st_size > 256 * 1024:
            raise ValueError("larger than 256 KiB")
        raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("not a JSON object")   # noqa: TRY004
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        log.warning("survey.json not readable: %s", e)
        return None
    sid = str(raw.get("id") or "").strip()
    questions = [c for c in (_clean_question(q) for q in (raw.get("questions") or [])[:_MAX_QUESTIONS]) if c]
    if not sid or not questions:
        log.warning("survey.json: missing id or valid questions")
        return None
    if len({q["id"] for q in questions}) != len(questions):
        log.warning("survey.json: question ids are not unique")
        return None
    closes = str(raw.get("closes") or "").strip()
    labels = dict(DEFAULT_LABELS)
    labels.update({k: str(v) for k, v in (raw.get("labels") or {}).items() if k in DEFAULT_LABELS})
    return {
        "id": sid,
        "open": bool(raw.get("open", True)) and not _past(closes),
        "closes": closes,
        "lang": str(raw.get("lang") or "en"),
        "title": str(raw.get("title") or "Satisfaction survey"),
        "intro": str(raw.get("intro") or ""),
        "labels": labels,
        "questions": questions,
        "exclude_email_domains": [str(d).lower().lstrip("@") for d in raw.get("exclude_email_domains") or []],
    }


def public_definition(d: dict) -> dict:
    return {k: d[k] for k in ("id", "open", "closes", "lang", "title", "intro", "labels", "questions")}


# ---------------------------------------------------------------------------
# Who may answer
# ---------------------------------------------------------------------------
def _account(uid: str) -> dict:
    """The full users.json record (get_user_by_id keeps only id, name and email, not the role)."""
    if not uid:
        return {}
    return next((u for u in auth._load_users() if isinstance(u, dict) and u.get("id") == uid), {})


def _is_test(uid: str) -> bool:
    """Test accounts (TEST_ACCOUNTS) and administrators: they see the survey as the respondents do,
    can answer more than once, and their answers are kept out of the results."""
    u = _account(uid)
    if auth.is_admin_account(u):
        return True
    if not TEST_ACCOUNTS:
        return False
    return any(str(v or "").strip().lower() in TEST_ACCOUNTS for v in (uid, u.get("email"), u.get("name")))


def eligible(d: dict, uid: str) -> bool:
    """Accounts whose email is in an excluded domain (e.g. the staff, when the survey is for the
    citizen volunteers) cannot answer; the test accounts and the administrators always can."""
    if _is_test(uid):
        return True
    if not d["exclude_email_domains"]:
        return True
    email = str(_account(uid).get("email") or "").lower()
    return not any(email.endswith("@" + dom) for dom in d["exclude_email_domains"])


def _respondent_key(survey_id: str, uid: str) -> str:
    if not auth.auth_secret_ok():
        raise RuntimeError("AUTH_SECRET is missing or too short")
    msg = f"survey\0{survey_id}\0{uid}".encode()
    return hmac.new(auth.AUTH_SECRET.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def _read_respondents() -> dict[str, list[str]]:
    # the API creates the data files empty at startup (ensure_private): empty = nobody yet
    st = require_regular(RESPONDENTS_FILE)
    if st is None or st.st_size == 0:
        return {}
    data = read_json_bounded(RESPONDENTS_FILE, 4 * 1024 * 1024, dict)
    return {str(k): [str(x) for x in v] for k, v in data.items() if isinstance(v, list)}


def has_answered(survey_id: str, uid: str) -> bool:
    return _respondent_key(survey_id, uid) in set(_read_respondents().get(survey_id, []))


# ---------------------------------------------------------------------------
# Answers
# ---------------------------------------------------------------------------
def _free_text(text: str) -> str:
    """Same rule as the feedback comments: a text with personal data is dropped altogether."""
    if contains_personal(text):
        return "[risposta rimossa: conteneva dati personali]"
    return redact(text)


def validate_answers(d: dict, answers: dict) -> dict:
    """The answers kept, in the order of the questions. Raises ValueError with the reason."""
    known = {q["id"] for q in d["questions"]}
    unknown = set(answers) - known
    if unknown:
        raise ValueError("Domanda sconosciuta: " + ", ".join(sorted(unknown))[:200])
    out: dict = {}
    for q in d["questions"]:
        v = answers.get(q["id"])
        if v in (None, "", []):
            if q["required"]:
                raise ValueError(f"Risposta mancante: {q['id']}")
            continue
        if q["type"] == "scale":
            if isinstance(v, bool) or not isinstance(v, int) or not q["min"] <= v <= q["max"]:
                raise ValueError(f"Valore non valido: {q['id']}")
            out[q["id"]] = v
        elif q["type"] == "choice":
            vals = v if isinstance(v, list) else [v]
            if not q["multiple"] and len(vals) != 1:
                raise ValueError(f"Una sola scelta: {q['id']}")
            if (not all(isinstance(x, str) and x in q["options"] for x in vals)
                    or len(set(vals)) != len(vals)):
                raise ValueError(f"Scelta non valida: {q['id']}")
            out[q["id"]] = sorted(vals, key=q["options"].index) if q["multiple"] else vals[0]
        else:
            if not isinstance(v, str):
                raise ValueError(f"Testo non valido: {q['id']}")
            text = " ".join(v.split())[: q["max_length"]]
            if text:
                out[q["id"]] = _free_text(text)
            elif q["required"]:
                raise ValueError(f"Risposta mancante: {q['id']}")
    if not out:
        raise ValueError("Nessuna risposta")
    return out


def store(d: dict, uid: str, answers: dict) -> None:
    """Saves the answers of `uid`, once per survey (test accounts excepted).
    Raises FileExistsError when the account has already answered."""
    test = _is_test(uid)
    row: dict = {"survey": d["id"], "answers": answers}
    if test:
        row["test"] = True
    with file_lock(RESPONDENTS_FILE):
        if not test:
            data = _read_respondents()
            key = _respondent_key(d["id"], uid)
            keys = set(data.get(d["id"], []))
            if key in keys:
                raise FileExistsError(d["id"])
        require_regular(ANSWERS_FILE)
        with file_lock(ANSWERS_FILE), open(ANSWERS_FILE, "a", encoding="utf-8") as f:
            f.write(dump_line(row) + "\n")
        if not test:
            keys.add(key)
            data[d["id"]] = sorted(keys)
            write_text_atomic(RESPONDENTS_FILE, json.dumps(data, ensure_ascii=False, indent=0) + "\n")


# ---------------------------------------------------------------------------
# Results (admin)
# ---------------------------------------------------------------------------
def _rows(survey_id: str) -> tuple[list[dict], int]:
    rows, tests = [], 0
    if not ANSWERS_FILE.exists():
        return rows, tests
    require_regular(ANSWERS_FILE)
    with open(ANSWERS_FILE, encoding="utf-8") as f:
        for line in f.read().split("\n"):
            try:
                r = json.loads(line) if line.strip() else None
            except json.JSONDecodeError:
                continue
            if not isinstance(r, dict) or r.get("survey") != survey_id or not isinstance(r.get("answers"), dict):
                continue
            if r.get("test"):
                tests += 1
            else:
                rows.append(r["answers"])
    return rows, tests


def results(d: dict) -> dict:
    """Totals per question, test answers excluded. Free texts are listed in random order
    (shuffled), so their position does not hint at who wrote them."""
    rows, tests = _rows(d["id"])
    out = []
    for q in d["questions"]:
        vals = [r[q["id"]] for r in rows if q["id"] in r]
        item: dict = {"id": q["id"], "type": q["type"], "text": q["text"], "answered": len(vals)}
        if q["type"] == "scale":
            nums = [v for v in vals if isinstance(v, int)]
            item.update({"min": q["min"], "max": q["max"], "low": q["low"], "high": q["high"],
                         "counts": {str(n): nums.count(n) for n in range(q["min"], q["max"] + 1)},
                         "average": round(sum(nums) / len(nums), 2) if nums else None})
        elif q["type"] == "choice":
            flat = [x for v in vals for x in (v if isinstance(v, list) else [v])]
            item.update({"multiple": q["multiple"], "counts": {o: flat.count(o) for o in q["options"]}})
        else:
            texts = [v for v in vals if isinstance(v, str)]
            random.shuffle(texts)
            item["texts"] = texts
        out.append(item)
    return {"id": d["id"], "title": d["title"], "open": d["open"], "responses": len(rows),
            "test_responses": tests, "questions": out}


# ---------------------------------------------------------------------------
# Endpoints for the users
# ---------------------------------------------------------------------------
def _definition_or_404() -> dict:
    d = load_definition()
    if d is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return d


@router.get("")
async def survey_definition(_: dict = Security(require_user)):
    """Questions and texts of the survey, for the logged-in users only."""
    d = await asyncio.to_thread(_definition_or_404)
    return public_definition(d)


@router.get("/me")
async def survey_me(user: dict = Security(require_user)):
    """Whether this account may answer: `answered`, `eligible`."""
    d = await asyncio.to_thread(_definition_or_404)
    uid = user.get("uid", "")
    ok = await asyncio.to_thread(eligible, d, uid)
    done = await asyncio.to_thread(has_answered, d["id"], uid)
    return {"id": d["id"], "open": d["open"], "closes": d["closes"], "eligible": ok, "answered": done,
            "banner": d["labels"]["banner"], "dismiss": d["labels"]["dismiss"]}


class SurveyAnswers(BaseModel):
    survey: str = Field(..., max_length=80)
    answers: dict[str, Any] = Field(..., max_length=_MAX_QUESTIONS)


@router.post("")
@limiter.limit("10/hour")
async def survey_submit(request: Request, req: SurveyAnswers, user: dict = Security(require_user)):
    """Records the answers of the logged-in user, without saving who gave them."""
    d = await asyncio.to_thread(_definition_or_404)
    uid = user.get("uid", "")
    if not uid or uid == "anon":
        raise HTTPException(status_code=401, detail="Autenticazione richiesta")
    if req.survey != d["id"]:
        raise HTTPException(status_code=409, detail="Il questionario è cambiato: ricarica la pagina")
    if not d["open"]:
        raise HTTPException(status_code=403, detail=d["labels"]["closed"])
    if not await asyncio.to_thread(eligible, d, uid):
        raise HTTPException(status_code=403, detail=d["labels"]["not_eligible"])
    try:
        answers = validate_answers(d, req.answers)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    try:
        await asyncio.to_thread(store, d, uid, answers)
    except FileExistsError as e:
        raise HTTPException(status_code=409, detail=d["labels"]["already"]) from e
    log.info("Survey %s: answers recorded", d["id"])
    return {"ok": True}
