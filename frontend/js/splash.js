// splash.js — the splash screen while Chimera opens. The bar follows the real startup steps (startup.py):
// starting Chimera, checking the SFA login, reading itineraries. "Continue to dashboard" appears after a few
// seconds; whatever is left keeps running in the background (Running now shows it).
// The look comes from Settings › Customize Appearance: a red opening that turns into the dark splash with a
// "dimensional displacement" or a slice glitch (or Classic: dark splash only), plus a soundtrack.
(function () {
  const el = document.getElementById("splash"); if (!el) return;
  const $ = (id) => document.getElementById(id);
  const red = $("sp-red");
  const ICON = { done: "&#10003;", failed: "&#10005;", skipped: "&ndash;", running: "", pending: "" };
  const reduce = () => window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
  const INTRO_AT = 1700;                       // the soundtracks are timed to the switch at 1.7 s
  let poll = null, closed = false, preview = false, introDone = true, closeWanted = false;
  let fxStop = null, audio = null;
  const clock = () => { $("sp-clock").textContent = new Date().toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }); };
  clock(); setInterval(clock, 15000);

  function close() {
    if (closed) return; closed = true; closeWanted = false;
    if (poll) { clearInterval(poll); poll = null; }
    if (fxStop) fxStop();
    if (audio && (preview || audio.duration > 9)) stopSound(true);   // a long soundtrack you added fades out with the splash
    el.classList.add("sp-out");
    setTimeout(() => {
      el.style.display = "none";
      document.documentElement.classList.remove("sp-lock");
      if (preview) { preview = false; $("sp-skip").innerHTML = el.dataset.skipText || "Continue to dashboard &rarr;"; }
      else if (window.loadOverviewKpis) window.loadOverviewKpis();
    }, 650);
  }
  // an automatic close waits until the opening animation has finished
  const closeWhenReady = () => { if (introDone) close(); else closeWanted = true; };

  $("sp-skip").onclick = () => {
    const pending = el.dataset.active === "1" && !preview;
    close();
    if (pending && window.showToast) window.showToast("Startup sync continues in the background — see Running now.");
  };
  setTimeout(() => $("sp-skip").classList.add("on"), 4000);

  function render(s) {
    const pct = Math.max(2, Math.min(100, s.percent || 0));
    $("sp-fill").style.width = pct + "%";
    $("sp-pct").textContent = pct + "%";
    $("sp-msg").textContent = s.message || "";
    $("r-fill").style.width = Math.max(3, pct) + "%";
    $("r-msg").textContent = s.message || "";
    $("sp-steps").innerHTML = (s.steps || []).map(st => `<div class="sp-step ${st.status}"><span class="sp-ic">${ICON[st.status] || ""}</span>
      <span class="sp-lbl">${st.label}</span><span class="sp-m">${(st.message || "").replace(/</g, "&lt;")}</span></div>`).join("");
    const sfa = s.sfa || "checking";
    $("sp-sfa").textContent = { connected: "SFA CONNECTED", offline: "SFA OFFLINE", checking: "SFA: CHECKING…" }[sfa] || "SFA";
    $("sp-sfa-dot").className = "sp-" + sfa;
    $("sp-dot").className = sfa === "offline" ? "sp-warn" : "";
    el.dataset.active = s.active ? "1" : "0";
    if (s.done) {
      $("sp-skip").classList.add("on");
      $("sp-skip").innerHTML = "Open dashboard &rarr;";
      const failed = (s.steps || []).some(x => x.status === "failed");
      $("sp-note").textContent = failed ? "Something couldn't be done — Chimera opens anyway; the details are above." : "";
      if (!failed) setTimeout(closeWhenReady, 900);
    }
  }

  // ------------------------------------------------------------------ soundtracks and videos you added
  // Played straight from Chimera's data folder when the window allows it; otherwise read through Chimera in
  // pieces and played from memory.
  const media = {};
  function canPlay(url, kind) {
    return new Promise((res) => {
      const m = document.createElement(kind === "video" ? "video" : "audio");
      let settled = false; const end = (ok) => { if (!settled) { settled = true; m.removeAttribute("src"); res(ok); } };
      m.preload = "metadata"; m.onloadedmetadata = () => end(true); m.onerror = () => end(false);
      setTimeout(() => end(false), 1500); m.src = url;
    });
  }
  async function mediaUrl(id) {
    if (media[id]) return media[id];
    const api = window.pywebview && window.pywebview.api;
    if (!api || typeof api.get_look_media !== "function") return null;
    const info = await api.get_look_media(id);
    if (!info || !info.ok) return null;
    const kind = id.startsWith("v_") ? "video" : "sound";
    if (await canPlay(info.url, kind)) return (media[id] = info.url);
    const parts = [];
    for (let i = 0; i < info.chunks; i++) {
      const c = await api.get_look_media_chunk(id, i); if (!c || !c.ok) return null;
      const bin = atob(c.data), u8 = new Uint8Array(bin.length);
      for (let k = 0; k < bin.length; k++) u8[k] = bin.charCodeAt(k);
      parts.push(u8);
    }
    return (media[id] = URL.createObjectURL(new Blob(parts, { type: info.mime })));
  }
  window.chimeraMediaUrl = mediaUrl;
  async function prepare(look) {
    if (!look) return;
    const ids = [look.look_sound, look.look_splash].filter(x => x && (x.startsWith("s_") || x.startsWith("v_")));
    await Promise.race([Promise.all(ids.map(id => mediaUrl(id).catch(() => null))), new Promise(res => setTimeout(res, 6000))]);
  }

  // ------------------------------------------------------------------ soundtrack
  function stopSound(fade) {
    const a = audio; audio = null; if (!a) return;
    if (!fade) { try { a.pause(); } catch (e) { /* */ } return; }
    const v0 = a.volume, t0 = performance.now();
    const step = (now) => { const q = (now - t0) / 1200; if (q >= 1) { a.pause(); return; } a.volume = v0 * (1 - q); requestAnimationFrame(step); };
    requestAnimationFrame(step);
  }
  function playSound(look) {
    stopSound(false);
    if (!look || !look.look_sound || look.look_sound === "none") return;
    const vol = Math.max(0, Math.min(100, parseInt(look.look_volume, 10) || 0)) / 100;
    if (!vol) return;
    const key = look.look_sound;
    const src = key.startsWith("s_") ? media[key] : `assets/sounds/chimera-splash-${key}.mp3`;
    if (!src) return;
    audio = new Audio(src);
    audio.volume = vol;
    audio.play().catch(() => { /* the system blocked sound — the splash still runs */ });
  }

  // ------------------------------------------------------------------ the opening
  const fxCtx = () => ({ el, red, cloneRed, alive: () => !!fxStop, done: finishIntro });
  function startIntro(look) {
    let style = (look && look.look_splash) || "displacement";
    el.classList.remove("sp-wait");
    playSound(look);
    if (style.startsWith("v_")) {
      const item = ((look && look.custom) || []).find(x => x.id === style);
      if (item && media[style]) return startVideo(item, media[style], look);
      style = "displacement";                     // the video can't be played: use the default opening
    }
    const FX = window.ChimeraFX || {};
    const fx = FX[style] && FX[style].run ? FX[style] : null;
    if (!fx && !["displacement", "glitch", "classic"].includes(style)) style = "displacement";
    if (style === "classic" || !red) { if (red) red.style.display = "none"; introDone = true; return; }
    introDone = false;
    const showRed = fx ? fx.red : true;
    red.style.cssText = ""; red.className = "sp-red";
    if (!showRed) red.style.display = "none";
    void red.offsetWidth;
    if (reduce()) {                               // no flashing for people who turned animations off
      if (!showRed) { introDone = true; return; }
      const t = setTimeout(() => { red.style.transition = "opacity .35s"; red.style.opacity = 0; setTimeout(finishIntro, 400); }, INTRO_AT);
      fxStop = () => { clearTimeout(t); finishIntro(); };
      return;
    }
    const t = setTimeout(() => {
      if (!fxStop) return;
      if (fx) fx.run(fxCtx());
      else (style === "glitch" ? sliceGlitch : displacement)(finishIntro);
    }, fx ? fx.at : INTRO_AT);
    fxStop = () => { clearTimeout(t); finishIntro(); };
  }

  // a video opening you added: plays full screen (its own sound only when no soundtrack is chosen),
  // then hands over to the dark splash with the transition you picked for it
  function startVideo(item, url, look) {
    introDone = false; red.style.display = "none";
    const lay = document.createElement("div"); lay.className = "sp-fx fx-video";
    const v = document.createElement("video"); v.src = url; v.playsInline = true; v.preload = "auto";
    v.muted = !!(look.look_sound && look.look_sound !== "none");
    v.volume = Math.max(0, Math.min(100, parseInt(look.look_volume, 10) || 0)) / 100;
    lay.appendChild(v); el.appendChild(lay);
    let handed = false;
    const go = () => { if (handed || !fxStop) return; handed = true; clearTimeout(cap); handover(item.transition || "displacement", v, lay); };
    const cap = setTimeout(go, 15000);            // a long video hands over after 15 s
    v.onended = go; v.onerror = go;
    v.play().catch(() => { v.muted = true; v.play().catch(go); });
    fxStop = () => { clearTimeout(cap); finishIntro(); };
  }
  function handover(tr, v, lay) {
    if (tr === "cut" || reduce()) return finishIntro();
    let from = null;
    if (tr !== "fade") {
      try {                                       // freeze the last frame so the glitch can tear it apart
        const c = document.createElement("canvas");
        c.width = v.videoWidth || el.clientWidth; c.height = v.videoHeight || el.clientHeight;
        c.getContext("2d").drawImage(v, 0, 0, c.width, c.height);
        from = document.createElement("div"); from.className = "sp-fx fx-frame";
        from.innerHTML = `<img src="${c.toDataURL("image/jpeg", .88)}" alt="">`; el.appendChild(from);
      } catch (e) { from = null; }
    }
    if (!from) { lay.style.transition = "opacity .6s ease"; lay.style.opacity = 0; setTimeout(finishIntro, 650); return; }
    lay.remove();
    (tr === "glitch" ? sliceGlitch : displacement)(finishIntro, from);
  }
  function finishIntro() {
    if (red) { red.style.display = "none"; red.style.opacity = ""; red.style.transform = ""; red.style.visibility = ""; red.style.filter = ""; red.className = "sp-red"; }
    el.querySelectorAll(".sp-fx").forEach(n => n.remove());
    el.style.transform = ""; el.classList.remove("gl-flash");
    fxStop = null;
    if (!introDone) { introDone = true; if (closeWanted) close(); }
  }

  const cloneOf = (node) => { const c = node.cloneNode(true); c.removeAttribute("id"); c.querySelectorAll("[id]").forEach(e => e.removeAttribute("id"));
    c.style.cssText = ""; c.classList.remove("sp-fx"); c.style.position = "absolute"; c.style.inset = "0"; return c; };
  const cloneRed = () => { const c = cloneOf(red); c.style.display = ""; return c; };

  // fast horizontal slices of the red screen tear away into the dark one
  function sliceGlitch(done, from) {
    const src = from || red, cloneSrc = () => cloneOf(src);
    const slices = [], N = 12, DUR = 800, until = performance.now() + DUR;
    for (let k = 0; k < N; k++) {
      const s = document.createElement("div"); s.className = "sp-fx gl-slice" + (k % 3 === 0 ? " c1" : "");
      s.appendChild(cloneSrc()); el.appendChild(s); slices.push(s);
    }
    const noise = document.createElement("div"); noise.className = "sp-fx gl-noise"; el.appendChild(noise);
    const scan = document.createElement("div"); scan.className = "sp-fx gl-scan"; el.appendChild(scan);
    src.style.visibility = "hidden";
    let frame = 0;
    const tick = () => {
      if (!fxStop) return done();
      const left = until - performance.now(), p = 1 - Math.max(0, left) / DUR;
      slices.forEach(s => {
        const top = Math.random() * 100, h = 3 + Math.random() * 16;
        s.style.clipPath = `inset(${top}% 0 ${Math.max(0, 100 - top - h)}% 0)`;
        s.style.transform = `translateX(${(Math.random() - .5) * 90 * (1 - p * .6)}px)`;
        s.style.opacity = Math.random() < p ? 0 : 1;
      });
      scan.style.top = (Math.random() * 100) + "%";
      el.classList.toggle("gl-flash", frame % 5 === 2 && p < .7);
      src.style.visibility = p < .45 && frame % 4 < 2 ? "visible" : "hidden";
      frame++;
      if (left > 0 && fxStop) requestAnimationFrame(tick); else done();
    };
    requestAnimationFrame(tick);
  }

  // "dimensional displacement": the two screens bleed into each other — offset magenta/cyan copies jitter at a
  // choppy 12 fps, boxes of the other screen tear open with misprinted edges, comic-print dots flicker, then dark.
  function displacement(done, from) {
    const src = from || red, cloneSrc = () => cloneOf(src);
    const darkParts = [...el.children].filter(n => n !== red && !n.classList.contains("sp-fx"));
    const snapDark = () => { const w = document.createElement("div"); w.className = "dd-dark";
      darkParts.forEach(n => w.appendChild(n.cloneNode(true))); w.querySelectorAll("[id]").forEach(e => e.removeAttribute("id")); return w; };
    const ghost = (cls) => { const g = document.createElement("div"); g.className = "sp-fx dd-layer dd-ghost " + cls; g.appendChild(cloneSrc()); el.appendChild(g); return g; };
    const gm = ghost("m"), gc = ghost("c");
    const dots = Object.assign(document.createElement("div"), { className: "sp-fx dd-layer dd-dots" }); el.appendChild(dots);
    const lines = Object.assign(document.createElement("div"), { className: "sp-fx dd-layer dd-lines" }); el.appendChild(lines);
    const boxes = [];
    const DUR = 1400, t0 = performance.now(); let lastStep = 0, step = 0;
    const W = () => el.clientWidth, H = () => el.clientHeight, r = (a, b) => a + Math.random() * (b - a);
    const frame = (now) => {
      if (!fxStop) return done();
      const p = Math.min(1, (now - t0) / DUR);
      if (now - lastStep >= 83) {                                   // 12 fps: choppy on purpose
        lastStep = now; step++;
        const amp = 26 * Math.sin(Math.PI * Math.min(1, p * 1.15));
        gm.style.transform = `translate(${r(-amp, amp)}px, ${r(-amp / 3, amp / 3)}px) scale(${1 + r(0, .03) * (1 - p)})`;
        gc.style.transform = `translate(${r(-amp, amp)}px, ${r(-amp / 3, amp / 3)}px)`;
        gm.style.opacity = gc.style.opacity = p < .85 ? r(.18, .38) : 0;
        const band = (g) => { const a = r(0, 70); g.style.clipPath = Math.random() < .5 ? `inset(${a}% 0 ${Math.max(0, 100 - a - r(15, 45))}% 0)` : "none"; };
        band(gm); band(gc);
        dots.style.opacity = p < .9 ? (step % 3 === 0 ? r(.6, .85) : r(.15, .35)) : 0;
        lines.style.opacity = p < .9 ? r(.3, .8) : 0;
        el.style.transform = p < .9 ? `translate(${r(-6, 6)}px, ${r(-4, 4)}px)` : "";
        boxes.forEach(bx => bx.remove()); boxes.length = 0;
        const n = p < .9 ? Math.round(r(2, 6)) : 0;
        for (let k = 0; k < n; k++) {
          const bw = r(.14, .42) * W(), bh = r(.06, .2) * H(), bl = r(.12 * W(), .88 * W() - bw), bt = r(.08 * H(), .82 * H() - bh);
          const box = document.createElement("div"); box.className = "sp-fx dd-box";
          box.style.cssText = `left:${bl}px;top:${bt}px;width:${bw}px;height:${bh}px`;
          const inner = Math.random() < p ? snapDark() : cloneSrc();
          Object.assign(inner.style, { position: "absolute", left: `${-bl + r(-30, 30)}px`, top: `${-bt}px`, width: W() + "px", height: H() + "px" });
          box.appendChild(inner); el.appendChild(box); boxes.push(box);
        }
        src.style.opacity = p < .5 ? 1 : p < .8 ? (step % 2 ? 1 : 0) : 0;   // the first world flickers out
        src.style.transform = `translate(${r(-amp / 2, amp / 2)}px, 0)`;
      }
      if (p < 1) return requestAnimationFrame(frame);
      done();
      if (el.animate) el.animate([{ filter: "drop-shadow(-6px 0 0 rgba(0,230,255,.7)) drop-shadow(6px 0 0 rgba(255,0,150,.7))" }, { filter: "none" }], { duration: 260, easing: "ease-out" });
    };
    requestAnimationFrame(frame);
  }

  // ------------------------------------------------------------------ start
  async function readLook(api) {
    if (typeof api.get_appearance !== "function") return null;
    try { return await Promise.race([api.get_appearance(), new Promise(res => setTimeout(() => res(null), 1500))]); }
    catch (e) { return null; }
  }
  window.addEventListener("pywebviewready", async () => {
    const api = window.pywebview.api;
    const look = await readLook(api);
    window.chimeraLook = look || window.chimeraLook || null;
    await prepare(look);
    if (!closed) startIntro(look);
    if (typeof api.start_startup !== "function") { closeWhenReady(); return; }      // older backend
    try { render(await api.start_startup()); } catch (e) { closeWhenReady(); return; }
    poll = setInterval(async () => {
      try { render(await api.get_startup()); } catch (e) { /* keep showing */ }
    }, 600);
  });
  // no desktop bridge at all (opened in a plain browser): don't trap the page
  setTimeout(() => { if (!window.pywebview && !closed) { el.classList.remove("sp-wait"); close(); } }, 6000);
  window.closeSplash = close;

  // Settings › Customize Appearance › Preview splash: plays the opening again with the chosen look and sound
  window.chimeraSplashPreview = async (look) => {
    if (!closed) return;
    await prepare(look);
    closed = false; preview = true; closeWanted = false;
    document.documentElement.classList.add("sp-lock");
    el.style.display = ""; el.classList.remove("sp-out"); void el.offsetWidth;
    el.dataset.skipText = $("sp-skip").innerHTML;
    $("sp-skip").innerHTML = "Close preview"; $("sp-skip").classList.add("on");
    startIntro(look);
    // close the preview a couple of seconds after the opening has finished
    const t0 = Date.now();
    const check = setInterval(() => {
      if (!preview || closed) { clearInterval(check); return; }
      if (introDone && Date.now() - t0 > 2500) { clearInterval(check); setTimeout(() => { if (preview && !closed) close(); }, 1800); }
    }, 200);
  };
  window.chimeraStopSound = () => stopSound(false);
})();
