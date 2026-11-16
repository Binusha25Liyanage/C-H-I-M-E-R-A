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
  const DEFAULT = { look_splash: "displacement", look_sound: "c", look_volume: "70", look_logo: "neon", look_logo_freq: "rare", look_ecg: "live" };
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
  const SOUND_INFO = { a: "Warm pad over a soft heartbeat", b: "Glassy bells caught in a stutter", c: "Clean, confident brand landing",
    d: "Fast punk riff that glitches", e: "Lo-fi beat, 808 and scratches", f: "Trailer synths with a big hit",
    g: "Fingerpicked guitar, then a strum", h: "Gliding synth lead, vowel stabs" };
  let sounds = Object.keys(SOUND_INFO).map(k => ({ key: k, name: k.toUpperCase() }));
  const pv = { audio: null, key: null };

  function stopPreview() { if (pv.audio) { pv.audio.pause(); pv.audio = null; } pv.key = null; paintSounds(); }
  function playPreview(key) {
    const same = pv.key === key; stopPreview(); if (same) return;
    pv.audio = new Audio(`assets/sounds/chimera-splash-${key}.mp3`);
    pv.audio.volume = Math.max(0.05, (parseInt(LOOK.look_volume, 10) || 0) / 100);
    pv.audio.onended = stopPreview; pv.key = key;
    pv.audio.play().catch(() => { msg("Couldn't play the sound on this computer.", true); stopPreview(); });
    paintSounds();
  }
  function paintSounds() {
    const box = $("lk-sounds"); if (!box) return;
    const row = (s) => `<div class="lk-snd ${LOOK.look_sound === s.key ? "on" : ""}" data-snd="${s.key}" role="button" tabindex="0">
        ${s.key === "none" ? `<span class="lk-play" style="display:grid;place-items:center;">&#8856;</span>`
          : `<button class="lk-play ${pv.key === s.key ? "playing" : ""}" data-play="${s.key}" title="Listen">${pv.key === s.key ? "&#9632;" : "&#9654;"}</button>`}
        <span><b>${s.key === "none" ? "No sound" : `${s.key.toUpperCase()} · ${s.name}`}</b><small>${s.key === "none" ? "The splash opens silently" : SOUND_INFO[s.key] || ""}</small></span></div>`;
    box.innerHTML = [...sounds, { key: "none", name: "No sound" }].map(row).join("");
  }
  function paint() {
    document.querySelectorAll("#lk-splash .lk-card").forEach(b => b.classList.toggle("on", b.dataset.v === LOOK.look_splash));
    [["lk-logo", "look_logo"], ["lk-logo-freq", "look_logo_freq"], ["lk-ecg", "look_ecg"]].forEach(([id, k]) =>
      document.querySelectorAll(`#${id} button`).forEach(b => b.classList.toggle("on", b.dataset.v === LOOK[k])));
    $("lk-logo-freq").classList.toggle("lk-dis", LOOK.look_logo === "off");
    $("lk-logo-try").disabled = LOOK.look_logo === "off";
    $("lk-volume").value = LOOK.look_volume; $("lk-volume-v").textContent = LOOK.look_volume + "%";
    paintSounds();
  }
  function msg(t, err) { const m = $("lk-msg"); if (m) m.innerHTML = t ? `<span class="${err ? "error" : "hint"}">${t}</span>` : ""; }

  async function save(vals) {
    const before = { ...LOOK };
    applyLook({ ...LOOK, ...vals }); paint();                  // show it at once
    if (typeof API().save_appearance !== "function") { msg("Needs the latest backend files (api.py, automation_store.py) — replace them and restart Chimera.", true); return; }
    try {
      const res = await API().save_appearance(vals);
      if (!res.ok) { applyLook(before); paint(); msg(res.message, true); return; }
      msg("Saved.");
    } catch (e) { msg("Couldn't save: " + e, true); }
  }

  window.loadAppearanceSettings = async () => {
    if (typeof API().get_appearance !== "function") { msg("Needs the latest backend files (api.py, automation_store.py) — replace them and restart Chimera.", true); paint(); return; }
    try { const l = await API().get_appearance(); if (l.sounds) sounds = l.sounds; applyLook(l); } catch (e) { /* keep what's shown */ }
    msg(""); paint();
  };

  if ($("st-panel-look")) {
    $("lk-splash").addEventListener("click", (e) => { const b = e.target.closest(".lk-card"); if (b) save({ look_splash: b.dataset.v }); });
    [["lk-logo", "look_logo"], ["lk-logo-freq", "look_logo_freq"], ["lk-ecg", "look_ecg"]].forEach(([id, k]) =>
      $(id).addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) save({ [k]: b.dataset.v }); }));
    $("lk-sounds").addEventListener("click", (e) => {
      const p = e.target.closest("[data-play]"); if (p) { e.stopPropagation(); playPreview(p.dataset.play); return; }
      const row = e.target.closest("[data-snd]"); if (row) save({ look_sound: row.dataset.snd });
    });
    $("lk-volume").addEventListener("input", (e) => { $("lk-volume-v").textContent = e.target.value + "%"; if (pv.audio) pv.audio.volume = Math.max(0.05, e.target.value / 100); });
    $("lk-volume").addEventListener("change", (e) => save({ look_volume: e.target.value }));
    $("lk-play").addEventListener("click", () => { stopPreview(); if (window.chimeraSplashPreview) window.chimeraSplashPreview(LOOK); });
    $("lk-logo-try").addEventListener("click", () => { clearTimeout(LG.timer); glitch(logoSchedule); });
  }

  window.addEventListener("pywebviewready", async () => {
    if (typeof API().get_appearance !== "function") return;
    try { const l = await API().get_appearance(); if (l.sounds) sounds = l.sounds; applyLook(l); } catch (e) { /* defaults stay */ }
  });
})();
