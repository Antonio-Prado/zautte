/**
 * Zautte — footer comune a tutte le pagine (dashboard in ogni vista, pagina pilota,
 * "Come funziona"). Si include con:
 *
 *   <script src="./site-footer.js" defer></script>
 *
 * Mostra l'ultima release e l'ultimo commit su GitHub (letti dall'API pubblica a ogni
 * apertura della pagina; la release porta alla sua pagina, il commit sempre al
 * repository), il link all'accessibilità, l'indirizzo IP del visitatore come lo vede
 * l'API (GET /client-ip sullo stesso server dello script), "Powered by" con il logo
 * di SBTAP (logo.png accanto a questo script, link a as59715.net) e "Served by" con
 * il logo di FreeBSD (freebsd-logo.png: logo ufficiale orizzontale dell'archivio
 * della FreeBSD Foundation, solo ridotto in proporzione; il marchio sul web deve
 * portare a freebsd.org, vedi le Trademark Usage Terms della Foundation), "IPv6
 * enabled" con il logo World IPv6 Launch (ipv6-logo.svg, Internet Society, CC BY 3.0,
 * file originale da Wikimedia Commons) e "Made with vi :wq" con vi-logo.svg (icona
 * nostra: vi non ha un logo ufficiale, quello di Vim è di un altro editor).
 *
 * Accessibilità: le linee guida AgID chiedono il link alla dichiarazione di
 * accessibilità nel footer. Quando l'amministrazione l'ha pubblicata, il suo
 * indirizzo va nell'attributo data-accessibility del tag script e il link diventa
 * "Dichiarazione di accessibilità"; fino ad allora "Accessibilità" porta alla
 * sezione di come-funziona.html.
 *
 *   <script src="./site-footer.js" data-accessibility="https://form.agid.gov.it/view/…" defer></script>
 */
(function () {
  "use strict";

  var REPO = "Antonio-Prado/zautte";
  // Il logo sta accanto allo script: l'URL non dipende dalla pagina che lo include
  var SCRIPT = document.currentScript;
  var BASE = (SCRIPT && SCRIPT.src) || location.href;
  var SBTAP_LOGO = new URL("logo.png", BASE).href;
  var FREEBSD_LOGO = new URL("freebsd-logo.png", BASE).href;
  var IPV6_LOGO = new URL("ipv6-logo.svg", BASE).href;
  var VI_LOGO = new URL("vi-logo.svg", BASE).href;
  // Le pagine e l'API stanno sullo stesso server (widget/ servita da FastAPI)
  var CLIENT_IP_URL = new URL("/client-ip", BASE).href;
  var STATEMENT = (SCRIPT && SCRIPT.getAttribute("data-accessibility")) || "";
  var A11Y_LINK = /^https:\/\//.test(STATEMENT)
    ? { href: STATEMENT, text: "Dichiarazione di accessibilità" }
    : { href: new URL("come-funziona.html#accessibilita", BASE).href, text: "Accessibilità" };

  // Data e ora del commit come YYYYMMDDhhmmss, nel fuso italiano
  function stamp(d) {
    var parts = {};
    new Intl.DateTimeFormat("it-IT", {
      timeZone: "Europe/Rome", year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
    }).formatToParts(d).forEach(function (p) { parts[p.type] = p.value; });
    return parts.year + parts.month + parts.day + parts.hour + parts.minute + parts.second;
  }

  function build() {
    if (document.getElementById("zautte-site-footer")) return;
    var footer = document.createElement("footer");
    footer.id = "zautte-site-footer";
    footer.setAttribute("style", [
      "width:100%", "max-width:760px", "margin:16px auto 0", "padding-top:10px",
      "border-top:1px solid #e0e0e0", "font-size:12px", "line-height:1.6",
      "color:#666", "text-align:center", "flex-shrink:0",
    ].join(";"));
    // Due righe: sopra le informazioni sul servizio, sotto la parte tecnica. La
    // seconda è un flex: testo e logo di ogni voce centrati sullo stesso asse,
    // ogni voce intera quando la riga va a capo
    var ITEM = "<span style=\"display:inline-flex;align-items:center;gap:4px;white-space:nowrap\">";
    var SEP = "<span style=\"padding:0 6px\">·</span>";
    footer.innerHTML =
      "<div>" +
      "<span id=\"zautte-release\" hidden><a href=\"https://github.com/" + REPO + "/releases\" target=\"_blank\" rel=\"noopener\" style=\"color:inherit\"></a> &nbsp;·&nbsp; </span>" +
      "<span id=\"zautte-last-commit\" hidden><a href=\"https://github.com/" + REPO + "\" target=\"_blank\" rel=\"noopener\" style=\"color:inherit\"></a> &nbsp;·&nbsp; </span>" +
      "<a id=\"zautte-a11y-link\" style=\"color:inherit\"></a>" +
      "<span id=\"zautte-client-ip\" hidden> &nbsp;·&nbsp; Il tuo IP: <span style=\"overflow-wrap:anywhere\"></span></span>" +
      "</div>" +
      "<div style=\"display:flex;flex-wrap:wrap;justify-content:center;align-items:center;row-gap:4px;margin-top:4px\">" +
      ITEM + "Powered by <a href=\"https://as59715.net\" target=\"_blank\" rel=\"noopener\" title=\"SBTAP\" style=\"display:flex\">" +
      "<img src=\"" + SBTAP_LOGO + "\" alt=\"SBTAP\" width=\"24\" height=\"24\" style=\"border-radius:3px\"></a></span>" + SEP +
      ITEM + "Served by <a href=\"https://www.freebsd.org\" target=\"_blank\" rel=\"noopener\" title=\"FreeBSD\" style=\"display:flex\">" +
      "<img src=\"" + FREEBSD_LOGO + "\" alt=\"FreeBSD\" width=\"83\" height=\"24\"></a></span>" + SEP +
      // Icone decorative: il testo accanto dice già tutto (crediti in come-funziona.html)
      ITEM + "<img src=\"" + IPV6_LOGO + "\" alt=\"\" width=\"24\" height=\"24\">IPv6 enabled</span>" + SEP +
      // :wq = salva ed esci, in vi
      ITEM + "<img src=\"" + VI_LOGO + "\" alt=\"\" width=\"24\" height=\"24\">Made with vi <code style=\"font-size:inherit;color:inherit\">:wq</code></span>" +
      "</div>";
    document.body.appendChild(footer);
    var a11y = document.getElementById("zautte-a11y-link");
    a11y.href = A11Y_LINK.href;
    a11y.textContent = A11Y_LINK.text;

    // Indirizzo IP del visitatore; se l'API non risponde resta nascosto
    fetch(CLIENT_IP_URL, { cache: "no-store" })
      .then(function (r) { if (!r.ok) throw new Error(String(r.status)); return r.json(); })
      .then(function (d) {
        var ip = String((d && d.ip) || "");
        if (!/^[0-9A-Fa-f.:]{2,45}$/.test(ip)) return;
        var box = document.getElementById("zautte-client-ip");
        box.querySelector("span").textContent = ip;
        box.hidden = false;
      })
      .catch(function () { /* API non raggiungibile */ });

    // Ultima release pubblicata; se non c'è o GitHub non risponde il riferimento resta nascosto
    fetch("https://api.github.com/repos/" + REPO + "/releases/latest", {
      headers: { "Accept": "application/vnd.github+json" },
    })
      .then(function (r) { if (!r.ok) throw new Error(String(r.status)); return r.json(); })
      .then(function (rel) {
        var tag = String(rel.tag_name || "");
        if (!/^v?\d+\.\d+\.\d+$/.test(tag)) return;
        var box = document.getElementById("zautte-release");
        var link = box.querySelector("a");
        var url = String(rel.html_url || "");
        if (url.indexOf("https://github.com/" + REPO + "/releases/") === 0) link.href = url;
        link.textContent = "Release " + tag;
        box.hidden = false;
      })
      .catch(function () { /* GitHub non raggiungibile o limite di richieste */ });

    // Ultimo commit su main; se GitHub non risponde il riferimento resta nascosto
    fetch("https://api.github.com/repos/" + REPO + "/commits/main", {
      headers: { "Accept": "application/vnd.github+json" },
    })
      .then(function (r) { if (!r.ok) throw new Error(String(r.status)); return r.json(); })
      .then(function (c) {
        var sha = String(c.sha || "");
        if (!/^[0-9a-f]{40}$/.test(sha)) return;
        var date = c.commit && c.commit.committer && c.commit.committer.date;
        var box = document.getElementById("zautte-last-commit");
        var link = box.querySelector("a");
        link.textContent = "Ultimo commit " + sha.slice(0, 7) + (date ? "@" + stamp(new Date(date)) : "");
        box.hidden = false;
      })
      .catch(function () { /* GitHub non raggiungibile o limite di richieste */ });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", build);
  } else {
    build();
  }
})();
