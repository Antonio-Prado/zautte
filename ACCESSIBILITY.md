# Accessibility

Zautte answers citizens on behalf of a public administration, so everyone must be able to use it: with a keyboard only, a screen reader, a magnifier, voice control, large text or a small screen. This page states the target, where the project stands today, what is known not to work yet, and how to report a problem.

*Last review: 5 October 2026.*

## Target

**WCAG 2.1 level AA**, the level required of Italian public administrations: Law 4/2004 ("Legge Stanca"), as amended by Legislative Decree 106/2018 implementing the EU Web Accessibility Directive (2016/2102), and AgID's accessibility guidelines refer to the harmonised standard EN 301 549, whose web requirements are WCAG 2.1 AA. Where it costs little, the project also meets the criteria added by **WCAG 2.2 AA**.

**In scope:** the chat widget (`widget/chatbot-widget.js`) in both modes, floating button and panel inside a page (`inline`), with login, answers, sources, 👍/👎 and the report form, plus the pages shipped in `widget/`: `dashboard.html`, `pilot.html` and `come-funziona.html`.

**Out of scope:** the website that embeds the widget, which is the administration's responsibility (see [below](#for-administrations-deploying-zautte)), and the wording of the answers, which a language model generates from the site's own pages.

## Current status

**Partially conformant** with WCAG 2.1 AA. The first audit (5 October 2026) combined automated checks with axe-core 4.10.2 in Chrome on every state of the widget, keyboard-only walkthroughs, checks at 320 px width, 200% zoom and reduced motion, contrast ratios computed from the CSS, and a review of the widget code. The API was simulated, so no data was sent anywhere.

axe found no violations in the colleague view of `dashboard.html` (login and inline chat) or in `come-funziona.html`. It found two in the floating widget of `pilot.html` and only contrast problems in the admin view of the dashboard. Automated tools catch only part of the barriers; most of the issues below come from the keyboard tests and the code review.

### Known issues

Severity: **high** blocks or seriously misleads keyboard or screen reader users; **medium** makes a task hard; **low** is a nuisance or affects only administrators.

| # | Problem | WCAG | Severity |
|---|---|---|---|
| 1 | When the floating panel is closed it is only transparent: its buttons, links and text box stay in the Tab order, so keyboard users land on invisible controls | 2.4.3, 2.4.7, 4.1.2 | High |
| 2 | The floating panel declares itself a modal dialog (`aria-modal="true"`) but does not keep focus inside: Tab leaves it while screen readers treat the rest of the page as inert | 4.1.2, 2.4.3 | High |
| 3 | Streamed answers rewrite the live message area at every fragment, without `aria-busy`: screen readers may read partial or repeated text (inferred from the code, not yet tried with a screen reader) | 4.1.3, 1.3.1 | High |
| 4 | The "thinking" indicator is three animated dots with no text: screen reader users hear nothing until the 10-second notice | 4.1.3 | Medium |
| 5 | On screens 480 px wide or less the full-screen panel covers the launcher button, which can still receive focus | 2.4.11 | Medium |
| 6 | English answers, and the English interface, are not marked with `lang="en"`, so screen readers read them with the Italian voice | 3.1.2 | Medium |
| 7 | 👍/👎 are named by their emoji (the Italian text is only a tooltip) and the selected one is shown only by a faint background, with no `aria-pressed` | 4.1.2, 1.4.1 | Medium |
| 8 | Login fields, the comment box and the link fields are labelled only by placeholder text, which disappears while typing | 3.3.2, 1.3.1 | Medium |
| 9 | Borders of the text box, form fields and 👍/👎 are too faint (1.3:1–1.6:1, 3:1 required) | 1.4.11 | Medium |
| 10 | Low contrast for the text box placeholder (2.2:1) and the waiting notice (2.7:1) | 1.4.3 | Medium |
| 11 | Low contrast for the hint text in `pilot.html` (3.5:1) | 1.4.3 | Medium |
| 12 | Grey texts in the admin view of the dashboard: 127 contrast failures | 1.4.3 | Low |
| 13 | Source links are 13 px tall and 2 px apart: small, crowded targets | 2.5.8 | Low |
| 14 | After a failed login the disabled button drops focus to the top of the page | 2.4.3 | Low |
| 15 | Animations ignore the "reduce motion" system setting | 2.3.3 (AAA) | Low |
| 16 | The login title is not a heading, and "Password dimenticata?" is a link that acts as a button | 1.3.1 | Low |
| 17 | The "Conversazione" label and the 👍/👎 tooltips stay in Italian in the English interface | 3.1.2 | Low |
| 18 | The "write a comment" error in the report form is not marked as an alert | 4.1.3 | Low |
| 19 | `come-funziona.html` says the chat works with keyboard and screen readers and asks users to report problems "to the Comune", with no contact or link to the accessibility statement | — | Low |

### What already works

- Buttons are real `<button>` elements with accessible names; the launcher reports whether the panel is open (`aria-expanded`).
- Escape closes the floating panel and puts focus back on the launcher. Focus moves to the right field when the chat opens, after login, after each answer and after a 👎.
- Login errors are announced (`role="alert"`); login fields use `type="email"` and `autocomplete`.
- The "IA" badge has a text alternative; source links use the document title as link text.
- Focus is visible on buttons, links and the text box.
- No horizontal scrolling at 320 px; at 200% zoom the chat stays usable, though cramped.
- Body text, user messages, sources and footer meet the 4.5:1 contrast ratio.
- Pages declare `lang="it"`, have meaningful titles and a main heading.

### Not tested yet

- Real screen readers: NVDA and JAWS on Windows, VoiceOver on macOS and iOS, TalkBack on Android.
- Real mobile devices and browser zoom (only emulated), forced colors / Windows high contrast, text spacing (1.4.12).
- Testing with people with disabilities.

## Roadmap

1. Fix the three high-severity issues (1–3) and the medium ones (4–11) in the widget.
2. Repeat the audit after each change to the widget, and add an automated axe-core check to CI.
3. Test with screen readers on desktop and mobile.
4. Test with people with disabilities, with the help of the administration.
5. Fix the remaining low-severity issues.

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
