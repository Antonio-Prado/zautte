# Accessibility

Zautte answers citizens on behalf of a public administration, so everyone must be able to use it: with a keyboard only, a screen reader, a magnifier, voice control, large text or a small screen. This page states the target, where the project stands today, what is known not to work yet, and how to report a problem.

*Last review: 5 October 2026 (first audit and fixes).*

## Target

**WCAG 2.1 level AA**, the level required of Italian public administrations: Law 4/2004 ("Legge Stanca"), as amended by Legislative Decree 106/2018 implementing the EU Web Accessibility Directive (2016/2102), and AgID's accessibility guidelines refer to the harmonised standard EN 301 549, whose web requirements are WCAG 2.1 AA. Where it costs little, the project also meets the criteria added by **WCAG 2.2 AA**.

**In scope:** the chat widget (`widget/chatbot-widget.js`) in both modes, floating button and panel inside a page (`inline`), with login, answers, sources, 👍/👎 and the report form, plus the pages shipped in `widget/`: `dashboard.html`, `pilot.html` and `come-funziona.html`.

**Out of scope:** the website that embeds the widget, which is the administration's responsibility (see [below](#for-administrations-deploying-zautte)), and the wording of the answers, which a language model generates from the site's own pages.

## Current status

**Partially conformant** with WCAG 2.1 AA: all the barriers found so far by automated and keyboard checks have been fixed, but the widget has not yet been tried with real screen readers or by people with disabilities, and two smaller issues remain open.

The first audit (5 October 2026) combined automated checks with axe-core 4.10.2 in Chrome on 13 states of the widget and its pages (closed and open, login and login error, conversation with sources and 👍/👎, report form, error, 320 px width, admin view), keyboard-only walkthroughs, checks at 320 px, 200% zoom and reduced motion, contrast ratios computed from the CSS, and a review of the widget code. The API was simulated, so no data was sent anywhere. It found 19 issues, three of them high severity; all were fixed the same day, and a second run of the same checks found **no axe violations in any of the 13 states**.

### Known issues

| Problem | WCAG | Severity |
|---|---|---|
| Headings and lists written by the model in an answer are shown as bold text and lines starting with "-", not as real headings and lists, so screen readers do not convey their structure | 1.3.1 | Medium |
| The chat text box has an accessible name, but its only visible label is the placeholder ("Scrivi la tua domanda..."), which disappears while typing | 3.3.2 | Low |

### Fixed on 5 October 2026

- **Keyboard and focus.** The closed floating panel is now hidden (`visibility: hidden`), so its controls are no longer reachable with Tab; it no longer claims to be a modal dialog it could not trap focus in (in `inline` mode it is a region of the page); on screens up to 480 px the launcher hidden behind the full-screen panel no longer takes focus; Escape closes the panel only when focus is in the chat or on its button; focus is no longer lost while an answer is being prepared, after a failed login, or after sending or skipping a report.
- **Screen readers.** The conversation is no longer a live region rewritten at every streamed fragment: a hidden status region announces "Sto elaborando la risposta...", the long-wait notice, the finished answer once (without markdown symbols, with "(link)" in place of addresses) followed by the number of sources, and errors. The panel declares the interface language and each answer its own (`lang="en"` for English answers, sent by the API). 👍/👎 have names ("Risposta utile", "Risposta non utile") and `aria-pressed`, inside a "Valuta la risposta" group. Login fields, the report comment and the link fields have visible labels; the login title is a heading and "Password dimenticata?" a button; the report form's error is an alert; every interface text follows the configured language.
- **Visual.** Borders of the text box, form fields and 👍/👎 at 3.3:1 or more; placeholder at 5.1:1; waiting notice, `pilot.html` hint, and the grey texts and orange tags of the dashboard admin view at 4.5:1 or more; the selected 👍/👎 is also marked by a thicker border, not only by color; source links in 24 px rows; animations and transitions stop with the system "reduce motion" setting.
- **Content.** `come-funziona.html` no longer states that the chat simply works with keyboard and screen readers: it gives the target, says that checks are ongoing, links this page and explains how to report a difficulty.

### What already worked

- Buttons are real `<button>` elements with accessible names; the launcher reports whether the panel is open (`aria-expanded`).
- Escape closes the floating panel and puts focus back on the launcher. Focus moves to the right field when the chat opens, after login, after each answer and after a 👎.
- Login errors are announced (`role="alert"`); login fields use `type="email"` and `autocomplete`.
- The "IA" badge has a text alternative; source links use the document title as link text.
- No horizontal scrolling at 320 px; at 200% zoom the chat stays usable, though cramped.
- Pages declare `lang="it"`, have meaningful titles and a main heading.

### Not tested yet

- Real screen readers: NVDA and JAWS on Windows, VoiceOver on macOS and iOS, TalkBack on Android. The status-region announcements, the non-modal panel and the language changes are the first things to verify.
- Real mobile devices and browser zoom (only emulated), forced colors / Windows high contrast, text spacing (1.4.12).
- Testing with people with disabilities.

## Roadmap

1. Test with screen readers on desktop and mobile, starting from the announcements.
2. Render headings and lists in answers as real HTML headings and lists.
3. Add an automated axe-core check to CI and repeat the full audit after each change to the widget.
4. Test with people with disabilities, with the help of the administration.

Progress is tracked in issues with the [`accessibility`](https://github.com/Antonio-Prado/zautte/labels/accessibility) label; this page is updated at each review.

## For administrations deploying Zautte

- **Your pages:** the widget lives inside your website. The page around it (structure, headings, skip links, contrast, focus order) is your responsibility, and so is the place where the floating button sits: make sure it does not cover content or controls.
- **Colors:** `primaryColor` is the background of white text (header, the user's messages, buttons) and the color of links and titles on white. If you change it, check a contrast ratio of at least 4.5:1 against white; the default `#003366` gives 12.6:1.
- **Accessibility statement:** Italian public administrations publish their statement (*dichiarazione di accessibilità*) through AgID's form by 23 September every year, with a feedback mechanism for users. Cover the pages that host the chat, report the known issues above that still apply to your version, and link the statement from the page given as `infoUrl` (`come-funziona.html` in the pages shipped with Zautte).
- **Language:** set `lang` in the widget configuration to the language of the host page.

## Reporting a problem

If something in Zautte is hard or impossible to use for you, please tell us, even if you are not sure it is an accessibility issue:

- open an issue with the [Accessibility problem](https://github.com/Antonio-Prado/zautte/issues/new?template=accessibility.md) template, or
- write to **antonio@prado.it**.

Please say what you were trying to do, what went wrong, and which browser, device and assistive technology you use. If you met the problem on an administration's website, you can also use the feedback mechanism in that administration's accessibility statement.
