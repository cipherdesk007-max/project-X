/* Sito pubblico di Alvea. Chi arriva con un collegamento dell'applicazione (conferma, nuova password, ritorno dal pagamento)
   o ha già fatto l'accesso in questa scheda viene portato subito all'applicazione, senza perdere il collegamento. */
(function () {
  "use strict";
  var hash = window.location.hash || "", token = null;
  try { token = window.sessionStorage.getItem("sfb_token"); } catch (e) { token = null; }
  if (token || /^#(registrati|reimposta-|pagamento-|crediti-|abbonamento$|google-|verifica-)/.test(hash)) {
    window.location.replace("/app" + hash);
    return;
  }
  var root = document.documentElement;
  root.classList.add("js");  // da qui in poi il foglio di stile può nascondere quello che comparirà scorrendo; senza script resta tutto visibile

  /* Animazioni legate allo scorrimento. Niente librerie: IntersectionObserver per le comparse, una variabile CSS per il resto. */
  function animate() {
    var still = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    var items = document.querySelectorAll("section h2, section .sub, .agent, .plan, .steps li, .safety li, .panel, .last .cta"), i;
    function show(el) { el.classList.add("in"); }
    function count(el) {  // i numeri salgono da zero quando entrano nello schermo
      var end = parseInt(el.textContent, 10), start = null;
      if (still || !(end > 0) || el.getAttribute("data-done")) return;
      el.setAttribute("data-done", "1");
      function step(now) {
        if (start === null) start = now;
        var k = Math.min(1, (now - start) / 900);
        el.textContent = String(Math.round(end * (1 - Math.pow(1 - k, 3))));
        if (k < 1) window.requestAnimationFrame(step); else el.textContent = String(end);
      }
      el.textContent = "0";
      window.requestAnimationFrame(step);
    }
    for (i = 0; i < items.length; i++) {
      items[i].classList.add("reveal");
      items[i].style.setProperty("--d", (Array.prototype.indexOf.call(items[i].parentNode.children, items[i]) % 8) * 55 + "ms");  // a scalare dentro ogni gruppo
    }
    var numbers = document.querySelectorAll("[data-count], .plan .count b");
    if (!("IntersectionObserver" in window) || still) {
      for (i = 0; i < items.length; i++) show(items[i]);
    } else {
      var seen = new IntersectionObserver(function (entries) {
        entries.forEach(function (e) {
          if (!e.isIntersecting) return;
          if (e.target.classList.contains("reveal")) show(e.target); else count(e.target);
          seen.unobserve(e.target);
        });
      }, { threshold: 0.12, rootMargin: "0px 0px -6% 0px" });
      for (i = 0; i < items.length; i++) seen.observe(items[i]);
      for (i = 0; i < numbers.length; i++) seen.observe(numbers[i]);
    }
    var bar = document.createElement("div"), header = document.querySelector(".top"), links = document.querySelectorAll(".top nav a"), waiting = false;
    bar.className = "progress";
    bar.setAttribute("aria-hidden", "true");
    document.body.insertBefore(bar, document.body.firstChild);
    function onScroll() {
      waiting = false;
      var y = window.pageYOffset || 0, max = Math.max(1, root.scrollHeight - window.innerHeight), current = "", k, box;
      root.style.setProperty("--p", String(Math.min(1, y / max)));   // barra di avanzamento
      root.style.setProperty("--y", String(still ? 0 : y));           // l'alveare si sposta e ruota con la pagina
      if (header) header.classList.toggle("scrolled", y > 24);
      root.classList.toggle("moved", y > 80);
      for (k = 0; k < links.length; k++) {
        box = document.getElementById((links[k].getAttribute("href") || "").slice(1));
        if (box && box.getBoundingClientRect().top <= window.innerHeight * 0.4) current = links[k].getAttribute("href");
      }
      for (k = 0; k < links.length; k++) links[k].classList.toggle("active", links[k].getAttribute("href") === current);
    }
    window.addEventListener("scroll", function () { if (!waiting) { waiting = true; window.requestAnimationFrame(onScroll); } }, { passive: true });
    window.addEventListener("resize", onScroll);
    onScroll();
    var hero = document.querySelector(".hero");
    if (hero && !still && window.matchMedia && window.matchMedia("(pointer: fine)").matches) {
      hero.addEventListener("mousemove", function (e) {  // l'alveare segue appena il mouse
        var r = hero.getBoundingClientRect();
        root.style.setProperty("--mx", String(((e.clientX - r.left) / r.width - 0.5) * 2));
        root.style.setProperty("--my", String(((e.clientY - r.top) / r.height - 0.5) * 2));
      });
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    try { animate(); } catch (e) { root.classList.remove("js"); }
    var mine = /Win/i.test(navigator.platform || navigator.userAgent) ? "windows" : (/Mac/i.test(navigator.platform || navigator.userAgent) ? "mac" : ""), osb = document.querySelectorAll("[data-os]"), n;
    for (n = 0; n < osb.length; n++) if (mine && osb[n].getAttribute("data-os") !== mine) osb[n].classList.add("dim");  // in evidenza il pulsante del sistema di chi guarda  // se qualcosa va storto la pagina resta tutta visibile
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
