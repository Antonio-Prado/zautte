/**
 * Zautte — footer shared by all pages (dashboard in every view, pilot page,
 * "Come funziona"). Include it with:
 *
 *   <script src="./site-footer.js" defer></script>
 *
 * Shows the latest release and the latest commit on GitHub (read from the public API on
 * every page load; the release links to its page, the commit always to the
 * repository), the accessibility link, and the visitor's IP address as the API
 * sees it (GET /client-ip on the same server as the script). Installations that
 * want other rows (logos, credits) replace this file with their own,
 * via INSTANCE_DIR.
 *
 * Accessibility: the AgID guidelines require a link to the accessibility statement
 * in the footer. Once the administration has published it, its address goes in the
 * data-accessibility attribute of the script tag and the link becomes
 * "Accessibility statement"; until then "Accessibility" leads to the
 * section of come-funziona.html.
 *
 *   <script src="./site-footer.js" data-accessibility="https://form.agid.gov.it/view/…" defer></script>
 */
(function () {
  "use strict";

  var REPO = "Antonio-Prado/zautte";
  var SCRIPT = document.currentScript;
  var BASE = (SCRIPT && SCRIPT.src) || location.href;
  // Pages and API live on the same server (widget/ served by FastAPI)
  var CLIENT_IP_URL = new URL("/client-ip", BASE).href;
  var STATEMENT = (SCRIPT && SCRIPT.getAttribute("data-accessibility")) || "";
  var A11Y_LINK = { href: new URL("come-funziona.html#accessibility", BASE).href, text: "Accessibility" };
  if (/^https:\/\//.test(STATEMENT)) {
    try {
      // Only https:// addresses, with the characters not allowed in a URL encoded
      A11Y_LINK = { href: encodeURI(decodeURI(STATEMENT)), text: "Accessibility statement" };
    } catch (e) {
      // invalid %xx sequence in the address: the "Accessibility" link stays
    }
  }

  // Commit date and time as YYYYMMDDhhmmss, in the visitor's time zone
  function stamp(d) {
    var parts = {};
    new Intl.DateTimeFormat("en-GB", {
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
      "<span id=\"zautte-client-ip\" hidden> &nbsp;·&nbsp; Your IP: <a target=\"_blank\" rel=\"noopener\" title=\"RIS data from RIPEstat\" style=\"color:inherit;overflow-wrap:anywhere\"></a></span>" +
      "</div>";
    document.body.appendChild(footer);
    var a11y = document.getElementById("zautte-a11y-link");
    a11y.href = A11Y_LINK.href;
    a11y.textContent = A11Y_LINK.text;

    // Visitor's IP address; hidden if the API does not respond
    fetch(CLIENT_IP_URL, { cache: "no-store" })
      .then(function (r) { if (!r.ok) throw new Error(String(r.status)); return r.json(); })
      .then(function (d) {
        var ip = String((d && d.ip) || "");
        if (!/^[0-9A-Fa-f.:]{2,45}$/.test(ip)) return;
        var box = document.getElementById("zautte-client-ip");
        var link = box.querySelector("a");
        link.textContent = ip;
        // RIPEstat routing data (RIS), in a new tab
        link.href = "https://stat.ripe.net/" + encodeURIComponent(ip);
        box.hidden = false;
      })
      .catch(function () { /* API unreachable */ });

    // Latest published release; hidden if there is none or GitHub does not respond
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
      .catch(function () { /* GitHub unreachable or rate limit reached */ });

    // Latest commit on main; hidden if GitHub does not respond
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
        link.textContent = "Latest commit " + sha.slice(0, 7) + (date ? "@" + stamp(new Date(date)) : "");
        box.hidden = false;
      })
      .catch(function () { /* GitHub unreachable or rate limit reached */ });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", build);
  } else {
    build();
  }
})();
