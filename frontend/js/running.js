// running.js — Monitoring › Running now: every Chimera process live (progress, steps, log, Stop), what's coming up,
// and what just finished. Relies on esc/API/chip/openModal/closeModal/confirmModal (automation.js), copyText (main.js),
// openStopNow (shell.js), switchView (main.js).

const RN = { open: false, timer: null, badgeTimer: null, data: null, openLogs: new Set(), busy: false };
const RN_ICON = {
  reports: '<path d="M12 3v12M7 10l5 5 5-5"/><path d="M4 21h16"/>',
  whatsapp: '<path d="M21 12a8.5 8.5 0 0 1-12.6 7.4L3 21l1.6-5.2A8.5 8.5 0 1 1 21 12z"/>',
  loadings: '<path d="M3 7h11v9H3zM14 10h4l3 3v3h-7"/><circle cx="7" cy="18" r="1.6"/><circle cx="17" cy="18" r="1.6"/>',
  itinerary: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>',
  outlets: '<path d="M4 6h16M4 12h10M4 18h6"/><path d="M17 15l2 2 3-4"/>',
};
const rnIcon = (id) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${RN_ICON[id] || '<circle cx="12" cy="12" r="8"/>'}</svg>`;
const rnDur = (s) => s == null ? "" : s < 60 ? `${s}s` : s < 3600 ? `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s` : `${Math.floor(s / 3600)}h ${Math.floor(s % 3600 / 60)}m`;
const rnIn = (s) => s < 60 ? "in under a minute" : s < 3600 ? `in ${Math.round(s / 60)} min` : `in ${Math.floor(s / 3600)}h ${Math.round(s % 3600 / 60)}m`;
const rnHM = (iso) => iso ? new Date(iso).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }) : "";

async function rnLoad() {
  if (RN.busy) return;
  if (typeof API().get_running_now !== "function") {
    document.getElementById("rn-running").innerHTML = `<div class="problem-box">This screen needs the latest backend files (backend\\api.py and running_now.py). Replace them, then restart Chimera.</div>`;
    return;
  }
  RN.busy = true;
  try { RN.data = await API().get_running_now(); } catch (e) { RN.busy = false; return; }
  RN.busy = false;
  rnBadge();
  if (RN.open) rnRender();
}

function rnBadge() {
  const b = document.getElementById("nav-run-badge"); if (!b || !RN.data) return;
  const n = RN.data.active_count;
  b.textContent = n; b.style.display = n ? "" : "none"; b.title = n ? `${n} process(es) running` : "";
}

function rnRender() {
  const d = RN.data; if (!d) return;
  document.getElementById("rn-clock").textContent = new Date(d.now).toLocaleTimeString("en-GB");
  document.getElementById("rn-paused").innerHTML = d.paused ? `<div class="warn-box">Automation is <b>paused</b> — scheduled runs and reminders won't start until you resume it (Schedules).</div>` : "";
  const act = d.running.filter(p => p.active), idle = d.running.filter(p => !p.active);
  document.getElementById("rn-stop-all").style.display = act.length ? "" : "none";
  const stepIcon = (st) => st === "done" ? "✓" : st === "failed" ? "!" : st === "skipped" ? "–" : "";
  const laneChip = { done: ["chip-ok", "Done"], empty: ["chip-ok", "No data"], failed: ["chip-missing", "Failed"], running: ["chip-warn", "Working"],
                     queued: ["chip-grey", "Queued"], cancelled: ["chip-grey", "Stopped"], skipped: ["chip-grey", "Skipped"] };
  const card = (p) => {
    const pct = p.progress == null ? null : (p.progress >= 100 && p.active ? 99 : p.progress);
    const logOpen = RN.openLogs.has(p.id);
    const last = (p.log || []).slice(-1)[0];
    return `<div class="rn-card active" data-pid="${p.id}">
      <div class="rn-top">
        <div class="rn-ico">${rnIcon(p.id)}</div>
        <div class="rn-what"><b>${esc(p.title)}</b>
          <div class="hint">${esc(p.label)}${p.trigger ? " · " + esc(String(p.trigger).replace("scheduled", "scheduled")) : ""}${p.slot ? " " + esc(p.slot) : ""}${p.run_id ? ` · run #${p.run_id}` : ""}${p.dry_run ? ` · <b>DRY RUN</b>` : ""}</div></div>
        <div class="rn-meta"><span class="rn-pulse"></span> ${esc({ logging_in: "Logging in", working: "Working", starting: "Starting", building: "Building reports", importing: "Importing" }[p.phase] || "Running")}
          <div class="hint">${esc(rnDur(p.elapsed))}${p.total ? ` · ${p.done}/${p.total}` : ""}</div></div>
        <button class="btn-stop btn-small" data-rn-stop="${p.id}">&#9632; Stop</button>
      </div>
      <div class="progress ${pct == null ? "indeterminate" : ""}"><div style="width:${pct == null ? 35 : Math.max(3, pct)}%"></div></div>
      ${p.lanes.length ? `<div class="rn-lanes">${p.lanes.map(l => { const c = laneChip[l.status] || ["chip-grey", l.status || ""]; const st = Object.keys(l.steps || {});
        return `<div class="rn-lane ${l.status}"><div class="rn-lane-name" title="${esc(l.name || "")}">${esc(l.name || "")}</div>
          <div class="stepper">${st.map(x => `<div class="st ${l.steps[x]}"><span class="dot">${stepIcon(l.steps[x])}</span>${esc((p.step_labels || {})[x] || x)}</div>`).join("")}</div>
          <div class="rn-lane-end"><span class="chip ${c[0]}">${esc(c[1])}</span><div class="hint">${esc(l.message || "")}</div></div></div>`; }).join("")}</div>` : ""}
      <div class="rn-logbar">${last ? `<span class="rn-last lv-${last.level}">${esc(last.t)} ${last.lane ? `[${esc(last.lane)}] ` : ""}${esc(last.msg)}</span>` : `<span class="hint">No log yet.</span>`}
        <button class="link-btn" data-rn-log="${p.id}">${logOpen ? "Hide log" : "Show log"}</button>
        <button class="link-btn" data-rn-copy="${p.id}">Copy log</button></div>
      ${logOpen ? `<div class="log-box rn-log">${(p.log || []).map(x => `<div class="lv-${x.level}">${esc(x.t)} ${x.lane ? `<span class="who">[${esc(x.lane)}]</span> ` : ""}${esc(x.msg)}</div>`).join("")}</div>` : ""}
    </div>`;
  };
  const idleRow = (p) => { const r = p.result || {};
    return `<div class="rn-idle"><div class="rn-ico small">${rnIcon(p.id)}</div><span>${esc(p.label)}</span>
      <span class="hint">${r.message ? "Last: " + esc(r.message.length > 90 ? r.message.slice(0, 88) + "…" : r.message) : "Idle"}</span></div>`; };
  document.getElementById("rn-running").innerHTML =
    (act.length ? act.map(card).join("") : `<div class="rn-quiet">Nothing is running right now.</div>`)
    + `<div class="rn-idles">${idle.map(idleRow).join("")}</div>`;
  // keep open logs scrolled to the end
  document.querySelectorAll(".rn-log").forEach(b => b.scrollTop = b.scrollHeight);
  const kindIcon = { job: "▶", reminder: "💬", itinerary: "🗓" };
  document.getElementById("rn-coming").innerHTML = d.coming_up.length ? `<table class="report-table am-table"><tbody>${d.coming_up.map(c => `
      <tr class="${c.skip ? "rn-skip" : ""}"><td class="rn-time"><b>${esc(rnHM(c.at))}</b><div class="hint">${esc(rnIn(c.in_seconds))}</div></td>
      <td>${kindIcon[c.kind] || ""}</td><td><b>${esc(c.what)}</b><div class="am-note">${esc(c.detail || "")}${c.skip ? ` — <b>won't be sent</b>: ${esc(c.skip)}` : ""}</div></td></tr>`).join("")}</tbody></table>`
    : `<p class="hint" style="padding:14px 18px;">${d.paused ? "Paused — nothing will start." : "Nothing scheduled in the next 6 hours."}</p>`;
  document.getElementById("rn-ended").innerHTML = d.just_ended.length ? `<table class="report-table am-table"><tbody>${d.just_ended.map(e => `
      <tr><td class="rn-time">${esc(rnHM(e.time))}</td><td><b>${esc(e.title)}</b>${e.detail ? `<div class="am-note">${esc(e.detail.length > 140 ? e.detail.slice(0, 138) + "…" : e.detail)}</div>` : ""}</td>
      <td>${chip({ ok: "success", fail: "failed", warn: "partial", info: "info", running: "running" }[e.status] || e.status)}</td></tr>`).join("")}</tbody></table>`
    : `<p class="hint" style="padding:14px 18px;">Nothing has finished today yet.</p>`;
}

function onRunningView(on) {
  RN.open = on;
  clearInterval(RN.timer); RN.timer = null;
  if (on) { rnLoad(); RN.timer = setInterval(rnLoad, 1000); }
}
window.onRunningView = onRunningView;

(function wireRunning() {
  const v = document.getElementById("view-running"); if (!v) return;
  v.addEventListener("click", async (e) => {
    const b = e.target.closest("button"); if (!b) return;
    if (b.dataset.rnLog) { RN.openLogs.has(b.dataset.rnLog) ? RN.openLogs.delete(b.dataset.rnLog) : RN.openLogs.add(b.dataset.rnLog); rnRender(); }
    if (b.dataset.rnCopy) { const p = RN.data.running.find(x => x.id === b.dataset.rnCopy);
      copyText((p.log || []).map(x => `${x.t} ${x.level.toUpperCase()} ${x.lane ? "[" + x.lane + "] " : ""}${x.msg}`).join("\n"), b); }
    if (b.dataset.rnStop) { const p = RN.data.running.find(x => x.id === b.dataset.rnStop);
      confirmModal("Stop " + p.label, `Stop <b>${esc(p.title)}</b> now? The other processes keep going. Whatever it finished is kept;
        it continues from there next time.`, "Stop now", async () => { const r = await API().stop_process(p.id); rnLoad();
          if (!r.ok) openModal({ title: "Stop", body: `<div class="problem-box">${esc(r.message)}</div>`, footer: `<button class="btn-primary" onclick="closeModal()">OK</button>` }); }, true); }
  });
  document.getElementById("rn-stop-all").onclick = () => openStopNow();
  document.getElementById("rn-to-activity").onclick = () => switchView("activity");
  // the sidebar count stays live wherever you are
  RN.badgeTimer = setInterval(() => { if (!RN.open) rnLoad(); }, 4000);
  window.addEventListener("pywebviewready", () => setTimeout(rnLoad, 1500));
})();
