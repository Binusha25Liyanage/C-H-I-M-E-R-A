// splash.js — the splash screen while Chimera opens. The bar follows the real startup steps (startup.py):
// starting Chimera, checking the SFA login, reading itineraries. "Continue to dashboard" appears after a few
// seconds; whatever is left keeps running in the background (Running now shows it).
(function () {
  const el = document.getElementById("splash"); if (!el) return;
  const $ = (id) => document.getElementById(id);
  const ICON = { done: "&#10003;", failed: "&#10005;", skipped: "&ndash;", running: "", pending: "" };
  let poll = null, closed = false, shownAt = Date.now();
  const clock = () => { $("sp-clock").textContent = new Date().toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }); };
  clock(); setInterval(clock, 15000);

  function close() {
    if (closed) return; closed = true;
    if (poll) clearInterval(poll);
    el.classList.add("sp-out");
    setTimeout(() => { el.style.display = "none"; if (window.loadOverviewKpis) window.loadOverviewKpis(); }, 650);
  }
  $("sp-skip").onclick = () => {
    const pending = el.dataset.active === "1";
    close();
    if (pending && window.showToast) window.showToast("Startup sync continues in the background — see Running now.");
  };
  setTimeout(() => $("sp-skip").classList.add("on"), 4000);

  function render(s) {
    const pct = Math.max(2, Math.min(100, s.percent || 0));
    $("sp-fill").style.width = pct + "%";
    $("sp-pct").textContent = pct + "%";
    $("sp-msg").textContent = s.message || "";
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
      if (!failed) setTimeout(close, 900);
    }
  }

  window.addEventListener("pywebviewready", async () => {
    const api = window.pywebview.api;
    if (typeof api.start_startup !== "function") { close(); return; }      // older backend
    try { render(await api.start_startup()); } catch (e) { close(); return; }
    poll = setInterval(async () => {
      try { render(await api.get_startup()); } catch (e) { /* keep showing */ }
    }, 600);
  });
  // no desktop bridge at all (opened in a plain browser): don't trap the page
  setTimeout(() => { if (!window.pywebview && !closed) close(); }, 6000);
  window.closeSplash = close;
})();
