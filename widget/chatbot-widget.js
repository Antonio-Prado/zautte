/**
 * Zautte — RAG virtual assistant widget
 * Standalone widget (zero dependencies) to embed with a single <script>.
 *
 * Configuration (optional, before the <script> tag):
 *
 *   <script>
 *     window.ChatbotConfig = {
 *       apiUrl:        'https://chatbot.comune.example.it', // backend URL
 *       primaryColor:  '#003366',                           // primary colour
 *       title:         'Assistente Zautte',                  // chat title
 *       subtitle:      'Comune di ...',                     // subtitle
 *       welcomeIt:     'Ciao! Come posso aiutarti?',        // initial message IT
 *       welcomeEn:     'Hello! How can I help you?',        // initial message EN
 *       position:      'right',                             // 'right' | 'left'
 *       logoUrl:       '',                                  // logo URL (optional)
 *       contactEmail:  '',                                  // error-report email (optional)
 *       infoUrl:       '',                                  // "Come funziona" page (optional)
 *       loginSubtitle: '',                                  // text under the login title (optional)
 *       applyNote:     '',                                  // note under the "Apply" label of the login form (optional)
 *       privacyUrl:    '',                                  // privacy notice (optional)
 *       lang:          'it',                                // default language ('it' | 'en')
 *       inline:        '#chat-area',                        // panel inside the page (optional)
 *     };
 *   </script>
 *   <script src="chatbot-widget.js"></script>
 */

(function () {
  "use strict";

  // ---------------------------------------------------------------------------
  // Configuration
  // ---------------------------------------------------------------------------
  const cfg = Object.assign(
    {
      apiUrl: "http://localhost:8000",
      primaryColor: "#003366",
      title: "Assistente Virtuale",
      subtitle: "",
      welcomeIt:
        "Ciao! Sono l'assistente virtuale del sito, basato sull'intelligenza artificiale. " +
        "Posso aiutarti a trovare informazioni su servizi, orari, documenti e molto altro.",
      welcomeEn:
        "Hi! I'm the website's virtual assistant, powered by artificial intelligence. " +
        "I can help you find information about services, opening hours, documents and more.",
      position: "right",
      logoUrl: "",
      contactEmail: "",
      infoUrl: "",     // "Come funziona" page (transparency on the AI system)
      privacyUrl: "",  // personal data processing notice
      loginSubtitle: "",  // optional text under the login form title
      applyNote: "",      // optional small note under the "Apply" label of the login form
      zIndex: 99999,
      suggestions: [],
      requireLogin: false,   // true = requires login (pilot project with a restricted group)
      feedbackDetails: true, // after a 👎 asks for the reason and links to the correct pages
      // CSS selector (or element) in which to show the always-open panel, without the
      // floating button. With requireLogin, only the form is visible until login.
      inline: null,
      inlineHeight: "min(640px, calc(100vh - 32px))",
    },
    window.ChatbotConfig || {}
  );

  // Language detection: config override or browser
  const browserLang = cfg.lang ||
    (navigator.language || navigator.userLanguage || "it").slice(0, 2).toLowerCase();
  const isItalian = browserLang !== "en";

  const T = {
    inputLabel: isItalian ? "La tua domanda" : "Your question",
    placeholder: isItalian ? "Scrivi qui e premi Invio" : "Type here and press Enter",
    inputHelp: isItalian
      ? "Invio per inviare, Maiuscolo più Invio per andare a capo."
      : "Enter to send, Shift plus Enter for a new line.",
    send: isItalian ? "Invia" : "Send",
    sources: isItalian ? "Fonti:" : "Sources:",
    error: isItalian
      ? "Si è verificato un errore. Riprova tra qualche istante."
      : "An error occurred. Please try again.",
    // Daily question limit (n from the server; 0 if missing)
    dailyLimit: (n) => isItalian
      ? `Hai raggiunto il limite di ${n > 0 ? n + " domande" : "domande"} al giorno. Potrai farne altre da domani.`
      : `You have reached the limit of ${n > 0 ? n + " questions" : "questions"} per day. You can ask more tomorrow.`,
    // Too many answers at once (HTTP 429 with detail.error "busy")
    busy: isItalian
      ? "Sto ancora rispondendo alla domanda precedente, oppure il servizio è molto richiesto. Riprova tra qualche istante."
      : "I am still answering the previous question, or the service is very busy. Please try again in a moment.",
    noInfo: isItalian
      ? "Non ho trovato informazioni specifiche su questo argomento nella base di conoscenza."
      : "I could not find specific information on this topic in the knowledge base.",
    typing: isItalian ? "Sto elaborando la risposta..." : "Preparing the answer...",
    waitLong: isItalian
      ? "Sto elaborando... potrebbe richiedere qualche minuto."
      : "Processing... this may take a moment.",
    conversation: isItalian ? "Conversazione" : "Conversation",
    rate: isItalian ? "Valuta la risposta" : "Rate the answer",
    helpful: isItalian ? "Risposta utile" : "Helpful answer",
    notHelpful: isItalian ? "Risposta non utile" : "Unhelpful answer",
    link: isItalian ? "(link)" : "(link)",
    open: isItalian ? "Apri assistente" : "Open assistant",
    close: isItalian ? "Chiudi" : "Close",
    welcome: isItalian ? cfg.welcomeIt : cfg.welcomeEn,
    clearChat: isItalian ? "Nuova conversazione" : "New conversation",
    // Notice of interaction with an AI system (AI Act, art. 50): always under
    // the welcome message, even when welcomeIt/welcomeEn are customised.
    disclaimer: isItalian
      ? "🤖 **Stai dialogando con un sistema di intelligenza artificiale**, non con una persona. " +
        "Le risposte sono generate automaticamente a partire dalle pagine del sito e possono " +
        "essere incomplete o sbagliate: verifica le informazioni importanti nelle fonti indicate. " +
        "Servizio sperimentale." +
        (cfg.contactEmail ? " Per segnalare errori scrivi a " + cfg.contactEmail : "")
      : "🤖 **You are chatting with an artificial intelligence system**, not a person. " +
        "Answers are generated automatically from the website's pages and may be " +
        "incomplete or wrong: check important information in the sources provided. " +
        "Experimental service." +
        (cfg.contactEmail ? " To report errors write to " + cfg.contactEmail : ""),
    aiBadge: isItalian
      ? "Risposte generate dall'intelligenza artificiale"
      : "Answers generated by artificial intelligence",
    aiFooter: isItalian ? "🤖 Risposte generate dall'IA" : "🤖 AI-generated answers",
    infoLink: isItalian ? "Come funziona" : "How it works",
    privacyLink: isItalian ? "Privacy" : "Privacy",
    loginTitle: isItalian ? "Accedi a" : "Sign in to",  // followed by the widget title
    loginEmail: isItalian ? "Email" : "Email",
    loginPassword: isItalian ? "Password" : "Password",
    loginSubmit: isItalian ? "Entra" : "Sign in",
    loginLoading: isItalian ? "Accesso…" : "Signing in…",
    loginInvalid: isItalian ? "Email o password non validi." : "Invalid email or password.",
    loginErr: isItalian ? "Errore di accesso. Riprova." : "Sign-in error. Please try again.",
    logout: isItalian ? "Esci" : "Sign out",
    forgot: isItalian ? "Password dimenticata?" : "Forgot password?",
    apply: isItalian ? "Candidatura" : "Apply as a volunteer",
    forgotNoEmail: isItalian ? "Inserisci prima la tua email qui sopra." : "Enter your email above first.",
    forgotSent: isItalian
      ? "Se l'indirizzo è registrato, riceverai un'email con il link per scegliere una nuova password."
      : "If the address is registered, you'll receive an email with a link to choose a new password.",
    forgotSending: isItalian ? "Invio in corso…" : "Sending…",
    fbWhy: isItalian
      ? "Cosa non va nella risposta? Se conosci la pagina con l'informazione corretta, incolla qui il link."
      : "What's wrong with the answer? If you know the page with the correct information, paste its link here.",
    fbComment: isItalian ? "Commento (facoltativo)" : "Comment (optional)",
    fbUrlLabel: isItalian ? "Pagine con l'informazione corretta (facoltative)" : "Pages with the correct information (optional)",
    fbUrl: "https://…",
    fbAddUrl: isItalian ? "+ aggiungi un'altra pagina" : "+ add another page",
    fbSend: isItalian ? "Invia segnalazione" : "Send report",
    fbSkip: isItalian ? "Non ora" : "Not now",
    fbSending: isItalian ? "Invio…" : "Sending…",
    fbThanks: isItalian ? "Grazie! Segnalazione registrata." : "Thanks! Your report has been saved.",
    fbEmpty: isItalian ? "Scrivi un commento o inserisci almeno un link." : "Write a comment or enter at least one link.",
    fbError: isItalian ? "Invio non riuscito. Riprova." : "Sending failed. Please try again.",
  };

  // ---------------------------------------------------------------------------
  // CSS injection
  // ---------------------------------------------------------------------------
  const WIDGET_ID = "zautte-chatbot";
  const p = cfg.primaryColor;

  const css = `
    #${WIDGET_ID}-btn {
      position: fixed;
      bottom: 24px;
      ${cfg.position}: 24px;
      width: 56px;
      height: 56px;
      border-radius: 50%;
      background: ${p};
      color: #fff;
      border: none;
      cursor: pointer;
      box-shadow: 0 4px 16px rgba(0,0,0,0.25);
      display: flex;
      align-items: center;
      justify-content: center;
      z-index: ${cfg.zIndex};
      transition: transform 0.2s, box-shadow 0.2s;
    }
    #${WIDGET_ID}-btn:hover {
      transform: scale(1.08);
      box-shadow: 0 6px 20px rgba(0,0,0,0.32);
    }
    #${WIDGET_ID}-btn svg { pointer-events: none; }

    #${WIDGET_ID}-panel {
      position: fixed;
      bottom: 92px;
      ${cfg.position}: 16px;
      width: 380px;
      max-width: calc(100vw - 32px);
      height: 560px;
      max-height: calc(100vh - 120px);
      background: #fff;
      border-radius: 16px;
      box-shadow: 0 8px 40px rgba(0,0,0,0.18);
      display: flex;
      flex-direction: column;
      z-index: ${cfg.zIndex};
      overflow: hidden;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      font-size: 14px;
      opacity: 0;
      transform: translateY(16px) scale(0.97);
      pointer-events: none;
      /* Closed must also mean invisible: with opacity alone the controls
         stayed reachable with the Tab key (and read by screen readers). */
      visibility: hidden;
      transition: opacity 0.22s ease, transform 0.22s ease, visibility 0s linear 0.22s;
    }
    #${WIDGET_ID}-panel.open {
      opacity: 1;
      transform: translateY(0) scale(1);
      pointer-events: auto;
      visibility: visible;
      transition: opacity 0.22s ease, transform 0.22s ease, visibility 0s;
    }
    .${WIDGET_ID}-sr {
      position: absolute;
      width: 1px;
      height: 1px;
      padding: 0;
      margin: -1px;
      overflow: hidden;
      clip: rect(0 0 0 0);
      white-space: nowrap;
      border: 0;
    }

    /* Header */
    #${WIDGET_ID}-header {
      background: ${p};
      color: #fff;
      padding: 14px 16px;
      display: flex;
      align-items: center;
      gap: 10px;
      flex-shrink: 0;
    }
    #${WIDGET_ID}-header-icon {
      width: 36px;
      height: 36px;
      background: rgba(255,255,255,0.2);
      border-radius: 50%;
      display: flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
      overflow: hidden;
    }
    #${WIDGET_ID}-header-icon img {
      width: 100%;
      height: 100%;
      object-fit: contain;
      border-radius: 50%;
    }
    #${WIDGET_ID}-header-text { flex: 1; min-width: 0; }
    #${WIDGET_ID}-header-title {
      font-weight: 700;
      font-size: 15px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    #${WIDGET_ID}-header-subtitle {
      font-size: 11px;
      opacity: 0.8;
    }
    .${WIDGET_ID}-ai-badge {
      display: inline-block;
      margin-left: 6px;
      padding: 0 5px;
      border: 1px solid currentColor;
      border-radius: 4px;
      font-size: 10px;
      font-weight: 700;
      line-height: 15px;
      vertical-align: middle;
    }
    #${WIDGET_ID}-header-actions {
      display: flex;
      gap: 4px;
      flex-shrink: 0;
    }
    .${WIDGET_ID}-icon-btn {
      background: none;
      border: none;
      color: rgba(255,255,255,0.85);
      cursor: pointer;
      padding: 4px;
      border-radius: 6px;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: background 0.15s;
    }
    .${WIDGET_ID}-icon-btn:hover { background: rgba(255,255,255,0.15); }

    /* Messages */
    #${WIDGET_ID}-messages {
      flex: 1;
      overflow-y: auto;
      padding: 16px 12px;
      display: flex;
      flex-direction: column;
      gap: 12px;
      background: #f8f9fa;
    }
    #${WIDGET_ID}-messages::-webkit-scrollbar { width: 4px; }
    #${WIDGET_ID}-messages::-webkit-scrollbar-track { background: transparent; }
    #${WIDGET_ID}-messages::-webkit-scrollbar-thumb {
      background: #ccc;
      border-radius: 4px;
    }

    .${WIDGET_ID}-msg {
      display: flex;
      flex-direction: column;
      max-width: 88%;
    }
    .${WIDGET_ID}-msg.user { align-self: flex-end; align-items: flex-end; }
    .${WIDGET_ID}-msg.bot  { align-self: flex-start; align-items: flex-start; }

    .${WIDGET_ID}-bubble {
      padding: 10px 14px;
      border-radius: 14px;
      line-height: 1.5;
      word-break: break-word;
    }
    .${WIDGET_ID}-bubble p { margin: 0 0 8px; }
    .${WIDGET_ID}-bubble h3,
    .${WIDGET_ID}-bubble h4 {
      font-size: 1em;
      font-weight: 700;
      line-height: 1.4;
      color: inherit;
      margin: 10px 0 4px;
    }
    .${WIDGET_ID}-bubble ul,
    .${WIDGET_ID}-bubble ol { margin: 4px 0 8px; padding-left: 20px; }
    .${WIDGET_ID}-bubble li { margin: 2px 0; }
    .${WIDGET_ID}-bubble li > ul,
    .${WIDGET_ID}-bubble li > ol { margin: 2px 0; }
    .${WIDGET_ID}-bubble blockquote {
      margin: 4px 0 8px;
      padding-left: 8px;
      border-left: 3px solid #c5cbe0;
    }
    .${WIDGET_ID}-bubble hr { border: none; border-top: 1px solid #ddd; margin: 6px 0; }
    .${WIDGET_ID}-bubble > :first-child { margin-top: 0; }
    .${WIDGET_ID}-bubble > :last-child { margin-bottom: 0; }
    .${WIDGET_ID}-msg.user .${WIDGET_ID}-bubble {
      background: ${p};
      color: #fff;
      border-bottom-right-radius: 4px;
    }
    .${WIDGET_ID}-msg.bot .${WIDGET_ID}-bubble {
      background: #fff;
      color: #222;
      border-bottom-left-radius: 4px;
      box-shadow: 0 1px 4px rgba(0,0,0,0.08);
    }
    .${WIDGET_ID}-bubble.streaming::after {
      content: "▋";
      display: inline-block;
      animation: ${WIDGET_ID}-blink 0.8s step-start infinite;
      color: #888;
      margin-left: 1px;
    }
    @keyframes ${WIDGET_ID}-blink {
      0%, 100% { opacity: 1; }
      50% { opacity: 0; }
    }

    /* Feedback */
    .${WIDGET_ID}-feedback {
      display: flex;
      gap: 6px;
      margin-top: 6px;
    }
    .${WIDGET_ID}-feedback button {
      background: none;
      border: 1px solid #8a8a8a;
      border-radius: 12px;
      padding: 2px 8px;
      font-size: 14px;
      cursor: pointer;
      transition: background 0.2s;
    }
    .${WIDGET_ID}-feedback button:hover { background: #f0f0f0; }
    /* Selection also shown by a thicker border, not just by colour */
    .${WIDGET_ID}-feedback button[aria-pressed="true"] {
      background: #e8f5e9;
      border-color: ${p};
      box-shadow: inset 0 0 0 1px ${p};
    }

    /* Report form after a 👎 */
    .${WIDGET_ID}-fbform {
      margin-top: 8px;
      padding: 10px;
      background: #fafafa;
      border: 1px solid #e5e5e5;
      border-radius: 10px;
      font-size: 13px;
      color: #444;
    }
    .${WIDGET_ID}-fbform p { margin: 0 0 6px; }
    .${WIDGET_ID}-fbform textarea,
    .${WIDGET_ID}-fbform input {
      width: 100%;
      box-sizing: border-box;
      border: 1px solid #8a8a8a;
      border-radius: 6px;
      padding: 6px 8px;
      font: inherit;
      font-size: 13px;
      margin-bottom: 6px;
      background: #fff;
      color: #333;
    }
    .${WIDGET_ID}-fbform textarea { resize: vertical; min-height: 52px; }
    .${WIDGET_ID}-fbform textarea:focus,
    .${WIDGET_ID}-fbform input:focus { outline: 2px solid ${p}; outline-offset: 1px; border-color: ${p}; }
    .${WIDGET_ID}-fbform label,
    .${WIDGET_ID}-fbform .${WIDGET_ID}-fbform-label {
      display: block;
      font-weight: 600;
      margin: 0 0 3px;
    }
    .${WIDGET_ID}-fbform-add {
      background: none;
      border: none;
      color: ${p};
      cursor: pointer;
      font-size: 12px;
      line-height: 14px;
      padding: 5px 0;
      margin: 0 0 4px;
    }
    .${WIDGET_ID}-fbform-actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
    .${WIDGET_ID}-fbform-actions button {
      border-radius: 6px;
      padding: 5px 12px;
      font-size: 13px;
      cursor: pointer;
      border: 1px solid #8a8a8a;
      background: #fff;
      color: #444;
    }
    .${WIDGET_ID}-fbform-actions button.primary { background: ${p}; color: #fff; border-color: ${p}; }
    .${WIDGET_ID}-fbform-actions button[aria-disabled="true"] { opacity: 0.6; cursor: default; }
    .${WIDGET_ID}-fbform-msg { font-size: 12px; }
    .${WIDGET_ID}-fbform-msg.err { color: #c62828; }
    .${WIDGET_ID}-fbform-thanks { margin-top: 8px; font-size: 13px; color: #2e7d32; }

    /* Sources */
    .${WIDGET_ID}-sources {
      margin-top: 6px;
      font-size: 11px;
      color: #666;
    }
    .${WIDGET_ID}-sources-label {
      font-weight: 600;
      margin-bottom: 2px;
      color: #555;
    }
    /* 24 px tall rows: targets large and spaced enough (WCAG 2.5.8) */
    .${WIDGET_ID}-sources a {
      display: block;
      color: ${p};
      text-decoration: none;
      line-height: 16px;
      padding: 4px 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .${WIDGET_ID}-sources a:hover { text-decoration: underline; }
    .${WIDGET_ID}-sources span {
      display: block;
      color: #666;
      line-height: 16px;
      padding: 4px 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    /* Typing indicator */
    .${WIDGET_ID}-typing {
      display: flex;
      align-items: center;
      gap: 4px;
      padding: 10px 14px;
      background: #fff;
      border-radius: 14px;
      border-bottom-left-radius: 4px;
      box-shadow: 0 1px 4px rgba(0,0,0,0.08);
      width: fit-content;
    }
    .${WIDGET_ID}-typing .${WIDGET_ID}-dot {
      width: 7px;
      height: 7px;
      background: #8a8a8a;
      border-radius: 50%;
      animation: ${WIDGET_ID}-bounce 1.2s infinite ease-in-out;
    }
    .${WIDGET_ID}-typing .${WIDGET_ID}-dot:nth-child(2) { animation-delay: 0.2s; }
    .${WIDGET_ID}-typing .${WIDGET_ID}-dot:nth-child(3) { animation-delay: 0.4s; }
    @keyframes ${WIDGET_ID}-bounce {
      0%, 60%, 100% { transform: translateY(0); }
      30% { transform: translateY(-6px); }
    }

    /* Input area */
    #${WIDGET_ID}-input-area {
      padding: 6px 12px 10px;
      border-top: 1px solid #e8e8e8;
      display: flex;
      flex-wrap: wrap;
      gap: 4px 8px;
      align-items: flex-end;
      background: #fff;
      flex-shrink: 0;
    }
    #${WIDGET_ID}-input-label {
      flex-basis: 100%;
      font-size: 11px;
      font-weight: 600;
      color: #555;
    }
    #${WIDGET_ID}-input {
      flex: 1;
      border: 1.5px solid #8a8a8a;
      border-radius: 10px;
      padding: 9px 12px;
      font-size: 14px;
      font-family: inherit;
      resize: none;
      outline: none;
      line-height: 1.4;
      max-height: 100px;
      overflow-y: auto;
      transition: border-color 0.15s;
      background: #fafafa;
    }
    #${WIDGET_ID}-input:focus {
      border-color: ${p};
      box-shadow: 0 0 0 1px ${p};
      background: #fff;
    }
    #${WIDGET_ID}-input::placeholder { color: #6b6b6b; }
    #${WIDGET_ID}-send {
      background: ${p};
      color: #fff;
      border: none;
      border-radius: 10px;
      width: 38px;
      height: 38px;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
      transition: opacity 0.15s;
    }
    #${WIDGET_ID}-send:disabled { opacity: 0.4; cursor: not-allowed; }
    #${WIDGET_ID}-send:not(:disabled):hover { opacity: 0.85; }

    /* GDPR note */
    #${WIDGET_ID}-footer {
      text-align: center;
      font-size: 10px;
      color: #666;
      padding: 4px 12px 8px;
      background: #fff;
    }

    /* Welcome disclaimer */
    .${WIDGET_ID}-disclaimer {
      font-size: 11px;
      color: #555;
      margin-top: 6px;
      line-height: 1.5;
    }
    .${WIDGET_ID}-disclaimer a { color: #555; }

    /* Suggested questions */
    .${WIDGET_ID}-suggestions {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      padding: 8px 12px 4px;
    }
    .${WIDGET_ID}-suggestion {
      background: #f0f4ff;
      border: 1px solid #c5d0f5;
      border-radius: 14px;
      padding: 5px 12px;
      font-size: 12px;
      color: #3a4a8a;
      cursor: pointer;
      transition: background 0.15s;
      text-align: left;
    }
    .${WIDGET_ID}-suggestion:hover { background: #dce5ff; }

    /* Long-wait indicator */
    .${WIDGET_ID}-wait-hint {
      font-size: 11px;
      color: #595959;
      text-align: center;
      padding: 4px 12px;
      font-style: italic;
    }

    /* Inline mode: panel inside the page, always open */
    #${WIDGET_ID}-panel.inline {
      position: relative;
      bottom: auto;
      left: auto;
      right: auto;
      width: 100%;
      max-width: 100%;
      height: ${cfg.inlineHeight};
      max-height: none;
      border-radius: 16px;
      z-index: auto;
      opacity: 1;
      transform: none;
      pointer-events: auto;
      visibility: visible;
      transition: none;
    }
    #${WIDGET_ID}-panel.inline.login { height: auto; max-width: 420px; margin: 0 auto; }
    #${WIDGET_ID}-panel.inline.login #${WIDGET_ID}-header,
    #${WIDGET_ID}-panel.inline #${WIDGET_ID}-close { display: none; }

    /* Mobile */
    @media (max-width: 480px) {
      #${WIDGET_ID}-panel {
        bottom: 0;
        ${cfg.position}: 0;
        width: 100vw;
        max-width: 100vw;
        height: 100dvh;
        max-height: 100dvh;
        border-radius: 0;
      }
      #${WIDGET_ID}-btn {
        bottom: 16px;
        ${cfg.position}: 16px;
        width: 50px;
        height: 50px;
      }
      /* The panel covers the whole screen: the button underneath must not
         receive focus (close with the X or Esc) */
      #${WIDGET_ID}-btn[aria-expanded="true"] { visibility: hidden; }
      .${WIDGET_ID}-bubble {
        font-size: 14px;
      }
      #${WIDGET_ID}-input {
        font-size: 16px; /* avoids iOS automatic zoom */
      }
    }

    @media (prefers-reduced-motion: reduce) {
      #${WIDGET_ID}-btn, #${WIDGET_ID}-panel, #${WIDGET_ID}-panel.open, #${WIDGET_ID}-input, #${WIDGET_ID}-send,
      .${WIDGET_ID}-icon-btn, .${WIDGET_ID}-feedback button, .${WIDGET_ID}-suggestion {
        transition: none;
      }
      #${WIDGET_ID}-btn:hover { transform: none; }
      .${WIDGET_ID}-typing .${WIDGET_ID}-dot,
      .${WIDGET_ID}-bubble.streaming::after { animation: none; }
    }
  `;

  const styleEl = document.createElement("style");
  styleEl.textContent = css;
  document.head.appendChild(styleEl);

  // ---------------------------------------------------------------------------
  // Widget HTML
  // ---------------------------------------------------------------------------

  const iconChat = `<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>`;
  const iconClose = `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>`;
  const iconNew = `<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 .49-3.51"/></svg>`;
  const iconSend = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>`;

  const container = document.createElement("div");
  container.innerHTML = `
    <button id="${WIDGET_ID}-btn" aria-label="${T.open}" title="${T.open}" aria-controls="${WIDGET_ID}-panel">
      ${iconChat}
    </button>

    <div id="${WIDGET_ID}-panel" role="dialog" aria-label="${cfg.title}" lang="${isItalian ? "it" : "en"}">
      <div id="${WIDGET_ID}-header">
        <div id="${WIDGET_ID}-header-icon">
          ${cfg.logoUrl
            ? `<img src="${cfg.logoUrl}" alt="${cfg.title}" loading="lazy">`
            : iconChat}
        </div>
        <div id="${WIDGET_ID}-header-text">
          <div id="${WIDGET_ID}-header-title">${cfg.title}<span class="${WIDGET_ID}-ai-badge" title="${T.aiBadge}"><span aria-hidden="true">${isItalian ? "IA" : "AI"}</span><span class="${WIDGET_ID}-sr">${T.aiBadge}</span></span></div>
          <div id="${WIDGET_ID}-header-subtitle">${cfg.subtitle}</div>
        </div>
        <div id="${WIDGET_ID}-header-actions">
          <button class="${WIDGET_ID}-icon-btn" id="${WIDGET_ID}-new" title="${T.clearChat}" aria-label="${T.clearChat}">
            ${iconNew}
          </button>
          <button class="${WIDGET_ID}-icon-btn" id="${WIDGET_ID}-close" title="${T.close}" aria-label="${T.close}">
            ${iconClose}
          </button>
        </div>
      </div>

      <!-- The conversation is not a «live» region: while streaming it would be read
           again at every fragment. Announcements go through ${WIDGET_ID}-status. -->
      <div id="${WIDGET_ID}-messages" role="region" aria-label="${T.conversation}"></div>
      <div id="${WIDGET_ID}-status" class="${WIDGET_ID}-sr" role="status" aria-live="polite" aria-atomic="true"></div>

      <div id="${WIDGET_ID}-input-area">
        <label id="${WIDGET_ID}-input-label" for="${WIDGET_ID}-input">${T.inputLabel}</label>
        <span id="${WIDGET_ID}-input-help" class="${WIDGET_ID}-sr">${T.inputHelp}</span>
        <textarea
          id="${WIDGET_ID}-input"
          placeholder="${T.placeholder}"
          rows="1"
          aria-describedby="${WIDGET_ID}-input-help"
          maxlength="1000"
        ></textarea>
        <button id="${WIDGET_ID}-send" disabled aria-label="${T.send}">
          ${iconSend}
        </button>
      </div>

      <div id="${WIDGET_ID}-footer">
        ${T.aiFooter} &nbsp;·&nbsp;
        ${isItalian ? '🔒 Non inserire dati personali nella chat' : '🔒 Do not enter personal data in the chat'}
        &nbsp;·&nbsp; ${isItalian ? 'Servizio sperimentale' : 'Experimental service'}${cfg.contactEmail ? ` &nbsp;·&nbsp; <a href="mailto:${cfg.contactEmail}" style="color:inherit">${isItalian ? 'Segnala un errore' : 'Report an error'}</a>` : ""}${infoLinksHtml(" &nbsp;·&nbsp; ")}
      </div>
    </div>
  `;
  const inlineHost = cfg.inline
    ? (typeof cfg.inline === "string" ? document.querySelector(cfg.inline) : cfg.inline)
    : null;
  if (inlineHost) container.style.height = "100%";  // for a percentage inlineHeight
  (inlineHost || document.body).appendChild(container);

  // ---------------------------------------------------------------------------
  // DOM references
  // ---------------------------------------------------------------------------
  const btnToggle  = document.getElementById(`${WIDGET_ID}-btn`);
  const panel      = document.getElementById(`${WIDGET_ID}-panel`);
  const messages   = document.getElementById(`${WIDGET_ID}-messages`);
  const inputEl    = document.getElementById(`${WIDGET_ID}-input`);
  const sendBtn    = document.getElementById(`${WIDGET_ID}-send`);
  const closeBtn   = document.getElementById(`${WIDGET_ID}-close`);
  const newBtn     = document.getElementById(`${WIDGET_ID}-new`);
  const statusEl   = document.getElementById(`${WIDGET_ID}-status`);

  if (inlineHost) {
    panel.classList.add("inline");
    panel.setAttribute("role", "region");  // part of the page, not a window
    btnToggle.style.display = "none";
  }

  let isOpen = false;
  let isLoading = false;
  let conversationId = "";  // given by the server with each answer: it keeps the last turns itself

  // ---------------------------------------------------------------------------
  // Authentication (pilot project with a restricted group)
  // ---------------------------------------------------------------------------
  const AUTH_TOKEN_KEY = "zautte_token";
  const AUTH_NAME_KEY = "zautte_name";
  let authToken = null;
  let authName = null;
  try {
    authToken = localStorage.getItem(AUTH_TOKEN_KEY);
    authName = localStorage.getItem(AUTH_NAME_KEY);
  } catch (_) {}

  /** Adds the Authorization header if there is a token. */
  function authHeaders(base) {
    const h = Object.assign({}, base || {});
    if (authToken) h["Authorization"] = "Bearer " + authToken;
    return h;
  }

  function setAuth(token, name) {
    authToken = token || null;
    authName = name || "";
    try {
      if (token) {
        localStorage.setItem(AUTH_TOKEN_KEY, token);
        localStorage.setItem(AUTH_NAME_KEY, authName);
      } else {
        localStorage.removeItem(AUTH_TOKEN_KEY);
        localStorage.removeItem(AUTH_NAME_KEY);
      }
    } catch (_) {}
    emitAuth();
  }

  /** Notifies the host page of the login state (`zautte:auth` event). */
  function emitAuth() {
    document.dispatchEvent(new CustomEvent("zautte:auth", {
      detail: { loggedIn: !!authToken, name: authName || "" },
    }));
  }

  // References to the areas the login replaces
  const inputArea = document.getElementById(`${WIDGET_ID}-input-area`);
  const footerEl = document.getElementById(`${WIDGET_ID}-footer`);
  const headerActions = document.getElementById(`${WIDGET_ID}-header-actions`);

  // Login form (created once, shown only when needed)
  const loginEl = document.createElement("form");
  loginEl.id = `${WIDGET_ID}-login`;
  loginEl.style.cssText =
    "display:none;flex:1;flex-direction:column;gap:12px;padding:24px;justify-content:center;box-sizing:border-box;";
  const fieldStyle =
    "padding:11px 12px;border:1px solid #8a8a8a;border-radius:8px;font-size:14px;width:100%;box-sizing:border-box;";
  const labelStyle = "display:block;font-size:13px;font-weight:600;color:#333;margin-bottom:4px;";
  loginEl.innerHTML = `
    <h2 style="margin:0;font-weight:600;font-size:16px;color:${p};text-align:center;">${T.loginTitle} ${cfg.title}</h2>
    ${cfg.loginSubtitle ? `<div style="font-size:13px;opacity:.75;line-height:1.4;text-align:center;">${escapeHtml(cfg.loginSubtitle)}</div>` : ""}
    <div>
      <label for="${WIDGET_ID}-login-email" style="${labelStyle}">${T.loginEmail}</label>
      <input type="email" id="${WIDGET_ID}-login-email" autocomplete="username" required style="${fieldStyle}">
    </div>
    <div>
      <label for="${WIDGET_ID}-login-pw" style="${labelStyle}">${T.loginPassword}</label>
      <input type="password" id="${WIDGET_ID}-login-pw" autocomplete="current-password" required style="${fieldStyle}">
    </div>
    <div id="${WIDGET_ID}-login-error" role="alert"
      style="display:none;color:#c0392b;font-size:13px;"></div>
    <button type="submit" id="${WIDGET_ID}-login-submit"
      style="padding:11px 12px;border:none;border-radius:8px;background:${p};color:#fff;font-size:14px;font-weight:600;cursor:pointer;">
      ${T.loginSubmit}
    </button>
    <div style="display:flex;justify-content:center;align-items:center;gap:16px;flex-wrap:wrap;">
      <button type="button" id="${WIDGET_ID}-forgot"
        style="background:none;border:none;padding:4px;font:inherit;font-size:13px;color:${p};text-align:center;text-decoration:underline;cursor:pointer;">
        ${T.forgot}
      </button>
      <span id="${WIDGET_ID}-apply" style="padding:4px;font-size:13px;color:${p};">${T.apply}</span>
    </div>
    <div id="${WIDGET_ID}-apply-note" style="display:none;margin-top:-6px;text-align:center;font-size:12px;line-height:1.35;color:#595959;"></div>`;
  panel.appendChild(loginEl);
  if (cfg.applyNote) {
    const noteEl = loginEl.querySelector(`#${WIDGET_ID}-apply-note`);
    noteEl.textContent = cfg.applyNote;
    noteEl.style.display = "";
  }

  // Logout button (in the header, visible only when logged in)
  const logoutBtn = document.createElement("button");
  logoutBtn.className = `${WIDGET_ID}-icon-btn`;
  logoutBtn.id = `${WIDGET_ID}-logout`;
  logoutBtn.title = T.logout;
  logoutBtn.setAttribute("aria-label", T.logout);
  logoutBtn.style.display = "none";
  logoutBtn.innerHTML =
    '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/>' +
    '<polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>';
  function logout() {
    // Ask the server to revoke the token (best effort: the local sign-out does not wait)
    if (authToken) {
      try {
        fetch(`${cfg.apiUrl}/auth/logout`, {
          method: "POST", headers: authHeaders(), keepalive: true,
        }).catch(() => {});
      } catch (_) {}
    }
    setAuth(null, null);
    updateHeaderUser();
    showLogin();
  }
  logoutBtn.addEventListener("click", logout);
  if (headerActions) headerActions.insertBefore(logoutBtn, headerActions.firstChild);

  function updateHeaderUser() {
    logoutBtn.style.display = (cfg.requireLogin && authToken) ? "" : "none";
  }

  function showLogin() {
    panel.classList.add("login");
    messages.style.display = "none";
    if (inputArea) inputArea.style.display = "none";
    if (footerEl) footerEl.style.display = "none";
    loginEl.style.display = "flex";
    const emailInput = document.getElementById(`${WIDGET_ID}-login-email`);
    if (emailInput) setTimeout(() => emailInput.focus({ preventScroll: true }), 60);
  }

  function showChat() {
    panel.classList.remove("login");
    loginEl.style.display = "none";
    messages.style.display = "";
    if (inputArea) inputArea.style.display = "";
    if (footerEl) footerEl.style.display = "";
  }

  // Login submit handling. The button is not disabled (a disabled button
  // loses focus): a second submit is simply ignored.
  let loggingIn = false;
  loginEl.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (loggingIn) return;
    const emailInput = document.getElementById(`${WIDGET_ID}-login-email`);
    const pwInput = document.getElementById(`${WIDGET_ID}-login-pw`);
    const errEl = document.getElementById(`${WIDGET_ID}-login-error`);
    const submitBtn = document.getElementById(`${WIDGET_ID}-login-submit`);
    errEl.style.display = "none";
    errEl.style.color = "#c0392b";
    const email = (emailInput.value || "").trim();
    const password = pwInput.value || "";
    if (!email || !password) return;
    loggingIn = true;
    submitBtn.setAttribute("aria-disabled", "true");
    submitBtn.textContent = T.loginLoading;
    try {
      const resp = await fetch(`${cfg.apiUrl}/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      if (!resp.ok) {
        errEl.textContent = resp.status === 401 ? T.loginInvalid : T.loginErr;
        errEl.style.display = "block";
        return;
      }
      const data = await resp.json();
      setAuth(data.token, data.name);
      pwInput.value = "";
      updateHeaderUser();
      showChat();
      clearChat();
      inputEl.focus({ preventScroll: true });
    } catch (_) {
      errEl.textContent = T.loginErr;
      errEl.style.display = "block";
    } finally {
      loggingIn = false;
      submitBtn.removeAttribute("aria-disabled");
      submitBtn.textContent = T.loginSubmit;
    }
  });

  // "Password dimenticata?" — sends a reset request for the entered email.
  const _forgotLink = document.getElementById(`${WIDGET_ID}-forgot`);
  let forgotSending = false;
  if (_forgotLink) _forgotLink.addEventListener("click", async (e) => {
    e.preventDefault();
    if (forgotSending) return;
    const emailInput = document.getElementById(`${WIDGET_ID}-login-email`);
    const errEl = document.getElementById(`${WIDGET_ID}-login-error`);
    const email = ((emailInput && emailInput.value) || "").trim();
    if (!email) {
      errEl.style.color = "#c0392b";
      errEl.textContent = T.forgotNoEmail;
      errEl.style.display = "block";
      if (emailInput) emailInput.focus();
      return;
    }
    errEl.style.display = "none";
    const prev = _forgotLink.textContent;
    forgotSending = true;
    _forgotLink.textContent = T.forgotSending;
    try {
      await fetch(`${cfg.apiUrl}/auth/forgot`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email }),
      });
    } catch (_) {}
    // Always a generic message (anti-enumeration, like the backend).
    errEl.style.color = "#2e7d32";
    errEl.textContent = T.forgotSent;
    errEl.style.display = "block";
    _forgotLink.textContent = prev;
    forgotSending = false;
  });

  // ---------------------------------------------------------------------------
  // Utilities
  // ---------------------------------------------------------------------------

  function scrollToBottom() {
    messages.scrollTop = messages.scrollHeight;
  }

  /** Announces a text to screen readers (hidden status region). Clearing it
   *  first makes an announcement identical to the previous one be repeated. `lang` applies only
   *  to `text`; `suffix` (e.g. «Fonti: 2») stays in the interface language.
   *  After a few seconds the region is emptied: the screen reader has already taken the
   *  text, and whoever explores the chat does not meet a second copy of the answer
   *  (verified with VoiceOver). */
  let announceTimer = null;
  let announceClear = null;
  function announce(text, lang, suffix) {
    clearTimeout(announceTimer);
    clearTimeout(announceClear);
    statusEl.textContent = "";
    announceTimer = setTimeout(() => {
      const main = document.createElement("span");
      if (lang) main.setAttribute("lang", lang);
      main.textContent = text;
      statusEl.appendChild(main);
      if (suffix) statusEl.appendChild(document.createTextNode("\n" + suffix));
      announceClear = setTimeout(() => { statusEl.textContent = ""; }, 5000);
    }, 100);
  }

  /** Text to read aloud for an answer: without markdown symbols and without URLs
   *  (spelled letter by letter they would be unreadable; links stay in the message). */
  function plainText(str) {
    return str
      .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, "$1")
      .replace(/https?:\/\/[^\s)\]]+/g, T.link)
      .replace(/^[ \t]*(?:-{3,}|\*{3,}|_{3,})[ \t]*$/gm, "")
      .replace(/^[ \t]*#{1,6}[ \t]+/gm, "")
      .replace(/^[ \t]*>[ \t]?/gm, "")
      .replace(/^[ \t]*[-*•][ \t]+/gm, "")
      .replace(/\*\*(.+?)\*\*/g, "$1")
      .replace(/\n{2,}/g, "\n")
      .trim();
  }

  function escapeHtml(str) {
    return str
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  /** Links to "Come funziona" and to the privacy notice, only if configured.
   *  `lead` precedes the first link (default: the same separator). */
  function infoLinksHtml(sep, lead) {
    const links = [];
    const a = (url, label) =>
      `<a href="${escapeHtml(url)}" target="_blank" rel="noopener" style="color:inherit">${label}</a>`;
    if (cfg.infoUrl) links.push(a(cfg.infoUrl, T.infoLink));
    if (cfg.privacyUrl) links.push(a(cfg.privacyUrl, T.privacyLink));
    if (!links.length) return "";
    return (lead === undefined ? sep : lead) + links.join(sep);
  }

  // Links, URLs and emails in a single pass: applied one after the other, the bare-URL
  // rule found the URL again inside the href just created by a markdown link
  // [text](url) and produced an <a> inside another (broken link).
  const INLINE_RE = new RegExp([
    /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/.source,          // [text](url)
    /\[(https?:\/\/[^\]\s]+)\]/.source,                     // [url]
    // Bare URLs: &amp; is allowed (encoded & in parameters), stop
    // at &quot;/&gt;/&lt; (entities that do not belong to the URL)
    /(https?:\/\/(?:[^\s<>"&]|&amp;)+)/.source,
    /([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})/.source,
  ].join("|"), "g");

  /** Hosts the current answer may link to, sent by the server before the first token
   *  (null = unknown, an older server: every http(s) link is allowed). */
  let linkHosts = null;

  function hostOf(url) {
    try { return new URL(url.replace(/&amp;/g, "&")).hostname.toLowerCase().replace(/\.$/, ""); }
    catch (e) { return ""; }
  }

  function linkAllowed(url) {
    if (linkHosts === null) return true;
    const h = hostOf(url);
    return !!h && linkHosts.some((a) => h === a || h.endsWith("." + a));
  }

  function linkHtml(url, text) {
    return `<a href="${url}" target="_blank" rel="noopener" style="color:inherit;text-decoration:underline">${text}</a>`;
  }

  /** Formatting of a line (already escaped text): links, URLs, emails, bold. */
  function formatInline(html) {
    return html
      .replace(INLINE_RE, (m, mdText, mdUrl, brUrl, url, email) => {
        if (mdUrl) {
          if (!linkAllowed(mdUrl)) return `${mdText} (${hostOf(mdUrl)})`;   // text only, the host in clear
          // a text that looks like an address but leads elsewhere: show the real address
          if (/^(?:https?:\/\/|www\.)/i.test(mdText) && hostOf(mdText.replace(/^www\./i, "https://www.")) !== hostOf(mdUrl)) {
            return linkHtml(mdUrl, mdUrl);
          }
          return linkHtml(mdUrl, mdText);
        }
        if (brUrl) return linkAllowed(brUrl) ? linkHtml(brUrl, brUrl) : brUrl;
        if (url) {
          const tail = (url.match(/[.,;:!?]+$/) || [""])[0];   // trailing punctuation outside the link
          const clean = url.slice(0, url.length - tail.length);
          return (linkAllowed(clean) ? linkHtml(clean, clean) : clean) + tail;
        }
        return `<a href="mailto:${email}" style="color:inherit">${email}</a>`;
      })
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  }

  /**
   * Essential markdown of the answers into real HTML: paragraphs, headings (# and ## →
   * h3, ### and beyond → h4), bulleted and numbered lists (also nested), quotes
   * and separator lines. Screen readers thus pick up the structure.
   * A single-paragraph text stays inline, without <p>.
   */
  function formatText(str) {
    const lines = escapeHtml(str)
      // Removes raw HTML attributes that the LLM may append to URLs
      // (e.g. ...comunali&quot; target=&quot;_blank&quot; rel=...)
      .replace(/&quot;\s+(?:target|rel|style|class)=[^\n<]*/g, "")
      .split("\n");
    const out = [];
    let para = [];
    let quote = [];
    const lists = [];   // open lists, outermost first: {tag, indent}; each has an open <li>

    const flushPara = () => {
      if (para.length) out.push(`<p>${para.map(formatInline).join("<br>")}</p>`);
      para = [];
    };
    const flushQuote = () => {
      if (quote.length) out.push(`<blockquote>${quote.map(formatInline).join("<br>")}</blockquote>`);
      quote = [];
    };
    const closeLists = (downTo = 0) => {
      while (lists.length > downTo) out.push(`</li></${lists.pop().tag}>`);
    };
    const closeAll = () => { flushPara(); flushQuote(); closeLists(); };

    for (const line of lines) {
      const indent = line.match(/^[ \t]*/)[0].replace(/\t/g, "    ").length;
      let m;
      if (!line.trim()) {
        // blank line: closes paragraph and quote; a list may continue
        flushPara();
        flushQuote();
      } else if (/^[ \t]*(?:-{3,}|\*{3,}|_{3,})[ \t]*$/.test(line)) {
        closeAll();
        out.push("<hr>");
      } else if ((m = line.match(/^[ \t]*(#{1,6})[ \t]+(.+?)[ \t#]*$/))) {
        closeAll();
        const tag = m[1].length <= 2 ? "h3" : "h4";
        out.push(`<${tag}>${formatInline(m[2])}</${tag}>`);
      } else if ((m = line.match(/^[ \t]*&gt;[ \t]?(.*)$/))) {
        flushPara();
        closeLists();
        quote.push(m[1]);
      } else if ((m = line.match(/^[ \t]*(?:([-*•])|(\d{1,3})[.)])[ \t]+(.*)$/))) {
        flushPara();
        flushQuote();
        const tag = m[1] ? "ul" : "ol";
        while (lists.length && lists[lists.length - 1].indent > indent) closeLists(lists.length - 1);
        const top = lists[lists.length - 1];
        if (top && top.indent === indent && top.tag !== tag) closeLists(lists.length - 1);
        const cur = lists[lists.length - 1];
        if (cur && cur.indent === indent) {
          out.push(`</li><li>${formatInline(m[3])}`);
        } else {
          const start = tag === "ol" && m[2] !== "1" ? ` start="${parseInt(m[2], 10)}"` : "";
          out.push(`<${tag}${start}><li>${formatInline(m[3])}`);
          lists.push({ tag, indent });
        }
      } else if (lists.length && indent >= 2) {
        out.push(`<br>${formatInline(line.trim())}`);   // continuation of the list item
      } else {
        flushQuote();
        closeLists();
        para.push(line);
      }
    }
    closeAll();
    if (out.length === 1 && out[0].startsWith("<p>")) return out[0].slice(3, -4);
    return out.join("");
  }

  // Renders a source: clickable link if it has a URL, otherwise just the title as
  // text (document on a migrated domain, whose link is no longer valid).
  function renderSource(s) {
    let el;
    if (s.url && /^https?:\/\//i.test(s.url)) {   // never a javascript: or file: link
      el = document.createElement("a");
      el.href = s.url;
      el.target = "_blank";
      el.rel = "noopener noreferrer";
      el.title = s.url;
    } else {
      el = document.createElement("span");
    }
    el.textContent = s.title || s.url || "";
    return el;
  }

  function addMessage(role, text, sources) {
    const wrap = document.createElement("div");
    wrap.className = `${WIDGET_ID}-msg ${role}`;

    const bubble = document.createElement("div");
    bubble.className = `${WIDGET_ID}-bubble`;
    bubble.innerHTML = formatText(text);
    wrap.appendChild(bubble);

    if (sources && sources.length > 0) {
      const srcDiv = document.createElement("div");
      srcDiv.className = `${WIDGET_ID}-sources`;
      const label = document.createElement("div");
      label.className = `${WIDGET_ID}-sources-label`;
      label.textContent = T.sources;
      srcDiv.appendChild(label);
      sources.forEach((s) => {
        srcDiv.appendChild(renderSource(s));
      });
      wrap.appendChild(srcDiv);
    }

    messages.appendChild(wrap);
    scrollToBottom();
    return bubble; // returns the bubble for streaming updates
  }

  function addTypingIndicator() {
    const wrap = document.createElement("div");
    wrap.className = `${WIDGET_ID}-msg bot`;
    wrap.id = `${WIDGET_ID}-typing`;
    const indicator = document.createElement("div");
    indicator.className = `${WIDGET_ID}-typing`;
    const dot = `<span class="${WIDGET_ID}-dot" aria-hidden="true"></span>`;
    indicator.innerHTML = dot + dot + dot + `<span class="${WIDGET_ID}-sr">${T.typing}</span>`;
    wrap.appendChild(indicator);
    messages.appendChild(wrap);
    scrollToBottom();
    return wrap;
  }

  function removeTypingIndicator() {
    const el = document.getElementById(`${WIDGET_ID}-typing`);
    if (el) el.remove();
  }

  function setLoading(val) {
    isLoading = val;
    sendBtn.disabled = val || inputEl.value.trim() === "";
    // readOnly and not disabled: a disabled field loses focus
    inputEl.readOnly = val;
    if (val) inputEl.setAttribute("aria-busy", "true"); else inputEl.removeAttribute("aria-busy");
  }

  function autoResizeInput() {
    inputEl.style.height = "auto";
    inputEl.style.height = Math.min(inputEl.scrollHeight, 100) + "px";
  }

  // ---------------------------------------------------------------------------
  // Open / close
  // ---------------------------------------------------------------------------

  function openPanel() {
    isOpen = true;
    panel.classList.add("open");
    btnToggle.setAttribute("aria-label", T.close);
    btnToggle.setAttribute("aria-expanded", "true");
    btnToggle.innerHTML = iconClose;
    updateHeaderUser();
    if (cfg.requireLogin && !authToken) {
      showLogin();
    } else {
      showChat();
      setTimeout(() => inputEl.focus({ preventScroll: true }), 100);
    }
  }

  function closePanel() {
    if (inlineHost) return;  // in inline mode the panel always stays open
    isOpen = false;
    panel.classList.remove("open");
    btnToggle.setAttribute("aria-label", T.open);
    btnToggle.setAttribute("aria-expanded", "false");
    btnToggle.innerHTML = iconChat;
    btnToggle.focus();  // restore focus to the open button
  }

  function showSuggestions() {
    if (!cfg.suggestions || !cfg.suggestions.length) return;
    const div = document.createElement("div");
    div.className = `${WIDGET_ID}-suggestions`;
    div.id = `${WIDGET_ID}-suggestions`;
    cfg.suggestions.forEach(text => {
      const btn = document.createElement("button");
      btn.className = `${WIDGET_ID}-suggestion`;
      btn.textContent = text;
      btn.addEventListener("click", () => {
        div.remove();
        inputEl.value = text;
        sendQuestion(text);
        inputEl.value = "";
      });
      div.appendChild(btn);
    });
    messages.appendChild(div);
    scrollToBottom();
  }

  function clearChat() {
    messages.innerHTML = "";
    conversationId = "";
    addMessage("bot", T.welcome, null);
    // Disclaimer under the welcome bubble, smaller font
    const disc = document.createElement("div");
    disc.className = `${WIDGET_ID}-disclaimer`;
    disc.innerHTML = formatText(T.disclaimer) + infoLinksHtml(" · ", "<br>");
    // Append it inside the wrapper of the message just created
    messages.lastElementChild.appendChild(disc);
    showSuggestions();
  }

  function addFeedback(wrap, question, answerText, rid) {
    const fb = document.createElement("div");
    fb.className = `${WIDGET_ID}-feedback`;
    fb.setAttribute("role", "group");
    fb.setAttribute("aria-label", T.rate);
    ["👍", "👎"].forEach((icon, i) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = icon;
      btn.title = i === 0 ? T.helpful : T.notHelpful;
      btn.setAttribute("aria-label", btn.title);
      btn.setAttribute("aria-pressed", "false");
      btn.addEventListener("click", () => {
        // A repeated click on the same thumb does not resend the vote (the server
        // counts only one vote per user and answer anyway).
        if (btn.getAttribute("aria-pressed") === "true") return;
        fb.querySelectorAll("button").forEach(b => b.setAttribute("aria-pressed", "false"));
        btn.setAttribute("aria-pressed", "true");
        removeFeedbackForm(wrap);
        const rating = i === 0 ? 1 : -1;
        fetch(`${cfg.apiUrl}/feedback`, {
          method: "POST",
          headers: authHeaders({ "Content-Type": "application/json" }),
          // rid: question identifier, so it can be deleted everywhere on request
          body: JSON.stringify({ question, answer: answerText, rating, rid: rid || undefined }),
        })
          .then(r => (r.ok ? r.json() : null))
          .then(data => {
            // The form appears only after a 👎 and only if the backend
            // returned the feedback id (earlier versions do not).
            if (rating === -1 && cfg.feedbackDetails && data && data.id) {
              showFeedbackForm(wrap, data.id);
            }
          })
          .catch(() => {});
      });
      fb.appendChild(btn);
    });
    wrap.appendChild(fb);
  }

  function removeFeedbackForm(wrap) {
    wrap.querySelectorAll(`.${WIDGET_ID}-fbform, .${WIDGET_ID}-fbform-thanks`)
      .forEach(el => el.remove());
  }

  /**
   * Form under the rejected answer: free comment + links to the pages
   * with the correct information. Replaces the email to the administrator:
   * the details end up in the dashboard next to the negative feedback.
   */
  let fbFormCount = 0;  // unique ids for labels of different forms

  function showFeedbackForm(wrap, feedbackId) {
    removeFeedbackForm(wrap);
    const MAX_URLS = 5;
    const uid = `${WIDGET_ID}-fb${++fbFormCount}`;
    const form = document.createElement("form");
    form.className = `${WIDGET_ID}-fbform`;

    const intro = document.createElement("p");
    intro.id = `${uid}-intro`;
    intro.textContent = T.fbWhy;
    form.appendChild(intro);

    const commentLabel = document.createElement("label");
    commentLabel.htmlFor = `${uid}-comment`;
    commentLabel.textContent = T.fbComment;
    form.appendChild(commentLabel);
    const comment = document.createElement("textarea");
    comment.id = `${uid}-comment`;
    comment.setAttribute("aria-describedby", intro.id);
    comment.maxLength = 2000;
    comment.rows = 2;
    form.appendChild(comment);

    const urlsLabel = document.createElement("div");
    urlsLabel.id = `${uid}-urls`;
    urlsLabel.className = `${WIDGET_ID}-fbform-label`;
    urlsLabel.textContent = T.fbUrlLabel;
    form.appendChild(urlsLabel);
    const urlsBox = document.createElement("div");
    form.appendChild(urlsBox);

    const addBtn = document.createElement("button");
    addBtn.type = "button";
    addBtn.className = `${WIDGET_ID}-fbform-add`;
    addBtn.textContent = T.fbAddUrl;

    function addUrlInput() {
      if (urlsBox.children.length >= MAX_URLS) return null;
      const input = document.createElement("input");
      input.type = "text";          // not "url": validation is done by the backend
      input.inputMode = "url";
      input.placeholder = T.fbUrl;
      input.setAttribute("aria-labelledby", urlsLabel.id);
      input.maxLength = 500;
      urlsBox.appendChild(input);
      if (urlsBox.children.length >= MAX_URLS) addBtn.style.display = "none";
      return input;
    }
    addBtn.addEventListener("click", () => {
      const el = addUrlInput();
      if (el) el.focus();
    });
    addUrlInput();
    form.appendChild(addBtn);

    const actions = document.createElement("div");
    actions.className = `${WIDGET_ID}-fbform-actions`;
    const sendBtn = document.createElement("button");
    sendBtn.type = "submit";
    sendBtn.className = "primary";
    sendBtn.textContent = T.fbSend;
    const skipBtn = document.createElement("button");
    skipBtn.type = "button";
    skipBtn.textContent = T.fbSkip;
    skipBtn.addEventListener("click", () => {
      form.remove();
      inputEl.focus();  // the button disappears with the form: focus must not be lost
    });
    const msg = document.createElement("span");
    msg.className = `${WIDGET_ID}-fbform-msg`;
    msg.setAttribute("role", "alert");
    actions.appendChild(sendBtn);
    actions.appendChild(skipBtn);
    actions.appendChild(msg);
    form.appendChild(actions);

    let sending = false;
    form.addEventListener("submit", (ev) => {
      ev.preventDefault();
      if (sending) return;
      const text = comment.value.trim();
      const urls = Array.from(urlsBox.querySelectorAll("input"))
        .map(el => el.value.trim())
        .filter(Boolean);
      if (!text && !urls.length) {
        msg.textContent = T.fbEmpty;
        msg.classList.add("err");
        return;
      }
      msg.textContent = "";
      msg.classList.remove("err");
      sending = true;
      sendBtn.setAttribute("aria-disabled", "true");
      sendBtn.textContent = T.fbSending;
      fetch(`${cfg.apiUrl}/feedback/detail`, {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ id: feedbackId, comment: text, urls }),
      })
        .then(r => {
          if (!r.ok) throw new Error("HTTP " + r.status);
        })
        .then(() => {
          const thanks = document.createElement("div");
          thanks.className = `${WIDGET_ID}-fbform-thanks`;
          thanks.textContent = T.fbThanks;
          form.replaceWith(thanks);
          scrollToBottom();
          announce(T.fbThanks);
          inputEl.focus();
        })
        .catch(() => {
          sending = false;
          sendBtn.removeAttribute("aria-disabled");
          sendBtn.textContent = T.fbSend;
          msg.textContent = T.fbError;
          msg.classList.add("err");
        });
    });

    wrap.appendChild(form);
    scrollToBottom();
    comment.focus();
  }

  // ---------------------------------------------------------------------------
  // Sending a question with SSE streaming
  // ---------------------------------------------------------------------------

  async function sendQuestion(question) {
    if (!question || isLoading) return;

    // Remove suggestions if present
    const suggestionsEl = document.getElementById(`${WIDGET_ID}-suggestions`);
    if (suggestionsEl) suggestionsEl.remove();

    addMessage("user", question, null);
    addTypingIndicator();
    setLoading(true);
    announce(T.typing);

    // Long-wait indicator after 10 seconds
    let waitHint = null;
    const waitTimer = setTimeout(() => {
      waitHint = document.createElement("div");
      waitHint.className = `${WIDGET_ID}-wait-hint`;
      waitHint.textContent = T.waitLong;
      messages.appendChild(waitHint);
      announce(T.waitLong);
      scrollToBottom();
    }, 10000);

    try {
      const resp = await fetch(`${cfg.apiUrl}/chat/stream`, {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ question, cid: conversationId || undefined }),
      });

      // Session expired/missing: go back to login without showing a generic error
      if (resp.status === 401) {
        setAuth(null, null);
        updateHeaderUser();
        removeTypingIndicator();
        clearTimeout(waitTimer);
        if (waitHint) { waitHint.remove(); waitHint = null; }
        setLoading(false);
        showLogin();
        return;
      }

      // Daily limit reached: clear message instead of the generic error
      if (resp.status === 429) {
        let limit = 0;
        let busy = false;
        try {
          const body = await resp.json();
          limit = Number(body && body.detail && body.detail.limit) || 0;
          busy = !!(body && body.detail && body.detail.error === "busy");
        } catch (e) { /* body is not JSON */ }
        const text = busy ? T.busy : T.dailyLimit(limit);
        removeTypingIndicator();
        addMessage("bot", text, null);
        announce(text);
        return;
      }

      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

      // Create the bot bubble — appended to the DOM only at the first token
      const wrap = document.createElement("div");
      wrap.className = `${WIDGET_ID}-msg bot`;
      const bubble = document.createElement("div");
      bubble.className = `${WIDGET_ID}-bubble`;
      wrap.appendChild(bubble);

      let fullText = "";
      let receivedSources = [];
      let receivedRid = "";
      let receivedCid = "";
      let receivedLang = "";
      let receivedBlocked = false;
      let firstToken = true;

      linkHosts = null;   // set again by the first event of this stream
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop(); // last incomplete line

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          try {
            const payload = JSON.parse(line.slice(6));

            if (payload.link_hosts !== undefined) {
              linkHosts = Array.isArray(payload.link_hosts) ? payload.link_hosts : null;
            } else if (payload.token !== undefined) {
              if (firstToken) {
                removeTypingIndicator();
                bubble.classList.add("streaming");
                messages.appendChild(wrap);
                firstToken = false;
                clearTimeout(waitTimer);
                if (waitHint) { waitHint.remove(); waitHint = null; }
              }
              fullText += payload.token;
              bubble.innerHTML = formatText(fullText);
              scrollToBottom();
            } else if (payload.sources) {
              receivedSources = payload.sources;
              receivedRid = payload.rid || "";
              receivedCid = payload.cid || "";
              receivedLang = payload.language || "";
              receivedBlocked = !!payload.blocked;
            } else if (payload.error) {
              // Server error: show message and set sentinel
              if (!fullText) {
                removeTypingIndicator();
                bubble.classList.remove("streaming");
                fullText = "\x00"; // sentinel: do not overwrite with noInfo
                bubble.textContent = T.error;
                messages.appendChild(wrap);
                scrollToBottom();
                announce(T.error);
              }
            } else if (payload.done) {
              bubble.classList.remove("streaming");
              // Answer in a language different from the interface: the screen reader
              // must read it with the right voice
              if (receivedLang) wrap.setAttribute("lang", receivedLang);
              // Add the sources under the bubble
              if (receivedSources.length > 0) {
                const srcDiv = document.createElement("div");
                srcDiv.className = `${WIDGET_ID}-sources`;
                const label = document.createElement("div");
                label.className = `${WIDGET_ID}-sources-label`;
                label.textContent = T.sources;
                srcDiv.appendChild(label);
                receivedSources.forEach((s) => {
                  srcDiv.appendChild(renderSource(s));
                });
                wrap.appendChild(srcDiv);
                scrollToBottom();
              }
            }
          } catch (_) {}
        }
      }

      if (!fullText || fullText === "\x00") {
        if (!fullText) {
          bubble.textContent = T.noInfo;
          messages.appendChild(wrap);
          scrollToBottom();
          announce(T.noInfo);
        }
        // fullText === "\x00" → wrap already in the DOM, error already shown
      } else {
        // The answer is announced only once, in full
        const n = receivedSources.length;
        announce(plainText(fullText), receivedLang, n ? `${T.sources} ${n}` : "");
        // Question discarded because it contains personal data: it does not enter the
        // history (must not be sent to the server again) and is not rated
        if (!receivedBlocked) {
          // The server keeps the last turns: here only the conversation id
          if (receivedCid) conversationId = receivedCid;
          // Add feedback buttons
          addFeedback(wrap, question, fullText, receivedRid);
        }
      }

    } catch (err) {
      removeTypingIndicator();
      addMessage("bot", T.error, null);
      announce(T.error);
      console.error("[Chatbot] Errore:", err);
    } finally {
      removeTypingIndicator(); // remove if no tokens arrived
      clearTimeout(waitTimer);
      if (waitHint) waitHint.remove();
      setLoading(false);
      scrollToBottom();
      inputEl.focus();  // accessibility: restore focus to the input
    }
  }

  // ---------------------------------------------------------------------------
  // Event listeners
  // ---------------------------------------------------------------------------

  btnToggle.addEventListener("click", () => (isOpen ? closePanel() : openPanel()));
  closeBtn.addEventListener("click", closePanel);
  newBtn.addEventListener("click", clearChat);

  inputEl.addEventListener("input", () => {
    autoResizeInput();
    sendBtn.disabled = isLoading || inputEl.value.trim() === "";
  });

  inputEl.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      const q = inputEl.value.trim();
      if (q && !isLoading) {
        inputEl.value = "";
        autoResizeInput();
        sendBtn.disabled = true;
        sendQuestion(q);
      }
    }
  });

  sendBtn.addEventListener("click", () => {
    const q = inputEl.value.trim();
    if (q && !isLoading) {
      inputEl.value = "";
      autoResizeInput();
      inputEl.focus();  // before disabling the button, which would lose focus
      sendBtn.disabled = true;
      sendQuestion(q);
    }
  });

  // Close with Escape, only if focus is in the chat (the chat is not modal:
  // Esc pressed elsewhere on the page does not concern it)
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && isOpen &&
        (panel.contains(document.activeElement) || document.activeElement === btnToggle)) {
      closePanel();
    }
  });

  // ---------------------------------------------------------------------------
  // Startup
  // ---------------------------------------------------------------------------
  btnToggle.setAttribute("aria-expanded", "false");
  clearChat();
  updateHeaderUser();
  if (inlineHost) openPanel();
  if (cfg.requireLogin) emitAuth();

  // Minimal API for the host page (e.g. an "Esci" button outside the widget).
  window.ZautteChatbot = { logout };

  // Token saved but expired or revoked: go back to login right away, instead of
  // finding out at the first question (which would be lost).
  if (cfg.requireLogin && authToken) {
    fetch(`${cfg.apiUrl}/auth/me`, { headers: authHeaders() })
      .then((r) => {
        if (r.status !== 401) return;
        setAuth(null, null);
        updateHeaderUser();
        if (isOpen) showLogin();
      })
      .catch(() => {});
  }

})();
