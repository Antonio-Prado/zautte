/**
 * Zautte — page statistics with the organisation's own Matomo (optional). Include it
 * in every page with:
 *
 *   <script src="./matomo.js" defer></script>
 *
 * Without configuration it does nothing. To enable it, before this script:
 *
 *   <script>window.ZautteMatomo = { url: "https://matomo.example.org/", siteId: "1" };</script>
 *
 * (or replace this file with your own, via INSTANCE_DIR).
 *
 * Only page views and clicked external links (the sources under the answers). Never
 * the text of the questions: the chat does not go through here and the page address
 * does not contain it. No cookies (disableCookies), so no consent banner is needed;
 * IP anonymisation is set in the Matomo server (Privacy → Anonymize data).
 *
 * Not counted: pages outside https (local tests, accessibility tests) and the
 * browser where the restricted-area key is stored, except Matomo's «Test
 * installation» (?tracker_install_check=… in the address), which is launched
 * from that very browser.
 */
(function () {
  "use strict";

  var CFG = window.ZautteMatomo;
  if (!CFG || !/^https:\/\//.test(CFG.url || "") || !CFG.siteId) return;
  var BASE = CFG.url.replace(/\/?$/, "/");

  if (location.protocol !== "https:") return;
  var installCheck = /[?&]tracker_install_check=[a-f0-9]{32}(&|$)/i.test(location.search);
  try {
    if (!installCheck && localStorage.getItem("zautte_admin_key")) return;
  } catch (_) { /* localStorage blocked: count as usual */ }

  var _paq = window._paq = window._paq || [];
  _paq.push(["disableCookies"]);
  _paq.push(["trackPageView"]);
  _paq.push(["enableLinkTracking"]);
  _paq.push(["setTrackerUrl", BASE + "matomo.php"]);
  _paq.push(["setSiteId", String(CFG.siteId)]);

  var g = document.createElement("script");
  g.async = true;
  g.src = BASE + "matomo.js";
  document.head.appendChild(g);
})();
