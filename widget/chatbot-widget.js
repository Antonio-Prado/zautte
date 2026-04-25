/**
 * Zautte — Widget assistente virtuale RAG
 * Widget autonomo (zero dipendenze) da embeddare con un singolo <script>.
 *
 * Configurazione (opzionale, prima del tag <script>):
 *
 *   <script>
 *     window.ChatbotConfig = {
 *       apiUrl:        'https://chatbot.comune.example.it', // URL del backend
 *       primaryColor:  '#003366',                           // colore principale
 *       title:         'Assistente Comunale',               // titolo chat
 *       subtitle:      'Comune di ...',                     // sottotitolo
 *       welcomeIt:     'Ciao! Come posso aiutarti?',        // messaggio iniziale IT
 *       welcomeEn:     'Hello! How can I help you?',        // messaggio iniziale EN
 *       position:      'right',                             // 'right' | 'left'
 *       logoUrl:       '',                                  // URL logo (opzionale)
 *       contactEmail:  '',                                  // email segnalazione errori (opzionale)
 *       lang:          'it',                                // lingua default ('it' | 'en')
 *     };
 *   </script>
 *   <script src="chatbot-widget.js"></script>
 */

(function () {
  "use strict";

  // ---------------------------------------------------------------------------
  // Configurazione
  // ---------------------------------------------------------------------------
  const cfg = Object.assign(
    {
      apiUrl: "http://localhost:8000",
      primaryColor: "#003366",
      secondaryColor: "#ffffff",
      title: "Assistente Virtuale",
      subtitle: "",
      welcomeIt:
        "Ciao! Sono l'assistente virtuale. " +
        "Posso aiutarti a trovare informazioni su servizi, orari, documenti e molto altro.",
      welcomeEn:
        "Hi! I'm the virtual assistant. " +
        "I can help you find information about services, opening hours, documents and more.",
      position: "right",
      logoUrl: "",
      contactEmail: "",
      zIndex: 99999,
      suggestions: [],
    },
    window.ChatbotConfig || {}
  );

  // Rilevamento lingua: config override oppure browser
  const browserLang = cfg.lang ||
    (navigator.language || navigator.userLanguage || "it").slice(0, 2).toLowerCase();
  const isItalian = browserLang !== "en";

  const T = {
    placeholder: isItalian
      ? "Scrivi la tua domanda..."
      : "Type your question...",
    send: isItalian ? "Invia" : "Send",
    sources: isItalian ? "Fonti:" : "Sources:",
    error: isItalian
      ? "Si è verificato un errore. Riprova tra qualche istante."
      : "An error occurred. Please try again.",
    noInfo: isItalian
      ? "Non ho trovato informazioni specifiche su questo argomento nella base di conoscenza."
      : "I could not find specific information on this topic in the knowledge base.",
    typing: isItalian ? "Sto elaborando..." : "Processing...",
    open: isItalian ? "Apri assistente" : "Open assistant",
    close: isItalian ? "Chiudi" : "Close",
    welcome: isItalian ? cfg.welcomeIt : cfg.welcomeEn,
    clearChat: isItalian ? "Nuova conversazione" : "New conversation",
    disclaimer: isItalian
      ? "⚠️ Servizio sperimentale — le risposte potrebbero essere incomplete o imprecise." +
        (cfg.contactEmail ? " Per segnalare errori scrivi a " + cfg.contactEmail : "")
      : "⚠️ Experimental service — answers may be incomplete or inaccurate." +
        (cfg.contactEmail ? " To report errors write to " + cfg.contactEmail : ""),
  };

  // ---------------------------------------------------------------------------
  // Iniezione CSS
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
      transition: opacity 0.22s ease, transform 0.22s ease;
    }
    #${WIDGET_ID}-panel.open {
      opacity: 1;
      transform: translateY(0) scale(1);
      pointer-events: auto;
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
      border: 1px solid #ddd;
      border-radius: 12px;
      padding: 2px 8px;
      font-size: 14px;
      cursor: pointer;
      transition: background 0.2s;
    }
    .${WIDGET_ID}-feedback button:hover { background: #f0f0f0; }
    .${WIDGET_ID}-feedback button.selected { background: #e8f5e9; border-color: #a5d6a7; }

    /* Fonti */
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
    .${WIDGET_ID}-sources a {
      display: block;
      color: ${p};
      text-decoration: none;
      margin-top: 2px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .${WIDGET_ID}-sources a:hover { text-decoration: underline; }

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
    .${WIDGET_ID}-typing span {
      width: 7px;
      height: 7px;
      background: #aaa;
      border-radius: 50%;
      animation: ${WIDGET_ID}-bounce 1.2s infinite ease-in-out;
    }
    .${WIDGET_ID}-typing span:nth-child(2) { animation-delay: 0.2s; }
    .${WIDGET_ID}-typing span:nth-child(3) { animation-delay: 0.4s; }
    @keyframes ${WIDGET_ID}-bounce {
      0%, 60%, 100% { transform: translateY(0); }
      30% { transform: translateY(-6px); }
    }

    /* Input area */
    #${WIDGET_ID}-input-area {
      padding: 10px 12px;
      border-top: 1px solid #e8e8e8;
      display: flex;
      gap: 8px;
      align-items: flex-end;
      background: #fff;
      flex-shrink: 0;
    }
    #${WIDGET_ID}-input {
      flex: 1;
      border: 1.5px solid #ddd;
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
      background: #fff;
    }
    #${WIDGET_ID}-input::placeholder { color: #aaa; }
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
      color: #aaa;
      padding: 4px 12px 8px;
      background: #fff;
    }

    /* Disclaimer benvenuto */
    .${WIDGET_ID}-disclaimer {
      font-size: 11px;
      color: #999;
      margin-top: 6px;
      line-height: 1.5;
    }
    .${WIDGET_ID}-disclaimer a { color: #999; }

    /* Domande suggerite */
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

    /* Indicatore attesa lunga */
    .${WIDGET_ID}-wait-hint {
      font-size: 11px;
      color: #999;
      text-align: center;
      padding: 4px 12px;
      font-style: italic;
    }

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
      .${WIDGET_ID}-bubble {
        font-size: 14px;
      }
      #${WIDGET_ID}-input {
        font-size: 16px; /* evita zoom automatico iOS */
      }
    }
  `;

  const styleEl = document.createElement("style");
  styleEl.textContent = css;
  document.head.appendChild(styleEl);

  // ---------------------------------------------------------------------------
  // HTML del widget
  // ---------------------------------------------------------------------------

  const iconChat = `<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>`;
  const iconClose = `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>`;
  const iconNew = `<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 .49-3.51"/></svg>`;
  const iconSend = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>`;

  const container = document.createElement("div");
  container.innerHTML = `
    <button id="${WIDGET_ID}-btn" aria-label="${T.open}" title="${T.open}">
      ${iconChat}
    </button>

    <div id="${WIDGET_ID}-panel" role="dialog" aria-label="${cfg.title}" aria-modal="true">
      <div id="${WIDGET_ID}-header">
        <div id="${WIDGET_ID}-header-icon">
          ${cfg.logoUrl
            ? `<img src="${cfg.logoUrl}" alt="${cfg.title}" loading="lazy">`
            : iconChat}
        </div>
        <div id="${WIDGET_ID}-header-text">
          <div id="${WIDGET_ID}-header-title">${cfg.title}</div>
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

      <div id="${WIDGET_ID}-messages" role="log" aria-live="polite" aria-label="Conversazione"></div>

      <div id="${WIDGET_ID}-input-area">
        <textarea
          id="${WIDGET_ID}-input"
          placeholder="${T.placeholder}"
          rows="1"
          aria-label="${T.placeholder}"
          maxlength="1000"
        ></textarea>
        <button id="${WIDGET_ID}-send" disabled aria-label="${T.send}">
          ${iconSend}
        </button>
      </div>

      <div id="${WIDGET_ID}-footer">
        ${isItalian ? '🔒 Non inserire dati personali nella chat' : '🔒 Do not enter personal data in the chat'}
        &nbsp;·&nbsp; ${isItalian ? 'Servizio sperimentale' : 'Experimental service'}${cfg.contactEmail ? ` &nbsp;·&nbsp; <a href="mailto:${cfg.contactEmail}" style="color:inherit">${isItalian ? 'Segnala un errore' : 'Report an error'}</a>` : ""}
      </div>
    </div>
  `;
  document.body.appendChild(container);

  // ---------------------------------------------------------------------------
  // Riferimenti DOM
  // ---------------------------------------------------------------------------
  const btnToggle  = document.getElementById(`${WIDGET_ID}-btn`);
  const panel      = document.getElementById(`${WIDGET_ID}-panel`);
  const messages   = document.getElementById(`${WIDGET_ID}-messages`);
  const inputEl    = document.getElementById(`${WIDGET_ID}-input`);
  const sendBtn    = document.getElementById(`${WIDGET_ID}-send`);
  const closeBtn   = document.getElementById(`${WIDGET_ID}-close`);
  const newBtn     = document.getElementById(`${WIDGET_ID}-new`);

  let isOpen = false;
  let isLoading = false;
  let conversationHistory = [];  // max 3 turni

  // ---------------------------------------------------------------------------
  // Utilità
  // ---------------------------------------------------------------------------

  function scrollToBottom() {
    messages.scrollTop = messages.scrollHeight;
  }

  function escapeHtml(str) {
    return str
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  /** Formattazione minimale: a capo → <br>, **testo** → <strong>, email → <a> */
  function formatText(str) {
    return escapeHtml(str)
      // Rimuove attributi HTML grezzi che l'LLM può accodare alle URL
      // (es: ...comunali&quot; target=&quot;_blank&quot; rel=...)
      .replace(/&quot;\s+(?:target|rel|style|class)=[^\n<]*/g, "")
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      // Markdown link: [testo](url)
      .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
               '<a href="$2" target="_blank" rel="noopener" style="color:inherit;text-decoration:underline">$1</a>')
      // URL tra parentesi quadre: [https://...]
      .replace(/\[(https?:\/\/[^\]\s]+)\]/g,
               '<a href="$1" target="_blank" rel="noopener" style="color:inherit;text-decoration:underline">$1</a>')
      // URL nude — &amp; è ammesso (encoding corretto di & nei query param),
      // ma ci si ferma a &quot;/&gt;/&lt; (entità HTML che non appartengono all'URL)
      .replace(/(https?:\/\/(?:[^\s<>"&]|&amp;)+)/g,
               '<a href="$1" target="_blank" rel="noopener" style="color:inherit;text-decoration:underline">$1</a>')
      .replace(/([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})/g,
               '<a href="mailto:$1" style="color:inherit">$1</a>')
      .replace(/\n/g, "<br>");
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
        const a = document.createElement("a");
        a.href = s.url;
        a.target = "_blank";
        a.rel = "noopener noreferrer";
        a.textContent = s.title || s.url;
        a.title = s.url;
        srcDiv.appendChild(a);
      });
      wrap.appendChild(srcDiv);
    }

    messages.appendChild(wrap);
    scrollToBottom();
    return bubble; // ritorna la bubble per aggiornamenti in streaming
  }

  function addTypingIndicator() {
    const wrap = document.createElement("div");
    wrap.className = `${WIDGET_ID}-msg bot`;
    wrap.id = `${WIDGET_ID}-typing`;
    const indicator = document.createElement("div");
    indicator.className = `${WIDGET_ID}-typing`;
    indicator.innerHTML = "<span></span><span></span><span></span>";
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
    inputEl.disabled = val;
  }

  function autoResizeInput() {
    inputEl.style.height = "auto";
    inputEl.style.height = Math.min(inputEl.scrollHeight, 100) + "px";
  }

  // ---------------------------------------------------------------------------
  // Apertura / chiusura
  // ---------------------------------------------------------------------------

  function openPanel() {
    isOpen = true;
    panel.classList.add("open");
    panel.removeAttribute("aria-hidden");
    btnToggle.setAttribute("aria-label", T.close);
    btnToggle.setAttribute("aria-expanded", "true");
    btnToggle.innerHTML = iconClose;
    setTimeout(() => inputEl.focus(), 100);
  }

  function closePanel() {
    isOpen = false;
    panel.classList.remove("open");
    panel.setAttribute("aria-hidden", "true");
    btnToggle.setAttribute("aria-label", T.open);
    btnToggle.setAttribute("aria-expanded", "false");
    btnToggle.innerHTML = iconChat;
    btnToggle.focus();  // rimetti focus sul pulsante di apertura
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
    conversationHistory = [];
    addMessage("bot", T.welcome, null);
    // Disclaimer sotto la bubble di benvenuto, font più piccolo
    const disc = document.createElement("div");
    disc.className = `${WIDGET_ID}-disclaimer`;
    disc.innerHTML = formatText(T.disclaimer);
    // Appendilo dentro il wrap del messaggio appena creato
    messages.lastElementChild.appendChild(disc);
    showSuggestions();
  }

  function addFeedback(wrap, question, answerText) {
    const fb = document.createElement("div");
    fb.className = `${WIDGET_ID}-feedback`;
    ["👍", "👎"].forEach((icon, i) => {
      const btn = document.createElement("button");
      btn.textContent = icon;
      btn.title = i === 0 ? "Risposta utile" : "Risposta non utile";
      btn.addEventListener("click", () => {
        fb.querySelectorAll("button").forEach(b => b.classList.remove("selected"));
        btn.classList.add("selected");
        fetch(`${cfg.apiUrl}/feedback`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            question,
            answer: answerText,
            rating: i === 0 ? 1 : -1,
          }),
        }).catch(() => {});
      });
      fb.appendChild(btn);
    });
    wrap.appendChild(fb);
  }

  // ---------------------------------------------------------------------------
  // Invio domanda con streaming SSE
  // ---------------------------------------------------------------------------

  async function sendQuestion(question) {
    if (!question || isLoading) return;

    // Rimuovi suggerimenti se presenti
    const suggestionsEl = document.getElementById(`${WIDGET_ID}-suggestions`);
    if (suggestionsEl) suggestionsEl.remove();

    addMessage("user", question, null);
    const typingEl = addTypingIndicator();
    setLoading(true);

    // Indicatore di attesa lunga dopo 10 secondi
    let waitHint = null;
    const waitTimer = setTimeout(() => {
      waitHint = document.createElement("div");
      waitHint.className = `${WIDGET_ID}-wait-hint`;
      waitHint.textContent = isItalian
        ? "Sto elaborando... potrebbe richiedere qualche minuto."
        : "Processing... this may take a moment.";
      messages.appendChild(waitHint);
      scrollToBottom();
    }, 10000);

    try {
      const resp = await fetch(`${cfg.apiUrl}/chat/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question, history: conversationHistory }),
      });

      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

      // Crea la bubble del bot — appendita al DOM solo al primo token
      const wrap = document.createElement("div");
      wrap.className = `${WIDGET_ID}-msg bot`;
      const bubble = document.createElement("div");
      bubble.className = `${WIDGET_ID}-bubble`;
      wrap.appendChild(bubble);

      let fullText = "";
      let receivedSources = [];
      let firstToken = true;

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop(); // ultima riga incompleta

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          try {
            const payload = JSON.parse(line.slice(6));

            if (payload.token !== undefined) {
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
            } else if (payload.error) {
              // Errore server: mostra messaggio e segna sentinel
              if (!fullText) {
                removeTypingIndicator();
                bubble.classList.remove("streaming");
                fullText = "\x00"; // sentinel: non sovrascrivere con noInfo
                bubble.textContent = T.error;
                messages.appendChild(wrap);
                scrollToBottom();
              }
            } else if (payload.done) {
              bubble.classList.remove("streaming");
              // Aggiunge le fonti sotto la bubble
              if (receivedSources.length > 0) {
                const srcDiv = document.createElement("div");
                srcDiv.className = `${WIDGET_ID}-sources`;
                const label = document.createElement("div");
                label.className = `${WIDGET_ID}-sources-label`;
                label.textContent = T.sources;
                srcDiv.appendChild(label);
                receivedSources.forEach((s) => {
                  const a = document.createElement("a");
                  a.href = s.url;
                  a.target = "_blank";
                  a.rel = "noopener noreferrer";
                  a.textContent = s.title || s.url;
                  a.title = s.url;
                  srcDiv.appendChild(a);
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
        }
        // fullText === "\x00" → wrap già in DOM, errore già mostrato
      } else {
        // Aggiorna history conversazionale (max 3 turni = 6 messaggi)
        conversationHistory.push({ role: "user", content: question });
        conversationHistory.push({ role: "assistant", content: fullText });
        if (conversationHistory.length > 6) {
          conversationHistory = conversationHistory.slice(-6);
        }
        // Aggiungi pulsanti feedback
        addFeedback(wrap, question, fullText);
      }

    } catch (err) {
      removeTypingIndicator();
      addMessage("bot", T.error, null);
      console.error("[Chatbot] Errore:", err);
    } finally {
      removeTypingIndicator(); // rimuovi se non sono arrivati token
      clearTimeout(waitTimer);
      if (waitHint) waitHint.remove();
      setLoading(false);
      scrollToBottom();
      inputEl.focus();  // accessibilità: rimetti il focus sull'input
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
      sendBtn.disabled = true;
      sendQuestion(q);
    }
  });

  // Chiudi con Escape
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && isOpen) closePanel();
  });

  // ---------------------------------------------------------------------------
  // Avvio
  // ---------------------------------------------------------------------------
  panel.setAttribute("aria-hidden", "true");
  btnToggle.setAttribute("aria-expanded", "false");
  clearChat();

})();
