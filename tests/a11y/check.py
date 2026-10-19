"""
Accessibility check of the widget and of the pages in widget/ (runs in CI).

axe-core (WCAG 2.0, 2.1 and 2.2, levels A and AA) on every chat state, plus the
rules axe cannot see: the closed chat out of the Tab order, focus that
is not lost, announcements for screen readers, the structure of answers
(headings, lists, non-nested links), the language of answers, labels.
The API is mocked: no request leaves the machine.

    pip install playwright && python -m playwright install chromium
    npm install --no-save axe-core@4.10.2
    python tests/a11y/check.py

Variables: AXE_JS (path of axe.min.js, default node_modules/axe-core/),
A11Y_CHANNEL=chrome to use the system Chrome instead of Chromium.
Exits with code 1 if something is wrong. See ACCESSIBILITY.md.
"""

import functools
import http.server
import json
import os
import re
import sys
import threading
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
AXE_JS = Path(os.environ.get("AXE_JS") or ROOT / "node_modules" / "axe-core" / "axe.min.js").read_text()
TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]
W = "#zautte-chatbot"

ANSWER = ("Per rinnovare la **carta d'identità** prenota un appuntamento.\n\n"
          "## Documenti\n- foto tessera\n- vecchia carta\n\n"
          "Vedi [la pagina della CIE](https://www.example.org/cie) o scrivi a anagrafe@example.org.")
ANSWER_EN = "To renew your identity card, book an appointment at the registry office."
SOURCES = [{"title": "Carta d'identità elettronica", "url": "https://www.example.org/cie", "score": 0.8},
           {"title": "Regolamento anagrafe (documento archiviato)", "url": "", "score": 0.6}]

failures: list[str] = []


def check(ok: bool, what: str) -> None:
    print(("  ok  " if ok else "  KO  ") + what)
    if not ok:
        failures.append(what)


def sse(answer: str, language: str) -> str:
    chunks = [answer[i:i + 25] for i in range(0, len(answer), 25)]
    body = "".join(f"data: {json.dumps({'token': c})}\n\n" for c in chunks)
    body += "data: " + json.dumps({"sources": SOURCES, "rid": "a1b2c3d4e5f6", "language": language}) + "\n\n"
    return body + 'data: {"done": true}\n\n'


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def serve() -> str:
    handler = functools.partial(_QuietHandler, directory=str(ROOT))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}"


def mock_api(page, base: str, login_status: int = 200, answer: str = ANSWER, language: str = "it",
             chat_status: int = 200, ui_lang: str | None = "it") -> None:
    """ui_lang "it" serves the widget pages with the widget in Italian (the generic pages are English):
    most of the checks below read Italian texts; None leaves the pages as they are."""
    def handle(route):
        url = route.request.url
        path = urlsplit(url).path
        if path.endswith("/dati"):                         # panel of the restricted area, which the server only serves with a key
            return route.fulfill(json={k: (ROOT / "admin_panel" / f"panel.{k}").read_text(encoding="utf-8")
                                       for k in ("css", "html", "js")})
        if path.endswith(".html") and ui_lang == "it" and url.startswith(base + "/widget/"):
            resp = route.fetch()
            return route.fulfill(response=resp, body=re.sub(r"lang:\s*'en'", "lang: 'it'", resp.text()))
        if url.startswith(base + "/widget/") or path == "/admin_panel/login.html":
            return route.continue_()
        if not url.startswith(base):
            return route.fulfill(status=404, body="")      # GitHub, external fonts...
        replies = {
            "/auth/login": {"token": "tok", "name": "Prova", "expires_in": 3600},
            "/auth/me": {"uid": "u1", "name": "Prova"},
            "/client-ip": {"ip": "2001:db8:85a3:8d3:1319:8a2e:370:7348"},
            "/feedback/detail": {"ok": True},
            "/feedback/negative": {"items": [], "total_negative": 0, "total": 0},
            "/feedback": {"ok": True, "id": "fb0000000001"},
            "/crawl-history": {"events": [], "current_html": None, "current_pdf": None},
            "/usage/summary": {"total_messages": 0, "total_users": 0, "total_registered": 0,
                               "total_active": 0, "users": [], "daily": []},
            "/usage/messages": {"messages": [], "total": 0},
            "/health": {"status": "ok", "llm_provider": "claude", "hybrid_search": True, "uptime_seconds": 100,
                        "indexed_chunks": 1000, "unique_sources": 10, "doc_types": {"html": 5, "pdf": 5},
                        "llm_model": "m", "last_indexed": "05/10/2026 10:00", "queries_since_restart": 3,
                        "gaps_total": 1, "gaps_recent": [{"ts": "2026-10-05T10:00:00", "query": "q"}],
                        "feedback": {"total": 2, "positive": 1, "negative": 1},
                        "activity": {"avg_response_ms": 1000, "top_queries": [["q", 2]], "hour_counts": [1] * 24,
                                     "token_in_total": 10, "token_out_total": 10, "token_cost_total": 0.1,
                                     "token_avg_in": 1, "token_avg_out": 1, "token_history": []},
                        "top_doc": None},
        }
        if path == "/auth/login" and login_status != 200:
            return route.fulfill(status=login_status, json={"detail": "x"})
        if path == "/chat/stream" and chat_status == 429:
            return route.fulfill(status=429, json={"detail": {"error": "daily_limit", "limit": 20}})
        if path == "/chat/stream":
            return route.fulfill(status=200, headers={"Content-Type": "text/event-stream"},
                                 body=sse(answer, language))
        return route.fulfill(json=replies.get(path, {}))
    page.route("**/*", handle)


def axe(page, state: str) -> None:
    page.add_script_tag(content=AXE_JS)
    result = page.evaluate("t => axe.run(document, {runOnly: {type: 'tag', values: t}})", TAGS)
    found = [f"{v['id']} ({len(v['nodes'])}): {v['nodes'][0]['target']}" for v in result["violations"]]
    check(not found, f"{state}: no axe violations" + (" — " + "; ".join(found) if found else ""))


def open_panel(page) -> None:
    """Opens the floating chat and waits for the fade to finish: during the
    transition axe would measure contrasts distorted by the partial opacity."""
    page.click(f"{W}-btn")
    page.wait_for_function("getComputedStyle(document.getElementById('zautte-chatbot-panel')).opacity === '1'")


def login(page) -> None:
    page.fill(f"{W}-login-email", "a@b.it")
    page.fill(f"{W}-login-pw", "x")
    page.press(f"{W}-login-pw", "Enter")
    page.wait_for_selector(f"{W}-input", state="visible")


def ask(page, question: str = "Come rinnovo la carta d'identità?") -> None:
    page.fill(f"{W}-input", question)
    page.press(f"{W}-input", "Enter")
    page.wait_for_selector(".zautte-chatbot-feedback")
    page.wait_for_timeout(250)   # the announcement starts 100 ms after the end of the answer


def new_page(browser, **kwargs):
    page = browser.new_page(**kwargs)
    page.set_default_timeout(10_000)   # a missing element fails early
    return page


def focus_in_panel(page) -> bool:
    return page.evaluate("() => !!document.activeElement.closest('#zautte-chatbot-panel')")


def main() -> int:
    base = serve()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel=os.environ.get("A11Y_CHANNEL") or None)

        print("pilot.html: floating widget")
        page = new_page(browser, viewport={"width": 1280, "height": 800})
        mock_api(page, base)
        page.goto(base + "/widget/pilot.html")
        axe(page, "closed")
        inside = False
        for _ in range(10):
            page.keyboard.press("Tab")
            inside = inside or focus_in_panel(page)
        check(not inside, "with the chat closed, Tab does not enter the panel")
        open_panel(page)
        page.wait_for_selector(f"{W}-login-email", state="visible")
        check(page.get_attribute(f"{W}-panel", "aria-modal") is None, "panel is not modal")
        check(page.is_visible("label[for='zautte-chatbot-login-email']")
              and page.is_visible("label[for='zautte-chatbot-login-pw']"), "visible labels in the login form")
        axe(page, "login")
        login(page)
        check(page.is_visible("#zautte-chatbot-input-label"), "visible label on the text field")
        check(page.evaluate("() => { const m = document.getElementById('zautte-chatbot-messages');"
                            " return !m.hasAttribute('aria-live') && m.getAttribute('role') !== 'log'; }"),
              "the conversation is not a live region")
        check(page.get_attribute(f"{W}-status", "role") == "status", "status region for announcements")
        axe(page, "chat open")
        ask(page)
        bubble = ".zautte-chatbot-msg.bot:last-of-type .zautte-chatbot-bubble"
        check(page.locator(f"{bubble} h3").count() == 1, "the answer title is an <h3>")
        check(page.locator(f"{bubble} ul > li").count() == 2, "the answer list is a <ul> with 2 items")
        check(page.locator(f"{bubble} a a").count() == 0, "no link inside another link")
        hrefs = page.eval_on_selector_all(f"{bubble} a", "els => els.map(e => e.getAttribute('href'))")
        check(hrefs == ["https://www.example.org/cie", "mailto:anagrafe@example.org"], f"answer links: {hrefs}")
        status = page.text_content(f"{W}-status") or ""
        check(status.startswith("Per rinnovare la carta d'identità") and "**" not in status
              and "https://" not in status and status.rstrip().endswith("Fonti: 2"),
              "the finished answer is announced once, without markdown or URLs, with the number of sources")
        check(page.evaluate("document.activeElement.id") == "zautte-chatbot-input", "focus in the field after the answer")
        axe(page, "answer with sources and 👍/👎")
        page.click(".zautte-chatbot-feedback button:nth-child(2)")
        page.wait_for_selector(".zautte-chatbot-fbform textarea")
        pressed = page.eval_on_selector_all(".zautte-chatbot-feedback button",
                                            "els => els.map(e => [e.getAttribute('aria-label'), e.getAttribute('aria-pressed')])")
        check(pressed == [["Risposta utile", "false"], ["Risposta non utile", "true"]], f"👍/👎 with name and state: {pressed}")
        check(page.evaluate("document.activeElement.tagName") == "TEXTAREA", "focus in the comment after 👎")
        axe(page, "report form")
        page.click(".zautte-chatbot-fbform button[type=submit]")
        check(page.get_attribute(".zautte-chatbot-fbform-msg", "role") == "alert"
              and bool(page.text_content(".zautte-chatbot-fbform-msg")), "form error announced (role=alert)")
        page.click(".zautte-chatbot-fbform-actions button:not(.primary)")
        check(page.evaluate("document.activeElement.id") == "zautte-chatbot-input", "focus in the field after «Non ora»")
        # With VoiceOver the full region made one meet a second copy of the
        # answer while exploring the chat: after the announcement it must be emptied
        page.wait_for_timeout(5500)
        check(not (page.text_content(f"{W}-status") or "").strip(),
              "after a few seconds the announcement region is emptied")
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        check(page.evaluate("document.activeElement.id") == "zautte-chatbot-btn"
              and page.eval_on_selector(f"{W}-panel", "e => getComputedStyle(e).visibility") == "hidden",
              "Esc closes the chat and returns focus to the button")
        page.close()

        print("pilot.html: wrong login, English answer, narrow screen, reduced motion")
        page = new_page(browser, viewport={"width": 1280, "height": 800})
        mock_api(page, base, login_status=401)
        page.goto(base + "/widget/pilot.html")
        open_panel(page)
        page.fill(f"{W}-login-email", "a@b.it")
        page.fill(f"{W}-login-pw", "x")
        page.click(f"{W}-login-submit")
        page.wait_for_selector(f"{W}-login-error", state="visible")
        check(page.evaluate("document.activeElement.id") == "zautte-chatbot-login-submit",
              "focus is kept after a wrong login")
        axe(page, "login error")
        page.close()

        page = new_page(browser, viewport={"width": 1280, "height": 800})
        mock_api(page, base, answer=ANSWER_EN, language="en")
        page.goto(base + "/widget/pilot.html")
        open_panel(page)
        login(page)
        ask(page, "How do I renew my identity card?")
        check(page.get_attribute(".zautte-chatbot-msg.bot:last-of-type", "lang") == "en"
              and page.get_attribute(f"{W}-status span", "lang") == "en", "English answer marked lang=en")
        page.close()

        page = new_page(browser, viewport={"width": 1280, "height": 800})
        mock_api(page, base, chat_status=429)
        page.goto(base + "/widget/pilot.html")
        open_panel(page)
        login(page)
        page.fill(f"{W}-input", "Un'altra domanda")
        page.press(f"{W}-input", "Enter")
        page.wait_for_selector(".zautte-chatbot-msg.bot:last-of-type :text('limite di 20 domande')")
        page.wait_for_timeout(250)
        check("limite di 20 domande al giorno" in (page.text_content(f"{W}-status") or ""),
              "daily limit: message with the number of questions, announced")
        check(page.evaluate("document.activeElement.id") == "zautte-chatbot-input", "focus in the field after the limit")
        axe(page, "daily limit reached")
        page.close()

        page = new_page(browser, viewport={"width": 320, "height": 640}, reduced_motion="reduce")
        mock_api(page, base)
        page.goto(base + "/widget/pilot.html")
        open_panel(page)
        login(page)
        check(page.eval_on_selector(f"{W}-btn", "e => getComputedStyle(e).visibility") == "hidden",
              "320 px: the button covered by the panel does not take focus")
        check(page.eval_on_selector(f"{W}-panel", "e => getComputedStyle(e).transitionDuration")
              .replace(" ", "").strip("0s,") == "", "reduced motion: no transitions")
        ask(page)
        check(page.evaluate("document.documentElement.scrollWidth") <= 320, "320 px: no horizontal scrolling")
        axe(page, "320 px, answer")
        page.close()

        print("dashboard.html and come-funziona.html")
        page = new_page(browser, viewport={"width": 1280, "height": 800})
        mock_api(page, base)
        page.goto(base + "/widget/dashboard.html")
        page.wait_for_selector(f"{W}-login-email", state="visible")
        axe(page, "dashboard, colleagues login")
        login(page)
        check(page.get_attribute(f"{W}-panel", "role") == "region", "in-page chat as a region, not a dialog")
        ask(page)
        axe(page, "dashboard, in-page chat")
        page.close()

        page = new_page(browser, viewport={"width": 1280, "height": 800})
        page.add_init_script("localStorage.setItem('zautte_admin_key', 'k')")
        mock_api(page, base)
        page.goto(base + "/admin_panel/login.html")
        page.wait_for_selector("#admin-grid", state="visible")
        page.wait_for_timeout(500)
        axe(page, "admin area, administrator view")
        page.close()

        page = new_page(browser, viewport={"width": 1280, "height": 800})
        mock_api(page, base)
        page.goto(base + "/widget/come-funziona.html")
        axe(page, "come-funziona.html")
        page.wait_for_selector("#zautte-a11y-link")
        check(page.get_attribute("#zautte-a11y-link", "href").endswith("/widget/come-funziona.html#accessibility")
              and page.locator("#accessibility").count() == 1,
              "footer: «Accessibility» link to the come-funziona.html section")
        check(page.get_attribute("nav.back a", "href") == "./dashboard.html"
              and page.locator("main > p.back-end a[href='./dashboard.html']").count() == 1,
              "come-funziona.html: «Back to Zautte» link at top and bottom")

        page = new_page(browser, viewport={"width": 1280, "height": 800})
        mock_api(page, base, ui_lang=None)
        page.goto(base + "/widget/dashboard.html")
        page.wait_for_selector(f"{W}-login-email", state="visible")
        check(page.evaluate("document.documentElement.lang") == "en"
              and "Sign in" in (page.text_content(f"{W}-login-submit") or ""),
              "generic pages in English: page and widget (login)")
        axe(page, "dashboard in English, login")
        page.close()
        browser.close()

    if failures:
        print(f"\n{len(failures)} accessibility problems")
        return 1
    print("\nAccessibility: all ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
