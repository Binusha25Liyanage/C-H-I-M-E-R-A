// splash_fx.js — the extra splash openings (Settings › Customize Appearance › Splash screen). splash.js runs them;
// each one ends by revealing the dark splash with the real loading bar. They all "switch" at 1.7 s, the moment
// the soundtracks are timed to.
//   neon       a dark wall: the logo and C H I M E R A light up tube by tube, then the room lights up
//   heartbeat  a monitor line runs across black; one big beat, a red pulse, and the splash
//   portal     the red opening tears open down the middle and splits apart
//   shatter    the red opening cracks like glass and falls away in pieces
//   assemble   the logo draws itself in a sweep, fills in, and the splash fades up
//   terminal   a boot console types the startup and the CHIMERA banner, then crash-glitches away
//   vhs        the red opening on an old tape: rewind, ▶ PLAY, and a CRT snap into the splash
(function () {
  const SWITCH = 1700;
  const r = (a, b) => a + Math.random() * (b - a);
  const ease = (p) => 1 - Math.pow(1 - Math.max(0, Math.min(1, p)), 3);
  const clamp01 = (p) => Math.max(0, Math.min(1, p));
  const mk = (ctx, cls, html) => { const d = document.createElement("div"); d.className = "sp-fx " + cls; if (html) d.innerHTML = html; ctx.el.appendChild(d); return d; };
  // runs frame(t) every animation frame until dur ms have passed (or the splash closed), then ctx.done()
  function loop(ctx, dur, frame) {
    const t0 = performance.now();
    const f = (now) => {
      if (!ctx.alive()) return ctx.done();
      const t = now - t0; frame(Math.min(t, dur), now);
      if (t < dur) requestAnimationFrame(f); else ctx.done();
    };
    requestAnimationFrame(f);
  }
  const bump = (s, c, w) => Math.exp(-((s - c) * (s - c)) / (2 * w * w));
  const beatAt = (s) => 0.14 * bump(s, 7, 2.6) - 0.14 * bump(s, 16, 1.1) + 1.0 * bump(s, 19, 1.25) - 0.38 * bump(s, 22.5, 1.3) + 0.26 * bump(s, 37, 4.5);

  // ------------------------------------------------------------------ neon power-up
  // Timeline (matches the soundtracks: intro → glitch at 1.7 s → landing at 3.1 s):
  //   0.0–1.7  the logo and letters flicker on, tube by tube
  //   1.7–3.1  the sign glitches: slices of it jump sideways with a cyan/magenta misprint, letters scramble,
  //            tubes short out for a moment, comic-print dots flash — getting wilder, then…
  //   3.1      a power surge: one bright flash and the sign burns at full glow, humming
  //   3.9–4.7  the room lights up into the splash
  function neon(ctx) {
    const letters = "CHIMERA".split(""), POOL = "CHIMERA█▓▒/\\<>#";
    const GL0 = SWITCH, GL1 = 3100, OUT = 3900, END = 4700;
    const wall = mk(ctx, "fx-wall", `<div class="fx-nc"><div class="fx-nlogo"><i></i></div>
      <div class="fx-ntitle">${letters.map(c => `<b>${c}</b>`).join("")}</div><div class="fx-nsub">SFA MANAGEMENT SYSTEM</div></div>
      <div class="fx-ndots"></div><div class="fx-flash"></div>`);
    const logo = wall.querySelector(".fx-nlogo"), bs = [...wall.querySelectorAll(".fx-ntitle b")], sub = wall.querySelector(".fx-nsub");
    const nc = wall.querySelector(".fx-nc"), dots = wall.querySelector(".fx-ndots"), flash = wall.querySelector(".fx-flash");
    let step = -1, slices = null;
    const allOn = (on) => { logo.classList.toggle("on", on); bs.forEach(b => b.classList.toggle("on", on)); sub.classList.toggle("on", on); };
    loop(ctx, END, (t) => {
      const s = Math.floor(t / (t < GL0 ? 70 : 83));
      if (s !== step) {
        step = s;
        if (t < GL0) {                                    // tubes flicker on in 70 ms steps
          const flick = (start, len) => t < start ? false : t > start + len ? true : Math.random() < .25 + (t - start) / len * .6;
          logo.classList.toggle("on", flick(250, 650));
          bs.forEach((b, i) => b.classList.toggle("on", flick(650 + i * 120, 230)));
          sub.classList.toggle("on", flick(1450, 160));
          const lit = (logo.classList.contains("on") ? 3 : 0) + bs.filter(b => b.classList.contains("on")).length;
          wall.style.setProperty("--glow", (lit / (letters.length + 3)).toFixed(2));
        } else if (t < GL1) {                             // the glitch, at a choppy 12 fps, growing wilder
          const k = (t - GL0) / (GL1 - GL0), amp = 8 + 46 * k;
          if (!slices) {
            slices = [];
            for (let i = 0; i < 7; i++) {
              const c = nc.cloneNode(true); c.classList.add("fx-nslice"); if (i % 3 === 0) c.classList.add("m"); if (i % 3 === 1) c.classList.add("c");
              wall.insertBefore(c, dots); slices.push(c);
            }
          }
          slices.forEach(c => {
            const top = r(0, 90), h = r(4, 10 + 18 * k);
            c.style.clipPath = `inset(${top}% 0 ${Math.max(0, 100 - top - h)}% 0)`;
            c.style.transform = `translate(${r(-amp, amp)}px, ${r(-3, 3)}px)`;
            c.style.opacity = Math.random() < .35 + .5 * k ? 1 : 0;
          });
          nc.style.transform = `translate(${r(-4 - 8 * k, 4 + 8 * k)}px, 0) skewX(${r(-2, 2) * k}deg)`;
          const out = Math.random() < .12 + .18 * k;        // tubes short out for a frame
          allOn(!out);
          wall.style.setProperty("--glow", out ? ".1" : "1");
          bs.forEach((b, i) => { b.textContent = Math.random() < .12 + .3 * k ? POOL[Math.floor(r(0, POOL.length))] : letters[i]; });
          dots.style.opacity = Math.random() < .25 + .4 * k ? r(.35, .8) : 0;
          wall.classList.toggle("fx-nsplit", Math.random() < .5);
        } else {                                          // surge: back to clean, burning bright
          if (slices) { slices.forEach(c => c.remove()); slices = null; bs.forEach((b, i) => { b.textContent = letters[i]; });
            nc.style.transform = ""; dots.style.opacity = 0; wall.classList.remove("fx-nsplit"); allOn(true); wall.classList.add("fx-nsurge"); }
          wall.style.setProperty("--glow", t < OUT ? (Math.random() < .08 ? ".75" : "1") : "1");
        }
      }
      if (t >= GL1) flash.style.opacity = Math.max(0, .8 - (t - GL1) / 220);
      if (t >= OUT) {                                     // the room lights up
        const q = (t - OUT) / (END - OUT);
        wall.style.opacity = 1 - ease(q);
        nc.style.transform = `scale(${1 + .06 * ease(q)})`;
      }
    });
  }

  // ------------------------------------------------------------------ heartbeat boot
  function heartbeat(ctx) {
    const lay = mk(ctx, "fx-black", `<canvas></canvas><div class="fx-ring"></div><div class="fx-flash"></div>`);
    const cv = lay.querySelector("canvas"), ring = lay.querySelector(".fx-ring"), flash = lay.querySelector(".fx-flash");
    const dpr = window.devicePixelRatio || 1, W = ctx.el.clientWidth, H = ctx.el.clientHeight, mid = H * .5;
    cv.width = W * dpr; cv.height = H * dpr; const g = cv.getContext("2d"); g.scale(dpr, dpr);
    const K = Math.max(3, W / 320), xb = W * .5 - 19 * K, small = Math.min(40, H * .05), big = H * .3;
    const ys = new Float32Array(W + 1);
    for (let x = 0; x <= W; x++) {
      let v = 0;
      if (x >= xb && x < xb + 50 * K) v = beatAt((x - xb) / K) * big;
      else if (x < xb - 40) { const s = (x + 30) % 170; if (s < 50) v = beatAt(s) * small; }
      ys[x] = mid - v;
    }
    const penEnd = W * .62;
    loop(ctx, 2400, (t) => {
      const pen = Math.floor(Math.min(1, t / SWITCH) * penEnd);
      g.clearRect(0, 0, W, H);
      g.lineJoin = "round"; g.lineCap = "round";
      for (let x = 1; x <= pen; x += 1) {                // older line is dimmer
        g.strokeStyle = `rgba(255, 40, 58, ${(.25 + .75 * x / Math.max(1, pen)).toFixed(3)})`; g.lineWidth = 2.4;
        g.beginPath(); g.moveTo(x - 1, ys[x - 1]); g.lineTo(x, ys[x]); g.stroke();
      }
      g.save(); g.shadowColor = "#ff2d3f"; g.shadowBlur = 16; g.strokeStyle = "#ff6b78"; g.lineWidth = 3;
      g.beginPath(); for (let x = Math.max(0, pen - 26); x <= pen; x++) (x === Math.max(0, pen - 26) ? g.moveTo(x, ys[x]) : g.lineTo(x, ys[x])); g.stroke();
      g.fillStyle = "#fff"; g.beginPath(); g.arc(pen, ys[pen], 3.2, 0, 7); g.fill(); g.restore();
      if (t >= SWITCH) {                                  // a red pulse from the beat, then the splash
        const q = (t - SWITCH) / 700;
        ring.style.transform = `translate(-50%, -50%) scale(${.15 + 3.2 * ease(q)})`; ring.style.opacity = 1 - clamp01(q);
        flash.style.opacity = Math.max(0, .55 - (t - SWITCH) / 160);
        lay.style.opacity = 1 - ease((t - SWITCH) / 650);
      }
    });
  }

  // ------------------------------------------------------------------ portal rift (after the red opening)
  function portal(ctx) {
    const N = 16, pts = [];
    for (let i = 0; i < N; i++) pts.push([50 + (i === 0 || i === N - 1 ? 0 : r(-3, 3)), i / (N - 1) * 100]);
    const path = pts.map(([x, y]) => `${x.toFixed(2)}% ${y.toFixed(2)}%`).join(", ");
    const half = (side) => {
      const w = mk(ctx, "fx-half " + side);
      const inner = document.createElement("div"); inner.className = "fx-half-in";
      inner.style.clipPath = side === "l" ? `polygon(0% 0%, ${path}, 0% 100%)` : `polygon(100% 0%, ${path}, 100% 100%)`;
      inner.appendChild(ctx.cloneRed()); w.appendChild(inner); return w;
    };
    const glow = mk(ctx, "fx-rift"), L = half("l"), R = half("r");
    const crack = mk(ctx, "fx-crack", `<svg viewBox="0 0 100 100" preserveAspectRatio="none"><polyline points="${pts.map(([x, y]) => `${x},${y}`).join(" ")}"/></svg>`);
    ctx.red.style.visibility = "hidden";
    const W = ctx.el.clientWidth;
    const inout = (p) => { p = clamp01(p); return p < .5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2; };
    loop(ctx, 1500, (t) => {
      const shake = t < 500 ? (1 - t / 500) * 7 : 0;
      ctx.el.style.transform = shake ? `translate(${r(-shake, shake)}px, ${r(-shake, shake)}px)` : "";
      crack.style.opacity = t < 220 ? (Math.random() < .8 ? 1 : .3) : Math.max(0, 1 - (t - 220) / 200);
      const q = inout((t - 220) / 1250), off = q * W * .62;
      L.style.transform = `translateX(${-off}px) rotate(${-4 * q}deg)`;
      R.style.transform = `translateX(${off}px) rotate(${4 * q}deg)`;
      glow.style.width = `${Math.max(.4, off * 2 / W * 100 * .5 + .4)}%`; glow.style.opacity = .85 * (1 - clamp01((t - 1000) / 450));
    });
  }

  // ------------------------------------------------------------------ shatter (after the red opening)
  function shatter(ctx) {
    const W = ctx.el.clientWidth, H = ctx.el.clientHeight, ix = W * .56, iy = H * .42;
    let lines = "";
    for (let k = 0; k < 15; k++) {                         // cracks running out from the hit
      const a = k / 15 * Math.PI * 2 + r(-.15, .15); let x = ix, y = iy, pts = `${x},${y}`;
      const len = r(.35, .8) * Math.max(W, H);
      for (let s = 1; s <= 4; s++) { const d = len * s / 4, aa = a + r(-.12, .12); x = ix + Math.cos(aa) * d; y = iy + Math.sin(aa) * d; pts += ` ${x.toFixed(1)},${y.toFixed(1)}`; }
      lines += `<polyline points="${pts}"/>`;
    }
    [70, 170].forEach(rad => { let pts = ""; for (let k = 0; k <= 15; k++) { const a = k / 15 * Math.PI * 2, rr = rad * r(.8, 1.2); pts += `${(ix + Math.cos(a) * rr).toFixed(1)},${(iy + Math.sin(a) * rr).toFixed(1)} `; } lines += `<polyline points="${pts}"/>`; });
    const crack = mk(ctx, "fx-cracks", `<svg viewBox="0 0 ${W} ${H}">${lines}</svg>`);
    const flash = mk(ctx, "fx-flash"); flash.style.opacity = .55;
    let shards = null;
    const build = () => {                                  // a grid of jittered points, cut into triangles
      const C = 5, Rw = 4, P = [];
      for (let j = 0; j <= Rw; j++) { P.push([]); for (let i = 0; i <= C; i++) {
        const edge = i === 0 || j === 0 || i === C || j === Rw;
        P[j].push([i / C * W + (edge ? 0 : r(-.35, .35) * W / C), j / Rw * H + (edge ? 0 : r(-.35, .35) * H / Rw)]); } }
      shards = [];
      for (let j = 0; j < Rw; j++) for (let i = 0; i < C; i++) {
        const a = P[j][i], b = P[j][i + 1], c = P[j + 1][i + 1], d = P[j + 1][i];
        (Math.random() < .5 ? [[a, b, c], [a, c, d]] : [[a, b, d], [b, c, d]]).forEach(tri => {
          const s = mk(ctx, "fx-shard"); s.style.clipPath = `polygon(${tri.map(p => `${p[0].toFixed(1)}px ${p[1].toFixed(1)}px`).join(", ")})`;
          s.appendChild(ctx.cloneRed());
          const cx = (tri[0][0] + tri[1][0] + tri[2][0]) / 3, cy = (tri[0][1] + tri[1][1] + tri[2][1]) / 3;
          s.style.transformOrigin = `${cx}px ${cy}px`;
          const dx = cx - ix, dy = cy - iy, dl = Math.hypot(dx, dy) || 1, sp = r(250, 700);
          shards.push({ s, vx: dx / dl * sp + r(-80, 80), vy: dy / dl * sp * .6 - r(100, 300), vr: r(-260, 260), delay: dl / Math.max(W, H) * 160 });
        });
      }
      ctx.red.style.visibility = "hidden"; crack.style.display = "none";
    };
    loop(ctx, 1500, (t) => {
      flash.style.opacity = Math.max(0, .55 - t / 140);
      ctx.el.style.transform = t < 350 ? `translate(${r(-6, 6)}px, ${r(-5, 5)}px)` : "";
      if (t < 260) return;
      if (!shards) build();
      shards.forEach(o => {
        const u = Math.max(0, (t - 260 - o.delay) / 1000);
        o.s.style.transform = `translate(${o.vx * u}px, ${o.vy * u + 1700 * u * u}px) rotate(${o.vr * u}deg) scale(${1 - .25 * u})`;
        o.s.style.opacity = 1 - clamp01((u - .55) / .45);
      });
    });
  }

  // ------------------------------------------------------------------ logo assemble
  function assemble(ctx) {
    const lay = mk(ctx, "fx-dark", `<div class="fx-asm"><div class="fx-asm-glow"><div class="fx-asm-art"></div></div>
      <img class="fx-asm-fill" src="assets/chimera-logo-splash.png" alt=""><i class="fx-pen"></i></div>
      <div class="fx-asm-title">${"CHIMERA".split("").map(c => `<b>${c}</b>`).join("")}</div>`);
    const art = lay.querySelector(".fx-asm-art"), glow = lay.querySelector(".fx-asm-glow"), fill = lay.querySelector(".fx-asm-fill");
    const pen = lay.querySelector(".fx-pen"), bs = [...lay.querySelectorAll(".fx-asm-title b")];
    loop(ctx, 2500, (t) => {
      const q = ease(t / 1300), a = q * 360, th = a * Math.PI / 180;
      art.style.setProperty("--a", a.toFixed(1) + "deg");
      pen.style.left = `${50 + 47 * Math.sin(th)}%`; pen.style.top = `${50 - 47 * Math.cos(th)}%`;
      pen.style.opacity = t < 1300 ? 1 : Math.max(0, 1 - (t - 1300) / 200);
      const f = clamp01((t - 1250) / 450);
      fill.style.opacity = f; glow.style.opacity = 1 - f * .85;
      bs.forEach((b, i) => { const p = clamp01((t - 850 - i * 95) / 260); b.style.opacity = p; b.style.transform = `translateY(${(1 - ease(p)) * 14}px)`; });
      if (t >= SWITCH) lay.style.opacity = 1 - ease((t - SWITCH) / 750);
    });
  }

  // ------------------------------------------------------------------ terminal boot
  const BANNER = [
    " ██████╗██╗  ██╗██╗███╗   ███╗███████╗██████╗  █████╗ ",
    "██╔════╝██║  ██║██║████╗ ████║██╔════╝██╔══██╗██╔══██╗",
    "██║     ███████║██║██╔████╔██║█████╗  ██████╔╝███████║",
    "██║     ██╔══██║██║██║╚██╔╝██║██╔══╝  ██╔══██╗██╔══██║",
    "╚██████╗██║  ██║██║██║ ╚═╝ ██║███████╗██║  ██║██║  ██║",
    " ╚═════╝╚═╝  ╚═╝╚═╝╚═╝     ╚═╝╚══════╝╚═╝  ╚═╝╚═╝  ╚═╝",
  ];
  function terminal(ctx) {
    const today = new Date().toLocaleDateString("en-GB");
    const LOG = [`CHIMERA BOOT  ·  ${today}`, "", "memory check ................... ok", "loading chimera.db .............. ok",
      "sfa driver ...................... ok", "report builder .................. ok", "whatsapp sender ................. ok",
      "", "> chimera --start --sfa"];
    const text = LOG.join("\n");
    const lay = mk(ctx, "fx-term", `<pre class="fx-log"></pre><pre class="fx-banner"></pre>`);
    const log = lay.querySelector(".fx-log"), ban = lay.querySelector(".fx-banner");
    let slices = null;
    loop(ctx, 2450, (t) => {
      const n = Math.floor(text.length * clamp01(t / 950));
      log.textContent = text.slice(0, n) + (Math.floor(t / 260) % 2 ? "▌" : " ");
      const rows = Math.floor(clamp01((t - 1000) / 520) * BANNER.length);
      ban.textContent = BANNER.slice(0, rows).join("\n");
      if (t < SWITCH) return;
      if (!slices) {                                       // crash: slices of the console jump about, colour split
        slices = [];
        for (let k = 0; k < 9; k++) { const c = lay.cloneNode(true); c.classList.add("fx-term-slice"); if (k % 3 === 0) c.classList.add("c1"); ctx.el.appendChild(c); slices.push(c); }
      }
      const q = (t - SWITCH) / 600;
      slices.forEach(s => {
        const top = Math.random() * 100, h = 3 + Math.random() * 14;
        s.style.clipPath = `inset(${top}% 0 ${Math.max(0, 100 - top - h)}% 0)`;
        s.style.transform = `translateX(${(Math.random() - .5) * 120 * (1 - q * .5)}px)`;
        s.style.opacity = q > 1 ? 0 : (Math.random() < q ? 0 : 1);
      });
      lay.style.opacity = q < .55 ? (Math.random() < .5 ? 1 : .2) : Math.max(0, 1 - (q - .55) * 3);
    });
  }

  // ------------------------------------------------------------------ VHS rewind (on the red opening)
  function vhs(ctx) {
    const red = ctx.red;
    const lay = mk(ctx, "fx-vhs", `<div class="fx-band"></div><div class="fx-osd">&#9664;&#9664; REWIND</div><div class="fx-tc"></div><div class="fx-crtline"></div>`);
    const band = lay.querySelector(".fx-band"), osd = lay.querySelector(".fx-osd"), tc = lay.querySelector(".fx-tc"), line = lay.querySelector(".fx-crtline");
    const tube = mk(ctx, "fx-tube", `<i class="t"></i><i class="b"></i>`);   // the screen is black while the CRT is off
    red.classList.add("fx-vhs-red");
    let step = -1;
    loop(ctx, 2400, (t) => {
      const play = t >= 1350;
      const s = Math.floor(t / 83);
      if (s !== step && t < SWITCH) {
        step = s;
        const j = play ? 1.5 : 7;
        red.style.transform = `translate(${r(-j, j)}px, ${play ? 0 : r(-2, 2)}px) skewX(${play ? 0 : r(-1.5, 1.5)}deg)`;
        osd.innerHTML = play ? "&#9654; PLAY" : "&#9664;&#9664; REWIND";
        const secs = play ? 0 : Math.round(47 * (1 - t / 1350));
        tc.textContent = `SP  0:00:${String(secs).padStart(2, "0")}`;
      }
      band.style.top = play ? "-20%" : `${(100 - (t * .22) % 120)}%`;
      if (t >= SWITCH && t < SWITCH + 150) {               // CRT switches off: picture squashes to a line…
        const q = (t - SWITCH) / 150;
        tube.style.display = "block";
        red.style.transform = `scaleY(${Math.max(.004, 1 - q)})`; red.style.filter = `brightness(${1 + 2 * q})`;
        osd.style.opacity = 0; tc.style.opacity = 0; band.style.opacity = 0;
      } else if (t >= SWITCH + 150 && t < SWITCH + 320) {  // …the line shrinks to a dot…
        red.style.visibility = "hidden"; tube.style.display = "block";
        const q = (t - SWITCH - 150) / 170;
        line.style.opacity = 1; line.style.transform = `translate(-50%, -50%) scaleX(${Math.max(.002, 1 - q)})`;
      } else if (t >= SWITCH + 320) {                      // …and the dark splash switches on from a bright line
        lay.style.display = "none"; red.style.visibility = "hidden";
        const q = ease((t - SWITCH - 320) / 300);
        tube.style.display = "block"; tube.style.setProperty("--open", (q * 50).toFixed(2) + "%");
        tube.style.filter = `brightness(${1 + (1 - q) * 1.5})`;
        if (q >= 1) tube.style.display = "none";
      }
    });
  }

  // `at`: when splash.js starts it (0 = from the start; SWITCH = after the red opening). `red`: the red opening shows.
  window.ChimeraFX = {
    SWITCH,
    neon: { at: 0, red: false, run: neon },
    heartbeat: { at: 0, red: false, run: heartbeat },
    assemble: { at: 0, red: false, run: assemble },
    terminal: { at: 0, red: false, run: terminal },
    vhs: { at: 0, red: true, run: vhs },
    portal: { at: SWITCH, red: true, run: portal },
    shatter: { at: SWITCH, red: true, run: shatter },
  };
})();
