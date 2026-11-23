/* Alvea: schermata. Tutto il testo che arriva dal server entra con textContent, mai come HTML. */
(function () {
  "use strict";

  var ICONS = {
    bag: '<path d="M5 8h14l-1 12H6L5 8z"/><path d="M9 8V6a3 3 0 0 1 6 0v2"/>',
    headset: '<path d="M4 13v-1a8 8 0 0 1 16 0v1"/><rect x="3" y="13" width="4" height="6" rx="1.5"/><rect x="17" y="13" width="4" height="6" rx="1.5"/><path d="M19 19a4 4 0 0 1-4 3h-2"/>',
    receipt: '<path d="M6 3h12v18l-3-2-3 2-3-2-3 2V3z"/><path d="M9 8h6M9 12h6"/>',
    mail: '<rect x="3" y="5" width="18" height="14" rx="2.5"/><path d="M3.5 7.5l8.5 6 8.5-6"/>',
    calendar: '<rect x="3.5" y="5" width="17" height="15" rx="2.5"/><path d="M3.5 10h17M8 3v4M16 3v4M8 14h3M13.5 14H16M8 17h5"/>',
    wave: '<path d="M4 10v4M8 6v12M12 3v18M16 7v10M20 10v4"/>',
    pencil: '<path d="M4 20l1-5L16 4l4 4L9 19l-5 1z"/><path d="M13.5 6.5l4 4"/>',
    megaphone: '<path d="M4 10v4h3l8 4V6l-8 4H4z"/><path d="M18.5 9.5a3.5 3.5 0 0 1 0 5M8 14.5l1 5h2.5"/>',
    briefcase: '<rect x="3" y="7.5" width="18" height="12" rx="2.5"/><path d="M9 7.5V5.5h6v2M3 13h18"/>',
    camera: '<rect x="3" y="6.5" width="18" height="13" rx="3"/><circle cx="12" cy="13" r="3.6"/><path d="M8 6.5l1.5-2h5l1.5 2"/>',
    spark: '<path d="M12 3l1.9 6.1L20 11l-6.1 1.9L12 19l-1.9-6.1L4 11l6.1-1.9L12 3z"/><path d="M19 16v4M17 18h4"/>',
    users: '<circle cx="9" cy="8.5" r="3"/><circle cx="17" cy="9.5" r="2.3"/><path d="M3.5 19c.6-3.2 2.7-5 5.5-5s4.9 1.8 5.5 5M15.5 14.2c2.5-.2 4.3 1.3 5 4.3"/>',
    speaker: '<path d="M4 10v4h3.5L12 18V6l-4.5 4H4z"/><path d="M15.5 9.5a3.5 3.5 0 0 1 0 5M18 7a7 7 0 0 1 0 10"/>',
    search: '<circle cx="10.5" cy="10.5" r="6"/><path d="M15 15l5.5 5.5"/>',
    chart: '<path d="M4 4v16h16"/><path d="M8.5 16v-4M12.5 16V8M16.5 16v-6"/>',
    shield: '<path d="M12 3l7.5 3v5.5c0 4.5-3 7.8-7.5 9.5-4.5-1.700-7.500-5-7.500-9.500V6L12 3z"/><rect x="9.5" y="11" width="5" height="4" rx="1"/><path d="M10.5 11V9.800a1.500 1.500 0 0 1 3 0V11"/>',
    bubble: '<path d="M12 4a8 8 0 0 0-6.900 12L4 20l4.200-1A8 8 0 1 0 12 4z"/><path d="M9.500 10.500h5M9.500 13.500h3"/>',
    plane: '<path d="M21 4L3 11l6 2.500L11 20l3-4.500 4.500 3L21 4z"/><path d="M9 13.500L21 4"/>',
    lock: '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
    film: '<rect x="3" y="5" width="18" height="14" rx="2.5"/><path d="M8 5v14M16 5v14M3 10h5M3 14h5M16 10h5M16 14h5"/>',
    code: '<path d="M8 8l-4 4 4 4M16 8l4 4-4 4M13.5 6l-3 12"/>',
    up: '<path d="M12 19V5M6 11l6-6 6 6"/>'
  };
  var STATUS = { pending: ["in attesa", "wait"], approved: ["approvata", "wait"], executed: ["eseguita", "done"],
                 rejected: ["rifiutata", "bad"], failed: ["fallita", "bad"] };
  var ROLE = { admin: "amministratore", member: "operatore", superadmin: "gestore" };

  var root = document.getElementById("root");
  var token = null;
  try { token = window.sessionStorage.getItem("sfb_token"); } catch (e) { token = null; }
  var S = { me: null, view: "agente", agent: null, focus: null, model: null, threads: {}, busy: false, busyFor: null,
            osel: null, osnap: null, odetail: null, opoll: false, opollD: false, oreload: "", viewSet: false,
            draft: "", tab: "approvazioni", pending: 0, loginError: "",
            sum: { unread: 0, latest: null, stopped: false, automations: 0, connections: 0 }, flash: "", stopArmed: false,
            auth: "login", reg: null, notice: "", noticeBad: false, lastEmail: "" };
  var STARTER = ["email", "calendario", "contenuti", "ricerca", "report", "telegram", "assistenza"];
  var GOOGLE_RESULT = { ok: "Google collegato.", stato: "Collegamento non valido o già usato: riprova da qui.",
    scaduto: "Sono passati più di 10 minuti: riprova.", browser: "Il collegamento va concluso nello stesso browser e allo stesso indirizzo in cui è iniziato (quello impostato in SFB_PUBLIC_URL).",
    negato: "Il consenso è stato negato su Google.", scambio: "Google non ha accettato il codice: controlla client ID, secret e indirizzo di ritorno.",
    rinnovo: "Google non ha dato il permesso di accesso continuo: riprova.", permessi: "Non è stato concesso nessuno dei servizi richiesti.",
    cifratura: "Non è stato possibile salvare le credenziali cifrate: controlla SFB_SECRET_KEY." };
  var DAYS = ["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"];

  // ---------- utilità ----------
  function append(node, kid) {
    if (kid == null || kid === false) return;
    if (Array.isArray(kid)) { kid.forEach(function (k) { append(node, k); }); return; }
    node.appendChild(typeof kid === "string" || typeof kid === "number" ? document.createTextNode(String(kid)) : kid);
  }
  function el(tag, attrs) {
    var node = document.createElement(tag), k, i;
    attrs = attrs || {};
    for (k in attrs) {
      if (k === "class") node.className = attrs[k];
      else if (k === "text") node.textContent = attrs[k];
      else if (k.slice(0, 2) === "on") node.addEventListener(k.slice(2), attrs[k]);
      else if (attrs[k] === true) node.setAttribute(k, "");
      else if (attrs[k] !== false && attrs[k] != null) node.setAttribute(k, attrs[k]);
    }
    for (i = 2; i < arguments.length; i++) append(node, arguments[i]);
    return node;
  }
  function icon(name) {
    var t = document.createElement("template");
    t.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true">' + ICONS[name] + "</svg>";
    return t.content.firstChild;
  }
  function setToken(value) {
    token = value;
    try { if (value) window.sessionStorage.setItem("sfb_token", value); else window.sessionStorage.removeItem("sfb_token"); } catch (e) { /* resta in memoria */ }
  }
  function api(method, path, body) {
    var headers = { "Content-Type": "application/json" };
    if (token) headers.Authorization = "Bearer " + token;
    return fetch("/api" + path, { method: method, headers: headers, body: body ? JSON.stringify(body) : undefined })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (j) {
          if (r.status === 401 && path !== "/login") { setToken(null); S.me = null; render(); }
          if (!r.ok) throw new Error(j.error || ("Errore " + r.status));
          return j;
        });
      });
  }
  function seg(items, current, onPick, label) {
    return el("div", { class: "seg", role: "group", "aria-label": label }, items.map(function (it) {
      return el("button", { type: "button", "aria-pressed": String(it.id === current), disabled: !!it.disabled, title: it.title || null,
                            onclick: function () { onPick(it.id); } },
                            it.short ? [el("span", { class: "l", text: it.label }), el("span", { class: "s", text: it.short })] : it.label, it.small ? el("small", { text: it.small }) : null);
    }));
  }
  function field(label, id, attrs) {
    return el("div", { class: "field" }, el("label", { for: id, text: label }), el("input", Object.assign({ id: id }, attrs || {})));
  }
  function val(id) { var n = document.getElementById(id); return n ? n.value : ""; }
  function target() { return S.agent || "centrale"; }
  /* Una conversazione per interlocutore: cambiare agente non mescola i messaggi e non perde il filo. */
  function th(id) { id = id || target(); if (!S.threads[id]) S.threads[id] = { log: [], conv: null }; return S.threads[id]; }
  function table(headers, rows) {
    return el("div", { class: "scroll" }, el("table", {},
      el("thead", {}, el("tr", {}, headers.map(function (h) { return el("th", { text: h }); }))),
      el("tbody", {}, rows.map(function (r) { return el("tr", {}, r.map(function (c, i) { var label = headers[i] || "";  // su telefono la riga diventa una scheda e questa è l'etichetta
        return c && c.nodeType ? el("td", { "data-label": label }, c) : el("td", { class: "wrapcell", "data-label": label, text: c == null ? "" : String(c) }); })); }))));
  }
  function when(iso) {
    if (!iso) return "";
    var text = String(iso), d = /Z$/.test(text) ? new Date(text) : null;  // gli orari con Z sono UTC: li mostro nell'ora di chi guarda
    if (d && !isNaN(d.getTime())) {
      var two = function (n) { return (n < 10 ? "0" : "") + n; };
      return d.getFullYear() + "-" + two(d.getMonth() + 1) + "-" + two(d.getDate()) + " " + two(d.getHours()) + ":" + two(d.getMinutes());
    }
    return text.replace("T", " ").replace("Z", "");
  }

  // ---------- catalogo ----------
  function cat() { return S.me.catalog; }
  function tierOf(agentId) { var t = -1; cat().packages.forEach(function (p, i) { if (p.adds.indexOf(agentId) !== -1) t = i; }); return t; }
  function countUpTo(i) { return cat().packages.slice(0, i + 1).reduce(function (n, p) { return n + p.adds.length; }, 0); }
  function agentById(id) { return cat().agents.filter(function (a) { return a.id === id; })[0]; }
  function entitled(id) { return S.me.entitled.indexOf(id) !== -1; }           // acceso nel team
  function inCeiling(id) { return (S.me.ceiling || []).indexOf(id) !== -1; }   // permesso dal pacchetto
  function bytes(n) { return n >= 1048576 ? (n / 1048576).toFixed(n >= 104857600 ? 0 : 1) + " MB" : Math.max(1, Math.round(n / 1024)) + " KB"; }
  function toggleAgent(id, on, errorBox) {
    return api("POST", "/tenant/agents", { agent_id: id, is_active: on }).then(function () {
      if (!on && S.agent === id) { S.agent = null; if (S.osel === id) { S.osel = null; S.odetail = null; } }
      keepDraft();
      return loadMe();
    }).catch(function (err) { if (errorBox) errorBox.textContent = err.message; else { th().log.push({ role: "err", text: err.message }); render(); } });
  }
  function myPackIndex() { var out = 0; cat().packages.forEach(function (p, i) { if (S.me.tenant && p.id === S.me.tenant.package_id) out = i; }); return out; }

  // ---------- avvio e accesso ----------
  function loadMe() {
    return api("GET", "/me").then(function (me) {
      S.me = me;
      var ready = me.models.filter(function (m) { return m.configured; })[0] || me.models[0];
      S.model = ready ? ready.id : null;
      S.osnap = null;  // la stanza si ricostruisce da questi dati; gli stati arrivano subito dopo da /summary
      if (!S.viewSet) {  // al primo ingresso: ufficio 3D su schermi larghi con WebGL, vista compatta su telefono
        S.viewSet = true;
        if (me.tenant && S.view === "agente" && canOffice() && !window.matchMedia("(max-width: 900px)").matches) S.view = "ufficio";
      }
      if (me.tenant) refreshPending();
      render();
    });
  }
  function stripText() {
    return S.me.entitled.length + " agenti attivi su " + S.me.ceiling.length + " · " + S.sum.connections + " collegamenti · " + S.sum.automations + " lavori automatici attivi";
  }
  function tickerText() {
    if (!S.sum.latest) return "Nessuna novità";
    return (S.sum.unread ? S.sum.unread + " da leggere · " : "") + S.sum.latest.title;
  }
  function refreshPending() {
    if (!S.me || !S.me.tenant) return;
    api("GET", "/summary").then(function (out) {
      S.pending = out.pending;
      S.sum = out;
      if (S.me && S.me.tenant && S.me.tenant.stopped !== out.stopped) { S.me.tenant.stopped = out.stopped; keepDraft(); render(); return; }
      var set = function (id, text) { var n = document.getElementById(id); if (n) n.textContent = text; };
      set("pendingCount", String(S.pending));
      set("tickerText", tickerText());
      set("stripText", stripText());
    }).catch(function () {});
  }
  function setStopped(active) {
    S.stopArmed = false;
    api("POST", "/stop", { active: active }).then(function () { S.me.tenant.stopped = active; S.sum.stopped = active; refreshPending(); keepDraft(); render(); })
      .catch(function (err) { th().log.push({ role: "err", text: err.message }); render(); });
  }
  function stopControl() {
    if (S.me.user.role !== "admin") return null;
    if (S.me.tenant.stopped) return el("button", { class: "btn primary", type: "button", text: "Riavvia gli agenti", onclick: function () { setStopped(false); } });
    if (!S.stopArmed) return el("button", { class: "btn danger", type: "button", text: "Arresto d'emergenza", onclick: function () {
      keepDraft(); S.stopArmed = true; render();
      setTimeout(function () { if (S.stopArmed) { S.stopArmed = false; keepDraft(); if (S.me) render(); } }, 6000);
    } });
    return el("button", { class: "btn stop", type: "button", text: "Conferma: ferma tutti gli agenti", onclick: function () { setStopped(true); } });
  }
  function loadRegInfo() {
    return api("GET", "/register").then(function (info) { S.reg = info; if (!S.me) render(); }).catch(function () {});
  }
  function viewLogin() {
    if (S.auth === "register" && S.reg && S.reg.open) return viewRegister();
    if (S.auth === "forgot" || S.auth === "reset") return viewPassword();
    var resend = /Conferma prima/.test(S.loginError) ? el("button", { class: "btn small", type: "button", text: "Rimanda il collegamento", onclick: function () {
      api("POST", "/register/resend", { email: S.lastEmail }).then(function () { S.loginError = ""; S.notice = "Se l'indirizzo è in attesa di conferma, ti abbiamo mandato un nuovo collegamento."; S.noticeBad = false; render(); })
        .catch(function (err) { S.loginError = err.message; render(); });
    } }) : null;
    return el("form", { class: "login", autocomplete: "on", onsubmit: function (e) {
      e.preventDefault();
      S.loginError = ""; S.notice = ""; S.lastEmail = val("loginEmail");
      api("POST", "/login", { email: val("loginEmail"), password: val("loginPassword") })
        .then(function (out) { setToken(out.token); return loadMe(); })
        .catch(function (err) { S.loginError = err.message; render(); });
    } },
      el("div", { class: "brand" }, el("img", { class: "lockup", src: "/logo-alvea.png", alt: "alvea", width: "626", height: "186" })),
      el("p", { text: "Entra con l'email e la password della tua azienda." }),
      S.notice ? el("p", { class: S.noticeBad ? "error" : "okmsg", role: "status", text: S.notice }) : null,
      el("div", { class: "panel form" },
        field("Email", "loginEmail", { type: "email", required: true, autocomplete: "username", value: S.lastEmail }),
        field("Password", "loginPassword", { type: "password", required: true, autocomplete: "current-password" }),
        S.loginError ? el("p", { class: "error", role: "alert", text: S.loginError }) : null, resend,
        el("button", { class: "btn primary", type: "submit", text: "Entra" })),
      S.reg && S.reg.password_recovery ? el("button", { class: "btn small", type: "button", text: "Password dimenticata?",
        onclick: function () { S.lastEmail = val("loginEmail") || S.lastEmail; S.auth = "forgot"; S.loginError = ""; S.notice = ""; render(); } }) : null,
      S.reg && S.reg.open ? el("button", { class: "btn", type: "button", text: "Non hai un account? Registra la tua azienda",
        onclick: function () { S.auth = "register"; S.loginError = ""; S.notice = ""; render(); } }) : null);
  }
  /* Recupero della password: prima si chiede il collegamento per email, poi dal collegamento si sceglie la nuova. */
  function viewPassword() {
    var reset = S.auth === "reset", err = el("p", { class: "error", role: "alert" });
    var send = el("button", { class: "btn primary", type: "submit", text: reset ? "Salva la nuova password" : "Mandami il collegamento" });
    function back(notice) { S.auth = "login"; S.resetToken = null; S.notice = notice || ""; S.noticeBad = false; render(); }
    return el("form", { class: "login", autocomplete: "on", onsubmit: function (e) {
      e.preventDefault();
      send.disabled = true; err.textContent = "";
      (reset ? api("POST", "/password/reset", { token: S.resetToken, password: val("new_password") })
             : api("POST", "/password/forgot", { email: val("forgot_email") }))
        .then(function () { back(reset ? "Password cambiata: ora puoi entrare." : "Se l'indirizzo è registrato, tra poco ricevi un'email con il collegamento. Vale un'ora."); })
        .catch(function (e2) { send.disabled = false; err.textContent = e2.message; });
    } },
      el("div", { class: "brand" }, el("img", { class: "lockup", src: "/logo-alvea.png", alt: "alvea", width: "626", height: "186" })),
      el("p", { text: reset ? "Scegli una nuova password di almeno 10 caratteri." : "Scrivi l'email con cui entri: ti mandiamo un collegamento per scegliere una nuova password." }),
      el("div", { class: "panel form" },
        reset ? field("Nuova password", "new_password", { type: "password", required: true, minlength: "10", autocomplete: "new-password" })
              : field("Email", "forgot_email", { type: "email", required: true, autocomplete: "username", value: S.lastEmail }),
        err, send),
      el("button", { class: "btn", type: "button", text: "Torna all'accesso", onclick: function () { back(""); } }));
  }
  function viewRegister() {
    var err = el("p", { class: "error", role: "alert" });
    var boxes = el("div", { class: "checks" }, S.reg.agents.map(function (a) {
      return el("label", { class: "check" }, el("input", { type: "checkbox", id: "reg_a_" + a.id, checked: STARTER.indexOf(a.id) !== -1 }),
        el("span", {}, el("strong", { text: a.name }), el("small", { class: "plain", text: a.does })));
    }));
    var send = el("button", { class: "btn primary", type: "submit", text: "Crea l'azienda" });
    return el("form", { class: "login wide", autocomplete: "on", onsubmit: function (e) {
      e.preventDefault();
      err.textContent = ""; send.disabled = true;
      var agents = S.reg.agents.filter(function (a) { return document.getElementById("reg_a_" + a.id).checked; }).map(function (a) { return a.id; });
      S.lastEmail = val("reg_email");
      api("POST", "/register", { company: val("reg_company"), name: val("reg_name"), email: val("reg_email"), password: val("reg_password"),
                                 website: val("reg_website"), agents: agents })
        .then(function (out) {
          if (out.token) { setToken(out.token); S.auth = "login"; return loadMe(); }
          S.auth = "login";
          S.noticeBad = !out.sent;
          S.notice = out.sent ? "Account creato. Ti abbiamo mandato un'email: apri il collegamento per attivarlo, poi entra da qui."
                              : "Account creato, ma l'email di conferma non è partita. Prova a entrare e usa «Rimanda il collegamento».";
          render();
        })
        .catch(function (e2) { send.disabled = false; err.textContent = e2.message; });
    } },
      el("div", { class: "brand" }, el("img", { class: "lockup", src: "/logo-alvea.png", alt: "alvea", width: "626", height: "186" })),
      el("p", { text: "Registra la tua azienda e scegli gli agenti del tuo team. Potrai accenderli e spegnerli quando vuoi." }),
      el("div", { class: "panel form" },
        field("Nome dell'azienda", "reg_company", { required: true, maxlength: "120", autocomplete: "organization" }),
        field("Il tuo nome", "reg_name", { required: true, maxlength: "120", autocomplete: "name" }),
        field("Email", "reg_email", { type: "email", required: true, autocomplete: "username" }),
        field("Password (almeno 10 caratteri)", "reg_password", { type: "password", required: true, minlength: "10", autocomplete: "new-password" }),
        el("div", { class: "trap", "aria-hidden": "true" }, el("label", { for: "reg_website", text: "Sito web" }),
          el("input", { id: "reg_website", type: "text", tabindex: "-1", autocomplete: "off" })),
        el("div", { class: "field" }, el("label", { text: "Agenti del team" }), boxes),
        S.reg.verify_email ? el("p", { class: "empty", text: "Ti manderemo un'email per confermare l'indirizzo." }) : null,
        S.reg.trial_days ? el("p", { class: "empty", text: "Prova gratuita di " + S.reg.trial_days + " giorni, senza carta." }) : null,
        el("p", { class: "empty" }, "Vuoi far girare Video Editor, Email o Dev sul tuo computer? ",
          el("a", { href: "/api/launcher/download", download: "alvea-launcher.zip", text: "Scarica il launcher" }),
          " e verifica prima di scegliere: ti dice cosa gira e cosa no, senza account."),
        err, send),
      el("button", { class: "btn", type: "button", text: "Ho già un account", onclick: function () { S.auth = "login"; render(); } }));
  }
  function logout() {
    api("POST", "/logout", {}).catch(function () {}).then(function () {
      setToken(null);
      S.me = null; S.threads = {}; S.agent = null; S.focus = null; S.view = "agente";
      S.osel = null; S.osnap = null; S.odetail = null; S.viewSet = false; S.busy = false; S.busyFor = null;
      render();
    });
  }

  // ---------- intestazione ----------
  function header() {
    var me = S.me, ticker;
    if (me.tenant) {
      ticker = el("button", { class: "ticker", type: "button", title: "Apri le novità", onclick: function () { keepDraft(); S.view = "centro"; S.tab = "novita"; render(); } },
        el("span", { class: "tag" }, el("span", { class: "dot" }), "Ultime novità"),
        el("p", { id: "tickerText", text: tickerText() }));
    } else {
      ticker = el("div", { class: "ticker" }, el("span", { class: "tag" }, el("span", { class: "dot" }), "Gestore"),
        el("p", { text: "Gestione della piattaforma: aziende e pacchetti" }));
    }
    return el("header", { class: "top" },
      el("div", { class: "brand" }, el("img", { class: "lockup", src: "/logo-alvea.png", alt: "alvea", width: "626", height: "186" })),
      ticker,
      el("div", { class: "who" }, me.demo ? el("span", { class: "chip wait", title: "Risponde un modello finto a regole fisse", text: "Demo" }) : null,
        me.tenant ? el("span", { text: me.tenant.name + " · " + me.tenant.package_name }) : null,
        el("span", {}, el("strong", { text: me.user.name }), " · " + ROLE[me.user.role]),
        el("button", { class: "btn small", type: "button", onclick: logout, text: "Esci" })));
  }

  // ---------- vista Agente ----------
  function stateOf(id) { return (S.me.agent_status || {})[id] || "Pronto"; }
  function agentButton(a) {
    var on = entitled(a.id), can = inCeiling(a.id), pack = cat().packages[tierOf(a.id)];
    var b = el("button", { class: "agent" + (on ? "" : (can ? " off" : " locked")), type: "button",
      "aria-pressed": String(S.focus === a.id), "aria-label": "Agente " + a.name + (on ? "" : (can ? ", spento" : ", incluso da " + pack.name)),
      onclick: function () {
        keepDraft();
        S.focus = S.focus === a.id ? null : a.id;
        if (on) { S.agent = S.focus; S.osel = S.focus; S.odetail = null; }
        render();
        if (S.focus && window.matchMedia("(max-width: 900px)").matches) { var c = document.getElementById("card"); if (c) c.scrollIntoView({ block: "nearest", behavior: "smooth" }); }
      } },
      el("span", { class: "bubble" }, icon(a.icon), on || can ? null : el("span", { class: "lock" }, icon("lock"))),
      el("span", { class: "name" }, el("span", { class: "pre", text: "Agente " }), a.name),
      on ? el("span", { class: "state" + (stateOf(a.id) === "Da collegare" ? " todo" : ""), text: stateOf(a.id) })
         : (can ? el("span", { class: "state", text: "Spento" }) : el("span", { class: "from", text: "da " + pack.name })));
    b.style.setProperty("--col", a.pos[0]);
    b.style.setProperty("--row", a.pos[1]);
    b.style.setProperty("--tone", a.tone);
    return b;
  }
  function messageNode(m) {
    if (m.role === "me") return el("div", { class: "msg me" }, el("span", { class: "text", text: m.text }));
    if (m.role === "err") return el("div", { class: "msg err", role: "alert" }, el("span", { class: "text", text: m.text }));
    var who = m.agent === "centrale" ? "Centrale" : "Agente " + (agentById(m.agent) || { name: m.agent }).name;
    var chips = (m.events || []).map(function (e) {
      var kind = e.status === "eseguito" ? "done" : (e.status === "in attesa di approvazione" ? "wait" : "bad");
      return el("span", { class: "chip " + kind, text: e.tool + ": " + e.status + (e.approval_id ? " n. " + e.approval_id : "") });
    });
    return el("div", { class: "msg bot" }, el("span", { class: "meta", text: who + " · " + m.model }),
      el("span", { class: "text", text: m.text }), chips.length ? el("div", { class: "chips" }, chips) : null);
  }
  function keepDraft() { var n = document.getElementById("chatInput"); if (n) S.draft = n.value; }
  function sendChat(e) {
    e.preventDefault();
    var input = document.getElementById("chatInput"), text = input.value.trim(), who = target(), T = th(who);
    if (!text || S.busy) return;
    S.draft = "";
    T.log.push({ role: "me", text: text });
    S.busy = true; S.busyFor = who;
    render();
    pollOffice();
    api("POST", "/chat", { message: text, agent_id: who, model: S.model, conversation_id: T.conv })
      .then(function (out) {
        T.conv = out.conversation_id;
        T.log.push({ role: "bot", text: out.reply, agent: out.agent_id, model: out.model, events: out.events });
        if (out.events.some(function (ev) { return ev.approval_id; })) refreshPending();
      })
      .catch(function (err) { T.log.push({ role: "err", text: err.message }); })
      .then(function () { S.busy = false; S.busyFor = null; if (S.me) { keepDraft(); render(); pollOffice(); } });
  }
  function cardNode() {
    var p = cat().packages[myPackIndex()], admin = S.me.user.role === "admin";
    if (!S.focus) {
      return el("section", { class: "card", id: "card" },
        el("h2", { text: "Il tuo team" }),
        el("p", { class: "sub", text: S.me.entitled.length + " agenti attivi su " + S.me.ceiling.length + " disponibili (pacchetto " + p.name + ")." }),
        el("p", { class: "sub", text: "Tocca un agente per parlare solo con lui. Senza scelta risponde il Centrale, che usa tutti gli agenti attivi." }),
        admin ? el("button", { class: "back", type: "button", text: "Scegli gli agenti", onclick: function () { keepDraft(); S.view = "centro"; S.tab = "agenti"; render(); } }) : null);
    }
    var a = agentById(S.focus), on = entitled(a.id), can = inCeiling(a.id), t = cat().packages[tierOf(a.id)], err = el("p", { class: "error", role: "alert" });
    return el("section", { class: "card", id: "card" },
      el("h2", { text: "Agente " + a.name }),
      el("p", { class: "sub", text: a.does }),
      el("dl", {},
        el("dt", { text: "Nel team" }), el("dd", {}, el("span", { class: "pill " + (on ? "ok" : "no"), text: on ? "Attivo" : (can ? "Spento" : "Dal pacchetto " + t.name) })),
        on ? [el("dt", { text: "Stato" }), el("dd", { text: stateOf(a.id) })] : null,
        el("dt", { text: "Azioni" }), el("dd", { text: a.acts }),
        el("dt", { text: "Serve" }), el("dd", { text: a.ext })),
      on ? null : el("p", { class: "sub", text: can ? "È spento: il server rifiuta le richieste a questo agente finché un amministratore non lo attiva."
                                                   : "Supera il limite del pacchetto della tua azienda: il server rifiuta le richieste a questo agente." }),
      err,
      el("div", { class: "acts" },
        admin && can ? el("button", { class: "back", type: "button", text: on ? "Spegni" : "Attiva nel team", onclick: function () { toggleAgent(a.id, !on, err); } }) : null,
        el("button", { class: "back", type: "button", text: "Torna al Centrale", onclick: function () { keepDraft(); S.focus = null; S.agent = null; S.osel = null; S.odetail = null; render(); } })));
  }
  function viewAgente() {
    var models = S.me.models.map(function (m) {
      return { id: m.id, label: m.name, disabled: !m.configured, title: m.configured ? null : "Manca la chiave API di questo modello" };
    });
    var talking = S.agent ? "Agente " + agentById(S.agent).name : "Centrale";
    var stopped = S.me.tenant.stopped;
    var center = el("div", { class: "center" },
      el("div", { class: "hero" }, el("h1", { text: "Centro operativo" }),
        el("p", { text: "Coordina gli agenti e segui quello che succede nella tua azienda." }),
        canOffice() ? el("button", { class: "btn small", type: "button", text: "Apri l'Ufficio 3D", onclick: function () { keepDraft(); S.view = "ufficio"; render(); } }) : null),
      stopped ? el("div", { class: "banner stopbanner", role: "alert", text: "Arresto d'emergenza attivo: chat, lavori automatici e azioni approvate sono fermi." }) : null,
      el("div", { class: "notice" }, el("span", { class: "tag" }, el("span", { class: "dot warn" }), "Avvisi"),
        el("span", { text: S.me.user.role === "admin" ? "Azioni in attesa della tua approvazione" : "Tue azioni in attesa di approvazione" }),
        el("span", { class: "count", id: "pendingCount", text: String(S.pending) })),
      el("div", { class: "orb" + (stopped ? " off" : ""), "aria-hidden": "true" }),
      el("p", { class: "strip", id: "stripText", text: stripText() }),
      el("form", { class: "chat", autocomplete: "off", onsubmit: sendChat },
        el("div", { class: "log", id: "log", "aria-live": "polite" }, th().log.map(messageNode),
          S.busy && S.busyFor === target() ? el("div", { class: "thinking", text: talking + " sta lavorando..." }) : null),
        el("div", { class: "bar" },
          el("label", { for: "chatInput", hidden: true, text: "Messaggio" }),
          el("input", { id: "chatInput", type: "text", placeholder: "Scrivi a " + talking + "...", value: S.draft, maxlength: "8000" }),
          el("button", { class: "send", type: "submit", "aria-label": "Invia", disabled: S.busy || stopped }, icon("up"))),
        el("div", { class: "model" }, el("span", { text: "Modello" }),
          seg(models, S.model, function (id) { keepDraft(); S.model = id; render(); }, "Scegli il modello")),
        el("div", { class: "target" }, "Stai parlando con ", el("strong", { text: talking })),
        S.me.models.some(function (m) { return m.configured; }) ? null : el("p", { class: "error", text: "Nessun modello è pronto: un amministratore inserisce una chiave API nel Centro di controllo, scheda Collegamenti." }),
        el("div", { class: "adminrow" }, stopControl())),
      cardNode());
    var stage = el("div", { class: "stage" }, center, cat().agents.map(agentButton));
    setTimeout(function () { var log = document.getElementById("log"); if (log) log.scrollTop = log.scrollHeight; }, 0);
    return el("main", {}, stage);
  }

  // ---------- vista Ufficio 3D ----------
  var OFFICE = null, SIG = {};
  function canOffice() { return typeof SFBOffice !== "undefined" && SFBOffice.supported(); }
  /* Stanza provvisoria ricavata da /me, usata finché non arriva la prima risposta di /summary?office=1. */
  function provisional() {
    var stopped = !!S.me.tenant.stopped;
    return { stopped: stopped, central: { id: "centrale", name: "Centrale", state: stopped ? "fermo" : "pronto", detail: "" },
      agents: cat().agents.filter(function (a) { return entitled(a.id); }).map(function (a) {
        return { id: a.id, name: a.name, station: a.station || "scrivania", tone: a.tone, detail: stateOf(a.id), pending: 0,
                 state: stopped ? "fermo" : (stateOf(a.id) === "Da collegare" ? "da_collegare" : "pronto") };
      }) };
  }
  function officeSnap() { return S.osnap || provisional(); }
  function officeItem(id) { var o = officeSnap(); return id === "centrale" ? o.central : o.agents.filter(function (a) { return a.id === id; })[0]; }
  function pickOffice(id) {
    keepDraft();
    S.osel = id || null;
    S.agent = !id || id === "centrale" ? null : id;
    S.focus = S.agent;
    S.odetail = null;
    render();
    pollOffice();
    if (id && window.matchMedia("(max-width: 900px)").matches) { var p = document.getElementById("opanel"); if (p) p.scrollIntoView({ block: "start", behavior: "smooth" }); }
  }
  /* Parti della scheda che cambiano da sole: `live` le crea, `relive` le ridisegna solo se i dati sono cambiati,
     così l'aggiornamento continuo non tocca la casella in cui si sta scrivendo. */
  function live(id, cls, data, build) { SIG[id] = JSON.stringify(data); return el("div", { id: id, class: cls }, build(data)); }
  function relive(id, data, build) {
    var n = document.getElementById(id), sig = JSON.stringify(data);
    if (!n || SIG[id] === sig) return;
    SIG[id] = sig; n.textContent = ""; append(n, build(data));
  }
  function stateChip(item) {
    return el("span", { class: "ostate st-" + item.state }, el("i", {}), (SFBOffice.STATES[item.state] || { label: item.state }).label);
  }
  function bHead(item) { return [stateChip(item), el("p", { class: "odetail", text: item.detail || "" })]; }
  function bList(rows) {
    return rows.map(function (r) {
      var b = el("button", { class: "orow", type: "button", onclick: function () { pickOffice(r.id); } },
        el("span", { class: "tone" }), el("span", { class: "n", text: r.id === "centrale" ? "Centrale" : "Agente " + r.name }), stateChip(r));
      b.style.setProperty("--tone", r.tone || "#ff9a4d");
      return b;
    });
  }
  function bPending(list) {
    if (!list.length) return null;
    var admin = S.me.user.role === "admin", err = el("p", { class: "error", role: "alert" });
    function decide(id, verb) {
      return function () { api("POST", "/approvals/" + id + "/" + verb, {}).then(function () { refreshPending(); pollOffice(); }).catch(function (e) { err.textContent = e.message; }); };
    }
    return [el("h3", { text: "In attesa di approvazione" }), err, list.map(function (a) {
      return el("div", { class: "item" },
        el("div", { class: "body" }, a.summary, el("small", { text: "Richiesta n. " + a.id + " di " + a.requested_by_name + " · " + when(a.created_at) })),
        admin ? el("div", { class: "acts" },
          el("button", { class: "btn small primary", type: "button", text: "Approva ed esegui", onclick: decide(a.id, "approve") }),
          el("button", { class: "btn small danger", type: "button", text: "Rifiuta", onclick: decide(a.id, "reject") })) : null);
    })];
  }
  function logData(item, d) { return { now: item.state === "ragiona" || item.state === "lavora" || item.state === "monta" ? item.detail : "", log: d ? d.log : null }; }
  function bLog(x) {
    if (!x.log) return el("p", { class: "empty", text: "Carico le attività..." });
    if (!x.log.length && !x.now) return el("p", { class: "empty", text: "Nessuna attività registrata finora." });
    return el("ul", { class: "olog" }, x.now ? el("li", { class: "now" }, el("time", { text: "adesso" }), el("span", { text: x.now })) : null,
      x.log.map(function (r) { return el("li", {}, el("time", { text: when(r.when) }), el("span", { text: r.text })); }));
  }
  var OUTURL = {};  // i collegamenti ai file cambiano firma a ogni richiesta: tenerli fuori dal confronto evita di ricaricare il video mentre lo si guarda
  function outData(id, d) {
    return { central: id === "centrale", out: d ? d.outputs.map(function (o) {
      var key = o.kind + "|" + o.title + "|" + o.when;
      if (o.url && !OUTURL[key]) OUTURL[key] = o.url;
      return { kind: o.kind, title: o.title, label: o.label, text: o.text, when: o.when, mime: o.mime || "", key: o.url ? key : "" };
    }) : null };
  }
  function preview(o) {
    var url = o.key && OUTURL[o.key];
    if (!url) return null;
    if (o.kind === "video") return el("video", { class: "opreview", controls: true, preload: "metadata", src: url });
    if (o.kind === "audio") return el("audio", { class: "opreview", controls: true, preload: "metadata", src: url });
    return el("img", { class: "opreview", alt: o.title, src: url });
  }
  function bOut(x) {
    if (x.central) return el("p", { class: "empty", text: "Il Centrale non produce elaborati suoi: li trovi alla postazione dell'agente che ha fatto lavorare." });
    if (!x.out) return el("p", { class: "empty", text: "Carico gli elaborati..." });
    if (!x.out.length) return el("p", { class: "empty", text: "Nessun elaborato finora. Bozze, lead, appuntamenti e video creati da questo agente compaiono qui." });
    return x.out.map(function (o) {
      return el("article", { class: "oout" },
        el("header", {}, el("span", { class: "chip", text: o.kind }), el("strong", { text: o.title }), o.label ? el("small", { text: o.label }) : null),
        preview(o), o.text ? el("div", { class: "pre", text: o.text }) : null, el("small", { text: when(o.when) }));
    });
  }
  function rowsData(snap) { return [{ id: "centrale", name: "Centrale", state: snap.central.state, tone: "" }].concat(snap.agents.map(function (a) { return { id: a.id, name: a.name, state: a.state, tone: a.tone }; })); }
  function officeOverview(snap) {
    var admin = S.me.user.role === "admin";
    return el("aside", { class: "opanel", id: "opanel", "aria-label": "Ufficio" },
      el("h2", { text: "Ufficio di " + S.me.tenant.name }),
      el("p", { class: "sub", id: "stripText", text: stripText() }),
      S.me.tenant.stopped ? el("div", { class: "banner stopbanner", role: "alert", text: "Arresto d'emergenza attivo: chat, lavori automatici e azioni approvate sono fermi." }) : null,
      el("p", { class: "sub", text: snap.agents.length ? "Clicca un agente o la sua postazione per aprire la chat diretta, le attività in tempo reale e gli elaborati."
                                                       : "Nessun agente acceso: un amministratore li sceglie nel Centro di controllo, scheda Agenti." }),
      live("oList", "olist", rowsData(snap), bList),
      el("p", { class: "ohint", text: "Trascina per ruotare · rotella o due dita per lo zoom · Maiusc e trascina per spostare · frecce, + e − da tastiera" }),
      el("div", { class: "acts" },
        admin ? el("button", { class: "btn small", type: "button", text: "Scegli gli agenti", onclick: function () { keepDraft(); S.view = "centro"; S.tab = "agenti"; render(); } }) : null,
        stopControl()));
  }
  function officePanel(id) {
    var item = officeItem(id), a = id === "centrale" ? null : agentById(id), d = S.odetail && S.odetail.agent.id === id ? S.odetail : null;
    var talking = a ? "Agente " + a.name : "Centrale", stopped = S.me.tenant.stopped;
    var models = S.me.models.map(function (m) {
      return { id: m.id, label: m.name, disabled: !m.configured, title: m.configured ? null : "Manca la chiave API di questo modello" };
    });
    return el("aside", { class: "opanel", id: "opanel", "aria-label": "Postazione di " + talking },
      el("div", { class: "ohead" },
        el("button", { class: "btn small", type: "button", text: "← Ufficio", onclick: function () { pickOffice(null); } }),
        el("h2", { text: talking })),
      live("oHead", "olive", { state: item.state, detail: item.detail }, bHead),
      el("p", { class: "sub", text: a ? a.does : "Coordina tutti gli agenti accesi: scrivi a lui quando non sai a chi chiedere." }),
      live("oPending", "opend", d ? d.pending : [], bPending),
      el("h3", { text: "Chat diretta" }),
      el("form", { class: "chat", autocomplete: "off", onsubmit: sendChat },
        el("div", { class: "log", id: "log", "aria-live": "polite" }, th(id).log.map(messageNode),
          S.busy && S.busyFor === id ? el("div", { class: "thinking", text: talking + " sta lavorando..." }) : null),
        el("div", { class: "bar" },
          el("label", { for: "chatInput", hidden: true, text: "Messaggio" }),
          el("input", { id: "chatInput", type: "text", placeholder: "Scrivi a " + talking + "...", value: S.draft, maxlength: "8000" }),
          el("button", { class: "send", type: "submit", "aria-label": "Invia", disabled: S.busy || stopped }, icon("up"))),
        el("div", { class: "model" }, el("span", { text: "Modello" }),
          seg(models, S.model, function (m) { keepDraft(); S.model = m; render(); }, "Scegli il modello")),
        S.me.models.some(function (m) { return m.configured; }) ? null : el("p", { class: "error", text: "Nessun modello è pronto: un amministratore inserisce una chiave API nel Centro di controllo, scheda Collegamenti." })),
      el("h3", { text: "Attività in tempo reale" }),
      live("oLog", "ologbox", logData(item, d), bLog),
      el("h3", { text: "Elaborati generati" }),
      live("oOut", "ooutbox", outData(id, d), bOut));
  }
  function paintOfficeLive() {
    if (!S.me || !S.me.tenant || S.view !== "ufficio") return;
    var snap = officeSnap(), id = S.osel, item = id ? officeItem(id) : null, d;
    if (id && !item) { pickOffice(null); return; }
    if (!id) { relive("oList", rowsData(snap), bList); return; }
    d = S.odetail && S.odetail.agent.id === id ? S.odetail : null;
    relive("oHead", { state: item.state, detail: item.detail }, bHead);
    relive("oPending", d ? d.pending : [], bPending);
    relive("oLog", logData(item, d), bLog);
    relive("oOut", outData(id, d), bOut);
  }
  /* Chiede al server lo stato di tutti gli agenti e, se una postazione è aperta, il suo lavoro recente. */
  function pollOffice() {
    if (!S.me || !S.me.tenant || S.view !== "ufficio" || document.visibilityState === "hidden") return;
    var id = S.osel;
    if (!S.opoll) {
      S.opoll = true;
      api("GET", "/summary?office=1").then(function (out) {
        S.opoll = false;
        if (!S.me || !S.me.tenant) return;
        S.pending = out.pending; S.sum = out; S.osnap = out.office;
        var ids = out.office.agents.map(function (a) { return a.id; }).sort().join(","), set = function (n, text) { var x = document.getElementById(n); if (x) x.textContent = text; };
        if (S.me.tenant.stopped !== out.stopped) { S.me.tenant.stopped = out.stopped; keepDraft(); render(); return; }
        if (ids !== S.me.entitled.slice().sort().join(",") && S.oreload !== ids) { S.oreload = ids; keepDraft(); loadMe(); return; }  // il team è cambiato altrove
        if (S.view === "ufficio" && OFFICE) OFFICE.update(out.office, S.me.tenant.name);
        set("tickerText", tickerText()); set("stripText", stripText());
        paintOfficeLive();
      }).catch(function () { S.opoll = false; });
    }
    if (id && !S.opollD) {
      S.opollD = true;
      api("GET", "/office/agents/" + id).then(function (d) { S.opollD = false; if (S.osel === id) { S.odetail = d; paintOfficeLive(); } })
        .catch(function () { S.opollD = false; });
    }
  }
  function viewUfficio() {
    if (!OFFICE) OFFICE = SFBOffice.create({ onPick: pickOffice, onCompact: function () { keepDraft(); S.view = "agente"; render(); } });
    var snap = officeSnap();
    if (S.osel && !officeItem(S.osel)) { S.osel = null; S.agent = null; S.focus = null; S.odetail = null; }
    OFFICE.update(snap, S.me.tenant.name);
    OFFICE.select(S.osel);
    setTimeout(function () { OFFICE.resume(); var log = document.getElementById("log"); if (log) log.scrollTop = log.scrollHeight; }, 0);
    return el("main", { class: "office" }, OFFICE.node, S.osel ? officePanel(S.osel) : officeOverview(snap));
  }

  // ---------- Centro di controllo ----------
  function tabsFor() {
    var all = [["approvazioni", "Approvazioni"], ["novita", "Novità"], ["archivio", "Archivio Media"], ["computer", "Computer"], ["memoria", "Memoria"], ["dati", "Dati"], ["pacchetti", "Pacchetti"], ["abbonamento", "Abbonamento"], ["crediti", "Crediti"]];
    if (S.me.user.role === "admin") all = [["approvazioni", "Approvazioni"], ["novita", "Novità"], ["agenti", "Agenti"], ["automazioni", "Automazioni"],
      ["archivio", "Archivio Media"], ["computer", "Computer"], ["memoria", "Memoria"], ["collegamenti", "Collegamenti"], ["registro", "Registro"], ["utenti", "Utenti"], ["dati", "Dati"],
      ["consumi", "Consumi"], ["pacchetti", "Pacchetti"], ["abbonamento", "Abbonamento"], ["crediti", "Crediti"]];
    return all;
  }
  function viewCentro() {
    var body = el("div", { id: "tabBody" }, el("p", { class: "empty", text: "Carico..." }));
    var tabs = el("div", { class: "tabs", role: "group", "aria-label": "Sezioni" }, tabsFor().map(function (t) {
      return el("button", { type: "button", "aria-pressed": String(S.tab === t[0]), text: t[1], onclick: function () { S.tab = t[0]; render(); } });
    }));
    if (!tabsFor().some(function (t) { return t[0] === S.tab; })) S.tab = "approvazioni";
    var loader = { approvazioni: tabApprovazioni, registro: tabRegistro, collegamenti: tabCollegamenti, utenti: tabUtenti,
                   dati: tabDati, consumi: tabConsumi, pacchetti: tabPacchetti, novita: tabNovita, automazioni: tabAutomazioni,
                   memoria: tabMemoria, agenti: tabAgenti, archivio: tabArchivio, computer: tabComputer, abbonamento: tabAbbonamento, crediti: tabCrediti }[S.tab];
    Promise.resolve().then(loader).then(function (nodes) { body.textContent = ""; append(body, nodes); })
      .catch(function (err) { body.textContent = ""; append(body, el("p", { class: "error", role: "alert", text: err.message })); });
    return el("section", {}, tabs,
      S.me.tenant.stopped ? el("div", { class: "banner", role: "alert" }, "Arresto d'emergenza attivo: gli agenti sono fermi. ", stopControl()) : null,
      body);
  }
  function reloadTab() { if (S.me) { refreshPending(); render(); } }
  function act(promise, errorBox) {
    return promise.then(reloadTab).catch(function (err) { if (errorBox) errorBox.textContent = err.message; });
  }

  function tabApprovazioni() {
    return api("GET", "/approvals").then(function (out) {
      var admin = S.me.user.role === "admin", err = el("p", { class: "error", role: "alert" });
      S.pending = out.approvals.filter(function (a) { return a.status === "pending"; }).length;
      var items = out.approvals.map(function (a) {
        var st = STATUS[a.status] || [a.status, ""];
        return el("div", { class: "item" },
          el("div", { class: "body" }, a.summary,
            el("small", { text: "Richiesta n. " + a.id + " di " + a.requested_by_name + " · " + when(a.created_at) + " · agente " + (agentById(a.agent_id) || { name: a.agent_id }).name }),
            a.result ? el("small", { text: "Esito: " + a.result }) : null),
          el("div", { class: "acts" }, el("span", { class: "chip " + st[1], text: st[0] }),
            admin && a.status === "pending" ? [
              el("button", { class: "btn small primary", type: "button", text: "Approva ed esegui", onclick: function () { act(api("POST", "/approvals/" + a.id + "/approve", {}), err); } }),
              el("button", { class: "btn small danger", type: "button", text: "Rifiuta", onclick: function () { act(api("POST", "/approvals/" + a.id + "/reject", {}), err); } })] : null));
      });
      return el("div", { class: "panel" }, el("h2", { text: "Approvazioni" }),
        el("p", { text: "Ogni azione verso l'esterno (email, Telegram, WhatsApp) aspetta qui. Parte solo quando un amministratore la approva." }),
        err, items.length ? items : el("p", { class: "empty", text: "Nessuna richiesta." }));
    });
  }
  function tabRegistro() {
    return Promise.all([api("GET", "/audit?limit=200"), api("GET", "/audit/verify")]).then(function (res) {
      var v = res[1];
      return [
        el("div", { class: "banner" + (v.ok ? " good" : ""), text: v.ok
          ? "Registro integro: " + v.entries + " righe verificate. Ultimo hash: " + v.last_hash
          : "Registro manomesso: la catena si interrompe alla riga con id " + v.first_broken_id + "." }),
        el("div", { class: "panel" }, el("h2", { text: "Registro delle azioni" }),
          el("p", { text: "Ogni riga contiene l'hash della precedente: una modifica o una cancellazione rompe la catena. Qui non compare il testo dei messaggi." }),
          table(["Quando", "Utente", "Azione", "Dettaglio"], res[0].entries.map(function (e) {
            return [when(e.created_at), e.user_email || "sistema", e.action, JSON.stringify(e.detail)];
          })))];
    });
  }
  function tabCollegamenti() {
    return Promise.all([api("GET", "/connectors"), api("GET", "/google")]).then(function (res) {
      var out = res[0], nodes = [googlePanel(res[1])];
      if (S.me.tenant.own_keys_only && !S.me.models.some(function (m) { return m.configured; })) {
        nodes.unshift(el("div", { class: "banner", text: "La tua azienda usa le proprie chiavi dei modelli: inseriscine almeno una qui sotto, alla voce «Chiavi API dei modelli», altrimenti gli agenti non possono rispondere." }));
      }
      if (!out.secrets_encrypted) nodes.push(el("div", { class: "banner", text: "Le credenziali vengono salvate in chiaro nel database del server: manca SFB_SECRET_KEY o il pacchetto cryptography. Vedi il file LEGGIMI." }));
      out.connectors.forEach(function (c) {
        var err = el("p", { class: "error", role: "alert" });
        if (!c.available) {
          nodes.push(el("div", { class: "panel" }, el("h2", { text: c.label }),
            el("p", { text: "Serve all'Agente " + agentById(c.agent).name + ", che entra con il pacchetto " + cat().packages[tierOf(c.agent)].name + "." })));
          return;
        }
        var form = el("form", { class: "form row", autocomplete: "off", onsubmit: function (e) {
          e.preventDefault();
          var body = {};
          c.fields.forEach(function (f) { body[f.key] = val("c_" + c.name + "_" + f.key); });
          act(api("PUT", "/connectors/" + c.name, body), err);
        } }, c.fields.map(function (f) {
          return field(f.label, "c_" + c.name + "_" + f.key, { type: f.secret ? "password" : "text", value: f.value || "",
            placeholder: f.secret ? (f.set ? "impostato (lascia vuoto per non cambiarlo)" : "non impostato") : "", autocomplete: "off" });
        }), el("div", { class: "acts" },
          el("button", { class: "btn primary", type: "submit", text: "Salva" }),
          el("button", { class: "btn danger", type: "button", text: "Rimuovi", onclick: function () { act(api("DELETE", "/connectors/" + c.name), err); } })));
        nodes.push(el("div", { class: "panel" },
          el("h2", {}, c.label + " ", el("span", { class: "chip " + (c.ready ? "done" : "wait"), text: c.name === "modelli" ? (S.me.tenant.own_keys_only ? "necessario" : "facoltativo") : (c.ready ? "configurato" : "da configurare") })),
          c.problem ? el("p", { class: "error", text: c.problem }) : null, form, err));
      });
      return nodes;
    });
  }
  function tabUtenti() {
    return api("GET", "/users").then(function (out) {
      var err = el("p", { class: "error", role: "alert" });
      var rows = out.users.map(function (u) {
        var self = u.id === S.me.user.id;
        return [u.name, u.email, ROLE[u.role], u.active ? "attivo" : "disattivato", el("div", { class: "acts" },
          el("button", { class: "btn small", type: "button", text: u.role === "admin" ? "Rendi operatore" : "Rendi amministratore",
            onclick: function () { act(api("POST", "/users/" + u.id, { role: u.role === "admin" ? "member" : "admin" }), err); } }),
          self ? null : el("button", { class: "btn small danger", type: "button", text: u.active ? "Disattiva" : "Riattiva",
            onclick: function () { act(api("POST", "/users/" + u.id, { active: !u.active }), err); } }))];
      });
      var add = out.can_add ? el("form", { class: "form row", autocomplete: "off", onsubmit: function (e) {
        e.preventDefault();
        act(api("POST", "/users", { name: val("nuName"), email: val("nuEmail"), role: val("nuRole"), password: val("nuPassword") }), err);
      } }, field("Nome", "nuName", { required: true }), field("Email", "nuEmail", { type: "email", required: true }),
        el("div", { class: "field" }, el("label", { for: "nuRole", text: "Ruolo" }), el("select", { id: "nuRole" },
          el("option", { value: "member", text: "Operatore" }), el("option", { value: "admin", text: "Amministratore" }))),
        field("Password (almeno 10 caratteri)", "nuPassword", { type: "password", required: true, autocomplete: "new-password" }),
        el("button", { class: "btn primary", type: "submit", text: "Aggiungi utente" }))
        : el("p", { class: "empty", text: inCeiling("team") ? "Per aggiungere utenti attiva l'Agente Team nella scheda Agenti."
                                                              : "Più utenti per azienda entrano con il pacchetto " + cat().packages[tierOf("team")].name + "." });
      return el("div", { class: "panel" }, el("h2", { text: "Utenti" }),
        el("p", { text: "Gli operatori usano gli agenti. Solo gli amministratori approvano le azioni, cambiano i collegamenti e vedono il registro." }),
        table(["Nome", "Email", "Ruolo", "Stato", ""], rows), err, add);
    });
  }
  function tabDati() {
    return Promise.all([api("GET", "/data/leads"), api("GET", "/data/events"), api("GET", "/data/drafts")]).then(function (res) {
      function block(title, note, headers, rows) {
        return el("div", { class: "panel" }, el("h2", { text: title }), el("p", { text: note }),
          rows.length ? table(headers, rows) : el("p", { class: "empty", text: "Ancora niente." }));
      }
      return [
        block("Lead", "Salvati dall'Agente CRM Lead.", ["N.", "Nome", "Azienda", "Email", "Telefono", "Stato", "Note"],
          res[0].rows.map(function (r) { return [r.id, r.name, r.company, r.email, r.phone, r.status, r.notes]; })),
        block("Calendario", "Appuntamenti salvati dall'Agente Calendario (calendario interno alla piattaforma).", ["Inizio", "Fine", "Titolo", "Luogo", "Note"],
          res[1].rows.map(function (r) { return [when(r.starts_at), when(r.ends_at), r.title, r.location, r.notes]; })),
        block("Bozze", "Testi, post, campagne, copioni e verbali preparati dagli agenti. La pubblicazione è manuale.", ["N.", "Canale", "Titolo", "Testo", "Quando"],
          res[2].rows.map(function (r) { return [r.id, r.channel, r.title, r.body, when(r.created_at)]; }))];
    });
  }
  function tabCrediti() {
    return api("GET", "/credits").then(function (c) {
      var n = function (v) { return Number(v || 0).toLocaleString("it-IT"); }, it = function (d) { return (d || "").split("-").reverse().join("/"); };
      var admin = S.me.user.role === "admin", err = el("p", { class: "error", role: "alert" }), msg = S.payMsg;
      S.payMsg = "";
      if (!c.enabled) {
        return el("div", { class: "panel" }, el("h2", { text: "Crediti" }), el("p", { id: "creditsOff", text: "I crediti non sono attivi su questa piattaforma: l'uso dei modelli segue le regole del tuo pacchetto (scheda Consumi)." }));
      }
      var qty = el("select", { id: "creditQty", "aria-label": "Quante ricariche" }, [1, 2, 3, 5, 10].map(function (q) {
        return el("option", { value: String(q), text: q + (q === 1 ? " ricarica" : " ricariche") + " · " + n(q * c.pack.size) + " crediti" });
      }));
      var buy = el("button", { class: "btn primary", type: "button", id: "creditBuy", text: "Compra con carta", onclick: function () {
        buy.disabled = true; err.textContent = "";
        api("POST", "/credits/checkout", { quantity: Number(qty.value) }).then(function (out) { window.location.href = out.url; })  // il pagamento si fa sulla pagina di Stripe
          .catch(function (e) { buy.disabled = false; err.textContent = e.message; });
      } });
      return [el("div", { class: "panel" }, el("h2", { text: "Crediti" }),
        msg ? el("p", { class: "okmsg", role: "status", text: msg }) : null,
        el("p", { class: "bignum", id: "creditsNow" }, el("b", { text: n(c.available) }), " crediti disponibili"),
        el("ul", { class: "caplist" },
          el("li", {}, el("span", { class: "pill ok", text: "inclusi" }), el("span", { id: "creditsIncluded", text: n(c.included_left) + " su " + n(c.included) + " del pacchetto. Si ricaricano il " + it(c.resets_on) + "; quelli non usati non si sommano al mese dopo." })),
          el("li", {}, el("span", { class: "pill ok", text: "comprati" }), el("span", { id: "creditsExtra", text: n(c.extra) + " crediti comprati o ricevuti. Non scadono e si usano solo dopo quelli inclusi." }))),
        c.available <= 0 ? el("div", { class: "banner", role: "alert", text: "Crediti finiti: gli agenti che usano i modelli della piattaforma sono fermi fino alla ricarica del " + it(c.resets_on) + ", oppure finché non ne compri altri." }) : null,
        !c.pack.buyable ? el("p", { class: "empty", text: "L'acquisto di ricariche non è attivo: per averne altri contatta chi ti ha venduto il servizio." })
          : (admin ? el("div", { class: "acts" }, el("div", { class: "field" }, qty), buy) : el("p", { class: "empty", text: "Le ricariche le compra un amministratore dell'azienda." })),
        err),
      el("div", { class: "panel" }, el("h2", { text: "Quanto costa ogni modello" }),
        el("p", { text: "Il costo dipende dal modello che scegli nella chat. Il modello «Locale» e le chiavi proprie dell'azienda non consumano crediti." }),
        table(["Modello", "Crediti ogni 1.000 token", "Usati questo mese"], c.rates.map(function (r) {
          var used = c.month.filter(function (m) { return m.model === r.model; })[0];
          return [r.name, r.per_1000 ? n(r.per_1000) : "gratis", n(used ? used.credits : 0)];
        }))),
      c.moves.length ? el("div", { class: "panel" }, el("h2", { text: "Ricariche" }),
        table(["Quando", "Tipo", "Crediti"], c.moves.map(function (m) { return [when(m.created_at), m.kind === "acquisto" ? "Comprati con carta" : "Dal gestore", (m.amount > 0 ? "+" : "") + n(m.amount)]; }))) : null];
    });
  }
  function tabAbbonamento() {
    return api("GET", "/billing").then(function (b) {
      var sub = S.me.tenant.subscription, admin = S.me.user.role === "admin", err = el("p", { class: "error", role: "alert" });
      var it = function (d) { return (d || "").split("-").reverse().join("/"); }, day = it(sub.paid_until), msg = S.payMsg;
      S.payMsg = "";
      function go(path, btn) {  // sia il pagamento sia la gestione si fanno sulle pagine di Stripe: qui la carta non si scrive mai
        return function () {
          btn.disabled = true; err.textContent = "";
          api("POST", path, {}).then(function (out) { window.location.href = out.url; }).catch(function (e) { btn.disabled = false; err.textContent = e.message; });
        };
      }
      var pay = el("button", { class: "btn primary", type: "button", text: sub.paid_until ? "Rinnova con carta" : "Paga con carta" });
      var manage = el("button", { class: "btn", type: "button", id: "portalBtn", text: "Carta, ricevute e disdetta" });
      pay.onclick = go("/billing/checkout", pay); manage.onclick = go("/billing/portal", manage);
      var state = !sub.paid_until ? "Nessuna scadenza impostata."
        : (sub.expired ? "Scaduto il " + day + ": gli agenti sono in pausa."
          : (sub.grace ? "Scaduto il " + day + ": gli agenti lavorano ancora " + (sub.grace_left === 0 ? "solo oggi" : "per " + sub.grace_left + (sub.grace_left === 1 ? " giorno" : " giorni")) + ", poi vanno in pausa."
            : "Attivo fino al " + day + " compreso."));
      return el("div", { class: "panel" }, el("h2", { text: "Abbonamento" }),
        msg ? el("p", { class: "okmsg", role: "status", text: msg }) : null,
        el("p", { id: "subLine", text: "Pacchetto " + S.me.tenant.package_name + ". " + state }),
        b.payment_failed ? el("div", { class: "banner", role: "alert", id: "payFailed", text: "L'ultimo addebito sulla carta non è riuscito. Stripe riproverà nei prossimi giorni: intanto controlla o cambia la carta." }) : null,
        b.subscribed ? el("p", { id: "renewLine", text: b.cancel_at ? "Disdetta registrata: la carta non verrà più addebitata e l'abbonamento finisce il " + it(b.cancel_at) + "."
                                                                    : "Pagamento con carta attivo: si rinnova da solo ogni mese." }) : null,
        !b.enabled && !b.portal ? el("p", { class: "empty", text: "Il pagamento con carta non è attivo: per rinnovare contatta chi ti ha venduto il servizio." })
          : (admin ? el("div", { class: "acts" }, b.enabled && !b.subscribed ? pay : null, b.portal ? manage : null)
                   : el("p", { class: "empty", text: "Pagamento e disdetta li fa un amministratore dell'azienda." })),
        err);
    });
  }
  function tabConsumi() {
    return api("GET", "/usage").then(function (out) {
      return el("div", { class: "panel" }, el("h2", { text: "Consumi dei modelli" }),
        el("p", { text: "Token usati per mese e per modello. Servono a calcolare il costo dell'azienda con i listini dei fornitori." }),
        out.token_cap ? el("p", { id: "capLine", text: "Tetto del mese: usati " + out.tokens_month.toLocaleString("it-IT") + " token su " + out.token_cap.toLocaleString("it-IT") +
          (out.tokens_month >= out.token_cap ? ". Tetto raggiunto: gli agenti riprendono il mese prossimo o quando il gestore lo alza." : ".") }) : null,
        out.usage.length ? table(["Mese", "Modello", "Chiamate", "Token in ingresso", "Token in uscita"], out.usage.map(function (u) {
          return [u.month, u.model, u.calls, u.input_tokens, u.output_tokens];
        })) : el("p", { class: "empty", text: "Ancora nessun consumo." }));
    });
  }
  function tabPacchetti() {
    var packs = cat().packages, mine = myPackIndex(), rows = [];
    packs.forEach(function (p, pi) {
      p.adds.forEach(function (id) {
        var a = agentById(id);
        rows.push([a.name].concat(packs.map(function (_, i) { return pi <= i ? "sì" : "–"; }), [a.acts, a.ext]));
      });
    });
    return el("div", { class: "panel" }, el("h2", { text: "Pacchetti" }),
      el("p", { text: "La tua azienda ha il pacchetto " + packs[mine].name + ". Ogni pacchetto contiene tutto quello del precedente più gli agenti nuovi." }),
      table(["Agente"].concat(packs.map(function (p, i) { return p.name + " (" + countUpTo(i) + ")"; }), ["Azioni", "Cosa serve"]), rows));
  }

  function textarea(label, id, attrs) {
    return el("div", { class: "field wide" }, el("label", { for: id, text: label }), el("textarea", Object.assign({ id: id, rows: "4" }, attrs || {})));
  }
  function select(label, id, options, current) {
    return el("div", { class: "field" }, el("label", { for: id, text: label }), el("select", { id: id }, options.map(function (o) {
      return el("option", { value: o[0], selected: o[0] === current, text: o[1] });
    })));
  }
  function googlePanel(g) {
    var err = el("p", { class: "error", role: "alert" }), flash = null;
    if (S.flash) {
      flash = el("p", { class: S.flash === "ok" ? "okmsg" : "error", role: "status", text: GOOGLE_RESULT[S.flash] || "Collegamento non riuscito." });
      S.flash = "";
    }
    var head = el("h2", {}, "Google: Gmail, Calendar, Fogli ", el("span", { class: "chip " + (g.connected && !g.broken ? "done" : "wait"),
      text: g.connected ? (g.broken ? "da ricollegare" : "collegato") : "da collegare" }));
    if (!g.configured) {
      return el("div", { class: "panel" }, head, flash,
        el("p", { text: "L'accesso Google non è ancora configurato sulla piattaforma. Il gestore crea un client OAuth di tipo «Applicazione web» nella console Google Cloud, mette GOOGLE_CLIENT_ID e GOOGLE_CLIENT_SECRET nel file .env e registra questo indirizzo di ritorno:" }),
        el("p", { class: "mono", text: g.redirect_uri }));
    }
    var boxes = el("div", { class: "checks" }, g.services.map(function (sv) {
      var on = g.connected ? g.granted.indexOf(sv.id) !== -1 : true;
      return el("label", { class: "check" }, el("input", { type: "checkbox", id: "g_" + sv.id, checked: on }), el("span", {}, sv.label, sv.note ? el("small", { text: sv.note }) : null));
    }));
    var go = el("button", { class: "btn primary", type: "button", text: g.connected ? "Ricollega con questi servizi" : "Collega Google", onclick: function () {
      var wanted = g.services.filter(function (sv) { return document.getElementById("g_" + sv.id).checked; }).map(function (sv) { return sv.id; });
      go.disabled = true;
      api("POST", "/google/start", { services: wanted }).then(function (out) { window.location.href = out.url; })
        .catch(function (e) { go.disabled = false; err.textContent = e.message; });
    } });
    return el("div", { class: "panel" }, head, flash,
      g.connected ? el("p", { text: "Collegato come " + (g.email || "account Google") + ". La posta in uscita e gli eventi su Calendar partono solo dopo approvazione." })
                  : el("p", { text: "Si apre la pagina di consenso di Google e poi torni qui. La password di Google non passa mai da Alvea." }),
      g.broken ? el("p", { class: "error", text: g.broken }) : null,
      g.storage_ok ? null : el("p", { class: "error", text: "Per collegare Google le credenziali devono essere cifrate: serve SFB_SECRET_KEY e il pacchetto cryptography (vedi LEGGIMI)." }),
      boxes, el("div", { class: "acts" }, go,
        g.connected ? el("button", { class: "btn danger", type: "button", text: "Scollega", onclick: function () { act(api("DELETE", "/google"), err); } }) : null), err);
  }
  function tabNovita() {
    return api("GET", "/news").then(function (out) {
      var err = el("p", { class: "error", role: "alert" });
      var kinds = { automazione: "lavoro automatico", approvazione: "da approvare", avviso: "avviso" };
      var items = out.news.map(function (n) {
        return el("div", { class: "item" + (n.is_read ? "" : " unread") },
          el("div", { class: "body" }, el("strong", { text: n.title }), n.body ? el("div", { class: "pre", text: n.body }) : null,
            el("small", { text: when(n.created_at) })),
          el("div", { class: "acts" }, el("span", { class: "chip" + (n.kind === "avviso" ? " bad" : (n.kind === "approvazione" ? " wait" : " done")), text: kinds[n.kind] || n.kind })));
      });
      return el("div", { class: "panel" }, el("h2", { text: "Novità" }),
        el("p", { text: "Quello che è successo mentre non guardavi: esiti dei lavori automatici, azioni in attesa, avvisi." }),
        out.news.some(function (n) { return !n.is_read; }) ? el("div", { class: "acts" }, el("button", { class: "btn small", type: "button", text: "Segna tutte come lette",
          onclick: function () { act(api("POST", "/news/read", {}), err); } })) : null,
        err, items.length ? items : el("p", { class: "empty", text: "Ancora nessuna novità." }));
    });
  }
  function tabAutomazioni() {
    return api("GET", "/automations").then(function (out) {
      var err = el("p", { class: "error", role: "alert" });
      var agentName = function (id) { return id === "centrale" ? "Centrale" : (agentById(id) || { name: id }).name; };
      var items = out.automations.map(function (a) {
        return el("div", { class: "item" },
          el("div", { class: "body" }, el("strong", { text: a.name }),
            el("small", { text: agentName(a.agent_id) + " · " + a.model + " · " + a.schedule + (a.active ? " · prossimo giro " + a.next_run : "") }),
            el("div", { class: "pre", text: "Richiesta: " + a.prompt }),
            a.last_run_at ? el("small", { text: "Ultimo giro " + when(a.last_run_at) + ": " + (a.last_status === "ok" ? "fatto" : "errore") }) : el("small", { text: "Mai eseguito" }),
            a.last_output ? el("div", { class: "pre dim", text: a.last_output }) : null),
          el("div", { class: "acts" }, el("span", { class: "chip " + (a.active ? "done" : "wait"), text: a.active ? "attivo" : "in pausa" }),
            el("button", { class: "btn small", type: "button", text: "Esegui ora", onclick: function (e) { e.target.disabled = true; act(api("POST", "/automations/" + a.id + "/run", {}), err); } }),
            el("button", { class: "btn small", type: "button", text: a.active ? "Metti in pausa" : "Riattiva", onclick: function () { act(api("POST", "/automations/" + a.id, { active: !a.active }), err); } }),
            el("button", { class: "btn small danger", type: "button", text: "Rimuovi", onclick: function () { act(api("DELETE", "/automations/" + a.id), err); } })));
      });
      var agents = [["centrale", "Centrale (tutti gli agenti)"]].concat(S.me.entitled.map(function (id) { return [id, "Agente " + agentById(id).name]; }));
      var models = S.me.models.filter(function (m) { return m.configured; }).map(function (m) { return [m.id, m.name]; });
      var days = el("div", { class: "checks row" }, DAYS.map(function (d, i) {
        return el("label", { class: "check" }, el("input", { type: "checkbox", id: "au_d" + i, checked: i < 5 }), el("span", { text: d }));
      }));
      var form = el("form", { class: "form row", autocomplete: "off", onsubmit: function (e) {
        e.preventDefault();
        var t = (val("au_time") || "08:00").split(":"), body = { name: val("au_name"), agent_id: val("au_agent"), model: val("au_model"),
          prompt: val("au_prompt"), kind: val("au_kind") };
        if (body.kind === "daily") { body.hour = Number(t[0]); body.minute = Number(t[1]); body.weekdays = DAYS.map(function (_, i) { return i; }).filter(function (i) { return document.getElementById("au_d" + i).checked; }); }
        else body.every_minutes = Number(val("au_every"));
        act(api("POST", "/automations", body), err);
      } },
        field("Nome", "au_name", { required: true, maxlength: "80", placeholder: "Posta del mattino" }),
        select("Agente", "au_agent", agents, "centrale"), select("Modello", "au_model", models, S.model),
        select("Quando", "au_kind", [["daily", "Nei giorni scelti, a un'ora"], ["interval", "Ogni tot minuti"]], "daily"),
        field("Ora (" + out.timezone + ")", "au_time", { type: "time", value: "08:00" }),
        field("Oppure ogni quanti minuti (minimo " + out.min_every + ")", "au_every", { type: "number", min: String(out.min_every), value: "60" }),
        el("div", { class: "field wide" }, el("label", { text: "Giorni (per i lavori a un'ora)" }), days),
        textarea("Richiesta per l'agente", "au_prompt", { required: true, maxlength: "4000", placeholder: "Leggi la posta non letta e dimmi le tre cose più urgenti." }),
        el("button", { class: "btn primary", type: "submit", text: "Crea lavoro automatico" }));
      var full = out.automations.length >= out.limit;
      return [el("div", { class: "panel" }, el("h2", { text: "Lavori automatici" }),
          el("p", { text: "Un agente esegue da solo una richiesta salvata. Le azioni verso l'esterno finiscono comunque nelle approvazioni. Dopo 3 errori di fila il lavoro va in pausa. " + out.automations.length + " di " + out.limit + " previsti dal pacchetto." }),
          err, items.length ? items : el("p", { class: "empty", text: "Nessun lavoro automatico." })),
        el("div", { class: "panel" }, el("h2", { text: "Nuovo lavoro" }),
          full ? el("p", { class: "empty", text: "Hai raggiunto il numero di lavori del pacchetto " + S.me.tenant.package_name + "." })
               : (models.length ? form : el("p", { class: "empty", text: "Serve almeno un modello con la chiave API configurata." })))];
    });
  }
  function tabMemoria() {
    return api("GET", "/memory").then(function (out) {
      var admin = S.me.user.role === "admin", err = el("p", { class: "error", role: "alert" }), found = el("div", {});
      var remove = function (id) { return admin ? el("button", { class: "btn small danger", type: "button", text: "Rimuovi", onclick: function () { act(api("DELETE", "/memory/" + id), err); } }) : null; };
      var nodes = [];
      if (admin && out.proposals.length) {
        nodes.push(el("div", { class: "panel" }, el("h2", { text: "Proposte degli agenti" }),
          el("p", { text: "Un agente non scrive mai in memoria da solo: propone, e decidi tu. Conferma solo ciò che è vero e che vuoi venga ricordato in ogni conversazione." }),
          out.proposals.map(function (p) {
            return el("div", { class: "item" }, el("div", { class: "body" }, p.body, el("small", { text: "Dalla chat di " + (p.proposed_in_chat_of || "?") + " · " + when(p.created_at) })),
              el("div", { class: "acts" }, el("button", { class: "btn small primary", type: "button", text: "Conferma", onclick: function () { act(api("POST", "/memory/" + p.id + "/confirm", {}), err); } }),
                el("button", { class: "btn small danger", type: "button", text: "Scarta", onclick: function () { act(api("DELETE", "/memory/" + p.id), err); } })));
          })));
      }
      nodes.push(el("div", { class: "panel" }, el("h2", { text: "Fatti sull'azienda" }),
        el("p", { text: "Frasi brevi che ogni agente legge prima di rispondere: orari, regole, nomi, preferenze. " + out.facts.length + " di " + out.limits.facts + "." }),
        out.facts.length ? out.facts.map(function (f) { return el("div", { class: "item" }, el("div", { class: "body", text: f.body }), el("div", { class: "acts" }, remove(f.id))); })
                         : el("p", { class: "empty", text: "Ancora nessun fatto." }),
        admin ? el("form", { class: "form row", autocomplete: "off", onsubmit: function (e) { e.preventDefault(); act(api("POST", "/memory/facts", { text: val("mem_fact") }), err); } },
          field("Nuovo fatto", "mem_fact", { required: true, maxlength: "600", placeholder: "Il sabato siamo aperti solo la mattina." }),
          el("button", { class: "btn primary", type: "submit", text: "Aggiungi" })) : null, err));
      var file = el("input", { type: "file", id: "mem_file", accept: ".txt,.md,.csv,text/plain", onchange: function () {
        var f = file.files[0];
        if (!f) return;
        var reader = new FileReader();
        reader.onload = function () { document.getElementById("mem_text").value = String(reader.result || ""); if (!val("mem_title")) document.getElementById("mem_title").value = f.name; };
        reader.readAsText(f);
      } });
      nodes.push(el("div", { class: "panel" }, el("h2", { text: "Documenti" }),
        el("p", { text: "Listini, condizioni, procedure. A ogni domanda il server cerca i passaggi pertinenti e li passa all'agente. " + out.documents.length + " di " + out.limits.documents + " previsti dal pacchetto. Solo testo: i PDF vanno prima convertiti." }),
        out.documents.length ? table(["Titolo", "Caratteri", "Pezzi", "Aggiunto", ""], out.documents.map(function (d) { return [d.title, d.chars, d.chunks, when(d.created_at), remove(d.id) || ""]; }))
                             : el("p", { class: "empty", text: "Ancora nessun documento." }),
        admin ? el("form", { class: "form row", autocomplete: "off", onsubmit: function (e) { e.preventDefault(); act(api("POST", "/memory/documents", { title: val("mem_title"), text: val("mem_text") }), err); } },
          field("Titolo", "mem_title", { required: true, maxlength: "150" }),
          el("div", { class: "field" }, el("label", { for: "mem_file", text: "File di testo (.txt, .md, .csv)" }), file),
          textarea("Testo", "mem_text", { required: true, rows: "6", placeholder: "Incolla qui il testo oppure scegli un file." }),
          el("button", { class: "btn primary", type: "submit", text: "Aggiungi documento" })) : null));
      nodes.push(el("div", { class: "panel" }, el("h2", { text: "Prova la ricerca" }),
        el("p", { text: "Per vedere cosa troverebbe un agente. La ricerca è per parole, non per significato." }),
        el("form", { class: "form row", autocomplete: "off", onsubmit: function (e) {
          e.preventDefault();
          api("GET", "/memory/search?q=" + encodeURIComponent(val("mem_q"))).then(function (res) {
            found.textContent = "";
            append(found, res.hits.length ? res.hits.map(function (h) { return el("div", { class: "item" }, el("div", { class: "body" }, el("strong", { text: h.kind === "fatto" ? "Fatto" : h.title }), el("div", { class: "pre", text: h.text }))); })
                                          : el("p", { class: "empty", text: "Niente di pertinente." }));
          }).catch(function (e2) { found.textContent = e2.message; });
        } }, field("Domanda", "mem_q", { required: true, maxlength: "300" }), el("button", { class: "btn", type: "submit", text: "Cerca" })), found));
      return nodes;
    });
  }

  function tabAgenti() {
    return api("GET", "/tenant/agents").then(function (out) {
      var err = el("p", { class: "error", role: "alert" });
      var items = out.agents.map(function (a) {
        var info = agentById(a.agent_id), id = "ag_i_" + a.agent_id;
        if (!a.in_ceiling) {
          return el("div", { class: "item" }, el("div", { class: "body" }, el("strong", { text: a.name }), el("small", { text: info.does })),
            el("div", { class: "acts" }, el("span", { class: "chip", text: "dal pacchetto " + a.available_from })));
        }
        return el("div", { class: "item" },
          el("div", { class: "body" }, el("strong", { text: a.name }), el("small", { text: info.does + " " + info.acts + "." }),
            el("form", { class: "form row tight", autocomplete: "off", onsubmit: function (e) {
              e.preventDefault();
              act(api("POST", "/tenant/agents", { agent_id: a.agent_id, config: { istruzioni: val(id) } }), err);
            } }, field("Istruzioni dell'azienda per questo agente (facoltative)", id, { maxlength: "1000", value: a.config.istruzioni || "", placeholder: "Es. dai sempre del lei ai clienti." }),
              el("button", { class: "btn small", type: "submit", text: "Salva" }))),
          el("div", { class: "acts" }, el("span", { class: "chip " + (a.is_active ? "done" : "wait"), text: a.is_active ? "attivo" : "spento" }),
            el("button", { class: "btn small" + (a.is_active ? " danger" : " primary"), type: "button", text: a.is_active ? "Spegni" : "Attiva",
              onclick: function () { toggleAgent(a.agent_id, !a.is_active, err); } })));
      });
      var on = out.agents.filter(function (a) { return a.is_active; }).length, can = out.agents.filter(function (a) { return a.in_ceiling; }).length;
      return el("div", { class: "panel" }, el("h2", { text: "Agenti del team" }),
        el("p", { text: "Accendi solo gli agenti che ti servono: " + on + " attivi su " + can + " disponibili con il pacchetto " + out.package_name + ". Un agente spento non risponde, non riceve strumenti e non esegue azioni, nemmeno quelle già in attesa." }),
        err, items);
    });
  }
  /* Scheda Computer: launcher, computer collegati con la loro verifica, dove gira ogni agente, lavori. */
  function tabComputer() {
    return api("GET", "/devices").then(function (out) {
      var admin = S.me.user.role === "admin", err = el("p", { class: "error", role: "alert" }), codeBox = el("p", { class: "codebox", "aria-live": "polite" });
      if (out.blocked) err.textContent = out.blocked + " La verifica del computer con il launcher resta gratuita.";
      var VERD = { gira: ["gira", "done"], non_gira: ["non gira", "bad"], in_arrivo: ["in arrivo", "wait"] };
      var devices = out.devices.map(function (d) {
        var sp = d.specs, shown = cat().agents.filter(function (a) { var c = d.checks["agent:" + a.id]; return c && VERD[c.verdict]; });
        return el("div", { class: "item" },
          el("div", { class: "body" }, el("strong", { text: d.name }),
            el("small", { text: (sp.os_version || sp.os || "") + " · " + (sp.cores || "?") + " core · " + (sp.ram_gb || "?") + " GB di memoria · " + (sp.disk_free_gb || "?") + " GB liberi · launcher " + (d.launcher_version || "?") }),
            el("div", { class: "chips" }, shown.map(function (a) {
              var c = d.checks["agent:" + a.id];
              return el("span", { class: "chip " + VERD[c.verdict][1], title: c.why, text: a.name + ": " + VERD[c.verdict][0] });
            }), d.checks["pkg:modello"] ? el("span", { class: "chip " + (d.checks["pkg:modello"].ok ? "done" : "bad"), title: d.checks["pkg:modello"].why,
                                                    text: "Modello locale: " + (d.checks["pkg:modello"].ok ? "gira" : "non gira") }) : null),
            shown.filter(function (a) { return d.checks["agent:" + a.id].verdict === "non_gira"; }).map(function (a) {
              return el("small", { text: a.name + ": " + d.checks["agent:" + a.id].why });
            })),
          el("div", { class: "acts" }, el("span", { class: "chip " + (d.online ? "done" : "wait"), text: d.online ? "acceso" : "spento" }),
            admin ? el("button", { class: "btn small danger", type: "button", text: "Scollega", onclick: function () { act(api("DELETE", "/devices/" + d.id), err); } }) : null));
      });
      var where = out.agents.filter(function (a) { return a.is_active; }).map(function (a) {
        var local = a.run.local, both = a.run.cloud && local && local.ready;
        return el("div", { class: "item" },
          el("div", { class: "body" }, el("strong", { text: a.name }), el("small", { text: local ? local.note : "Usa servizi in rete: gira sui nostri server." })),
          el("div", { class: "acts" }, both
            ? seg([{ id: "cloud", label: "Nostri server", disabled: !admin }, { id: "locale", label: "Computer", disabled: !admin }], a.mode,
                  function (m) { if (m !== a.mode) act(api("POST", "/tenant/agents", { agent_id: a.agent_id, mode: m }).then(loadMe), err); }, "Dove gira l'Agente " + a.name)
            : el("span", { class: "chip", text: !a.run.cloud ? "solo computer" : (local ? "nostri server · computer in arrivo (" + local.engine + ")" : "solo nostri server") })));
      });
      var JOB = { in_coda: ["in coda", "wait"], in_corso: ["in corso", "wait"], fatto: ["fatto", "done"], errore: ["non riuscito", "bad"] };
      var jobs = out.jobs.map(function (j) {
        var st = JOB[j.status] || [j.status, ""];
        return el("div", { class: "item" },
          el("div", { class: "body" }, "Lavoro n. " + j.id + " · Agente " + (agentById(j.agent_id) || { name: j.agent_id }).name + " · " + j.tool.replace(/_/g, " "),
            el("small", { text: (j.place === "locale" ? "sul computer" + (j.device ? " «" + j.device + "»" : "") : "sui nostri server") + " · " + when(j.created_at) }),
            j.result ? el("small", { text: j.result }) : null),
          el("div", { class: "acts" }, el("span", { class: "chip " + st[1], text: st[0] + (j.status === "in_corso" ? " " + j.progress + "%" : "") })));
      });
      return [el("div", { class: "panel" }, el("h2", { text: "Computer dell'azienda" }),
          el("p", { text: "Alcuni agenti possono girare su un tuo computer invece che sui nostri server. Il launcher controlla il computer e dice, agente per agente, se può girare lì: lo puoi fare prima di scegliere. Le chiavi dei modelli restano sempre sui nostri server." }),
          el("div", { class: "acts" },
            el("a", { class: "btn primary", href: "/api/launcher/download", download: "alvea-launcher.zip", text: "Scarica il launcher" }),
            admin ? el("button", { class: "btn", type: "button", text: "Crea codice di collegamento", onclick: function () {
              api("POST", "/devices/code", {}).then(function (c) { codeBox.textContent = c.code; codeBox.title = "Vale " + Math.round(c.expires_in / 60) + " minuti e una volta sola: scrivilo nel launcher"; })
                .catch(function (e) { err.textContent = e.message; });
            } }) : null),
          codeBox, err,
          out.packages_signed ? null : el("div", { class: "banner", role: "alert", text: "I pacchetti locali non sono firmati su questo server: i launcher non potranno installarli. Il gestore deve eseguire «manage.py firma-pacchetti»." }),
          devices.length ? devices : el("p", { class: "empty", text: "Nessun computer collegato." })),
        el("div", { class: "panel" }, el("h2", { text: "Dove girano gli agenti" }),
          el("p", { text: "Per gli agenti accesi. Montaggio video sui nostri server: " + (out.cloud_video ? "attivo." : "non attivo su questo server.") }), where),
        el("div", { class: "panel" }, el("h2", { text: "Lavori" }),
          el("p", { text: "Montaggi e operazioni eseguiti fuori dalla chat, sul computer o sui nostri server." }),
          jobs.length ? jobs : el("p", { class: "empty", text: "Nessun lavoro finora." }))];
    });
  }
  function tabArchivio() {
    return api("GET", "/files").then(function (out) {
      var err = el("p", { class: "error", role: "alert" }), admin = S.me.user.role === "admin";
      var bar = el("div", { class: "meter", role: "img", "aria-label": "Spazio usato" }, el("span", {}));
      bar.firstChild.style.width = Math.min(100, out.quota_bytes ? (100 * out.used_bytes / out.quota_bytes) : 0) + "%";
      var progress = el("p", { class: "empty" });
      var file = el("input", { type: "file", id: "up_file", required: true, accept: out.types.map(function (t) { return "." + t; }).join(",") });
      var send = el("button", { class: "btn primary", type: "submit", text: "Carica" });
      var form = el("form", { class: "form row", onsubmit: function (e) {
        e.preventDefault();
        var f = file.files[0];
        err.textContent = "";
        if (!f) return;
        var ext = f.name.indexOf(".") === -1 ? "" : f.name.split(".").pop().toLowerCase();
        if (out.types.indexOf(ext) === -1) { err.textContent = "Tipo di file non ammesso. Ammessi: " + out.types.join(", ") + "."; return; }
        if (f.size > out.max_file_bytes) { err.textContent = "Il file supera la dimensione massima di " + bytes(out.max_file_bytes) + "."; return; }
        if (out.used_bytes + f.size > out.quota_bytes) { err.textContent = "Non c'è abbastanza spazio nell'archivio."; return; }
        var data = new FormData(), xhr = new XMLHttpRequest();
        data.append("file", f, f.name);
        send.disabled = true;
        xhr.open("POST", "/api/files/upload");
        xhr.setRequestHeader("Authorization", "Bearer " + token);
        xhr.upload.onprogress = function (ev) { if (ev.lengthComputable) progress.textContent = "Caricamento: " + Math.round(100 * ev.loaded / ev.total) + "%"; };
        xhr.onerror = function () { send.disabled = false; progress.textContent = ""; err.textContent = "Caricamento interrotto: controlla la connessione e la dimensione del file."; };
        xhr.onload = function () {
          var res = {};
          try { res = JSON.parse(xhr.responseText); } catch (e2) { /* risposta non leggibile */ }
          if (xhr.status === 200) { reloadTab(); return; }
          send.disabled = false; progress.textContent = "";
          err.textContent = res.error || ("Errore " + xhr.status);
        };
        xhr.send(data);
      } }, el("div", { class: "field" }, el("label", { for: "up_file", text: "File (" + out.types.join(", ") + "; massimo " + bytes(out.max_file_bytes) + ")" }), file), send);
      var cards = out.assets.map(function (a) {
        var view = a.kind === "immagine" ? el("img", { src: a.url, alt: a.name, loading: "lazy" })
          : (a.kind === "video" ? el("video", { src: a.url, controls: true, preload: "metadata" }) : el("audio", { src: a.url, controls: true, preload: "none" }));
        return el("figure", { class: "asset" }, el("div", { class: "frame " + a.kind }, view),
          el("figcaption", {}, el("strong", { text: a.name }), el("small", { text: a.kind + " · " + bytes(a.size_bytes) + " · " + when(a.created_at) }),
            admin || a.uploaded_by === S.me.user.id ? el("button", { class: "btn small danger", type: "button", text: "Rimuovi", onclick: function () { act(api("DELETE", "/files/" + a.id), err); } }) : null));
      });
      return [el("div", { class: "panel" }, el("h2", { text: "Archivio Media" }),
          el("p", { text: "Clip video, immagini e audio dell'azienda. Li vede solo chi appartiene alla tua azienda. Usati " + bytes(out.used_bytes) + " di " + bytes(out.quota_bytes) + "." }),
          bar, form, progress, err),
        el("div", { class: "panel" }, el("h2", { text: out.assets.length + " file" }),
          el("p", { text: "Le anteprime restano valide " + Math.round(out.link_seconds / 60) + " minuti: se un video non parte più, riapri la scheda." }),
          cards.length ? el("div", { class: "assets" }, cards) : el("p", { class: "empty", text: "Ancora nessun file." }))];
    });
  }

  // ---------- gestore della piattaforma ----------
  function viewGestore() {
    var body = el("div", {}, el("p", { class: "empty", text: "Carico..." }));
    api("GET", "/admin/tenants").then(function (out) {
      var err = el("p", { class: "error", role: "alert" });
      var options = function (current) { return cat().packages.map(function (p) { return el("option", { value: p.id, selected: p.id === current, text: p.name }); }); };
      var num = function (n) { return Number(n || 0).toLocaleString("it-IT"); };
      var rows = out.tenants.map(function (t) {  // una scheda per azienda: si legge uguale su computer e su telefono
        var post = function (body) { return function () { act(api("POST", "/admin/tenants/" + t.id, body()), err); }; };
        var select = el("select", { id: "pkg_" + t.id, onchange: function () { act(api("POST", "/admin/tenants/" + t.id, { package_id: select.value }), err); } }, options(t.package_id));
        return el("article", { class: "company" },
          el("header", {}, el("strong", { text: t.name }), el("span", { class: "pill " + (t.active ? "ok" : "no"), text: t.active ? "attiva" : "sospesa" }),
            el("span", { class: "meta", text: (t.origin === "registrazione" ? "Registrata da sola" : "Creata da te") + " · " + num(t.users) + (t.users === 1 ? " utente" : " utenti") + " · " + num(t.tokens) + " token usati, " + num(t.tokens_month) + " questo mese" })),
          el("div", { class: "company-grid" },
            el("div", { class: "field" }, el("label", { for: "pkg_" + t.id, text: "Pacchetto (tetto degli agenti)" }), select),
            el("div", { class: "field" }, el("label", { for: "cap_" + t.id, text: "Tetto mensile di token (0 = nessuno)" }), el("div", { class: "inline" },
              el("input", { id: "cap_" + t.id, type: "number", min: "0", step: "100000", value: String(t.monthly_token_cap || 0) }),
              el("button", { class: "btn small", type: "button", text: "Salva tetto", onclick: post(function () { return { monthly_token_cap: Math.max(0, Math.round(Number(val("cap_" + t.id)) || 0)) }; }) }))),
            el("div", { class: "field" }, el("label", { for: "paid_" + t.id, text: "Pagato fino al (vuoto = nessuna scadenza)" }), el("div", { class: "inline" },
              el("input", { id: "paid_" + t.id, type: "date", value: t.paid_until || "" }),
              el("button", { class: "btn small", type: "button", text: "Salva scadenza", onclick: post(function () { return { paid_until: val("paid_" + t.id) }; }) })))),
          t.credits == null ? null : el("div", { class: "acts creditrow" },
            el("span", { class: "meta", id: "cred_" + t.id, text: num(t.credits) + " crediti disponibili." }),
            el("label", { class: "cap" }, "Aggiungi o togli ", el("input", { id: "addcred_" + t.id, type: "number", step: "100", placeholder: "es. 500 o -200" })),
            el("button", { class: "btn small", type: "button", text: "Applica crediti", onclick: post(function () { return { credits_add: Math.round(Number(val("addcred_" + t.id)) || 0) }; }) })),
          el("div", { class: "acts" },
            el("span", { class: "meta", text: t.platform_keys ? "Usa le tue chiavi dei modelli." : "Usa solo le proprie chiavi dei modelli." }),
            el("button", { class: "btn small", type: "button", text: t.platform_keys ? "Togli le tue chiavi" : "Concedi le tue chiavi", onclick: post(function () { return { platform_keys: !t.platform_keys }; }) }),
            el("button", { class: "btn small" + (t.active ? " danger" : ""), type: "button", text: t.active ? "Sospendi" : "Riattiva", onclick: post(function () { return { active: !t.active }; }) })));
      });
      var form = el("form", { class: "form row", autocomplete: "off", onsubmit: function (e) {
        e.preventDefault();
        act(api("POST", "/admin/tenants", { name: val("ntName"), package_id: val("ntPack"), admin_name: val("ntAdmin"),
          admin_email: val("ntEmail"), admin_password: val("ntPassword") }), err);
      } }, field("Nome dell'azienda", "ntName", { required: true }),
        el("div", { class: "field" }, el("label", { for: "ntPack", text: "Pacchetto" }), el("select", { id: "ntPack" }, options(null))),
        field("Nome dell'amministratore", "ntAdmin", { required: true }), field("Email dell'amministratore", "ntEmail", { type: "email", required: true }),
        field("Password iniziale (almeno 10 caratteri)", "ntPassword", { type: "password", required: true, autocomplete: "new-password" }),
        el("button", { class: "btn primary", type: "submit", text: "Crea azienda" }));
      body.textContent = "";
      append(body, [
        el("div", { class: "panel" }, el("h2", { text: "Aziende" }),
          el("p", { text: "Il pacchetto è il tetto massimo: dentro quel tetto ogni azienda accende gli agenti che vuole. Le aziende registrate da sole usano le proprie chiavi dei modelli, finché non concedi le tue." }),
          rows.length ? rows : el("p", { class: "empty", text: "Nessuna azienda. Creane una qui sotto." }), err),
        el("div", { class: "panel" }, el("h2", { text: "Nuova azienda" }),
          el("p", { text: "Crea l'azienda e il suo primo amministratore. Comunicagli email e password iniziale: potrà cambiarla un altro amministratore." }), form)]);
    }).catch(function (e) { body.textContent = ""; append(body, el("p", { class: "error", role: "alert", text: e.message })); });
    return el("section", { class: "tabs-space" }, el("div", { class: "tabs" }), body, gestoreConsole(), gestoreSettings());
  }
  /* Console del gestore: stato del server e comandi di manutenzione scelti da un elenco chiuso. Non è un terminale libero. */
  function gestoreConsole() {
    var box = el("div", { id: "console" });
    api("GET", "/admin/console").then(function (out) {
      var screen = el("pre", { class: "console-out", id: "consoleOut", tabindex: "0", "aria-live": "polite", text: "Scegli un comando qui sopra: il risultato compare qui." });
      var err = el("p", { class: "error", role: "alert" }), buttons = [];
      append(box, el("div", { class: "panel" }, el("h2", { text: "Server" }),
        el("p", { id: "capacityLine", text: "Circa " + Number(out.capacity.people).toLocaleString("it-IT") + " persone possono lavorare nello stesso momento su questo server." }),
        el("ul", { class: "caplist" }, out.capacity.lines.map(function (l) {
          return el("li", {}, el("span", { class: "pill " + (l.state === "OK" ? "ok" : "no"), text: l.state }), el("span", { text: l.text }));
        })),
        el("ul", { class: "capnotes" }, out.capacity.assumptions.map(function (a) { return el("li", { text: a }); }))));
      append(box, el("div", { class: "panel" }, el("h2", { text: "Console" }),
        el("p", { text: "Comandi di manutenzione pronti, senza scrivere niente. Non è un terminale libero: così chi rubasse la tua password non avrebbe in mano il server. Ogni comando lanciato finisce nel registro." }),
        el("div", { class: "acts" }, out.commands.map(function (c) {
          var b = el("button", { class: "btn small", type: "button", title: c.does, text: c.label, onclick: function () {
            err.textContent = ""; screen.textContent = "$ " + c.label + "\n…";
            buttons.forEach(function (x) { x.disabled = true; });
            api("POST", "/admin/console", { command: c.id }).then(function (r) {
              screen.textContent = "$ " + c.label + "   (" + r.seconds + " s, esito " + r.code + ")\n\n" + r.output;
            }).catch(function (e) { screen.textContent = ""; err.textContent = e.message; })
              .then(function () { buttons.forEach(function (x) { x.disabled = false; }); });
          } });
          buttons.push(b);
          return b;
        })), err, screen));
    }).catch(function (e) { append(box, el("p", { class: "error", role: "alert", text: e.message })); });
    return box;
  }
  /* Impostazioni della piattaforma: quello che prima si scriveva nel file .env. I segreti non tornano mai indietro dal server. */
  function gestoreSettings() {
    var box = el("div", { id: "settings" }, el("p", { class: "empty", text: "Carico le impostazioni..." }));
    api("GET", "/admin/settings").then(function (out) {
      var groups = [], by = {};
      out.fields.forEach(function (f) { if (!by[f.group]) { by[f.group] = []; groups.push(f.group); } by[f.group].push(f); });
      box.textContent = "";
      append(box, el("div", { class: "panel" }, el("h2", { text: "Impostazioni della piattaforma" }),
        el("p", { text: "Chiavi dei modelli, posta, ricerca, Google, registrazione e pagamenti: quello che salvi qui vale subito e ha la precedenza sul file .env. Un campo lasciato vuoto torna al valore del file. Chiavi e password non vengono mai mostrate: vedi solo se sono impostate." }),
        out.encrypted ? null : el("div", { class: "banner", role: "alert", text: "Cifratura non attiva (manca SFB_SECRET_KEY nel file .env): chiavi e password non si possono salvare dal pannello finché non la imposti." })));
      groups.forEach(function (g) {
        var err = el("p", { class: "error", role: "alert" }), ok = el("p", { class: "okmsg", role: "status" });
        function send(values, done) {
          err.textContent = ""; ok.textContent = "";
          api("POST", "/admin/settings", { values: values }).then(function (r) { ok.textContent = r.changed.length ? "Salvato: vale da subito." : "Nessuna modifica."; if (done) done(); })
            .catch(function (e) { err.textContent = e.message; });
        }
        var form = el("form", { class: "form", autocomplete: "off", onsubmit: function (e) {
          e.preventDefault();
          var values = {};
          by[g].forEach(function (f) { var v = val("set_" + f.key); if (!f.secret || v !== "") values[f.key] = v; });  // segreto vuoto = resta com'è
          send(values, function () { by[g].forEach(function (f) { if (f.secret) { var n = document.getElementById("set_" + f.key); if (n && n.value) { n.value = ""; n.placeholder = "impostata"; } } }); });
        } },
          by[g].map(function (f) {
            var note = f.secret ? (f.set ? " — impostata" + (f.source ? " (" + f.source + ")" : "") + ": scrivi solo per cambiarla" : " — non impostata") : (f.source ? " (" + f.source + ")" : "");
            return [field(f.label + note, "set_" + f.key, { type: f.secret ? "password" : "text", value: f.secret ? "" : f.value, autocomplete: "off", maxlength: "500" }),
              f.secret && f.source === "pannello" ? el("button", { class: "btn small danger", type: "button", text: "Togli dal pannello",
                onclick: function () { var v = {}; v[f.key] = ""; send(v, render); } }) : null];
          }),
          g === "Pagamenti" ? el("p", { class: "empty", text: "In Stripe crea un webhook verso " + out.webhook_url + " con gli eventi checkout.session.completed, invoice.paid, invoice.payment_failed, customer.subscription.updated e customer.subscription.deleted, e copia qui il suo segreto. Per la disdetta e il cambio carta attiva in Stripe il portale clienti. I prezzi sono abbonamenti mensili creati in Stripe, uno per pacchetto." }) : null,
          err, ok,
          el("div", { class: "acts" }, el("button", { class: "btn small primary", type: "submit", text: "Salva " + g.toLowerCase() }),
            g === "Posta" ? el("button", { class: "btn small", type: "button", text: "Manda un'email di prova", onclick: function () {
              err.textContent = ""; ok.textContent = "Invio...";
              api("POST", "/admin/settings/test-email", {}).then(function (r) { ok.textContent = "Email di prova mandata a " + r.to + "."; })
                .catch(function (e) { ok.textContent = ""; err.textContent = e.message; });
            } }) : null));
        append(box, el("div", { class: "panel" }, el("h2", { text: g }), form));
      });
    }).catch(function (e) { box.textContent = ""; append(box, el("p", { class: "error", role: "alert", text: e.message })); });
    return box;
  }

  // ---------- disegno ----------
  function render() {
    var typing = document.activeElement && document.activeElement.id === "chatInput", box;
    root.textContent = "";
    if (!S.me) { append(root, viewLogin()); return; }
    append(root, header());
    var sub = S.me.tenant && S.me.tenant.subscription;  // scadenza dell'abbonamento: avviso negli ultimi sette giorni, blocco dopo
    if (sub && sub.payment_failed && !sub.expired) {
      append(root, el("div", { class: "banner", role: "alert", id: "payBanner", text: "L'ultimo addebito sulla carta non è riuscito: controlla la carta nella scheda Abbonamento del Centro di controllo." }));
    }
    if (sub && sub.paid_until && sub.days_left <= 7) {
      append(root, el("div", { class: "banner" + (sub.expired ? " stopbanner" : ""), role: "alert", id: "subBanner",
        text: sub.grace ? "Abbonamento scaduto il " + sub.paid_until.split("-").reverse().join("/") + ": gli agenti lavorano ancora " + (sub.grace_left === 0 ? "solo oggi" : "per " + sub.grace_left + (sub.grace_left === 1 ? " giorno" : " giorni")) + ", poi vanno in pausa."
            : sub.expired ? "Abbonamento scaduto il " + sub.paid_until.split("-").reverse().join("/") + ": gli agenti sono in pausa finché non viene rinnovato. I dati restano al loro posto."
                          : (sub.days_left === 0 ? "L'abbonamento scade oggi." : "L'abbonamento scade tra " + sub.days_left + (sub.days_left === 1 ? " giorno" : " giorni") + ", il " + sub.paid_until.split("-").reverse().join("/") + ".") }));
    }
    if (!S.me.tenant) { append(root, viewGestore()); return; }
    if (S.view === "ufficio" && !canOffice()) S.view = "agente";
    append(root, S.view === "ufficio" ? viewUfficio() : (S.view === "agente" ? viewAgente() : viewCentro()));
    append(root, el("footer", { class: "foot" }, seg([
      { id: "ufficio", label: "Ufficio 3D", short: "Ufficio", disabled: !canOffice(), title: canOffice() ? null : "Questo browser non mette a disposizione WebGL" },
      { id: "agente", label: "Modalità Compatta / Mobile", short: "Chat" }, { id: "centro", label: "Centro di controllo", short: "Controllo" }],
      S.view, function (id) { keepDraft(); S.view = id; render(); }, "Vista")));
    if (typing) { box = document.getElementById("chatInput"); if (box) box.focus(); }  // ridisegnare non toglie il cursore a chi scrive
    if (S.view === "ufficio" && !S.osnap) pollOffice();
  }

  /* Collegamenti che arrivano da fuori: dall'email di recupero e dal pulsante «Registrati» del sito. */
  function readHash() {
    var resetLink = /^#reimposta-([A-Za-z0-9_-]{20,})$/.exec(window.location.hash || ""), found = false;
    if (resetLink) {  // il codice sta dopo il # e quindi non finisce nei registri del server
      S.auth = "reset"; S.resetToken = resetLink[1]; found = true;
    } else if (window.location.hash === "#registrati") {
      S.auth = "register"; found = true;
    } else if (/^#crediti-(ok|annullato)$/.test(window.location.hash || "")) {  // ritorno da Stripe dopo una ricarica
      S.view = "centro"; S.tab = "crediti"; found = true;
      S.payMsg = window.location.hash === "#crediti-ok" ? "Pagamento ricevuto da Stripe: i crediti arrivano entro pochi secondi. Se non li vedi, ricarica la pagina."
                                                         : "Acquisto annullato: non è stato addebitato niente.";
    } else if (/^#(pagamento-(ok|annullato)|abbonamento)$/.test(window.location.hash || "")) {  // ritorno da Stripe, o collegamento di un'email
      S.view = "centro"; S.tab = "abbonamento"; found = true;
      S.payMsg = window.location.hash === "#pagamento-ok" ? "Pagamento ricevuto da Stripe: la scadenza si aggiorna entro pochi secondi. Se non cambia, ricarica la pagina."
               : (window.location.hash === "#pagamento-annullato" ? "Pagamento annullato: non è stato addebitato niente." : "");
    }
    if (found) history.replaceState(null, "", window.location.pathname);
    return found;
  }
  readHash();
  window.addEventListener("hashchange", function () { if (readHash() && !S.me) render(); });  // anche se la pagina era già aperta
  var arrived = /^#google-([a-z]+)$/.exec(window.location.hash || "");
  if (arrived) {  // ritorno dal consenso di Google
    S.flash = arrived[1]; S.view = "centro"; S.tab = "collegamenti";
    try { window.history.replaceState(null, "", window.location.pathname); } catch (e) { /* resta l'ancora */ }
  }
  var verified = /^#verifica-(ok|errore)$/.exec(window.location.hash || "");
  if (verified) {  // ritorno dal collegamento di conferma dell'email
    S.noticeBad = verified[1] !== "ok";
    S.notice = S.noticeBad ? "Collegamento di conferma non valido o scaduto. Entra e chiedi di rimandarlo." : "Indirizzo confermato: ora puoi entrare.";
    try { window.history.replaceState(null, "", window.location.pathname); } catch (e) { /* resta l'ancora */ }
  }
  loadRegInfo();
  if (token) loadMe().catch(function () { setToken(null); S.me = null; render(); });
  else render();
  setInterval(function () { if (S.me && S.me.tenant && document.visibilityState !== "hidden") refreshPending(); }, 30000);
  var officeTick = 0;  // ufficio aperto: stati ogni secondo e mezzo, ogni mezzo secondo mentre una richiesta è in corso
  setInterval(function () { if (S.view !== "ufficio") return; officeTick += 1; if (S.busy || officeTick % 3 === 0) pollOffice(); }, 500);
})();
