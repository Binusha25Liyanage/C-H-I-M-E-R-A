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

  // ------------------------------------------------------------------ soundtrack
  function playSound(look) {
    if (audio) { try { audio.pause(); } catch (e) { /* */ } audio = null; }
    if (!look || !look.look_sound || look.look_sound === "none") return;
    const vol = Math.max(0, Math.min(100, parseInt(look.look_volume, 10) || 0)) / 100;
    if (!vol) return;
    audio = new Audio(`assets/sounds/chimera-splash-${look.look_sound}.mp3`);
    audio.volume = vol;
    audio.play().catch(() => { /* the system blocked sound — the splash still runs */ });
  }

  // ------------------------------------------------------------------ the opening
  function startIntro(look) {
    const style = (look && look.look_splash) || "displacement";
    el.classList.remove("sp-wait");
    playSound(look);
    if (style === "classic" || !red) { if (red) red.style.display = "none"; introDone = true; return; }
    introDone = false;
    red.style.cssText = "";                       // visible, fresh (also for a replay)
    red.classList.remove("r-static"); void red.offsetWidth;
    const t = setTimeout(() => {
      if (closed) return;
      if (reduce()) {                             // no flashing for people who turned animations off
        red.style.transition = "opacity .35s"; red.style.opacity = 0;
        setTimeout(finishIntro, 400); return;
      }
      (style === "glitch" ? sliceGlitch : displacement)(finishIntro);
    }, INTRO_AT);
    fxStop = () => { clearTimeout(t); finishIntro(); };
  }
  function finishIntro() {
    if (red) { red.style.display = "none"; red.style.opacity = ""; red.style.transform = ""; red.style.visibility = ""; }
    el.querySelectorAll(".sp-fx").forEach(n => n.remove());
    el.style.transform = ""; el.classList.remove("gl-flash");
    fxStop = null;
    if (!introDone) { introDone = true; if (closeWanted) close(); }
  }

  const cloneRed = () => { const c = red.cloneNode(true); c.removeAttribute("id"); c.querySelectorAll("[id]").forEach(e => e.removeAttribute("id")); c.style.cssText = ""; return c; };

  // fast horizontal slices of the red screen tear away into the dark one
  function sliceGlitch(done) {
    const slices = [], N = 12, DUR = 800, until = performance.now() + DUR;
    for (let k = 0; k < N; k++) {
      const s = document.createElement("div"); s.className = "sp-fx gl-slice" + (k % 3 === 0 ? " c1" : "");
      s.appendChild(cloneRed()); el.appendChild(s); slices.push(s);
    }
    const noise = document.createElement("div"); noise.className = "sp-fx gl-noise"; el.appendChild(noise);
    const scan = document.createElement("div"); scan.className = "sp-fx gl-scan"; el.appendChild(scan);
    red.style.visibility = "hidden";
    let frame = 0;
    const tick = () => {
      if (closed && !preview) return done();
      const left = until - performance.now(), p = 1 - Math.max(0, left) / DUR;
      slices.forEach(s => {
        const top = Math.random() * 100, h = 3 + Math.random() * 16;
        s.style.clipPath = `inset(${top}% 0 ${Math.max(0, 100 - top - h)}% 0)`;
        s.style.transform = `translateX(${(Math.random() - .5) * 90 * (1 - p * .6)}px)`;
        s.style.opacity = Math.random() < p ? 0 : 1;
      });
      scan.style.top = (Math.random() * 100) + "%";
      el.classList.toggle("gl-flash", frame % 5 === 2 && p < .7);
      red.style.visibility = p < .45 && frame % 4 < 2 ? "visible" : "hidden";
      frame++;
      if (left > 0 && fxStop) requestAnimationFrame(tick); else done();
    };
    requestAnimationFrame(tick);
  }

  // "dimensional displacement": the two screens bleed into each other — offset magenta/cyan copies jitter at a
  // choppy 12 fps, boxes of the other screen tear open with misprinted edges, comic-print dots flicker, then dark.
  function displacement(done) {
    const darkParts = [...el.children].filter(n => n !== red && !n.classList.contains("sp-fx"));
    const snapDark = () => { const w = document.createElement("div"); w.className = "dd-dark";
      darkParts.forEach(n => w.appendChild(n.cloneNode(true))); w.querySelectorAll("[id]").forEach(e => e.removeAttribute("id")); return w; };
    const ghost = (cls) => { const g = document.createElement("div"); g.className = "sp-fx dd-layer dd-ghost " + cls; g.appendChild(cloneRed()); el.appendChild(g); return g; };
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
          const inner = Math.random() < p ? snapDark() : cloneRed();
          Object.assign(inner.style, { position: "absolute", left: `${-bl + r(-30, 30)}px`, top: `${-bt}px`, width: W() + "px", height: H() + "px" });
          box.appendChild(inner); el.appendChild(box); boxes.push(box);
        }
        red.style.opacity = p < .5 ? 1 : p < .8 ? (step % 2 ? 1 : 0) : 0;   // the red world flickers out
        red.style.transform = `translate(${r(-amp / 2, amp / 2)}px, 0)`;
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
  window.chimeraSplashPreview = (look) => {
    if (!closed) return;
    closed = false; preview = true; closeWanted = false;
    document.documentElement.classList.add("sp-lock");
    el.style.display = ""; el.classList.remove("sp-out"); void el.offsetWidth;
    el.dataset.skipText = $("sp-skip").innerHTML;
    $("sp-skip").innerHTML = "Close preview"; $("sp-skip").classList.add("on");
    startIntro(look);
    const wait = (look && look.look_splash === "classic") ? 2500 : INTRO_AT + 1600 + 2200;
    setTimeout(() => { if (preview && !closed) close(); }, wait);
  };
  window.chimeraPlaySound = (key, volume) => playSound({ look_sound: key, look_volume: volume });
  window.chimeraStopSound = () => { if (audio) { try { audio.pause(); } catch (e) { /* */ } audio = null; } };
})();
