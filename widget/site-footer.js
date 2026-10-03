/**
 * Zautte — footer comune a tutte le pagine (dashboard in ogni vista, pagina pilota,
 * "Come funziona"). Si include con:
 *
 *   <script src="./site-footer.js" defer></script>
 *
 * Mostra l'ultimo commit su GitHub (letto dall'API pubblica a ogni apertura della
 * pagina; il link porta sempre al repository) e "Powered by SBTAP" (link a as59715.net).
 */
(function () {
  "use strict";

  var REPO = "Antonio-Prado/zautte";

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
      "<span id=\"zautte-last-commit\" hidden><a href=\"https://github.com/" + REPO + "\" target=\"_blank\" rel=\"noopener\" style=\"color:inherit\"></a> &nbsp;·&nbsp; </span>" +
      "Powered by <a href=\"https://as59715.net\" target=\"_blank\" rel=\"noopener\" style=\"color:inherit\">SBTAP</a>";
    document.body.appendChild(footer);

    // Ultimo commit su main; se GitHub non risponde il riferimento resta nascosto
    fetch("https://api.github.com/repos/" + REPO + "/commits/main", {
      headers: { "Accept": "application/vnd.github+json" },
    })
      .then(function (r) { if (!r.ok) throw new Error(String(r.status)); return r.json(); })
      .then(function (c) {
        var sha = String(c.sha || "");
        if (!/^[0-9a-f]{40}$/.test(sha)) return;
        var date = c.commit && c.commit.committer && c.commit.committer.date;
        var when = date
          ? new Date(date).toLocaleDateString("it-IT", { day: "numeric", month: "short", year: "numeric" })
          : "";
        var box = document.getElementById("zautte-last-commit");
        var link = box.querySelector("a");
        link.textContent = "Ultimo commit " + sha.slice(0, 7) + (when ? " (" + when + ")" : "");
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
