# Accessibility

Zautte answers citizens on behalf of a public administration, so everyone must be able to use it: with a keyboard only, a screen reader, a magnifier, voice control, large text or a small screen. This page states the target, where the project stands today, what is known not to work yet, and how to report a problem.

*Last review: 5 October 2026 (first audit, fixes, automated checks in CI, first VoiceOver session).*

## Target

**WCAG 2.1 level AA**, the level required of Italian public administrations: Law 4/2004 ("Legge Stanca"), as amended by Legislative Decree 106/2018 implementing the EU Web Accessibility Directive (2016/2102), and AgID's accessibility guidelines refer to the harmonised standard EN 301 549, whose web requirements are WCAG 2.1 AA. Where it costs little, the project also meets the criteria added by **WCAG 2.2 AA**.

**In scope:** the chat widget (`widget/chatbot-widget.js`) in both modes, floating button and panel inside a page (`inline`), with login, answers, sources, 👍/👎 and the report form, plus the pages shipped in `widget/`: `dashboard.html`, `pilot.html` and `come-funziona.html`.

**Out of scope:** the website that embeds the widget, which is the administration's responsibility (see [below](#for-administrations-deploying-zautte)), and the wording of the answers, which a language model generates from the site's own pages.

## Current status

**Partially conformant** with WCAG 2.1 AA: every barrier found so far by automated checks, keyboard tests, code review and a first screen reader session (VoiceOver on macOS) has been fixed, and no known issue is open, but the other screen readers and tests with people with disabilities are still to come.

The first audit (5 October 2026) combined automated checks with axe-core 4.10.2 in Chrome on 13 states of the widget and its pages (closed and open, login and login error, conversation with sources and 👍/👎, report form, error, 320 px width, admin view), keyboard-only walkthroughs, checks at 320 px, 200% zoom and reduced motion, contrast ratios computed from the CSS, and a review of the widget code. The API was simulated, so no data was sent anywhere. It found 19 issues, three of them high severity; all were fixed the same day, together with two more found afterwards (see below). A second run found **no axe violations in any of the 13 states**, also on the deployed pages.

Since then, **every change is checked in CI** (`tests/a11y/check.py`): axe-core on all those states plus the behaviour axe cannot see, such as focus, announcements and the structure of answers. See [Accessibility Testing](docs/accessibility-testing.md).

### Screen reader tests

| Date | Setup | Result |
|---|---|---|
| 5 October 2026 | VoiceOver, macOS 26.7, Chrome; the real widget with a simulated API streaming the answer over about four seconds; speech log recorded through VoiceOver's AppleScript interface, plus listening | Tasks 1–3 and 5–7 of the [test protocol](docs/accessibility-testing.md#tasks-and-expected-results) as expected: dialog and fields announced with their labels, login error announced, "Sto elaborando la risposta..." and then the answer read **once, in full** (heard to the end) with "Fonti: 2", never in fragments; the English answer read with an English voice; report form label, error and thanks announced; Esc back to "Apri assistente, collapsed button" and Tab no longer entering the closed chat. Task 4 in part: moving back from the text box, VoiceOver read the "Valuta la risposta" group with its toggle buttons, the sources as links and the list items ("2 of 2"), but the session stopped before the headings. Tasks 8 (phone) and 9 (dashboard) not done. **Found and fixed:** exploring the chat after an answer, VoiceOver met a second copy of it in the hidden announcement area, which now empties a few seconds after each announcement |

### Known issues

None open. Report new ones as described [below](#reporting-a-problem).

### Fixed on 5 October 2026

- **Keyboard and focus.** The closed floating panel is now hidden (`visibility: hidden`), so its controls are no longer reachable with Tab; it no longer claims to be a modal dialog it could not trap focus in (in `inline` mode it is a region of the page); on screens up to 480 px the launcher hidden behind the full-screen panel no longer takes focus; Escape closes the panel only when focus is in the chat or on its button; focus is no longer lost while an answer is being prepared, after a failed login, or after sending or skipping a report.
- **Screen readers.** The conversation is no longer a live region rewritten at every streamed fragment: a hidden status region announces "Sto elaborando la risposta...", the long-wait notice, the finished answer once (without markdown symbols, with "(link)" in place of addresses) followed by the number of sources, and errors. The panel declares the interface language and each answer its own (`lang="en"` for English answers, sent by the API). 👍/👎 have names ("Risposta utile", "Risposta non utile") and `aria-pressed`, inside a "Valuta la risposta" group. Login fields, the report comment and the link fields have visible labels; the login title is a heading and "Password dimenticata?" a button; the report form's error is an alert; every interface text follows the configured language.
- **Visual.** Borders of the text box, form fields and 👍/👎 at 3.3:1 or more; placeholder at 5.1:1; waiting notice, `pilot.html` hint, and the grey texts and orange tags of the dashboard admin view at 4.5:1 or more; the selected 👍/👎 is also marked by a thicker border, not only by color; source links in 24 px rows; animations and transitions stop with the system "reduce motion" setting.
- **Content.** `come-funziona.html` no longer states that the chat simply works with keyboard and screen readers: it gives the target, says that checks are ongoing, links this page and explains how to report a difficulty.
- **Found afterwards and fixed.** The hidden announcement area empties five seconds after each announcement, so that whoever explores the chat with a screen reader does not meet a second copy of the answer (found with VoiceOver). Headings, lists, quotes and separators written by the model in an answer are now real HTML headings (`h3`, `h4`), lists (`ul`, `ol`, nested too), `blockquote` and `hr`, so screen readers convey their structure (1.3.1); a markdown link `[text](address)` no longer produces a broken link nested inside another. The chat text box has a visible label ("La tua domanda"), the placeholder became an instruction ("Scrivi qui e premi Invio") and screen readers also hear how to send and how to start a new line (3.3.2).

### What already worked

- Buttons are real `<button>` elements with accessible names; the launcher reports whether the panel is open (`aria-expanded`).
- Escape closes the floating panel and puts focus back on the launcher. Focus moves to the right field when the chat opens, after login, after each answer and after a 👎.
- Login errors are announced (`role="alert"`); login fields use `type="email"` and `autocomplete`.
- The "IA" badge has a text alternative; source links use the document title as link text.
- No horizontal scrolling at 320 px; at 200% zoom the chat stays usable, though cramped.
- Pages declare `lang="it"`, have meaningful titles and a main heading.

### Not tested yet

- Screen readers other than VoiceOver with Chrome on macOS: NVDA and JAWS on Windows, VoiceOver with Safari and on iOS, TalkBack on Android.
- Real mobile devices and browser zoom (only emulated), forced colors / Windows high contrast, text spacing (1.4.12).
- Testing with people with disabilities.

## Roadmap

1. Test with the other screen readers (NVDA first, then VoiceOver on iPhone and TalkBack), following the [test protocol](docs/accessibility-testing.md#screen-reader-tests).
2. Test with people with disabilities, with the help of the administration ([how](docs/accessibility-testing.md#tests-with-people-with-disabilities)).
3. Keep the CI check green and extend it whenever the widget changes.

Progress is tracked in issues with the [`accessibility`](https://github.com/Antonio-Prado/zautte/labels/accessibility) label; this page is updated at each review.

## For administrations deploying Zautte

- **Your pages:** the widget lives inside your website. The page around it (structure, headings, skip links, contrast, focus order) is your responsibility, and so is the place where the floating button sits: make sure it does not cover content or controls.
- **Colors:** `primaryColor` is the background of white text (header, the user's messages, buttons) and the color of links and titles on white. If you change it, check a contrast ratio of at least 4.5:1 against white; the default `#003366` gives 12.6:1.
- **Accessibility statement:** Italian public administrations publish their statement (*dichiarazione di accessibilità*) through AgID's form by 23 September every year, with a feedback mechanism for users, and link it from the footer. Cover the pages that host the chat and report the known issues that still apply to your version. In the pages shipped with Zautte, the footer (`site-footer.js`) links the "Accessibilità" section of `come-funziona.html` until the statement's address is set in `data-accessibility` on its script tag; then the link becomes "Dichiarazione di accessibilità".
- **Language:** set `lang` in the widget configuration to the language of the host page.

## Reporting a problem

If something in Zautte is hard or impossible to use for you, please tell us, even if you are not sure it is an accessibility issue:

- open an issue with the [Accessibility problem](https://github.com/Antonio-Prado/zautte/issues/new?template=accessibility.md) template, or
- write to **antonio@prado.it**.

Please say what you were trying to do, what went wrong, and which browser, device and assistive technology you use. If you met the problem on an administration's website, you can also use the feedback mechanism in that administration's accessibility statement.
