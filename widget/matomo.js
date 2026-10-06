/**
 * Zautte — statistiche delle pagine con il Matomo di SBTAP (webstats.as59715.net,
 * sito n. 19). Si include in ogni pagina con:
 *
 *   <script src="./matomo.js" defer></script>
 *
 * Solo pagine viste e link esterni cliccati (le fonti sotto le risposte). Mai il
 * testo delle domande: la chat non passa di qui e l'indirizzo della pagina non lo
 * contiene. Niente cookie (disableCookies), così non serve il banner del consenso;
 * l'anonimizzazione dell'IP si imposta nel server Matomo (Privacy → Anonimizza i dati).
 *
 * Non conta: le pagine fuori da https (prove in locale, test di accessibilità) e
 * il browser in cui è salvata la chiave della vista amministratore, salvo il
 * «Test installation» di Matomo (?tracker_install_check=… nell'indirizzo), che si
 * lancia proprio da quel browser.
 */
(function () {
  "use strict";

  var BASE = "https://webstats.as59715.net/";
  var SITE_ID = "19";

  if (location.protocol !== "https:") return;
  var installCheck = /[?&]tracker_install_check=[a-f0-9]{32}(&|$)/i.test(location.search);
  try {
    if (!installCheck && localStorage.getItem("zautte_admin_key")) return;   // la chiave admin di dashboard.html
  } catch (_) { /* localStorage bloccato: si conta normalmente */ }

  var _paq = window._paq = window._paq || [];
  _paq.push(["disableCookies"]);
  _paq.push(["trackPageView"]);
  _paq.push(["enableLinkTracking"]);
  _paq.push(["setTrackerUrl", BASE + "matomo.php"]);
  _paq.push(["setSiteId", SITE_ID]);

  var g = document.createElement("script");
  g.async = true;
  g.src = BASE + "matomo.js";
  document.head.appendChild(g);
})();
