/**
 * Zautte — footer comune a tutte le pagine (dashboard in ogni vista, pagina pilota,
 * "Come funziona"). Si include con:
 *
 *   <script src="./site-footer.js" defer></script>
 *
 * Mostra l'ultima release e l'ultimo commit su GitHub (letti dall'API pubblica a ogni
 * apertura della pagina; la release porta alla sua pagina, il commit sempre al
 * repository) e "Powered by" con il logo di SBTAP
 * (logo.png accanto a questo script, link a as59715.net).
 */
(function () {
  "use strict";

  var REPO = "Antonio-Prado/zautte";
  // Il logo sta accanto allo script: l'URL non dipende dalla pagina che lo include
  var SBTAP_LOGO = new URL("logo.png", (document.currentScript && document.currentScript.src) || location.href).href;

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
    footer.innerHTML =
      "<span id=\"zautte-release\" hidden><a href=\"https://github.com/" + REPO + "/releases\" target=\"_blank\" rel=\"noopener\" style=\"color:inherit\"></a> &nbsp;·&nbsp; </span>" +
      "<span id=\"zautte-last-commit\" hidden><a href=\"https://github.com/" + REPO + "\" target=\"_blank\" rel=\"noopener\" style=\"color:inherit\"></a> &nbsp;·&nbsp; </span>" +
      "Powered by <a href=\"https://as59715.net\" target=\"_blank\" rel=\"noopener\" title=\"SBTAP\" style=\"color:inherit\">" +
      "<img src=\"" + SBTAP_LOGO + "\" alt=\"SBTAP\" width=\"24\" height=\"24\" style=\"vertical-align:baseline;margin-left:2px;border-radius:3px\"></a>";
    document.body.appendChild(footer);

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
