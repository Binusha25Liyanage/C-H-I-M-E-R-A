// appearance.js — Settings › Customize Appearance, and the two sidebar effects it controls:
//   • the logo glitch: the Chimera logo + "C H I M E R A" switch to a red neon look and back with a glitch
//     (or just a quick glitch flicker, or off). Only the logo and name move; never while the mouse is on them.
//   • the heartbeat line under the logo, drawn like a heart monitor: a red pen sweeps left to right and draws the
//     line, heartbeats land in a new place each sweep, the old line fades behind it. Live = faster while busy.
// The splash look and soundtrack are played by splash.js (it reads the same settings).
(function () {
  const API = () => window.pywebview.api;
  const reduce = () => window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
  const r = (a, b) => a + Math.random() * (b - a);
  const DEFAULT = { look_splash: "displacement", look_sound: "c", look_volume: "70", look_logo: "neon", look_logo_freq: "rare", look_ecg: "live", look_icon: "open", look_icon_every: "30" };
  let LOOK = { ...DEFAULT };

  const brand = document.querySelector(".sidebar-brand");
  const logo = brand && brand.querySelector(".brand-logo"), title = brand && brand.querySelector(".brand-title");

  // =============================================================== logo glitch
  const LG = { wrap: null, neon: null, isNeon: false, busy: false, hovering: false, timer: null };
  function logoSetup() {
    if (!logo || !title || LG.wrap) return;
    LG.wrap = document.createElement("span"); LG.wrap.className = "bg-wrap";
    logo.parentNode.insertBefore(LG.wrap, logo); LG.wrap.appendChild(logo);
    LG.neon = document.createElement("span"); LG.neon.className = "bn-neon";
    LG.neon.innerHTML = '<span class="bn-glow"><span class="bn-art"></span></span>'; LG.wrap.appendChild(LG.neon);
    brand.addEventListener("mouseenter", () => { LG.hovering = true; });
    brand.addEventListener("mouseleave", () => { LG.hovering = false; });
    flicker();
  }
  function setNeon(n) {
    LG.isNeon = n; LG.wrap.classList.toggle("bn-on", n); title.classList.toggle("neon", n);
    LG.neon.style.clipPath = ""; LG.neon.style.transform = "";
  }
  // the glitch: slices of the logo jump about with a cyan/magenta misprint, the name scrambles for a moment;
  // in Neon mode it lands on the other look (normal -> neon, neon -> normal)
  function glitch(done) {
    if (!LG.wrap || LG.busy) return;
    LG.busy = true;
    const withNeon = LOOK.look_logo === "neon", to = withNeon ? !LG.isNeon : false;
    const slices = ["m", "c", "", "m"].map(cls => {
      const s = document.createElement("span"); s.className = "bg-slice " + cls;
      const im = document.createElement("img"); im.src = logo.src; s.appendChild(im); LG.wrap.appendChild(s); return s; });
    const text = title.textContent, letters = text.split(""), pool = "CHIMERA█▓▒/\\<>";
    if (withNeon) LG.wrap.classList.add("bn-on");
    const t0 = performance.now(), DUR = withNeon ? 520 : 450; let last = 0;
    const tick = (now) => {
      const p = (now - t0) / DUR;
      if (now - last >= 83) {                                   // 12 fps — choppy on purpose
        last = now;
        if (withNeon) {
          const want = to ? p : 1 - p, top = r(0, 70) * (1 - want), h = r(15, 30) + want * 60;
          LG.neon.style.clipPath = `inset(${top}% 0 ${Math.max(0, 100 - top - h)}% 0)`;
          LG.neon.style.transform = `translate(${r(-5, 5) * (1 - p)}px, 0)`;
          title.classList.toggle("neon", Math.random() < want + .15);
        }
        slices.forEach(s => {
          const st = r(0, 85), sh = r(8, 28);
          s.style.clipPath = `inset(${st}% 0 ${Math.max(0, 100 - st - sh)}% 0)`;
          s.style.transform = `translate(${r(-6, 6) * (1 - p)}px, ${r(-1.5, 1.5)}px)`;
          s.style.opacity = Math.random() < .55 ? 1 : 0;
        });
        logo.style.transform = `translate(${r(-2, 2)}px, 0)`;
        title.classList.toggle("bg-on", Math.random() < .7);
        title.style.transform = `translateX(${r(-3, 3) * (1 - p)}px)`;
        title.textContent = letters.map(ch => ch !== " " && Math.random() < .2 ? pool[Math.floor(r(0, pool.length))] : ch).join("");
      }
      if (p < 1) return requestAnimationFrame(tick);
      slices.forEach(s => s.remove()); logo.style.transform = ""; title.style.transform = "";
      title.classList.remove("bg-on"); title.textContent = text;
      setNeon(to); LG.busy = false; if (done) done();
    };
    requestAnimationFrame(tick);
  }
  function logoSchedule() {
    clearTimeout(LG.timer);
    if (!LG.wrap) return;
    if (LOOK.look_logo === "off") { if (LG.isNeon && !LG.busy) setNeon(false); return; }
    if (LOOK.look_logo !== "neon" && LG.isNeon && !LG.busy) setNeon(false);
    const often = LOOK.look_logo_freq === "often";
    const wait = LG.isNeon ? r(6000, 12000) : often ? r(8000, 15000) : r(20000, 45000);
    LG.timer = setTimeout(() => {
      if (LOOK.look_logo === "off") return;
      if (reduce() || LG.hovering || document.hidden) { if (LG.isNeon && !LG.busy) setNeon(false); logoSchedule(); }
      else glitch(logoSchedule);
    }, wait);
  }
  // a neon tube is never perfectly steady: now and then it dims for a blink
  function flicker() {
    setTimeout(() => {
      if (LG.isNeon && !LG.busy && !reduce()) {
        const blinks = Math.random() < .4 ? 2 : 1; let i = 0;
        const blink = () => { LG.neon.classList.add("dim"); title.classList.add("dim");
          setTimeout(() => { LG.neon.classList.remove("dim"); title.classList.remove("dim"); if (++i < blinks) setTimeout(blink, 70); }, r(50, 110)); };
        blink();
      }
      flicker();
    }, r(900, 2600));
  }

  // =============================================================== heartbeat line
  const EG = { cv: null, g: null, W: 0, dpr: 1, ys: [], head: 0, since: 999, next: 60, last: 0, busy: false, raf: null };
  const H = 30, MID = 17, AMP = 13, GAP = 16, BEAT_LEN = 50;
  const bump = (s, c, w) => Math.exp(-((s - c) * (s - c)) / (2 * w * w));
  // one heartbeat (P wave, Q dip, tall R spike, S dip, T wave) by px since the beat started
  const beatAt = (s) => 0.14 * bump(s, 7, 2.6) - 0.14 * bump(s, 16, 1.1) + 1.0 * bump(s, 19, 1.25) - 0.38 * bump(s, 22.5, 1.3) + 0.26 * bump(s, 37, 4.5);

  function ecgSize() {
    if (!EG.cv) return;
    EG.dpr = window.devicePixelRatio || 1; EG.W = Math.max(40, Math.round(EG.cv.clientWidth || 240));
    EG.cv.width = EG.W * EG.dpr; EG.cv.height = H * EG.dpr; EG.ys = new Array(EG.W).fill(null);
    if (LOOK.look_ecg === "still") ecgStill();
  }
  function ecgApply() {
    const mode = LOOK.look_ecg;
    if (!brand) return;
    if (mode === "off") {
      if (EG.cv) { EG.cv.remove(); EG.cv = null; }
      cancelAnimationFrame(EG.raf); EG.raf = null; brand.classList.remove("ecg-on"); return;
    }
    if (!EG.cv) {
      EG.cv = document.createElement("canvas"); EG.cv.className = "brand-ecg"; EG.cv.setAttribute("aria-hidden", "true");
      brand.insertAdjacentElement("afterend", EG.cv); EG.g = EG.cv.getContext("2d");
      brand.classList.add("ecg-on"); ecgSize();
    }
    cancelAnimationFrame(EG.raf); EG.raf = null;
    if (mode === "still" || reduce()) { ecgStill(); return; }
    EG.last = performance.now(); EG.raf = requestAnimationFrame(ecgStep);
  }
  function ecgStill() {
    for (let x = 0; x < EG.W; x++) { const s = (x + 40) % 112; EG.ys[x] = MID - (s < BEAT_LEN ? beatAt(s) : 0) * AMP; }
    EG.head = EG.W - 1; ecgDraw(true);
  }
  function ecgStep(now) {
    if (!EG.cv) return;
    const dt = Math.min(0.05, (now - EG.last) / 1000); EG.last = now;
    const fast = EG.busy && LOOK.look_ecg === "live";
    const p = fast ? { speed: 150, every: 64, jitter: 10 } : { speed: 85, every: 112, jitter: 22 };
    let move = p.speed * dt;
    while (move > 0) {                                          // write the signal pixel by pixel as the pen moves
      const d = Math.min(1, move); move -= d; EG.head += d; EG.since += d;
      if (EG.since >= EG.next) { EG.since = 0; EG.next = p.every + (Math.random() * 2 - 1) * p.jitter; }
      const x = Math.floor(EG.head) % EG.W;
      EG.ys[x] = MID - (EG.since < BEAT_LEN ? beatAt(EG.since) : 0) * AMP - (Math.random() - 0.5) * 0.04 * AMP;
      for (let k = 1; k <= GAP; k++) EG.ys[(x + k) % EG.W] = null; // wipe just ahead of the pen
    }
    if (!document.hidden) ecgDraw(false);
    EG.raf = requestAnimationFrame(ecgStep);
  }
  function ecgDraw(still) {
    const g = EG.g, W = EG.W, ys = EG.ys; if (!g) return;
    g.setTransform(EG.dpr, 0, 0, EG.dpr, 0, 0); g.clearRect(0, 0, W, H);
    const hx = Math.floor(EG.head) % W;
    g.lineWidth = 1.7; g.lineJoin = "round"; g.lineCap = "round";
    for (let x = 1; x < W; x++) {                               // older line = dimmer
      if (ys[x] == null || ys[x - 1] == null || (!still && x - 1 === hx)) continue;
      const a = still ? 0.8 : Math.max(0.08, 1 - (((hx - x + W) % W) / W) * 1.05);
      g.strokeStyle = `rgba(255, 40, 58, ${a.toFixed(3)})`;
      g.beginPath(); g.moveTo(x - 1, ys[x - 1]); g.lineTo(x, ys[x]); g.stroke();
    }
    if (still) return;
    g.save(); g.shadowColor = "rgba(255, 50, 65, 1)"; g.shadowBlur = 8; g.strokeStyle = "#ff4d5c"; g.lineWidth = 2;
    g.beginPath(); let started = false;
    for (let k = 10; k >= 0; k--) { const x = (hx - k + W) % W; if (ys[x] == null || x > hx) { started = false; continue; }
      if (started) g.lineTo(x, ys[x]); else g.moveTo(x, ys[x]); started = true; }
    g.stroke();
    if (ys[hx] != null) { g.fillStyle = "#fff"; g.beginPath(); g.arc(hx, ys[hx], 1.6, 0, Math.PI * 2); g.fill(); }
    g.restore();
  }
  window.addEventListener("resize", ecgSize);
  window.chimeraEcgBusy = (b) => { EG.busy = !!b; };           // running.js: something in Running now

  function applyLook(look) {
    LOOK = { ...DEFAULT, ...(look || {}) };
    window.chimeraLook = LOOK;
    logoSetup(); logoSchedule(); ecgApply();
  }
  window.chimeraBrandGlitch = () => glitch(logoSchedule);
  applyLook(DEFAULT);                                         // until the saved settings arrive

  // =============================================================== scrollbars appear only while scrolling
  function showWhileScrolling(target, node) {
    let t = null;
    target.addEventListener("scroll", () => {
      node.classList.add("is-scrolling"); clearTimeout(t);
      t = setTimeout(() => node.classList.remove("is-scrolling"), 900);
    }, { passive: true });
  }
  showWhileScrolling(window, document.documentElement);
  const sideEl = document.querySelector(".sidebar"); if (sideEl) showWhileScrolling(sideEl, sideEl);

  // =============================================================== Settings › Customize Appearance
  const $ = (id) => document.getElementById(id);
  const h = (t) => String(t == null ? "" : t).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const SOUND_INFO = { a: "Warm pad over a soft heartbeat", b: "Glassy bells caught in a stutter", c: "Clean, confident brand landing",
    d: "Fast punk riff that glitches", e: "Lo-fi beat, 808 and scratches", f: "Trailer synths with a big hit",
    g: "Fingerpicked guitar, then a strum", h: "Gliding synth lead, vowel stabs" };
  // the built-in openings: name, what happens, and a small picture of it
  const RED = `<defs><linearGradient id="lkR" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#3a0c10"/><stop offset=".5" stop-color="#8f0e15"/><stop offset="1" stop-color="#c23a42"/></linearGradient></defs><rect width="200" height="96" fill="url(#lkR)"/>`;
  const DARK = `<rect width="200" height="96" fill="#111215"/>`;
  const LOGO = (x, y, rr, c) => `<circle cx="${x}" cy="${y}" r="${rr}" fill="none" stroke="${c}" stroke-width="1.6"/><circle cx="${x}" cy="${y}" r="${rr * .62}" fill="none" stroke="${c}" stroke-width="1"/>`;
  const SPLASHES = [
    ["displacement", "Dimensional displacement", "Red opening, then the comic-book displacement into the dark splash.",
      `${RED}${LOGO(100, 40, 15, "#f1e6d2")}<rect x="70" y="14" width="96" height="34" fill="#111215" stroke="#fff" stroke-width="1.5"/><rect x="66" y="14" width="4" height="34" fill="#00e6ff" opacity=".8"/><rect x="166" y="14" width="4" height="34" fill="#ff0096" opacity=".8"/>${LOGO(118, 31, 9, "#ccc")}<rect x="62" y="72" width="76" height="3" fill="#fff" opacity=".8"/>`],
    ["glitch", "Glitch", "Red opening, then a fast slice glitch into the dark splash.",
      `${DARK}${RED.replace('height="96"', 'height="22"')}<rect y="40" width="200" height="12" fill="#a8101a" transform="translate(14 0)"/><rect y="66" width="200" height="8" fill="#b5121b" transform="translate(-10 0)"/>${LOGO(100, 46, 14, "#ddd")}<rect y="56" width="200" height="2" fill="#fff" opacity=".9"/>`],
    ["neon", "Neon power-up", "A dark wall: the logo and name light up tube by tube, the sign glitches and surges, then the room lights up.",
      `<rect width="200" height="96" fill="#0a0a0c"/><g opacity=".08" stroke="#fff"><path d="M0 24h200M0 48h200M0 72h200M50 0v24M150 0v24M100 24v24M0 48v24M100 48v24M50 72v24M150 72v24"/></g><g style="filter:drop-shadow(0 0 3px #ff2d3f)">${LOGO(100, 36, 15, "#ff4a5d")}<text x="100" y="78" text-anchor="middle" font-size="14" font-weight="700" letter-spacing="5" fill="#ffd9dd">CHIM<tspan fill="#3b171c">ERA</tspan></text></g>`],
    ["heartbeat", "Heartbeat boot", "A monitor line runs across black, one big beat sends out a red pulse, then the splash.",
      `<rect width="200" height="96" fill="#050506"/><polyline points="0,58 30,58 34,54 38,58 44,58 47,62 50,50 53,66 56,58 90,58 96,58 100,62 104,12 110,90 114,58 140,58" fill="none" stroke="#ff2d3f" stroke-width="2" style="filter:drop-shadow(0 0 3px #ff2d3f)"/><circle cx="140" cy="58" r="2.5" fill="#fff"/><circle cx="104" cy="48" r="30" fill="none" stroke="#ff2d3f" stroke-width="1.2" opacity=".5"/>`],
    ["portal", "Portal rift", "The red opening tears open down the middle and splits apart onto the dark splash.",
      `${DARK}${LOGO(100, 40, 13, "#bbb")}<g style="filter:drop-shadow(0 0 4px #ff3d55)"><polygon points="0,0 72,0 76,20 70,42 77,64 71,96 0,96" fill="url(#lkR)"/><polygon points="200,0 128,0 124,22 131,44 123,66 129,96 200,96" fill="url(#lkR)"/></g>${RED.replace(/<rect[^>]*\/>/, "")}<rect x="96" y="0" width="8" height="96" fill="#ff5a6b" opacity=".25"/>`],
    ["shatter", "Shatter", "The red opening cracks like glass and falls away in pieces.",
      `${DARK}${RED.replace(/<rect[^>]*\/>/, "")}<polygon points="0,0 110,0 112,40 60,52 0,60" fill="url(#lkR)"/><polygon points="110,0 200,0 200,46 112,40" fill="url(#lkR)"/><polygon points="12,66 58,58 64,86 20,96" fill="url(#lkR)" transform="rotate(-10 40 76) translate(-4 6)"/><polygon points="130,52 196,56 186,92 142,86" fill="url(#lkR)" transform="rotate(12 160 72) translate(6 10)"/><g stroke="#fff" stroke-width=".9" opacity=".9" fill="none"><path d="M112 40 L150 10 M112 40 L170 44 M112 40 L80 8 M112 40 L60 52 M112 40 L118 70"/></g>`],
    ["assemble", "Chimera assemble", "The logo draws itself in a sweep, fills in, then the splash fades up.",
      `${DARK}<g style="filter:drop-shadow(0 0 3px #ff2d3f)"><path d="M100 18 A22 22 0 1 1 78 40" fill="none" stroke="#ff6474" stroke-width="2"/><path d="M100 26 A14 14 0 1 1 86 40" fill="none" stroke="#ff6474" stroke-width="1.2"/></g><circle cx="78" cy="40" r="3" fill="#fff" style="filter:drop-shadow(0 0 4px #ff2d3f)"/><text x="100" y="82" text-anchor="middle" font-size="13" font-weight="700" letter-spacing="5" fill="#e9ecef">CHIM<tspan opacity=".25">ERA</tspan></text>`],
    ["terminal", "Terminal boot", "A console types the startup and a CHIMERA banner, then crash-glitches into the splash.",
      `<rect width="200" height="96" fill="#070708"/><g font-family="Consolas,monospace" font-size="7" fill="#c9ced4"><text x="10" y="14">memory check ........ ok</text><text x="10" y="23">sfa driver .......... ok</text><text x="10" y="32">&gt; chimera --start</text></g><g fill="#ff3347" style="filter:drop-shadow(0 0 2px #ff2d3f)"><rect x="10" y="44" width="16" height="4"/><rect x="10" y="44" width="4" height="22"/><rect x="10" y="62" width="16" height="4"/><rect x="32" y="44" width="4" height="22"/><rect x="44" y="44" width="4" height="22"/><rect x="32" y="53" width="16" height="4"/><rect x="54" y="44" width="4" height="22"/><rect x="64" y="44" width="22" height="4"/><rect x="64" y="44" width="4" height="22"/><rect x="73" y="44" width="4" height="22"/><rect x="82" y="44" width="4" height="22"/></g><rect x="92" y="62" width="6" height="5" fill="#ddd"/>`],
    ["vhs", "VHS rewind", "The red opening on an old tape — rewind, ▶ PLAY, then a CRT snap into the splash.",
      `${RED}${LOGO(100, 42, 14, "#f1e6d2")}<g opacity=".35">${Array.from({ length: 24 }, (_, i) => `<rect y="${i * 4}" width="200" height="2" fill="#000"/>`).join("")}</g><rect y="66" width="200" height="8" fill="#fff" opacity=".35"/><text x="10" y="18" font-family="Consolas,monospace" font-size="11" font-weight="700" fill="#fff">&#9654; PLAY</text><text x="190" y="88" text-anchor="end" font-family="Consolas,monospace" font-size="9" fill="#fff">SP 0:00:00</text>`],
    ["classic", "Classic", "Straight to the dark splash, no opening animation.",
      `${DARK}${LOGO(100, 36, 15, "#ccc")}<text x="100" y="72" text-anchor="middle" font-size="11" font-family="Georgia,serif" letter-spacing="4" fill="#ccc">CHIMERA</text><rect x="70" y="80" width="60" height="2" fill="#d3132b"/>`],
  ];
  const TRANSITIONS = [["displacement", "Displacement"], ["glitch", "Glitch"], ["fade", "Fade"], ["cut", "Cut"]];
  let sounds = Object.keys(SOUND_INFO).map(k => ({ key: k, name: k.toUpperCase() }));
  let custom = [];
  const pv = { audio: null, key: null };
  const editing = { id: null };
  const mb = (n) => n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`;

  function stopPreview() { if (pv.audio) { pv.audio.pause(); pv.audio = null; } pv.key = null; paintSounds(); }
  async function playPreview(key) {
    const same = pv.key === key; stopPreview(); if (same) return;
    let src = `assets/sounds/chimera-splash-${key}.mp3`;
    if (key.startsWith("s_")) { pv.key = key; paintSounds(); src = window.chimeraMediaUrl ? await window.chimeraMediaUrl(key) : null; if (pv.key !== key) return;
      if (!src) { msg("That sound file can't be played.", true); stopPreview(); return; } }
    pv.audio = new Audio(src);
    pv.audio.volume = Math.max(0.05, (parseInt(LOOK.look_volume, 10) || 0) / 100);
    pv.audio.onended = stopPreview; pv.key = key;
    pv.audio.play().catch(() => { msg("Couldn't play the sound on this computer.", true); stopPreview(); });
    paintSounds();
  }
  const nameCell = (id, name, sub) => editing.id === id
    ? `<span class="lk-name"><input class="lk-rename" data-rename="${id}" value="${h(name)}" maxlength="40" style="font:inherit;padding:2px 6px;width:100%;"></span>`
    : `<span><b>${h(name)}</b><small>${h(sub)}</small></span>`;
  function paintSounds() {
    const box = $("lk-sounds"); if (!box) return;
    const builtIn = (s) => `<div class="lk-snd ${LOOK.look_sound === s.key ? "on" : ""}" data-snd="${s.key}" role="button" tabindex="0">
        <button class="lk-play ${pv.key === s.key ? "playing" : ""}" data-play="${s.key}" title="Listen">${pv.key === s.key ? "&#9632;" : "&#9654;"}</button>
        <span><b>${s.key.toUpperCase()} · ${h(s.name)}</b><small>${h(SOUND_INFO[s.key] || "")}</small></span></div>`;
    const mine = (c) => `<div class="lk-snd ${LOOK.look_sound === c.id ? "on" : ""}" data-snd="${c.id}" role="button" tabindex="0">
        <button class="lk-play ${pv.key === c.id ? "playing" : ""}" data-play="${c.id}" title="Listen">${pv.key === c.id ? "&#9632;" : "&#9654;"}</button>
        ${nameCell(c.id, c.name, `Yours · ${mb(c.size || 0)}`)}
        <span class="lk-ed"><button class="lk-mini" data-ren="${c.id}">Rename</button><button class="lk-mini" data-del="${c.id}">Remove</button></span></div>`;
    box.innerHTML = sounds.map(builtIn).join("") + custom.filter(c => c.kind === "sound").map(mine).join("")
      + `<div class="lk-snd ${LOOK.look_sound === "none" ? "on" : ""}" data-snd="none" role="button" tabindex="0"><span class="lk-play" style="display:grid;place-items:center;">&#8856;</span>
          <span><b>No sound</b><small>The splash opens silently</small></span></div>
         <button class="lk-snd lk-add" id="lk-add-sound"><span class="lk-play" style="display:grid;place-items:center;background:none;color:var(--color-primary);font-size:20px;">+</span>
          <span><b>Add soundtrack</b><small>MP3, WAV, OGG or M4A from your computer</small></span></button>`;
    const inp = box.querySelector(".lk-rename"); if (inp) { inp.focus(); inp.select(); }
  }
  function paintSplashes() {
    const box = $("lk-splash"); if (!box) return;
    const card = ([v, name, about, svg]) => `<div class="lk-card ${LOOK.look_splash === v ? "on" : ""}" data-v="${v}" role="button" tabindex="0">
        <span class="lk-thumb"><svg viewBox="0 0 200 96" preserveAspectRatio="xMidYMid slice">${svg}</svg></span>
        ${v === "classic" ? "" : `<button class="lk-pv" data-pv="${v}" title="Watch it">&#9654;</button>`}
        <b>${h(name)}</b><span class="hint">${h(about)}</span></div>`;
    const video = (c) => `<div class="lk-card ${LOOK.look_splash === c.id ? "on" : ""}" data-v="${c.id}" role="button" tabindex="0">
        <span class="lk-thumb"><svg viewBox="0 0 200 96"><rect width="200" height="96" fill="#1b1c20"/><polygon points="88,32 88,64 116,48" fill="#fff" opacity=".85"/></svg></span>
        <span class="lk-tag">YOUR VIDEO</span><button class="lk-pv" data-pv="${c.id}" title="Watch it">&#9654;</button>
        ${editing.id === c.id ? `<input class="lk-rename" data-rename="${c.id}" value="${h(c.name)}" maxlength="40" style="font:inherit;font-weight:700;padding:2px 6px;">` : `<b>${h(c.name)}</b>`}
        <span class="hint">Your video, then into the splash with:</span>
        <span class="lk-row"><select data-tr="${c.id}">${TRANSITIONS.map(([k, l]) => `<option value="${k}" ${c.transition === k ? "selected" : ""}>${l}</option>`).join("")}</select>
          <button class="lk-mini" data-ren="${c.id}">Rename</button><button class="lk-mini" data-del="${c.id}">Remove</button>
          <span class="hint" style="font-size:11px;">${mb(c.size || 0)}</span></span></div>`;
    box.innerHTML = SPLASHES.map(card).join("") + custom.filter(c => c.kind === "video").map(video).join("")
      + `<button class="lk-card lk-add" id="lk-add-video"><span class="lk-plus">+</span><b>Add video opening</b>
          <span class="hint">An MP4 or WebM of your own (e.g. from CapCut). It plays full screen, then hands over to the splash.
          Its own sound plays only when the soundtrack is set to No sound.</span></button>`;
    const inp = box.querySelector(".lk-rename"); if (inp) { inp.focus(); inp.select(); }
  }
  function paint() {
    paintSplashes();
    [["lk-logo", "look_logo"], ["lk-logo-freq", "look_logo_freq"], ["lk-ecg", "look_ecg"], ["lk-icon", "look_icon"], ["lk-icon-every", "look_icon_every"]].forEach(([id, k]) =>
      document.querySelectorAll(`#${id} button`).forEach(b => b.classList.toggle("on", b.dataset.v === LOOK[k])));
    $("lk-logo-freq").classList.toggle("lk-dis", LOOK.look_logo === "off");
    $("lk-logo-try").disabled = LOOK.look_logo === "off";
    if ($("lk-icon-every")) $("lk-icon-every").classList.toggle("lk-dis", LOOK.look_icon === "off");
    $("lk-volume").value = LOOK.look_volume; $("lk-volume-v").textContent = LOOK.look_volume + "%";
    paintSounds();
  }
  function msg(t, err) { const m = $("lk-msg"); if (m) m.innerHTML = t ? `<span class="${err ? "error" : "hint"}">${h(t)}</span>` : ""; }
  const needNew = () => msg("Needs the latest backend files (api.py, automation_store.py, appearance_media.py) — replace them and restart Chimera.", true);
  function took(res) {                                        // a server answer that carries the new look
    if (res && res.look) { if (res.look.sounds) sounds = res.look.sounds; custom = res.look.custom || []; applyLook(res.look); }
    paint();
  }

  async function save(vals) {
    const before = { ...LOOK };
    applyLook({ ...LOOK, ...vals }); paint();                  // show it at once
    if (typeof API().save_appearance !== "function") { needNew(); return; }
    try {
      const res = await API().save_appearance(vals);
      if (!res.ok) { applyLook(before); paint(); msg(res.message, true); return; }
      msg("Saved.");
    } catch (e) { msg("Couldn't save: " + e, true); }
  }
  async function addMedia(kind) {
    if (typeof API().add_look_media !== "function") { needNew(); return; }
    msg(kind === "sound" ? "Choose a sound file…" : "Choose a video file…");
    const res = await API().add_look_media(kind);
    if (res.cancelled) { msg(""); return; }
    if (!res.ok) { msg(res.message, true); return; }
    took(res);
    await save(kind === "sound" ? { look_sound: res.item.id } : { look_splash: res.item.id });
    msg(`${res.message} It's chosen now — ${kind === "sound" ? "click ▶ to listen" : "click ▶ on its card to watch it"}.`);
  }
  async function rename(id, name) {
    editing.id = null;
    const res = await API().update_look_media(id, { name });
    if (!res.ok) msg(res.message, true); else msg("Renamed.");
    took(res);
  }
  function remove(id) {
    const c = custom.find(x => x.id === id); if (!c) return;
    const go = async () => {
      if (pv.key === id) stopPreview();
      const res = await API().delete_look_media(id);
      msg(res.message, !res.ok); took(res);
    };
    if (typeof confirmModal === "function") confirmModal(`Remove “${c.name}”?`, `Chimera deletes its own copy of the file (the original on your computer stays). ${
      (LOOK.look_sound === id || LOOK.look_splash === id) ? "It's chosen now, so the default comes back." : ""}`, "Remove", go, true);
    else go();
  }

  window.loadAppearanceSettings = async () => {
    if (typeof API().get_appearance !== "function") { needNew(); paint(); return; }
    try { took({ look: await API().get_appearance() }); } catch (e) { paint(); }
    msg("");
  };

  if ($("st-panel-look")) {
    const panel = $("st-panel-look");
    panel.addEventListener("click", (e) => {
      const t = e.target;
      if (t.closest(".lk-rename") || t.closest("select")) return;
      const pvb = t.closest("[data-pv]");
      if (pvb) { e.stopPropagation(); stopPreview(); if (window.chimeraSplashPreview) window.chimeraSplashPreview({ ...LOOK, look_splash: pvb.dataset.pv, custom }); return; }
      const play = t.closest("[data-play]"); if (play) { e.stopPropagation(); playPreview(play.dataset.play); return; }
      const ren = t.closest("[data-ren]"); if (ren) { e.stopPropagation(); editing.id = ren.dataset.ren; paint(); return; }
      const del = t.closest("[data-del]"); if (del) { e.stopPropagation(); remove(del.dataset.del); return; }
      if (t.closest("#lk-add-sound")) { addMedia("sound"); return; }
      if (t.closest("#lk-add-video")) { addMedia("video"); return; }
      const card = t.closest("#lk-splash .lk-card[data-v]"); if (card) { save({ look_splash: card.dataset.v }); return; }
      const row = t.closest("#lk-sounds [data-snd]"); if (row) { save({ look_sound: row.dataset.snd }); return; }
    });
    panel.addEventListener("change", async (e) => {
      const sel = e.target.closest("select[data-tr]"); if (!sel) return;
      const res = await API().update_look_media(sel.dataset.tr, { transition: sel.value });
      msg(res.ok ? "Saved." : res.message, !res.ok); took(res);
    });
    panel.addEventListener("keydown", (e) => {
      const inp = e.target.closest(".lk-rename");
      if (inp && e.key === "Enter") rename(inp.dataset.rename, inp.value);
      else if (inp && e.key === "Escape") { editing.id = null; paint(); }
    });
    panel.addEventListener("focusout", (e) => {
      const inp = e.target.closest(".lk-rename");
      if (inp && editing.id === inp.dataset.rename) rename(inp.dataset.rename, inp.value);
    });
    [["lk-logo", "look_logo"], ["lk-logo-freq", "look_logo_freq"], ["lk-ecg", "look_ecg"], ["lk-icon", "look_icon"], ["lk-icon-every", "look_icon_every"]].forEach(([id, k]) =>
      $(id).addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) save({ [k]: b.dataset.v }); }));
    $("lk-volume").addEventListener("input", (e) => { $("lk-volume-v").textContent = e.target.value + "%"; if (pv.audio) pv.audio.volume = Math.max(0.05, e.target.value / 100); });
    $("lk-volume").addEventListener("change", (e) => save({ look_volume: e.target.value }));
    $("lk-play").addEventListener("click", () => { stopPreview(); if (window.chimeraSplashPreview) window.chimeraSplashPreview({ ...LOOK, custom }); });
    $("lk-logo-try").addEventListener("click", () => { clearTimeout(LG.timer); glitch(logoSchedule); });
    $("lk-icon-try").addEventListener("click", async () => {
      if (typeof API().try_desktop_icon !== "function") { needNew(); return; }
      const b = $("lk-icon-try"); b.disabled = true; msg("Glitching the desktop shortcut…");
      try { const r = await API().try_desktop_icon(); msg(r.message, !r.ok); } catch (e) { msg("Couldn't: " + e, true); }
      b.disabled = false;
    });
  }

  window.addEventListener("pywebviewready", async () => {
    if (typeof API().get_appearance !== "function") return;
    try { const l = await API().get_appearance(); if (l.sounds) sounds = l.sounds; custom = l.custom || []; applyLook(l); } catch (e) { /* defaults stay */ }
  });
})();
