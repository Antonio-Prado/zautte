<sub>[Zautte](../README.md) › [Documentation](README.md) › Frontend Widget</sub>

# Frontend Widget

`widget/chatbot-widget.js` — self-contained chat widget (injected JS + CSS, zero dependencies).

## Site Integration

Paste before `</body>` on all pages (or in the CMS template):

```html
<script>
  window.ChatbotConfig = {
    apiUrl:       'https://chatbot.myorg.com',
    primaryColor: '#003366',
    title:        'Zautte',
    subtitle:     'My Organization',
    position:     'right',
  };
</script>
<script src="https://chatbot.myorg.com/widget/chatbot-widget.js" defer></script>
```

The `widget/embed-snippet.html` file contains a ready-to-paste snippet with more options (`logoUrl`, `contactEmail`, `requireLogin`, `suggestions`, `infoUrl`, `privacyUrl`, `inline`).

## Configuration Options

| Option            | Default                              | Description |
|-------------------|--------------------------------------|-------------|
| `apiUrl`          | `'http://localhost:8000'`            | Backend API base URL (set it in production) |
| `primaryColor`    | `'#003366'`                          | Primary color (button, header, login) |
| `title`           | `'Assistente Virtuale'`              | Assistant name (header; login title "Accedi a …" / "Sign in to …") |
| `subtitle`        | `''`                                 | Subtitle in the panel header |
| `logoUrl`         | `''`                                 | Round header icon; a chat icon when empty |
| `position`        | `'right'`                            | `'right'` or `'left'` (floating button and panel) |
| `lang`            | from `navigator.language`            | Interface language: `'en'` = English, any other value = Italian |
| `welcomeIt` / `welcomeEn` | built-in text                | Welcome message; the AI notice is always shown below it |
| `suggestions`     | `[]`                                 | Suggested questions shown as chips under the welcome message |
| `contactEmail`    | `''`                                 | Adds a "report an error" email link to the AI notice and footer |
| `infoUrl`         | `''`                                 | "How it works" page linked in the AI notice and footer (e.g. `/widget/come-funziona.html`) |
| `privacyUrl`      | `''`                                 | Privacy notice linked in the AI notice and footer |
| `requireLogin`    | `false`                              | Show the email + password login before the chat (needs `AUTH_ENABLED=true`) |
| `loginSubtitle`   | `''`                                 | Optional text under the login title |
| `feedbackDetails` | `true`                               | After a 👎, show the comment/links form (`POST /feedback/detail`) |
| `inline`          | `null`                               | CSS selector or element: panel always open inside it, no floating or close button |
| `inlineHeight`    | `'min(640px, calc(100vh - 32px))'`   | Panel height in inline mode |
| `zIndex`          | `99999`                              | z-index of the button and panel |

## Features

- **SSE streaming** (`POST /chat/stream`): tokens arrive progressively and a cursor blinks during generation; if no token arrives within 10 seconds, a "Processing…" hint appears
- **Conversation history**: the last 3 turns (6 messages) are kept in memory and sent with each request; reset by **New conversation** (↻ in the header) and at login
- **Sources** under each answer, as links (title only when the URL is hidden for migrated domains)
- **Formatting**: the answer's Markdown becomes real HTML: paragraphs, headings (`#`/`##` → `h3`, `###` and deeper → `h4`), bulleted and numbered lists (nested too), quotes (`>`), separators (`---`) and `**bold**`; Markdown links, bare URLs and email addresses become links in a single pass (never nested); everything else is HTML-escaped
- **Suggested questions** (`suggestions`): chips under the welcome message, removed at the first question
- **AI transparency**: "IA" badge next to the title; an AI notice always shown under the welcome message; footer "AI-generated answers · Do not enter personal data in the chat · Experimental service", plus "How it works" (`infoUrl`), "Privacy" (`privacyUrl`) and "Report an error" (`contactEmail`)
- **Feedback**: 👍/👎 under each answer → `POST /feedback`; after a 👎 (with `feedbackDetails`) a form for a comment and up to 5 links → `POST /feedback/detail`
- **Login** (`requireLogin`): email + password form (`/auth/login`), "Forgot password?" (`/auth/forgot`), sign-out button in the header; the token is kept in `localStorage` and sent as `Authorization: Bearer`, checked with `/auth/me` at startup; a `401` returns to the login form
- **Host-page API**: a `zautte:auth` event on `document` with `detail: {loggedIn, name}`, and `window.ZautteChatbot.logout()`
- **Inline mode** (`inline`): panel embedded in the page, always open, no floating or close button
- **Accessibility** (target WCAG 2.1 AA, see [ACCESSIBILITY.md](../ACCESSIBILITY.md)): non-modal `role="dialog"` (a labelled region in `inline` mode) with `lang` set to the interface language, hidden with `visibility` when closed; `aria-expanded`/`aria-controls` on the open button; the conversation is a labelled region, not a live one, and a hidden `role="status"` region announces "preparing the answer", the long-wait notice, each finished answer once (in its own language, from `language` in the SSE `sources` event) and errors; 👍/👎 with names and `aria-pressed`; visible labels in the login and report forms and on the text box ("La tua domanda", with a hidden hint on Enter and Shift+Enter); focus kept in the text box while an answer is prepared and returned there after it; `×` and Esc (with focus in the chat) close the panel and return focus to the open button; `prefers-reduced-motion` stops animations
- **Mobile** (≤ 480 px): full-screen panel, open button hidden while the panel covers it, `font-size: 16px` on the input (prevents automatic zoom on iOS)

## Pages in `widget/`

- `dashboard.html`: colleagues see title, description and login, then the chat opens inside the page (`inline`). `dashboard.html#admin` asks for `ADMIN_API_KEY` and opens the admin view: service status, indexed content, activity, feedback, open 👎 with "Resolve", users, message log, token costs, crawl history and changelog
- `pilot.html`: pilot landing page with the floating widget
- `come-funziona.html`: public "How it works" page (AI transparency)
- `site-footer.js`: footer shared by the three pages, with the latest release and the last commit read from the public GitHub API (so each visitor's browser contacts `api.github.com`) and "Powered by" with `logo.png`

## Local Testing

The pages in `widget/` use `window.location.origin` as the API URL, so open them through the backend, not from disk: start it (`uvicorn api.main:app --port 8000 --reload`) and open `http://localhost:8000/widget/dashboard.html` (or `pilot.html`). Both pages set `requireLogin: true`: set `AUTH_ENABLED=true` and `AUTH_SECRET` in `.env` and create a user with `python -m scripts.adduser`.

---

← [Backend API](api.md) · [Documentation index](README.md) · [Operations](operations.md) →
