/* Sito pubblico di Alvea. Chi arriva con un collegamento dell'applicazione (conferma, nuova password, ritorno dal pagamento)
   o ha già fatto l'accesso in questa scheda viene portato subito all'applicazione, senza perdere il collegamento. */
(function () {
  "use strict";
  var hash = window.location.hash || "", token = null;
  try { token = window.sessionStorage.getItem("sfb_token"); } catch (e) { token = null; }
  if (token || /^#(registrati|reimposta-|pagamento-|abbonamento$|google-|verifica-)/.test(hash)) {
    window.location.replace("/app" + hash);
    return;
  }
  document.addEventListener("DOMContentLoaded", function () {
    var request = new XMLHttpRequest();
    request.open("GET", "/api/register");
    request.onload = function () {
      var info;
      try { info = JSON.parse(request.responseText); } catch (e) { return; }
      var links = document.querySelectorAll("[data-register]"), i, note = document.getElementById("trial");
      if (!info.open) {  // registrazione chiusa: si entra solo con un account creato dal gestore
        for (i = 0; i < links.length; i++) { links[i].setAttribute("href", "/app"); links[i].textContent = "Accedi"; }
      } else if (note && info.trial_days > 0) {
        note.textContent = info.trial_days + " giorni di prova gratuita, senza carta.";
        note.hidden = false;
      }
    };
    request.send();
  });
}());
