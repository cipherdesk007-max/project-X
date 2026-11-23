/* Mini3D: piccolo motore 3D in WebGL scritto per SalesForB.
 * NON è Three.js e non ne imita le API. Nessuna dipendenza, nessun file esterno: gira tutto in locale.
 * Offre: matrici, costruzione di solidi colorati (parallelepipedi, cilindri, sfere, dischi, ottaedri),
 * pannelli con immagine presa da un canvas 2D, luce diffusa con nebbia, camera a orbita con zoom,
 * raggio dal puntatore per capire cosa è stato cliccato.
 */
var Mini3D = (function () {
  "use strict";

  // ---------- matrici 4x4 (per colonne) ----------
  function ident() { return new Float32Array([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]); }
  function mul(a, b) {
    var o = new Float32Array(16), i, j;
    for (i = 0; i < 4; i++) for (j = 0; j < 4; j++) {
      o[j * 4 + i] = a[i] * b[j * 4] + a[4 + i] * b[j * 4 + 1] + a[8 + i] * b[j * 4 + 2] + a[12 + i] * b[j * 4 + 3];
    }
    return o;
  }
  function perspective(fovy, aspect, near, far) {
    var f = 1 / Math.tan(fovy / 2), o = new Float32Array(16);
    o[0] = f / aspect; o[5] = f; o[10] = (far + near) / (near - far); o[11] = -1; o[14] = 2 * far * near / (near - far);
    return o;
  }
  function norm(v) { var l = Math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]) || 1; return [v[0] / l, v[1] / l, v[2] / l]; }
  function cross(a, b) { return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]; }
  function dot(a, b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }
  function lookAt(eye, center, up) {
    var z = norm([eye[0] - center[0], eye[1] - center[1], eye[2] - center[2]]), x = norm(cross(up, z)), y = cross(z, x);
    return new Float32Array([x[0], y[0], z[0], 0, x[1], y[1], z[1], 0, x[2], y[2], z[2], 0, -dot(x, eye), -dot(y, eye), -dot(z, eye), 1]);
  }
  /* Posizione, rotazione attorno a Y, inclinazione attorno a X, scala: M = T * Ry * Rx * S */
  function trs(x, y, z, ry, sx, sy, sz, rx) {
    var c = Math.cos(ry || 0), s = Math.sin(ry || 0), cx = Math.cos(rx || 0), sn = Math.sin(rx || 0);
    sx = sx == null ? 1 : sx; sy = sy == null ? sx : sy; sz = sz == null ? sx : sz;
    return new Float32Array([
      c * sx, 0, -s * sx, 0,
      s * sn * sy, cx * sy, c * sn * sy, 0,
      s * cx * sz, -sn * sz, c * cx * sz, 0,
      x, y, z, 1]);
  }
  function invert(m) {
    var a00 = m[0], a01 = m[1], a02 = m[2], a03 = m[3], a10 = m[4], a11 = m[5], a12 = m[6], a13 = m[7],
      a20 = m[8], a21 = m[9], a22 = m[10], a23 = m[11], a30 = m[12], a31 = m[13], a32 = m[14], a33 = m[15],
      b00 = a00 * a11 - a01 * a10, b01 = a00 * a12 - a02 * a10, b02 = a00 * a13 - a03 * a10, b03 = a01 * a12 - a02 * a11,
      b04 = a01 * a13 - a03 * a11, b05 = a02 * a13 - a03 * a12, b06 = a20 * a31 - a21 * a30, b07 = a20 * a32 - a22 * a30,
      b08 = a20 * a33 - a23 * a30, b09 = a21 * a32 - a22 * a31, b10 = a21 * a33 - a23 * a31, b11 = a22 * a33 - a23 * a32,
      det = b00 * b11 - b01 * b10 + b02 * b09 + b03 * b08 - b04 * b07 + b05 * b06, o = new Float32Array(16);
    if (!det) return ident();
    det = 1 / det;
    o[0] = (a11 * b11 - a12 * b10 + a13 * b09) * det; o[1] = (a02 * b10 - a01 * b11 - a03 * b09) * det;
    o[2] = (a31 * b05 - a32 * b04 + a33 * b03) * det; o[3] = (a22 * b04 - a21 * b05 - a23 * b03) * det;
    o[4] = (a12 * b08 - a10 * b11 - a13 * b07) * det; o[5] = (a00 * b11 - a02 * b08 + a03 * b07) * det;
    o[6] = (a32 * b02 - a30 * b05 - a33 * b01) * det; o[7] = (a20 * b05 - a22 * b02 + a23 * b01) * det;
    o[8] = (a10 * b10 - a11 * b08 + a13 * b06) * det; o[9] = (a01 * b08 - a00 * b10 - a03 * b06) * det;
    o[10] = (a30 * b04 - a31 * b02 + a33 * b00) * det; o[11] = (a21 * b02 - a20 * b04 - a23 * b00) * det;
    o[12] = (a11 * b07 - a10 * b09 - a12 * b06) * det; o[13] = (a00 * b09 - a01 * b07 + a02 * b06) * det;
    o[14] = (a31 * b01 - a30 * b03 - a32 * b00) * det; o[15] = (a20 * b03 - a21 * b01 + a22 * b00) * det;
    return o;
  }
  function apply(m, x, y, z) {
    var w = m[3] * x + m[7] * y + m[11] * z + m[15] || 1;
    return [(m[0] * x + m[4] * y + m[8] * z + m[12]) / w, (m[1] * x + m[5] * y + m[9] * z + m[13]) / w, (m[2] * x + m[6] * y + m[10] * z + m[14]) / w,
      m[3] * x + m[7] * y + m[11] * z + m[15]];
  }
  function hex(s) {
    var n = parseInt(String(s).replace("#", ""), 16);
    return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
  }
  function shade(c, k) { return [Math.min(1, c[0] * k), Math.min(1, c[1] * k), Math.min(1, c[2] * k)]; }

  // ---------- solidi colorati ----------
  function Geo() { this.d = []; }
  Geo.prototype.v = function (p, n, c) { this.d.push(p[0], p[1], p[2], n[0], n[1], n[2], c[0], c[1], c[2]); };
  Geo.prototype.quad = function (a, b, c, d, n, col) { this.v(a, n, col); this.v(b, n, col); this.v(c, n, col); this.v(a, n, col); this.v(c, n, col); this.v(d, n, col); };
  /* Parallelepipedo con centro (cx,cy,cz), ruotato di `ry` attorno alla verticale. */
  Geo.prototype.box = function (cx, cy, cz, w, h, d, col, ry) {
    var c = Math.cos(ry || 0), s = Math.sin(ry || 0), x = w / 2, y = h / 2, z = d / 2, self = this;
    function P(px, py, pz) { return [cx + px * c + pz * s, cy + py, cz - px * s + pz * c]; }
    function N(nx, ny, nz) { return [nx * c + nz * s, ny, -nx * s + nz * c]; }
    function face(a, b, e, f, n, k) { self.quad(P.apply(null, a), P.apply(null, b), P.apply(null, e), P.apply(null, f), N.apply(null, n), shade(col, k)); }
    face([-x, -y, z], [x, -y, z], [x, y, z], [-x, y, z], [0, 0, 1], 1);
    face([x, -y, -z], [-x, -y, -z], [-x, y, -z], [x, y, -z], [0, 0, -1], 1);
    face([x, -y, z], [x, -y, -z], [x, y, -z], [x, y, z], [1, 0, 0], 1);
    face([-x, -y, -z], [-x, -y, z], [-x, y, z], [-x, y, -z], [-1, 0, 0], 1);
    face([-x, y, z], [x, y, z], [x, y, -z], [-x, y, -z], [0, 1, 0], 1);
    face([-x, -y, -z], [x, -y, -z], [x, -y, z], [-x, -y, z], [0, -1, 0], 1);
    return this;
  };
  /* Cilindro o tronco di cono verticale: base in cy con raggio r0, cima in cy+h con raggio r1. */
  Geo.prototype.cyl = function (cx, cy, cz, r0, r1, h, col, seg) {
    var i, a0, a1, n0, n1, slope = (r0 - r1) / (h || 1);
    seg = seg || 14;
    for (i = 0; i < seg; i++) {
      a0 = i / seg * Math.PI * 2; a1 = (i + 1) / seg * Math.PI * 2;
      n0 = norm([Math.cos(a0), slope, Math.sin(a0)]); n1 = norm([Math.cos(a1), slope, Math.sin(a1)]);
      var b0 = [cx + Math.cos(a0) * r0, cy, cz + Math.sin(a0) * r0], b1 = [cx + Math.cos(a1) * r0, cy, cz + Math.sin(a1) * r0],
        t0 = [cx + Math.cos(a0) * r1, cy + h, cz + Math.sin(a0) * r1], t1 = [cx + Math.cos(a1) * r1, cy + h, cz + Math.sin(a1) * r1];
      this.v(b0, n0, col); this.v(b1, n1, col); this.v(t1, n1, col); this.v(b0, n0, col); this.v(t1, n1, col); this.v(t0, n0, col);
      if (r1 > 0) { this.v([cx, cy + h, cz], [0, 1, 0], col); this.v(t0, [0, 1, 0], col); this.v(t1, [0, 1, 0], col); }
      if (r0 > 0) { this.v([cx, cy, cz], [0, -1, 0], col); this.v(b1, [0, -1, 0], col); this.v(b0, [0, -1, 0], col); }
    }
    return this;
  };
  /* Sfera, anche schiacciata o allungata con sy. */
  Geo.prototype.sphere = function (cx, cy, cz, r, col, seg, sy) {
    var i, j, rings = seg || 10, segs = (seg || 10) * 2;
    sy = sy || 1;
    function pt(u, v) {
      var th = u * Math.PI, ph = v * Math.PI * 2, n = [Math.sin(th) * Math.cos(ph), Math.cos(th), Math.sin(th) * Math.sin(ph)];
      return { p: [cx + n[0] * r, cy + n[1] * r * sy, cz + n[2] * r], n: norm([n[0], n[1] / sy, n[2]]) };
    }
    for (i = 0; i < rings; i++) for (j = 0; j < segs; j++) {
      var a = pt(i / rings, j / segs), b = pt((i + 1) / rings, j / segs), c = pt((i + 1) / rings, (j + 1) / segs), d = pt(i / rings, (j + 1) / segs);
      this.v(a.p, a.n, col); this.v(b.p, b.n, col); this.v(c.p, c.n, col); this.v(a.p, a.n, col); this.v(c.p, c.n, col); this.v(d.p, d.n, col);
    }
    return this;
  };
  /* Disco orizzontale rivolto verso l'alto. */
  Geo.prototype.disc = function (cx, cy, cz, r, col, seg, rin) {
    var i, a0, a1;
    seg = seg || 24; rin = rin || 0;
    for (i = 0; i < seg; i++) {
      a0 = i / seg * Math.PI * 2; a1 = (i + 1) / seg * Math.PI * 2;
      var o0 = [cx + Math.cos(a0) * r, cy, cz + Math.sin(a0) * r], o1 = [cx + Math.cos(a1) * r, cy, cz + Math.sin(a1) * r],
        i0 = [cx + Math.cos(a0) * rin, cy, cz + Math.sin(a0) * rin], i1 = [cx + Math.cos(a1) * rin, cy, cz + Math.sin(a1) * rin];
      this.quad(i0, i1, o1, o0, [0, 1, 0], col);
    }
    return this;
  };
  /* Ottaedro (doppia piramide) centrato nell'origine: l'indicatore sopra la testa. */
  Geo.prototype.octa = function (r, h, col) {
    var top = [0, h, 0], bot = [0, -h, 0], ring = [[r, 0, 0], [0, 0, r], [-r, 0, 0], [0, 0, -r]], i, a, b;
    for (i = 0; i < 4; i++) {
      a = ring[i]; b = ring[(i + 1) % 4];
      var n1 = norm(cross([b[0] - a[0], b[1] - a[1], b[2] - a[2]], [top[0] - a[0], top[1] - a[1], top[2] - a[2]]));
      if (n1[1] < 0) n1 = [-n1[0], -n1[1], -n1[2]];
      this.v(a, n1, col); this.v(b, n1, col); this.v(top, n1, col);
      var n2 = [n1[0], -n1[1], n1[2]];
      this.v(b, n2, shade(col, 0.8)); this.v(a, n2, shade(col, 0.8)); this.v(bot, n2, shade(col, 0.8));
    }
    return this;
  };

  // ---------- disegno ----------
  var VS = "attribute vec3 aP;attribute vec3 aN;attribute vec3 aC;uniform mat4 uPV;uniform mat4 uM;varying vec3 vN;varying vec3 vC;varying vec3 vW;" +
    "void main(){vec4 w=uM*vec4(aP,1.0);vW=w.xyz;vN=mat3(uM)*aN;vC=aC;gl_Position=uPV*w;}";
  var FS = "precision mediump float;varying vec3 vN;varying vec3 vC;varying vec3 vW;uniform vec3 uL;uniform vec3 uTint;uniform vec3 uEye;uniform vec3 uFog;" +
    "uniform float uGlow;uniform float uAlpha;uniform float uFogNear;uniform float uFogFar;" +
    "void main(){vec3 n=normalize(vN);float d=max(dot(n,uL),0.0);float hemi=0.5+0.5*n.y;vec3 base=vC*uTint;" +
    "vec3 lit=base*(0.30+0.24*hemi+0.66*d);vec3 col=mix(lit,base*1.3,uGlow);" +
    "float f=clamp((distance(vW,uEye)-uFogNear)/(uFogFar-uFogNear),0.0,1.0);gl_FragColor=vec4(mix(col,uFog,f),uAlpha);}";
  var VST = "attribute vec3 aP;attribute vec2 aU;uniform mat4 uPV;uniform mat4 uM;varying vec2 vU;varying vec3 vW;" +
    "void main(){vec4 w=uM*vec4(aP,1.0);vW=w.xyz;vU=aU;gl_Position=uPV*w;}";
  var FST = "precision mediump float;varying vec2 vU;varying vec3 vW;uniform sampler2D uT;uniform vec3 uEye;uniform vec3 uFog;uniform float uGlow;" +
    "uniform float uFogNear;uniform float uFogFar;" +
    "void main(){vec3 col=texture2D(uT,vU).rgb*uGlow;float f=clamp((distance(vW,uEye)-uFogNear)/(uFogFar-uFogNear),0.0,1.0);gl_FragColor=vec4(mix(col,uFog,f),1.0);}";

  function program(gl, vs, fs, attrs, unis) {
    function sh(type, src) {
      var s = gl.createShader(type);
      gl.shaderSource(s, src); gl.compileShader(s);
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error("Mini3D: " + gl.getShaderInfoLog(s));
      return s;
    }
    var p = gl.createProgram(), out = { p: p, a: {}, u: {} };
    gl.attachShader(p, sh(gl.VERTEX_SHADER, vs)); gl.attachShader(p, sh(gl.FRAGMENT_SHADER, fs));
    attrs.forEach(function (n, i) { gl.bindAttribLocation(p, i, n); });
    gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error("Mini3D: " + gl.getProgramInfoLog(p));
    attrs.forEach(function (n) { out.a[n] = gl.getAttribLocation(p, n); });
    unis.forEach(function (n) { out.u[n] = gl.getUniformLocation(p, n); });
    return out;
  }

  function Renderer(canvas) {
    var opts = { antialias: true, alpha: false, powerPreference: "low-power" };
    var gl = canvas.getContext("webgl", opts) || canvas.getContext("experimental-webgl", opts);
    if (!gl) throw new Error("WebGL non disponibile");
    this.gl = gl; this.canvas = canvas;
    this.pc = program(gl, VS, FS, ["aP", "aN", "aC"], ["uPV", "uM", "uL", "uTint", "uEye", "uFog", "uGlow", "uAlpha", "uFogNear", "uFogFar"]);
    this.pt = program(gl, VST, FST, ["aP", "aU"], ["uPV", "uM", "uT", "uEye", "uFog", "uGlow", "uFogNear", "uFogFar"]);
    gl.enable(gl.DEPTH_TEST);
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
  }
  /* Da un Geo a un oggetto disegnabile. tint moltiplica il colore, glow (0..1) lo rende luminoso, alpha < 1 lo rende trasparente. */
  Renderer.prototype.mesh = function (geo) {
    var gl = this.gl, buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(geo.d), gl.STATIC_DRAW);
    return { buf: buf, count: geo.d.length / 9, model: ident(), tint: [1, 1, 1], glow: 0, alpha: 1, visible: true };
  };
  /* Pannello piatto w x h, centrato, rivolto verso +Z, che mostra un canvas 2D. */
  Renderer.prototype.panel = function (w, h, source) {
    var gl = this.gl, buf = gl.createBuffer(), x = w / 2, y = h / 2, tex = gl.createTexture();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-x, -y, 0, 0, 1, x, -y, 0, 1, 1, x, y, 0, 1, 0, -x, -y, 0, 0, 1, x, y, 0, 1, 0, -x, y, 0, 0, 0]), gl.STATIC_DRAW);
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, source);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    return { buf: buf, count: 6, tex: tex, model: ident(), glow: 1, visible: true };
  };
  Renderer.prototype.dispose = function (m) {
    if (!m) return;
    this.gl.deleteBuffer(m.buf);
    if (m.tex) this.gl.deleteTexture(m.tex);
  };
  Renderer.prototype.resize = function () {
    var c = this.canvas, dpr = Math.min(window.devicePixelRatio || 1, 2), w = Math.max(2, Math.round(c.clientWidth * dpr)), h = Math.max(2, Math.round(c.clientHeight * dpr));
    if (c.width !== w || c.height !== h) { c.width = w; c.height = h; return true; }
    return false;
  };
  /* Disegna: prima gli oggetti pieni, poi i pannelli con immagine, poi quelli trasparenti. */
  Renderer.prototype.draw = function (meshes, panels, cam, env) {
    var gl = this.gl, pc = this.pc, i, m, pass;
    gl.viewport(0, 0, this.canvas.width, this.canvas.height);
    gl.clearColor(env.fog[0], env.fog[1], env.fog[2], 1);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    gl.useProgram(pc.p);
    gl.uniformMatrix4fv(pc.u.uPV, false, cam.pv);
    gl.uniform3fv(pc.u.uL, env.light); gl.uniform3fv(pc.u.uEye, cam.eye); gl.uniform3fv(pc.u.uFog, env.fog);
    gl.uniform1f(pc.u.uFogNear, env.fogNear); gl.uniform1f(pc.u.uFogFar, env.fogFar);
    gl.enableVertexAttribArray(pc.a.aP); gl.enableVertexAttribArray(pc.a.aN); gl.enableVertexAttribArray(pc.a.aC);
    for (pass = 0; pass < 2; pass++) {
      if (pass === 1) {
        this._panels(panels, cam, env);
        gl.useProgram(pc.p);
        gl.enableVertexAttribArray(pc.a.aP); gl.enableVertexAttribArray(pc.a.aN); gl.enableVertexAttribArray(pc.a.aC);
        gl.enable(gl.BLEND); gl.depthMask(false);
      }
      for (i = 0; i < meshes.length; i++) {
        m = meshes[i];
        if (!m.visible || (m.alpha < 1) !== (pass === 1)) continue;
        gl.bindBuffer(gl.ARRAY_BUFFER, m.buf);
        gl.vertexAttribPointer(pc.a.aP, 3, gl.FLOAT, false, 36, 0);
        gl.vertexAttribPointer(pc.a.aN, 3, gl.FLOAT, false, 36, 12);
        gl.vertexAttribPointer(pc.a.aC, 3, gl.FLOAT, false, 36, 24);
        gl.uniformMatrix4fv(pc.u.uM, false, m.model);
        gl.uniform3fv(pc.u.uTint, m.tint); gl.uniform1f(pc.u.uGlow, m.glow); gl.uniform1f(pc.u.uAlpha, m.alpha);
        gl.drawArrays(gl.TRIANGLES, 0, m.count);
      }
    }
    gl.disable(gl.BLEND); gl.depthMask(true);
    gl.disableVertexAttribArray(pc.a.aN); gl.disableVertexAttribArray(pc.a.aC);
  };
  Renderer.prototype._panels = function (panels, cam, env) {
    var gl = this.gl, pt = this.pt, pc = this.pc, i, m;
    gl.disableVertexAttribArray(pc.a.aN); gl.disableVertexAttribArray(pc.a.aC);
    gl.useProgram(pt.p);
    gl.uniformMatrix4fv(pt.u.uPV, false, cam.pv);
    gl.uniform3fv(pt.u.uEye, cam.eye); gl.uniform3fv(pt.u.uFog, env.fog);
    gl.uniform1f(pt.u.uFogNear, env.fogNear); gl.uniform1f(pt.u.uFogFar, env.fogFar);
    gl.uniform1i(pt.u.uT, 0); gl.activeTexture(gl.TEXTURE0);
    gl.enableVertexAttribArray(pt.a.aP); gl.enableVertexAttribArray(pt.a.aU);
    for (i = 0; i < panels.length; i++) {
      m = panels[i];
      if (!m.visible) continue;
      gl.bindBuffer(gl.ARRAY_BUFFER, m.buf);
      gl.vertexAttribPointer(pt.a.aP, 3, gl.FLOAT, false, 20, 0);
      gl.vertexAttribPointer(pt.a.aU, 2, gl.FLOAT, false, 20, 12);
      gl.bindTexture(gl.TEXTURE_2D, m.tex);
      gl.uniformMatrix4fv(pt.u.uM, false, m.model); gl.uniform1f(pt.u.uGlow, m.glow);
      gl.drawArrays(gl.TRIANGLES, 0, 6);
    }
    gl.disableVertexAttribArray(pt.a.aU);
  };

  // ---------- camera a orbita ----------
  /* Trascina per ruotare, rotella o due dita per lo zoom, tasto destro o Maiusc per spostare. Frecce e +/- da tastiera. */
  function Orbit(canvas, o) {
    var self = this, pointers = {}, pinch = 0;
    this.target = o.target.slice(); this.dist = o.dist; this.az = o.az; this.pol = o.pol;
    this.minDist = o.minDist; this.maxDist = o.maxDist; this.fov = o.fov || 0.6; this.bounds = o.bounds || 20;
    this.moved = 0; this.onChange = o.onChange || function () {};
    function clamp() {
      self.pol = Math.max(0.28, Math.min(1.42, self.pol));
      self.dist = Math.max(self.minDist, Math.min(self.maxDist, self.dist));
      self.target[0] = Math.max(-self.bounds, Math.min(self.bounds, self.target[0]));
      self.target[2] = Math.max(-self.bounds, Math.min(self.bounds, self.target[2]));
      self.onChange();
    }
    this.clamp = clamp;
    canvas.addEventListener("pointerdown", function (e) {
      pointers[e.pointerId] = { x: e.clientX, y: e.clientY, pan: e.button === 2 || e.shiftKey };
      self.moved = 0;
      try { canvas.setPointerCapture(e.pointerId); } catch (err) { /* puntatore già rilasciato */ }
    });
    canvas.addEventListener("pointermove", function (e) {
      var p = pointers[e.pointerId], ids = Object.keys(pointers);
      if (!p) return;
      var dx = e.clientX - p.x, dy = e.clientY - p.y;
      p.x = e.clientX; p.y = e.clientY;
      self.moved += Math.abs(dx) + Math.abs(dy);
      if (ids.length >= 2) {
        var a = pointers[ids[0]], b = pointers[ids[1]], d = Math.hypot(a.x - b.x, a.y - b.y);
        if (pinch) self.dist *= pinch / (d || 1);
        pinch = d;
      } else if (p.pan) {
        var k = self.dist * 0.0016, c = Math.cos(self.az), s = Math.sin(self.az);
        self.target[0] -= (dx * c + dy * s) * k; self.target[2] -= (-dx * s + dy * c) * k;
      } else {
        self.az -= dx * 0.006; self.pol -= dy * 0.005;
      }
      clamp();
    });
    function up(e) { delete pointers[e.pointerId]; pinch = 0; }
    canvas.addEventListener("pointerup", up); canvas.addEventListener("pointercancel", up);
    canvas.addEventListener("wheel", function (e) { e.preventDefault(); self.dist *= Math.exp(e.deltaY * 0.0012); clamp(); }, { passive: false });
    canvas.addEventListener("contextmenu", function (e) { e.preventDefault(); });
    canvas.addEventListener("keydown", function (e) {
      var used = true;
      if (e.key === "ArrowLeft") self.az -= 0.12; else if (e.key === "ArrowRight") self.az += 0.12;
      else if (e.key === "ArrowUp") self.pol -= 0.08; else if (e.key === "ArrowDown") self.pol += 0.08;
      else if (e.key === "+" || e.key === "=") self.dist *= 0.9; else if (e.key === "-") self.dist *= 1.1;
      else used = false;
      if (used) { e.preventDefault(); clamp(); }
    });
  }
  Orbit.prototype.camera = function (aspect) {
    var t = this.target, sp = Math.sin(this.pol), eye = [t[0] + this.dist * sp * Math.sin(this.az), t[1] + this.dist * Math.cos(this.pol), t[2] + this.dist * sp * Math.cos(this.az)];
    var pv = mul(perspective(this.fov, aspect, 0.3, 400), lookAt(eye, t, [0, 1, 0]));
    return { pv: pv, eye: eye, inv: invert(pv) };
  };

  /* Raggio dal punto dello schermo (coordinate -1..1). */
  function ray(cam, nx, ny) {
    var a = apply(cam.inv, nx, ny, -1), b = apply(cam.inv, nx, ny, 1);
    return { o: a, d: norm([b[0] - a[0], b[1] - a[1], b[2] - a[2]]) };
  }
  /* Distanza lungo il raggio fino alla scatola [min,max], oppure -1 se non la tocca. */
  function rayBox(r, min, max) {
    var t0 = -Infinity, t1 = Infinity, i, a, b, inv;
    for (i = 0; i < 3; i++) {
      if (Math.abs(r.d[i]) < 1e-9) { if (r.o[i] < min[i] || r.o[i] > max[i]) return -1; continue; }
      inv = 1 / r.d[i]; a = (min[i] - r.o[i]) * inv; b = (max[i] - r.o[i]) * inv;
      if (a > b) { var s = a; a = b; b = s; }
      if (a > t0) t0 = a;
      if (b < t1) t1 = b;
      if (t0 > t1) return -1;
    }
    return t1 < 0 ? -1 : Math.max(t0, 0);
  }
  /* Punto 3D -> punto in pixel CSS dentro un riquadro w x h. */
  function project(cam, p, w, h) {
    var q = apply(cam.pv, p[0], p[1], p[2]);
    return { x: (q[0] * 0.5 + 0.5) * w, y: (1 - (q[1] * 0.5 + 0.5)) * h, nx: q[0], ny: q[1], ahead: q[3] > 0, front: q[3] > 0 && Math.abs(q[0]) < 1.2 && Math.abs(q[1]) < 1.2 };
  }

  return { ident: ident, mul: mul, trs: trs, hex: hex, shade: shade, Geo: Geo, Renderer: Renderer, Orbit: Orbit, ray: ray, rayBox: rayBox, project: project };
})();
