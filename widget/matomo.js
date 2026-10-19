/**
 * Zautte — statistiche delle pagine con un Matomo dell'ente (facoltativo). Si include
 * in ogni pagina con:
 *
 *   <script src="./matomo.js" defer></script>
 *
 * Senza configurazione non fa nulla. Per attivarlo, prima di questo script:
 *
 *   <script>window.ZautteMatomo = { url: "https://matomo.example.org/", siteId: "1" };</script>
 *
 * (oppure si sostituisce questo file con il proprio, tramite INSTANCE_DIR).
 *
 * Solo pagine viste e link esterni cliccati (le fonti sotto le risposte). Mai il
 * testo delle domande: la chat non passa di qui e l'indirizzo della pagina non lo
 * contiene. Niente cookie (disableCookies), così non serve il banner del consenso;
 * l'anonimizzazione dell'IP si imposta nel server Matomo (Privacy → Anonimizza i dati).
 *
 * Non conta: le pagine fuori da https (prove in locale, test di accessibilità) e
 * il browser in cui è salvata la chiave dell'area riservata, salvo il «Test
 * installation» di Matomo (?tracker_install_check=… nell'indirizzo), che si lancia
 * proprio da quel browser.
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
  } catch (_) { /* localStorage bloccato: si conta normalmente */ }

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
