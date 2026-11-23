/* Ufficio 3D di Alvea: costruisce la stanza a partire dai dati del server e la tiene aggiornata.
 * Una postazione per ogni agente acceso, attrezzata secondo il suo tipo (`station` nel catalogo),
 * un personaggio seduto e un indicatore sopra la testa che segue lo stato vero dell'agente.
 * Dipende solo da Mini3D (web/vendor/mini3d.js). Tutti i testi che arrivano dal server finiscono
 * in textContent o disegnati su un canvas: mai interpretati come HTML.
 */
var SFBOffice = (function () {
  "use strict";
  var M = typeof Mini3D === "undefined" ? null : Mini3D;

  /* Gli stati che il server può restituire. */
  var STATES = {
    fermo: { label: "Fermo", color: "#ff5d5d" },
    lavora: { label: "Al lavoro", color: "#38d6ff" },
    ragiona: { label: "Sta ragionando", color: "#a78bfa" },
    attesa: { label: "Attende approvazione", color: "#ffd166" },
    da_collegare: { label: "Da collegare", color: "#8a93a6" },
    pronto: { label: "Pronto", color: "#46d39a" },
    monta: { label: "Sta montando un video", color: "#ff7ad9" }
  };
  var ORDER = ["pronto", "ragiona", "lavora", "monta", "attesa", "da_collegare", "fermo"];
  var SX = 3.5, SZ = 3.9, SEAT = 0.82, AZ = 0.62, POL = 0.9;
  var supportedCache = null;

  function supported() {
    if (supportedCache !== null) return supportedCache;
    try {
      var c = document.createElement("canvas");
      supportedCache = !!(M && window.WebGLRenderingContext && (c.getContext("webgl") || c.getContext("experimental-webgl")));
    } catch (e) { supportedCache = false; }
    return supportedCache;
  }
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function hash(str) { var h = 2166136261, i; for (i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 16777619); } return h >>> 0; }
  function rng(seed) { var s = seed || 1; return function () { s = (Math.imul(s, 1664525) + 1013904223) >>> 0; return s / 4294967296; }; }
  function stateOf(name) { return STATES[name] ? name : "pronto"; }

  // ---------- schermi e lavagne: disegnati su un canvas 2D e usati come immagine ----------
  function paint(kind, w, h, tone, name) {
    var W = w >= h ? 256 : Math.round(256 * w / h), H = w >= h ? Math.round(256 * h / w) : 256;
    var c = document.createElement("canvas"), x, r = rng(hash(kind + "|" + name)), i, j, y;
    c.width = W; c.height = H;
    x = c.getContext("2d");
    function rect(col, a, b, cw, ch) { x.fillStyle = col; x.fillRect(a, b, cw, ch); }
    function text(s, a, b, size, col, bold, max) { x.fillStyle = col; x.font = (bold ? "600 " : "") + size + "px system-ui, -apple-system, Segoe UI, sans-serif"; x.fillText(s, a, b, max || W - a - 6); }
    function lines(a, b, n, gap, col, maxw) { for (var k = 0; k < n; k++) rect(col, a, b + k * gap, Math.round(maxw * (0.45 + r() * 0.55)), 4); }
    if (kind === "insegna") {
      rect("#0b0f14", 0, 0, W, H); rect(tone, 0, H - 5, W, 5);
      text("alvea", 14, H * 0.5, Math.round(H * 0.36), "#e9f0ff", true);
      text(name, 14, H * 0.82, Math.round(H * 0.2), "#8ea2c8", false);
      return c;
    }
    var light = kind === "lavagna" || kind === "calendario" || kind === "testo";
    var ink = light ? "#1c2a4a" : "#c9d6f5", soft = light ? "#b9c8c2" : "#2a3640";
    rect(light ? "#eef4f1" : "#0c1218", 0, 0, W, H);
    rect(tone, 0, 0, W, 22);
    text(name, 8, 16, 13, "#04101f", true);
    y = 32;
    if (kind === "grafico" || kind === "lavagna") {
      var half = kind === "lavagna" ? W * 0.55 : W - 24, bw = half / 8;
      for (i = 0; i < 7; i++) { var bh = (0.25 + r() * 0.7) * (H - y - 22); rect(i % 3 === 2 ? ink : tone, 14 + i * bw, H - 14 - bh, bw * 0.62, bh); }
      rect(soft, 10, H - 14, half + 6, 2);
      x.strokeStyle = kind === "lavagna" ? "#e2574c" : "#ffffff"; x.lineWidth = 2.5; x.beginPath();
      var x0 = kind === "lavagna" ? W * 0.62 : 14, span = kind === "lavagna" ? W * 0.33 : half;
      for (i = 0; i < 7; i++) { var py = y + 8 + r() * (H - y - 50); if (i) x.lineTo(x0 + i * span / 6, py); else x.moveTo(x0, py); }
      x.stroke();
      if (kind === "lavagna") { rect("#ffd166", W * 0.62, H - 46, 26, 26); rect("#7fd6ff", W * 0.62 + 34, H - 46, 26, 26); rect("#ff93b4", W * 0.62 + 68, H - 46, 26, 26); }
    } else if (kind === "posta") {
      for (i = 0; i < 5; i++) {
        var unread = r() > 0.5;
        x.fillStyle = unread ? tone : soft; x.beginPath(); x.arc(16, y + 9 + i * 24, 5, 0, 6.3); x.fill();
        rect(unread ? ink : soft, 30, y + 3 + i * 24, 70 + r() * 60, 5); rect(soft, 30, y + 13 + i * 24, 120 + r() * 80, 4);
      }
    } else if (kind === "agenda" || kind === "calendario") {
      var cw = (W - 20) / 7, rows = kind === "calendario" ? 5 : 4, ch = (H - y - 10) / rows;
      for (j = 0; j < rows; j++) for (i = 0; i < 7; i++) {
        var pick = r();
        rect(pick > 0.82 ? tone : (pick > 0.7 ? ink : (light ? "#dbe4f7" : "#1a232c")), 10 + i * cw + 2, y + j * ch + 2, cw - 4, ch - 4);
      }
    } else if (kind === "testo") {
      rect(ink, 18, y + 4, W * 0.5, 8); lines(18, y + 24, Math.floor((H - y - 30) / 11), 11, "#8493b5", W - 40);
    } else if (kind === "ricerca") {
      x.strokeStyle = tone; x.lineWidth = 2; x.strokeRect(12, y, W - 24, 20); rect(soft, 20, y + 8, W * 0.4, 4);
      for (i = 0; i < 3; i++) { rect(tone, 12, y + 34 + i * 30, 90 + r() * 70, 5); lines(12, y + 44 + i * 30, 2, 8, soft, W - 40); }
    } else if (kind === "onda") {
      var mid = y + (H - y) / 2, n = 40;
      for (i = 0; i < n; i++) { var a = (0.15 + r() * 0.85) * (H - y - 16) / 2 * Math.sin((i + 0.5) / n * Math.PI); rect(i % 5 === 0 ? ink : tone, 10 + i * (W - 20) / n, mid - a, (W - 20) / n - 2, a * 2); }
    } else if (kind === "negozio") {
      var tw = (W - 40) / 3;
      for (j = 0; j < 2; j++) for (i = 0; i < 3; i++) { rect(i === j ? tone : soft, 12 + i * (tw + 8), y + j * ((H - y) / 2), tw, (H - y) / 2 - 22); rect(ink, 12 + i * (tw + 8), y + j * ((H - y) / 2) + (H - y) / 2 - 16, tw * 0.6, 5); }
    } else if (kind === "chat" || kind === "telefono") {
      var bubbles = Math.floor((H - y - 6) / 26);
      for (i = 0; i < bubbles; i++) { var mine = i % 2 === 1, bwid = W * (0.35 + r() * 0.3); rect(mine ? tone : soft, mine ? W - 12 - bwid : 12, y + i * 26, bwid, 18); }
    } else if (kind === "codice") {
      var cols = [tone, "#7ee0a1", "#c9d6f5", "#ffb547"];
      for (i = 0; i < Math.floor((H - y - 4) / 12); i++) { var ind = 12 + (i % 4) * 10, len = 30 + r() * 70; rect(cols[i % 4], ind, y + i * 12, len, 5); rect(soft, ind + len + 6, y + i * 12, 20 + r() * 60, 5); }
      rect("#ffffff", 12, H - 12, 8, 6);
    } else if (kind === "squadra") {
      x.strokeStyle = soft; x.lineWidth = 2; x.beginPath();
      for (i = 0; i < 3; i++) { x.moveTo(W / 2, y + 22); x.lineTo(W * (0.22 + i * 0.28), H - 34); }
      x.stroke();
      x.fillStyle = tone; x.beginPath(); x.arc(W / 2, y + 20, 13, 0, 6.3); x.fill();
      for (i = 0; i < 3; i++) { x.fillStyle = ink; x.beginPath(); x.arc(W * (0.22 + i * 0.28), H - 30, 11, 0, 6.3); x.fill(); }
    } else if (kind === "anteprima") {
      rect("#1a232c", 12, y, W - 24, H - y - 34); x.fillStyle = "#ffffff"; x.beginPath(); x.moveTo(W / 2 - 10, y + (H - y - 34) / 2 - 12); x.lineTo(W / 2 + 12, y + (H - y - 34) / 2); x.lineTo(W / 2 - 10, y + (H - y - 34) / 2 + 12); x.fill();
      rect(soft, 12, H - 24, W - 24, 4); rect(tone, 12, H - 24, (W - 24) * 0.4, 4);
    } else if (kind === "timeline") {
      for (i = 0; i < 12; i++) rect(soft, 12 + i * (W - 24) / 12, y, 1, 6);
      var tcols = [tone, "#7ee0a1", "#ffb547", "#a78bfa"];
      for (j = 0; j < 4; j++) { var px = 12; while (px < W - 30) { var clip = 18 + r() * 50; rect(tcols[j], px, y + 12 + j * 24, Math.min(clip, W - 14 - px), 18); px += clip + 3 + r() * 14; } }
      rect("#ff5d5d", W * 0.42, y, 2, H - y - 6);
    } else {
      lines(12, y + 4, Math.floor((H - y - 10) / 12), 12, soft, W - 30);
    }
    return c;
  }

  // ---------- postazioni ----------
  var COL = null;
  function colors() {
    return { floor: M.hex("#161d25"), tile: M.hex("#212b35"), wall: M.hex("#11171e"), trim: M.hex("#ff6a00"), desk: M.hex("#28333d"), top: M.hex("#3b4a56"),
      metal: M.hex("#5c6c77"), dark: M.hex("#0b0f14"), light: M.hex("#e6ecfb"), paper: M.hex("#f4f7ff"), box: M.hex("#b98a5a"), plant: M.hex("#3fae6b"), pot: M.hex("#7b5a45") };
  }
  function Station(ox, oz, tone) { this.g = new M.Geo(); this.ox = ox; this.oz = oz; this.tone = tone; this.screens = []; }
  Station.prototype.b = function (x, y, z, w, h, d, col, ry) { this.g.box(this.ox + x, y, this.oz + z, w, h, d, col, ry); return this; };
  Station.prototype.c = function (x, y, z, r0, r1, h, col, seg) { this.g.cyl(this.ox + x, y, this.oz + z, r0, r1, h, col, seg); return this; };
  Station.prototype.s = function (x, y, z, r, col, seg, sy) { this.g.sphere(this.ox + x, y, this.oz + z, r, col, seg, sy); return this; };
  Station.prototype.screen = function (x, y, z, w, h, kind, ry) { this.screens.push({ x: this.ox + x, y: y, z: this.oz + z, w: w, h: h, kind: kind, ry: ry || 0 }); return this; };
  Station.prototype.monitor = function (x, z, w, h, kind, ry) {
    ry = ry || 0;
    this.b(x, 0.79, z, 0.3, 0.02, 0.18, COL.dark, ry).c(x, 0.79, z, 0.03, 0.03, 0.2, COL.metal, 8);
    this.b(x, 0.99 + h / 2, z, w + 0.06, h + 0.06, 0.05, COL.dark, ry);
    return this.screen(x + Math.sin(ry) * 0.03, 0.99 + h / 2, z + Math.cos(ry) * 0.03, w, h, kind, ry);
  };
  Station.prototype.board = function (x, z, w, h, kind, ry) {
    ry = ry || 0;
    var c = Math.cos(ry), s = Math.sin(ry), half = w / 2 - 0.1;
    this.b(x - half * c, 0.75, z + half * s, 0.06, 1.5, 0.06, COL.metal, ry).b(x + half * c, 0.75, z - half * s, 0.06, 1.5, 0.06, COL.metal, ry);
    this.b(x, 1.0 + h / 2, z, w + 0.08, h + 0.08, 0.05, COL.metal, ry);
    return this.screen(x + s * 0.03, 1.0 + h / 2, z + c * 0.03, w, h, kind, ry);
  };
  Station.prototype.desk = function () {
    var t = this;
    t.b(0, 0.75, 0, 1.9, 0.06, 0.9, COL.top);
    [[-0.88, -0.38], [0.88, -0.38], [-0.88, 0.38], [0.88, 0.38]].forEach(function (p) { t.b(p[0], 0.36, p[1], 0.06, 0.72, 0.06, COL.metal); });
    t.b(0.62, 0.5, 0, 0.5, 0.44, 0.8, COL.desk).b(0, 0.79, 0.2, 0.5, 0.02, 0.16, COL.dark);
    t.c(0, 0.04, SEAT, 0.26, 0.26, 0.04, COL.dark, 12).c(0, 0.08, SEAT, 0.04, 0.04, 0.36, COL.metal, 8);
    t.b(0, 0.46, SEAT, 0.5, 0.06, 0.5, COL.desk).b(0, 0.8, SEAT + 0.25, 0.5, 0.62, 0.06, COL.desk);
    return t;
  };
  var EQUIP = {
    lavagna: function (t) { t.monitor(0, -0.28, 0.9, 0.52, "grafico").board(0, -1.15, 2.0, 1.1, "lavagna"); },
    posta: function (t) {
      t.monitor(0, -0.28, 0.9, 0.52, "posta");
      t.b(-0.66, 0.8, 0.02, 0.36, 0.03, 0.5, COL.metal).b(-0.66, 0.88, 0.02, 0.36, 0.03, 0.5, COL.metal).b(-0.66, 0.96, 0.02, 0.36, 0.03, 0.5, COL.metal);
      t.b(-0.66, 0.83, 0.04, 0.3, 0.02, 0.42, COL.paper, 0.1).b(-0.66, 0.91, 0.0, 0.3, 0.02, 0.42, COL.paper, -0.12).b(0.62, 0.8, 0.22, 0.3, 0.015, 0.2, COL.paper, 0.4);
    },
    calendario: function (t) { t.monitor(0, -0.28, 0.9, 0.52, "agenda").board(0, -1.15, 1.5, 1.1, "calendario"); t.c(0.66, 0.78, 0.1, 0.09, 0.09, 0.03, COL.light, 14); },
    scrittura: function (t) {
      t.monitor(0, -0.28, 1.15, 0.52, "testo");
      t.b(-0.64, 0.8, 0.12, 0.3, 0.06, 0.4, COL.paper, 0.15).b(-0.64, 0.86, 0.12, 0.3, 0.04, 0.4, COL.paper, -0.1);
      t.c(0.7, 0.78, -0.1, 0.06, 0.07, 0.13, t.tone, 10).b(0.7, 0.95, -0.1, 0.015, 0.14, 0.015, COL.light).b(0.73, 0.94, -0.08, 0.015, 0.12, 0.015, COL.box);
    },
    ricerca: function (t) {
      t.monitor(0, -0.28, 0.9, 0.52, "ricerca");
      t.b(0, 1.0, -1.2, 2.0, 2.0, 0.34, COL.desk);
      var r = rng(hash("libri" + t.ox + t.oz)), palette = [t.tone, COL.light, COL.box, COL.trim, COL.plant], row, bx, bw, bh;
      for (row = 0; row < 3; row++) {
        t.b(0, 0.42 + row * 0.6, -1.02, 1.9, 0.04, 0.06, COL.metal);
        bx = -0.88;
        while (bx < 0.82) { bw = 0.07 + r() * 0.07; bh = 0.3 + r() * 0.18; t.b(bx + bw / 2, 0.44 + row * 0.6 + bh / 2, -1.0, bw, bh, 0.1, palette[Math.floor(r() * palette.length)]); bx += bw + 0.012; }
      }
      t.c(-0.68, 0.78, 0.05, 0.07, 0.05, 0.05, COL.metal, 10).s(-0.68, 0.96, 0.05, 0.13, t.tone, 8);
    },
    audio: function (t) {
      t.monitor(0, -0.28, 0.9, 0.52, "onda");
      t.b(-0.72, 0.96, -0.2, 0.22, 0.36, 0.2, COL.dark).b(0.72, 0.96, -0.2, 0.22, 0.36, 0.2, COL.dark);
      t.c(-0.72, 0.9, -0.09, 0.07, 0.07, 0.02, t.tone, 10).c(0.72, 0.9, -0.09, 0.07, 0.07, 0.02, t.tone, 10);
      t.c(0.46, 0.78, 0.18, 0.08, 0.08, 0.02, COL.metal, 10).c(0.46, 0.8, 0.18, 0.015, 0.015, 0.26, COL.metal, 6).s(0.46, 1.11, 0.18, 0.06, COL.light, 8, 1.4);
    },
    negozio: function (t) {
      t.monitor(0, -0.28, 0.9, 0.52, "negozio");
      t.b(1.4, 0.25, -0.2, 0.6, 0.5, 0.6, COL.box, 0.2).b(1.42, 0.7, -0.22, 0.48, 0.4, 0.48, COL.box, -0.15).b(1.38, 1.04, -0.2, 0.34, 0.28, 0.34, COL.box, 0.4);
      t.b(1.4, 0.505, -0.2, 0.08, 0.012, 0.6, COL.paper, 0.2).b(-0.66, 0.86, 0.1, 0.3, 0.16, 0.24, t.tone, 0.2);
    },
    canale: function (t) {
      t.monitor(0, -0.28, 0.9, 0.52, "chat");
      t.b(-1.42, 0.04, -0.3, 0.6, 0.08, 0.4, COL.dark, 0.35).b(-1.42, 0.98, -0.3, 0.72, 1.84, 0.08, COL.dark, 0.35);
      t.screen(-1.42 + Math.sin(0.35) * 0.045, 0.98, -0.3 + Math.cos(0.35) * 0.045, 0.62, 1.7, "telefono", 0.35);
    },
    terminale: function (t) {
      t.monitor(-0.3, -0.28, 0.9, 0.52, "codice");
      t.b(1.42, 0.95, -0.3, 0.7, 1.9, 0.7, COL.dark);
      for (var i = 0; i < 6; i++) t.b(1.42, 0.35 + i * 0.28, 0.055, 0.56, 0.16, 0.02, COL.desk).b(1.2 + (i % 3) * 0.08, 0.35 + i * 0.28, 0.07, 0.04, 0.04, 0.01, i % 2 ? t.tone : COL.plant);
    },
    squadra: function (t) {
      t.monitor(0, -0.28, 0.9, 0.52, "squadra");
      t.c(0, 0.02, -1.35, 0.3, 0.3, 0.04, COL.dark, 14).c(0, 0.06, -1.35, 0.05, 0.05, 0.62, COL.metal, 8).c(0, 0.68, -1.35, 0.62, 0.62, 0.05, COL.top, 20);
      [[-0.8, -1.35], [0.8, -1.35], [0, -2.0]].forEach(function (p) { t.c(p[0], 0, p[1], 0.04, 0.04, 0.42, COL.metal, 6).c(p[0], 0.42, p[1], 0.18, 0.18, 0.05, t.tone, 12); });
    },
    montaggio: function (t) {
      t.monitor(-0.5, -0.26, 0.82, 0.48, "anteprima", 0.22).monitor(0.5, -0.26, 0.82, 0.48, "timeline", -0.22);
      t.c(0.62, 0.78, 0.2, 0.08, 0.08, 0.035, COL.metal, 14).b(-0.62, 0.8, 0.2, 0.3, 0.03, 0.18, COL.dark);
    },
    scrivania: function (t) { t.monitor(0, -0.28, 0.9, 0.52, "generico"); }
  };

  function layout(n) {
    var cols = n <= 3 ? Math.max(n, 1) : Math.ceil(Math.sqrt(n)), rows = Math.max(1, Math.ceil(n / cols)), pos = [], i, r, inRow;
    for (i = 0; i < n; i++) {
      r = Math.floor(i / cols); inRow = Math.min(cols, n - r * cols);
      pos.push([(i - r * cols - (inRow - 1) / 2) * SX, (r - (rows - 1) / 2) * SZ]);
    }
    return { pos: pos, cols: cols, rows: rows };
  }

  /* Costruisce tutta la scena. Ritorna gli oggetti da disegnare e, per ogni agente, i pezzi che si animano. */
  function buildScene(R, office, title) {
    var sc = { meshes: [], panels: [], owned: [], agents: [], hub: null }, L = layout(office.agents.length), room = new M.Geo(), i, x, z;
    var halfW = Math.max(L.cols * SX / 2 + 1.3, 5), zBack = -(L.rows * SZ / 2) - 1.9, hubZ = L.rows * SZ / 2 + 1.0, zFront = hubZ + 2.0;
    var cz = (zBack + zFront) / 2, depth = zFront - zBack;
    function own(m) { sc.owned.push(m); return m; }
    function share(src) { return { buf: src.buf, count: src.count, model: M.ident(), tint: [1, 1, 1], glow: 0, alpha: 1, visible: true }; }
    function addPanel(s, tone, name) {
      var p = own(R.panel(s.w, s.h, paint(s.kind, s.w, s.h, tone, name)));
      p.model = M.trs(s.x, s.y, s.z, s.ry);
      sc.panels.push(p);
      return p;
    }
    sc.view = { target: [0, 0.8, (zBack + 0.6 + hubZ + 1.4) / 2], dist: 12, bounds: Math.max(halfW, depth / 2), corners: [] };
    function frameBox(min, max) {  // gli spigoli di ciò che deve restare nel riquadro nella vista d'insieme
      [min[0], max[0]].forEach(function (px) { [min[2], max[2]].forEach(function (pz) { sc.view.corners.push([px, 0, pz], [px, 2.4, pz]); }); });
    }

    room.box(0, -0.1, cz, halfW * 2, 0.2, depth, COL.floor);
    for (x = -Math.floor(halfW / 2) * 2; x <= halfW; x += 2) room.box(x, 0.004, cz, 0.03, 0.008, depth, COL.tile);
    for (z = Math.ceil(zBack / 2) * 2; z <= zFront; z += 2) room.box(0, 0.004, z, halfW * 2, 0.008, 0.03, COL.tile);
    room.box(0, 1.7, zBack - 0.1, halfW * 2 + 0.4, 3.6, 0.2, COL.wall).box(-halfW - 0.1, 1.7, cz, 0.2, 3.6, depth, COL.wall);
    room.box(0, 0.06, zBack + 0.02, halfW * 2, 0.12, 0.04, COL.trim).box(-halfW + 0.02, 0.06, cz, 0.04, 0.12, depth, COL.trim);
    [[-halfW + 0.7, zBack + 0.7], [halfW - 0.7, zBack + 0.7], [-halfW + 0.7, zFront - 0.7]].forEach(function (p) {
      room.cyl(p[0], 0, p[1], 0.22, 0.28, 0.4, COL.pot, 12).sphere(p[0], 0.85, p[1], 0.42, COL.plant, 8, 1.25).sphere(p[0] + 0.2, 1.2, p[1] - 0.1, 0.26, M.shade(COL.plant, 1.2), 7);
    });
    room.box(0, 2.75, zBack + 0.03, 4.3, 1.0, 0.06, COL.metal);
    sc.meshes.push(own(R.mesh(room)));
    addPanel({ x: 0, y: 2.75, z: zBack + 0.07, w: 4.1, h: 0.82, kind: "insegna", ry: 0 }, "#ff6a00", title);

    // Pezzi uguali per tutti: si disegnano più volte con colore e posizione diversi.
    var gem = own(R.mesh(new M.Geo().octa(0.13, 0.2, [1, 1, 1])));
    var ring = own(R.mesh(new M.Geo().disc(0, 0, 0, 1.3, [1, 1, 1], 40, 1.14)));
    var dots = own(R.mesh(new M.Geo().sphere(0.3, 0, 0, 0.045, [1, 1, 1], 5).sphere(-0.15, 0, 0.26, 0.045, [1, 1, 1], 5).sphere(-0.15, 0, -0.26, 0.045, [1, 1, 1], 5)));
    var arm = own(R.mesh(new M.Geo().box(0, -0.17, 0, 0.085, 0.34, 0.085, [1, 1, 1]).sphere(0, -0.37, 0, 0.058, [1.15, 1.15, 1.15], 6)));

    office.agents.forEach(function (a, n) {
      var ox = L.pos[n][0], oz = L.pos[n][1], tone = M.hex(a.tone), st = new Station(ox, oz, tone), body = new M.Geo(), item;
      st.desk();
      (EQUIP[a.station] || EQUIP.scrivania)(st);
      sc.meshes.push(own(R.mesh(st.g)));
      body.box(0, 0.54, -0.2, 0.36, 0.13, 0.5, COL.dark).box(0, 0.27, -0.42, 0.32, 0.5, 0.13, COL.dark);
      body.cyl(0, 0.5, 0, 0.21, 0.18, 0.5, tone, 12).cyl(0, 1.0, 0, 0.07, 0.07, 0.06, COL.light, 8);
      body.sphere(0, 1.22, 0, 0.18, COL.light, 9).box(0, 1.23, -0.13, 0.27, 0.09, 0.12, COL.dark).box(0, 1.39, 0.01, 0.2, 0.05, 0.3, tone);
      item = { id: a.id, name: a.name, x: ox, z: oz, tone: tone, state: "pronto", pending: 0, seed: (hash(a.id) % 628) / 100,
        body: own(R.mesh(body)), armL: share(arm), armR: share(arm), gem: share(gem), ring: share(ring), dots: share(dots),
        screens: st.screens.map(function (s) { return addPanel(s, a.tone, a.name); }),
        min: [ox - 1.05, 0, oz - 0.6], max: [ox + 1.05, 1.95, oz + 1.25], top: [ox, 2.08, oz + SEAT] };
      item.armL.tint = item.armR.tint = tone;
      item.ring.alpha = 0.5; item.ring.glow = 1; item.gem.glow = 0.6; item.dots.glow = 1;
      sc.meshes.push(item.body, item.armL, item.armR, item.gem, item.ring, item.dots);
      sc.agents.push(item);
      frameBox([ox - 1.5, 0, oz - 1.3], [ox + 1.5, 0, oz + 1.5]);
    });

    // Il Centrale: una pedana con una sfera sospesa, davanti alle scrivanie.
    var hub = new M.Geo();
    hub.cyl(0, 0, hubZ, 1.35, 1.25, 0.12, COL.desk, 28).cyl(0, 0.12, hubZ, 0.5, 0.32, 0.72, COL.metal, 16).cyl(0, 0.84, hubZ, 0.42, 0.42, 0.04, COL.trim, 16);
    sc.meshes.push(own(R.mesh(hub)));
    sc.hub = { id: "centrale", name: "Centrale", x: 0, z: hubZ, state: "pronto", seed: 0, orb: own(R.mesh(new M.Geo().sphere(0, 0, 0, 0.4, [1, 1, 1], 12))), ring: share(ring),
      min: [-1.3, 0, hubZ - 1.3], max: [1.3, 2.1, hubZ + 1.3], top: [0, 2.2, hubZ] };
    sc.hub.ring.alpha = 0.5; sc.hub.ring.glow = 1; sc.hub.orb.glow = 0.55;
    frameBox(sc.hub.min, sc.hub.max);
    sc.meshes.push(sc.hub.orb, sc.hub.ring);
    return sc;
  }

  /* Posa e indicatore di un agente, a seconda dello stato. t è il tempo in secondi (0 se il movimento è ridotto). */
  function pose(a, t, picked, hovered) {
    var c = M.hex(STATES[a.state].color), s = a.seed, by = 0, aL = 0.95, aR = 0.95, gy = 1.78, gs = 1, gsy = 1, gry = t * 0.6 + s, glow = 0.6, body = 1, screen = 1, k;
    a.dots.visible = false;
    if (a.state === "pronto") { by = Math.sin(t * 1.2 + s) * 0.012; gy += Math.sin(t * 1.5 + s) * 0.035; }
    else if (a.state === "ragiona") { gry = t * 3; gs = 1.05 + 0.16 * Math.sin(t * 5); glow = 0.9; aR = 2.25; a.dots.visible = true; }
    else if (a.state === "lavora" || a.state === "monta") { gry = t * 5; gy += Math.abs(Math.sin(t * 6 + s)) * 0.1; glow = 1; aL = 1.15 + 0.13 * Math.sin(t * 16); aR = 1.15 - 0.13 * Math.sin(t * 16); screen = 1.0 + 0.22 * Math.sin(t * 8); }
    else if (a.state === "attesa") { k = 0.5 + 0.5 * Math.sin(t * 6); gs = 1.05 + 0.3 * k; glow = 0.55 + 0.45 * k; aR = 2.8; }
    else if (a.state === "da_collegare") { gry = s; gs = 0.8; glow = 0.15; aL = aR = 0.3; screen = 0.22; }
    else { gry = 0.785; gs = 0.95; gsy = 0.45; glow = 0.5; aL = aR = 0.3; screen = 0.1; body = 0.45; }
    a.body.model = M.trs(a.x, by, a.z + SEAT, 0);
    a.body.tint = [body, body, body];
    a.armL.model = M.trs(a.x - 0.26, 0.98 + by, a.z + SEAT, 0, 1, 1, 1, aL);
    a.armR.model = M.trs(a.x + 0.26, 0.98 + by, a.z + SEAT, 0, 1, 1, 1, aR);
    a.armL.tint = a.armR.tint = M.shade(a.tone, 0.85 * body);
    a.gem.model = M.trs(a.x, gy, a.z + SEAT, gry, gs, gs * gsy, gs);
    a.gem.tint = c; a.gem.glow = glow;
    a.dots.model = M.trs(a.x, 1.5, a.z + SEAT, t * 4, 1);
    a.dots.tint = c;
    a.ring.model = M.trs(a.x, 0.02, a.z + 0.3, 0, picked ? 1.12 : 1);
    a.ring.tint = c; a.ring.alpha = picked ? 0.95 : (hovered ? 0.75 : 0.42);
    a.screens.forEach(function (p) { p.glow = screen; });
  }
  function poseHub(h, t, picked, hovered) {
    var c = M.hex(STATES[h.state].color), k = h.state === "fermo" || h.state === "da_collegare" ? 0 : 1, fast = h.state === "ragiona" || h.state === "lavora" || h.state === "monta";
    h.orb.model = M.trs(0, 1.5 + k * Math.sin(t * (fast ? 4 : 1.3)) * 0.07, h.z, t * (fast ? 2.4 : 0.5) * k, fast ? 1 + 0.08 * Math.sin(t * 7) : 1);
    h.orb.tint = h.state === "pronto" ? M.hex("#46d39a") : c;
    h.orb.glow = k ? 0.6 : 0.2;
    h.ring.model = M.trs(0, 0.14, h.z, 0, picked ? 0.92 : 0.84);
    h.ring.tint = c; h.ring.alpha = picked ? 0.95 : (hovered ? 0.75 : 0.5);
  }

  /* Crea la vista. opts.onPick(id) viene chiamato quando si sceglie un agente o il Centrale; opts.onCompact() dal pulsante della vista compatta. */
  function create(opts) {
    var node = el("div", "ostage"), canvas = el("canvas", "ocanvas"), tags = el("div", "otags"), legend = el("ul", "olegend"), ctl = el("div", "octl");
    var api = { node: node, ok: false, update: function () {}, select: function () {}, resume: function () {} };
    var R = null, scene = null, orbit = null, sceneKey = null, last = null, lastTitle = "", selected = null, hover = null, lost = false, running = false;
    var dirty = true, fly = null, labels = {}, frames = 0, cam = null, pressed = false, auto = true, far = null;
    var reduce = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : { matches: false };

    canvas.tabIndex = 0;
    canvas.setAttribute("role", "img");
    canvas.setAttribute("aria-label", "Ufficio virtuale in 3D. Gli agenti sono elencati anche come pulsanti.");
    node.appendChild(canvas); node.appendChild(tags);
    ORDER.forEach(function (id) { var li = el("li", "st-" + id); li.appendChild(el("i")); li.appendChild(el("span", null, STATES[id].label)); legend.appendChild(li); });
    legend.setAttribute("aria-label", "Legenda degli stati");
    node.appendChild(legend);
    function button(label, title, fn) { var b = el("button", "obtn", label); b.type = "button"; b.title = title; b.setAttribute("aria-label", title); b.addEventListener("click", fn); ctl.appendChild(b); return b; }
    button("+", "Avvicina", function () { if (orbit) { fly = null; auto = false; orbit.dist *= 0.82; orbit.clamp(); } });
    button("−", "Allontana", function () { if (orbit) { fly = null; auto = false; orbit.dist *= 1.22; orbit.clamp(); } });
    button("Vista d'insieme", "Torna alla vista d'insieme", function () { if (scene) { fitView(false); fly = { target: scene.view.target.slice(), dist: scene.view.dist, az: AZ, pol: POL, auto: true }; dirty = true; } });
    if (opts.onCompact) button("Modalità Compatta / Mobile", "Passa alla vista 2D compatta", opts.onCompact).classList.add("wide");
    node.appendChild(ctl);

    if (!supported()) { node.classList.add("nogl"); node.appendChild(el("p", "onote", "Questo browser non mette a disposizione WebGL: l'ufficio 3D non può essere disegnato. Usa la Modalità Compatta / Mobile.")); return api; }
    try { COL = COL || colors(); R = new M.Renderer(canvas); } catch (e) { node.classList.add("nogl"); node.appendChild(el("p", "onote", "L'ufficio 3D non può partire su questo dispositivo. Usa la Modalità Compatta / Mobile.")); return api; }
    api.ok = true;

    function label(item) {
      var b = el("button", "otag"), dot = el("i"), name = el("b", null, item.name), state = el("small");
      b.type = "button";
      b.appendChild(dot); b.appendChild(name); b.appendChild(state);
      b.addEventListener("click", function () { opts.onPick(item.id); });
      b.addEventListener("pointerenter", function () { hover = item.id; dirty = true; });
      b.addEventListener("pointerleave", function () { if (hover === item.id) hover = null; dirty = true; });
      tags.appendChild(b);
      return { node: b, state: state, sig: "", pos: "" };
    }
    function refreshLabel(item) {
      var l = labels[item.id], text = STATES[item.state].label + (item.pending ? " · " + item.pending : ""), sig = item.state + "|" + text + "|" + (selected === item.id);
      if (l.sig === sig) return;
      l.sig = sig;
      l.node.className = "otag st-" + item.state + (selected === item.id ? " sel" : "");
      l.state.textContent = text;
      l.node.setAttribute("aria-label", (item.id === "centrale" ? "Centrale" : "Agente " + item.name) + ": " + text);
      l.node.setAttribute("aria-pressed", String(selected === item.id));
    }
    function everyone() { return scene ? scene.agents.concat([scene.hub]) : []; }
    function rebuild(office, title) {
      if (scene) scene.owned.forEach(function (m) { R.dispose(m); });
      tags.textContent = ""; labels = {};
      scene = buildScene(R, office, title);
      everyone().forEach(function (item) { labels[item.id] = label(item); });
      if (!orbit) orbit = new M.Orbit(canvas, { target: scene.view.target, dist: 12, az: AZ, pol: POL, minDist: 4, maxDist: 200, bounds: scene.view.bounds, onChange: function () { dirty = true; } });
      orbit.bounds = scene.view.bounds;
      fly = null; auto = true;
      fitView(true);
    }
    /* Calcola la distanza da cui tutta la stanza entra nel riquadro; con `move` ci porta anche la camera. */
    function fitView(move) {
      R.resize();
      var keep = [orbit.target, orbit.dist, orbit.az, orbit.pol], aspect = canvas.width / canvas.height, d = 6, i, c, ok;
      orbit.target = scene.view.target.slice(); orbit.az = AZ; orbit.pol = POL;
      for (i = 0; i < 70; i++) {
        orbit.dist = d; c = orbit.camera(aspect);
        ok = scene.view.corners.every(function (p) { var q = M.project(c, p, 2, 2); return q.ahead && Math.abs(q.nx) < 0.93 && q.ny < 0.72 && q.ny > -0.78; });
        if (ok) break;
        d *= 1.04;
      }
      scene.view.dist = d; orbit.maxDist = d * 1.5;
      if (!move) { orbit.target = keep[0]; orbit.dist = keep[1]; orbit.az = keep[2]; orbit.pol = keep[3]; }
      dirty = true;
    }
    api.update = function (office, title) {
      if (!office || lost) { last = office; lastTitle = title; return; }
      var key = title + "|" + office.agents.map(function (a) { return [a.id, a.station, a.tone, a.name].join(":"); }).join(",");
      last = office; lastTitle = title;
      if (key !== sceneKey) { rebuild(office, title); sceneKey = key; if (selected && !labels[selected]) selected = null; }
      office.agents.forEach(function (a, i) { scene.agents[i].state = stateOf(a.state); scene.agents[i].pending = a.pending || 0; });
      scene.hub.state = stateOf(office.central.state);
      everyone().forEach(refreshLabel);
      dirty = true;
      api.resume();
    };
    api.select = function (id, move) {
      if (selected === id) return;
      selected = id;
      everyone().forEach(refreshLabel);
      var item = everyone().filter(function (a) { return a.id === id; })[0];
      if (item && move !== false && orbit) { auto = false; fly = { target: [item.x, 0.9, item.z + 0.3], dist: Math.min(orbit.dist, 10.5), az: orbit.az, pol: orbit.pol }; }
      dirty = true;
    };
    function pick(e) {
      if (!scene || !cam) return null;
      var box = canvas.getBoundingClientRect(), ray = M.ray(cam, (e.clientX - box.left) / box.width * 2 - 1, 1 - (e.clientY - box.top) / box.height * 2), best = null, bestT = Infinity;
      everyone().forEach(function (item) { var t = M.rayBox(ray, item.min, item.max); if (t >= 0 && t < bestT) { bestT = t; best = item.id; } });
      return best;
    }
    canvas.addEventListener("pointerdown", function () { fly = null; auto = false; pressed = true; });
    canvas.addEventListener("wheel", function () { fly = null; auto = false; }, { passive: true });
    canvas.addEventListener("keydown", function () { fly = null; auto = false; });
    canvas.addEventListener("pointerup", function (e) {  // un clic vale solo se è iniziato sul disegno e non è stato un trascinamento
      var was = pressed;
      pressed = false;
      if (was && orbit && orbit.moved < 6) { var id = pick(e); if (id) opts.onPick(id); }
    });
    canvas.addEventListener("pointercancel", function () { pressed = false; });
    canvas.addEventListener("pointermove", function (e) {
      if (e.buttons) return;
      var id = pick(e);
      if (id !== hover) { hover = id; canvas.classList.toggle("hot", !!id); dirty = true; }
    });
    canvas.addEventListener("pointerleave", function () { if (hover) { hover = null; canvas.classList.remove("hot"); dirty = true; } });
    canvas.addEventListener("keydown", function (e) { if ((e.key === "Enter" || e.key === " ") && hover) opts.onPick(hover); });
    canvas.addEventListener("webglcontextlost", function (e) { e.preventDefault(); lost = true; });
    canvas.addEventListener("webglcontextrestored", function () {
      try { R = new M.Renderer(canvas); } catch (err) { return; }
      lost = false; sceneKey = null; scene = null;
      if (last) api.update(last, lastTitle);
    });

    function frame(now) {
      if (!node.isConnected || document.hidden || lost || !scene) { running = false; return; }
      var resized = R.resize(), still = reduce.matches, t = still ? 0 : now / 1000, w = canvas.clientWidth, h = canvas.clientHeight;
      if (fly) {
        var k = still ? 1 : 0.12, done = true;
        [0, 1, 2].forEach(function (i) { orbit.target[i] += (fly.target[i] - orbit.target[i]) * k; if (Math.abs(fly.target[i] - orbit.target[i]) > 0.01) done = false; });
        orbit.dist += (fly.dist - orbit.dist) * k; orbit.az += (fly.az - orbit.az) * k; orbit.pol += (fly.pol - orbit.pol) * k;
        if (Math.abs(fly.dist - orbit.dist) > 0.02 || Math.abs(fly.az - orbit.az) > 0.005) done = false;
        if (done) { auto = !!fly.auto; fly = null; }
        dirty = true;
      }
      if (resized && auto && !fly) fitView(true);  // finché nessuno ha mosso la camera, la stanza resta tutta nel riquadro
      if ((orbit.dist > 15) !== far) { far = orbit.dist > 15; tags.classList.toggle("far", far); }
      if (still && !dirty && !resized) { window.requestAnimationFrame(frame); return; }
      cam = orbit.camera(canvas.width / canvas.height);
      scene.agents.forEach(function (a) { pose(a, t, selected === a.id, hover === a.id); });
      poseHub(scene.hub, t, selected === "centrale", hover === "centrale");
      R.draw(scene.meshes, scene.panels, cam, { light: [0.42, 0.8, 0.43], fog: M.hex("#0b0f14"), fogNear: orbit.dist * 1.25, fogFar: orbit.dist * 3.4 });
      everyone().forEach(function (item) {
        var p = M.project(cam, item.top, w, h), l = labels[item.id], pos = p.front ? Math.round(p.x) + "px," + Math.round(p.y) + "px" : "";
        if (l.pos === pos) return;
        l.pos = pos;
        l.node.hidden = !p.front;
        if (p.front) { l.node.style.transform = "translate(" + pos + ") translate(-50%,-100%) translateY(-4px)"; l.node.style.zIndex = String(1000 - Math.round(p.y < 0 ? 0 : Math.min(999, h - p.y))); }
      });
      if (frames === 0) { var err = R.gl.getError(); if (err) window.console.error("Ufficio 3D: errore WebGL " + err); }
      frames += 1; dirty = false;
      if (frames === 1 || frames % 30 === 0) node.setAttribute("data-frames", String(frames));
      window.requestAnimationFrame(frame);
    }
    api.resume = function () { if (!running && api.ok && scene && node.isConnected && !document.hidden) { running = true; dirty = true; window.requestAnimationFrame(frame); } };
    document.addEventListener("visibilitychange", function () { api.resume(); });
    if (reduce.addEventListener) reduce.addEventListener("change", function () { dirty = true; });
    return api;
  }

  return { create: create, supported: supported, STATES: STATES, ORDER: ORDER };
})();
