<sub>[Zautte](../README.md) › [Documentation](README.md) › Accessibility Testing</sub>

# Accessibility Testing

How to check that Zautte works with assistive technologies. The target and the current status are in [ACCESSIBILITY.md](../ACCESSIBILITY.md).

## Automated checks (CI)

`tests/a11y/check.py` runs on every push (job *Accessibility (axe-core)*). It serves `widget/` locally, simulates the API and checks, in Chromium:

- axe-core 4.10.2 with the WCAG 2.0, 2.1 and 2.2 A and AA rules, on every state: floating chat closed and open, login and login error, answer with sources and 👍/👎, report form, 320 px width, the dashboard's colleague and admin views, `come-funziona.html`;
- what axe cannot see: the closed chat stays out of the Tab order, the panel is not modal, focus is not lost (after an answer, a failed login, a 👎, "Non ora", Esc), the conversation is not a live region, the finished answer is announced once without markdown or addresses and with the number of sources, headings, lists and links in answers are real HTML, English answers carry `lang="en"`, 👍/👎 have names and `aria-pressed`, form fields have visible labels, no transitions with reduced motion, no horizontal scrolling at 320 px.

Run it locally:

```sh
pip install playwright && python -m playwright install chromium
npm install --no-save axe-core@4.10.2
python tests/a11y/check.py            # A11Y_CHANNEL=chrome to use the installed Chrome
```

Automated checks find only part of the barriers. They do not tell whether a screen reader actually announces what it should, or whether a person can complete a task: that needs the tests below.

## Screen reader tests

### Setups

| Screen reader | Browser | System | How to start it |
|---|---|---|---|
| NVDA (free) | Firefox and Chrome | Windows | Ctrl+Alt+N |
| JAWS, if available | Chrome | Windows | — |
| VoiceOver | Safari | macOS | Cmd+F5 |
| VoiceOver | Safari | iPhone | Settings › Accessibility, or triple-click the side button if set |
| TalkBack | Chrome | Android | Settings › Accessibility, or both volume keys for 3 seconds if set |

Test both pages: `https://<server>/widget/pilot.html` (floating chat) and `https://<server>/widget/dashboard.html` (chat inside the page). Use a test account (`python -m scripts.adduser`): questions are logged, and after the session `python -m scripts.forget --user <account> --all` deletes them.

### Tasks and expected results

| # | Task | Expected |
|---|---|---|
| 1 | Find the chat and open it (pilot) | The button is read as "Apri assistente", button, collapsed; after opening, focus is in the e-mail field and "Accedi a Zautte" is a heading |
| 2 | Log in with a wrong password, then the right one | Fields read as "Email" and "Password"; "Email o password non validi." is announced and focus stays where it was; after logging in focus is in "La tua domanda", with the hint "Invio per inviare, Maiuscolo più Invio per andare a capo" |
| 3 | Ask "Come rinnovo la carta d'identità?" | "Sto elaborando la risposta..." is announced; when the answer is complete it is read once, in full, followed by "Fonti:" and the number of sources; it is never read in fragments or twice. If the wait exceeds 10 seconds, "Sto elaborando... potrebbe richiedere qualche minuto." is announced |
| 4 | Explore the answer | Inside the "Conversazione" region, headings (H in NVDA, VO+Cmd+H) and lists (L) of the answer can be reached; links have meaningful names; sources are links named after the document |
| 5 | Ask in English: "How do I renew my identity card?" | The answer is read with an English voice; "Fonti:" stays Italian |
| 6 | Rate the answer with 👎, send the form empty, then with a comment | Buttons read as "Risposta utile" / "Risposta non utile", toggle button, pressed or not; the comment field has its label; the empty-form error is announced; after sending, "Grazie! Segnalazione registrata." is announced and focus returns to "La tua domanda" |
| 7 | Close the chat with Esc, then with × | Focus returns to the open button; the chat's content can no longer be reached by Tab or by the screen reader's reading keys |
| 8 | Phone (VoiceOver, TalkBack) | Swiping goes through header, conversation, field and send button in order; the open button behind the full-screen chat is not reached; × closes it |
| 9 | Dashboard | Login and chat behave as above, without the open and close buttons; the chat is a region of the page |

Record each result (task, screen reader and browser, ok or not, what was heard) and open an issue with the [Accessibility problem](https://github.com/Antonio-Prado/zautte/issues/new?template=accessibility.md) template for anything that does not match.

### Other checks without a screen reader

- **Keyboard only**: tasks 1–3 and 6–7 with Tab, Shift+Tab, Enter, Space and Esc; focus must always be visible.
- **Zoom**: browser zoom at 200% and 400% (1280 px wide window): everything readable, no horizontal scrolling of the page.
- **Forced colors**: Windows contrast themes, or `forced-colors` emulation in Chrome DevTools: borders, focus and the selected 👍/👎 stay visible.
- **Text spacing** (WCAG 1.4.12): a text-spacing bookmarklet must not cut or overlap text.

## Tests with people with disabilities

Tests with people who use assistive technologies every day find problems that no checklist does. The administration can organise them with local associations, for example of blind and visually impaired people (UICI), deaf people (ENS) and people with motor or cognitive disabilities.

- **Who**: a few people for each profile (screen reader, magnification, keyboard or switch access, cognitive), with their own devices and settings.
- **What**: the tasks above, worded as goals ("find out how to renew your identity card"), plus one or two real questions of their own; ask them to think aloud.
- **How**: the session place and materials must be accessible themselves; observe without helping, and note where they hesitate, give up or get a wrong idea.
- **Privacy**: use test accounts, explain that questions are logged and delete them afterwards with `scripts/forget.py`; collect consent for any recording.
- **After**: open an issue for each problem, fix, and repeat the task that failed.

---

← [Privacy and Security](privacy-and-security.md) · [Documentation index](README.md)
