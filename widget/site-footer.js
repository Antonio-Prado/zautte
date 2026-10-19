/**
 * Zautte — footer comune a tutte le pagine (dashboard in ogni vista, pagina pilota,
 * "Come funziona"). Si include con:
 *
 *   <script src="./site-footer.js" defer></script>
 *
 * Mostra l'ultima release e l'ultimo commit su GitHub (letti dall'API pubblica a ogni
 * apertura della pagina; la release porta alla sua pagina, il commit sempre al
 * repository), il link all'accessibilità, l'indirizzo IP del visitatore come lo vede
 * l'API (GET /client-ip sullo stesso server dello script). Le installazioni che
 * vogliono altre righe (loghi, crediti) sostituiscono questo file con il proprio,
 * tramite INSTANCE_DIR.
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
  var SCRIPT = document.currentScript;
  var BASE = (SCRIPT && SCRIPT.src) || location.href;
  // Le pagine e l'API stanno sullo stesso server (widget/ servita da FastAPI)
  var CLIENT_IP_URL = new URL("/client-ip", BASE).href;
  var STATEMENT = (SCRIPT && SCRIPT.getAttribute("data-accessibility")) || "";
  var A11Y_LINK = { href: new URL("come-funziona.html#accessibilita", BASE).href, text: "Accessibilità" };
  if (/^https:\/\//.test(STATEMENT)) {
    try {
      // Solo indirizzi https://, con i caratteri non ammessi in un URL codificati
      A11Y_LINK = { href: encodeURI(decodeURI(STATEMENT)), text: "Dichiarazione di accessibilità" };
    } catch (e) {
      // sequenza %xx non valida nell'indirizzo: resta il link ad «Accessibilità»
    }
  }

  // Data e ora del commit come YYYYMMDDhhmmss, nel fuso del visitatore
  function stamp(d) {
    var parts = {};
    new Intl.DateTimeFormat("it-IT", {
      year: "numeric", month: "2-digit", day: "2-digit",
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
    footer.innerHTML =
      "<div>" +
      "<span id=\"zautte-release\" hidden><a href=\"https://github.com/" + REPO + "/releases\" target=\"_blank\" rel=\"noopener\" style=\"color:inherit\"></a> &nbsp;·&nbsp; </span>" +
      "<span id=\"zautte-last-commit\" hidden><a href=\"https://github.com/" + REPO + "\" target=\"_blank\" rel=\"noopener\" style=\"color:inherit\"></a> &nbsp;·&nbsp; </span>" +
      "<a id=\"zautte-a11y-link\" style=\"color:inherit\"></a>" +
      "<span id=\"zautte-client-ip\" hidden> &nbsp;·&nbsp; Il tuo IP: <a target=\"_blank\" rel=\"noopener\" title=\"Dati RIS di RIPEstat\" style=\"color:inherit;overflow-wrap:anywhere\"></a></span>" +
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
        var link = box.querySelector("a");
        link.textContent = ip;
        // Dati di instradamento (RIS) di RIPEstat, in una nuova scheda
        link.href = "https://stat.ripe.net/" + encodeURIComponent(ip);
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
