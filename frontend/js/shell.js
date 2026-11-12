// shell.js — app-wide pieces: top bar (notifications, refresh, help, support),
// Overview feed, "preview before saving" review pop-up, WhatsApp Delivery tab.
// Uses openModal/closeModal/confirmModal/chip/esc from automation.js and
// escapeHtml/localToday/openPathButton from main.js.

const SH = { view: "overview", notifTimer: null, waPoll: null, preview: null, lb: null };

// Track the current view without touching every caller of switchView().
const _origSwitchView = window.switchView;
window.switchView = function (view, tab) {
  _origSwitchView(view, tab);
  SH.view = view;
  document.getElementById("notif-panel").style.display = "none";
  if (view === "overview") loadOverviewFeed();
  if (view === "daily") { loadDeliveryMode(); loadPending(); }
};

// ---------------------------------------------------------------- run-in-progress pill (top bar)
// When a run (manual or scheduled) ends with reports waiting for approval,
// open the preview pop-up straight away — once per report run.
SH.autoPreviewed = SH.autoPreviewed || new Set();
function maybeAutoPreview(s) {
  const rr = s && !s.active && s.result && s.result.report_run;
  if (!rr || rr.status !== "pending_review" || SH.autoPreviewed.has(rr.run_id)) return false;
  SH.autoPreviewed.add(rr.run_id);
  const modalOpen = document.getElementById("modal-backdrop").style.display === "flex";
  if (modalOpen && !AU.monitorOpen) return false;          // don't cover a pop-up the user is using
  if (AU.monitorOpen) closeModal(true);
  openPreview(rr.run_id);
  return true;
}
window.maybeAutoPreview = maybeAutoPreview;
SH.pillPrimed = false;
async function refreshRunPill() {
  let ls = null;   // a Distributor Loadings run shows in the same pill
  try { if (window.pywebview.api.get_loading_live) ls = await window.pywebview.api.get_loading_live(); } catch (e) { ls = null; }
  if (ls && window.onLoadingLive) window.onLoadingLive(ls);
  let is = null;   // an Outlet Updates itinerary read too
  try { if (window.pywebview.api.get_itinerary_live) is = await window.pywebview.api.get_itinerary_live(); } catch (e) { is = null; }
  if (is && window.onItineraryLive) window.onItineraryLive(is);
  let rs = null;   // Outlet Updates rep sheets (read / save in SFA) too
  try { if (window.pywebview.api.get_rep_sheets_live) rs = await window.pywebview.api.get_rep_sheets_live(); } catch (e) { rs = null; }
  if (rs && window.onRepSheetsLive) window.onRepSheetsLive(rs);
  let s; try { s = await window.pywebview.api.get_live_run(); } catch (e) { return; }
  if (ls && ls.active && !s.active) {
    const lanes = ls.lanes || [], done = lanes.filter(l => !["queued", "running"].includes(l.status)).length;
    document.getElementById("tb-run-text").textContent = `${ls.kind === "confirm" ? "Confirming loadings" : "Preparing loadings"} ${done}/${lanes.length}`;
    document.getElementById("tb-run").style.display = ""; SH.pillLoading = true; return;
  }
  SH.pillLoading = false;
  if (is && is.active && !s.active) {
    const lanes = is.lanes || [], done = lanes.filter(l => !["queued", "running"].includes(l.status)).length;
    document.getElementById("tb-run-text").textContent = `Reading itineraries ${done}/${lanes.length}`;
    document.getElementById("tb-run").style.display = ""; SH.pillItinerary = true; return;
  }
  SH.pillItinerary = false;
  if (rs && rs.active && !s.active) {
    const lanes = rs.lanes || [], done = lanes.filter(l => ["done", "failed", "cancelled"].includes(l.status)).length;
    document.getElementById("tb-run-text").textContent = rs.kind === "routes" ? "Reading SFA's routes" : `Outlet updates ${done}/${lanes.length}`;
    document.getElementById("tb-run").style.display = ""; SH.pillSheets = true; return;
  }
  SH.pillSheets = false;
  const pill = document.getElementById("tb-run");
  if (!SH.pillPrimed) {   // a finished run from before this window opened isn't "new"
    SH.pillPrimed = true;
    const rr = s.result && s.result.report_run; if (!s.active && rr) SH.autoPreviewed.add(rr.run_id);
  }
  if (!s.active) { pill.style.display = "none"; maybeAutoPreview(s); return; }
  const lanes = s.lanes || [], done = lanes.filter(l => ["done", "empty", "failed", "cancelled"].includes(l.status)).length;
  const phase = { logging_in: "Logging in to SFA", downloading: "Downloading", generating: "Building reports", sending: "Sending to WhatsApp" }[s.phase] || "Running";
  document.getElementById("tb-run-text").textContent = lanes.length && s.phase === "downloading" ? `${phase} ${done}/${lanes.length}` : phase;
  pill.style.display = "";
}
document.getElementById("tb-run-open").addEventListener("click", () => {
  if (SH.pillLoading && window.openLdMonitor) { switchView("loadings"); openLdMonitor("Distributor loadings"); return; }
  if (SH.pillItinerary && window.openOuMonitor) { switchView("outlet-updates"); openOuMonitor(); return; }
  if (SH.pillSheets && window.openRsMonitor) { switchView("outlet-updates"); selectOuTab("sheets"); openRsMonitor(); return; }
  if (typeof openMonitor === "function") openMonitor(); });
document.getElementById("tb-run-stop").addEventListener("click", () => openStopNow());

// ---------------------------------------------------------------- Stop now (everywhere)
function openStopNow() {
  openModal({ title: "Stop now", body: `
      <p>Stops whatever Chimera is doing <b>immediately</b>: the SFA download browser closes, a WhatsApp send in
        progress is stopped, and a background run started by Windows Task Scheduler is ended.</p>
      <p class="hint">Reports already downloaded and imported stay. A run left unfinished when Chimera was closed is cleared too.</p>
      <label class="check" style="margin-top:12px;"><input type="checkbox" id="stop-pause"> Also pause scheduled runs (nothing starts again until you switch it back on)</label>
      <div id="stop-result"></div>`,
    footer: `<button class="btn-outline" id="stop-cancel">Keep running</button><button class="btn-stop" id="stop-go">&#9632; Stop now</button>` });
  document.getElementById("stop-cancel").onclick = () => closeModal();
  document.getElementById("stop-go").onclick = async (e) => {
    e.target.disabled = true; e.target.textContent = "Stopping…";
    const r = await window.pywebview.api.emergency_stop(document.getElementById("stop-pause").checked);
    document.getElementById("stop-result").innerHTML = `<div class="ok-box" style="margin-top:14px;">${r.steps.map(x => esc(x)).join("<br>")}</div>`;
    document.getElementById("modal-footer").innerHTML = `<button class="btn-primary" id="stop-done">Done</button>`;
    document.getElementById("stop-done").onclick = () => closeModal(true);
    refreshRunPill();
    if (window.refreshClock) refreshClock();
    if (SH.view === "schedules" && window.loadSchedules) loadSchedules();
    if (SH.view === "automation" && window.loadAutomation) loadAutomation();
    if (SH.view === "loadings" && window.loadLoadings) loadLoadings();
    if (SH.view === "outlet-updates" && window.loadOutletUpdates) loadOutletUpdates();
    if (SH.view === "activity" && window.loadActivity) loadActivity();
  };
}
window.openStopNow = openStopNow;
window.addEventListener("pywebviewready", () => { refreshRunPill(); setInterval(refreshRunPill, 3000); checkDependencies(); });

// ---------------------------------------------------------------- missing Python packages
async function checkDependencies() {
  let d; try { d = await window.pywebview.api.check_dependencies(); } catch (e) { return; }
  if (!d.missing.length) return;
  const box = document.createElement("div");
  box.className = "problem-box dep-banner";
  box.innerHTML = `<b>Some Python packages are missing on this PC:</b> ${esc(d.missing.join(", "))}.
    Close Chimera, open a terminal in the Chimera folder and run <code>${esc(d.command)}</code>, then start Chimera again.
    <button class="btn-outline btn-small" id="dep-copy">Copy command</button>`;
  document.querySelector("main").prepend(box);
  document.getElementById("dep-copy").onclick = (e) => copyText(d.command, e.target);
}

// ---------------------------------------------------------------- notifications
async function refreshBell() {
  let n; try { n = await window.pywebview.api.get_notifications(); } catch (e) { return; }
  const badge = document.getElementById("tb-bell-count");
  badge.textContent = n.unread > 9 ? "9+" : n.unread;
  badge.style.display = n.unread ? "" : "none";
  SH.notifs = n.events;
  if (document.getElementById("notif-panel").style.display !== "none") renderNotifs();
}
function renderNotifs() {
  const list = document.getElementById("notif-list");
  const ev = SH.notifs || [];
  list.innerHTML = ev.length ? ev.map((e, i) => `
    <div class="notif-item ${e.unread ? "unread" : ""}" data-i="${i}">
      <span class="notif-dot ${e.level}"></span>
      <div><div class="t">${esc(e.title)}</div>${e.text ? `<div class="x">${esc(e.text)}</div>` : ""}<div class="a">${esc(e.ago)}</div></div>
    </div>`).join("") : `<p class="hint" style="padding:16px;">Nothing yet — runs, approvals and WhatsApp deliveries will show up here.</p>`;
}
document.getElementById("tb-bell").addEventListener("click", async (e) => {
  e.stopPropagation();
  const p = document.getElementById("notif-panel");
  if (p.style.display === "none") { p.style.display = "block"; await refreshBell(); renderNotifs(); }
  else p.style.display = "none";
});
document.getElementById("notif-read").addEventListener("click", async (e) => {
  e.stopPropagation(); await window.pywebview.api.mark_notifications_read(); refreshBell();
});
document.getElementById("notif-list").addEventListener("click", (e) => {
  const it = e.target.closest(".notif-item"); if (!it) return;
  const ev = SH.notifs[Number(it.dataset.i)];
  document.getElementById("notif-panel").style.display = "none";
  openAction(ev.action);
});

// The bell and Windows notifications (when clicked) open a screen by the same action names.
function openAction(action) {
  action = action || "overview";
  if (action.startsWith("preview:")) openPreview(Number(action.split(":")[1]));
  else if (action === "whatsapp") { switchView("automation"); selectAutomationTab("whatsapp"); }
  else if (action === "loadings-approve") { switchView("loadings"); if (window.openApproveLoadings) openApproveLoadings(); }
  else if (action === "rep-sheets") { switchView("outlet-updates"); if (window.selectOuTab) selectOuTab("sheets"); }
  else if (action === "settings") { switchView("settings"); const b = document.querySelector('#st-tabs .tab[data-tab="notify"]'); if (b) b.click(); }
  else if (action === "schedules") switchView("schedules");
  else if (document.getElementById("view-" + action)) switchView(action);
  else switchView("overview");
}
window.openAction = openAction;
// a notification clicked while Chimera is open (or the one that started it)
setInterval(async () => {
  try {
    if (!window.pywebview || !window.pywebview.api.take_open_request) return;
    const a = await window.pywebview.api.take_open_request();
    if (a) openAction(a);
  } catch (e) { /* not ready yet */ }
}, 2000);
document.addEventListener("click", (e) => {
  const p = document.getElementById("notif-panel");
  if (p.style.display !== "none" && !p.contains(e.target)) p.style.display = "none";
});

// ---------------------------------------------------------------- refresh / help / support
document.getElementById("tb-refresh").addEventListener("click", () => {
  const b = document.getElementById("tb-refresh"); b.classList.remove("spin"); void b.offsetWidth; b.classList.add("spin");
  switchView(SH.view); refreshBell();
  if (SH.view === "overview") loadDashboard && loadDashboard();
});
document.getElementById("tb-support").addEventListener("click", () => switchView("about"));

const HELP = {
  overview: ["Overview", "A summary of what Chimera holds and what it did recently. <b>Recent activity</b> lists SFA downloads, report runs and WhatsApp deliveries. <b>Data source health</b> shows which of today's SFA inputs are loaded and how fresh they are."],
  daily: ["Daily SFA Reports", "1) Pick the report date. 2) <b>Select SFA export files…</b> — choose the raw exports (all at once is fine); each is recognised and checked automatically. 3) Tick the reports and press <b>Generate reports</b>.<br><br><b>Preview before saving</b> shows every page first; you then <b>Save</b>, <b>Save &amp; send to WhatsApp</b>, or <b>Discard</b>. Scheduled runs wait for your approval too (the bell tells you)."],
  schedules: ["Schedules", "<b>Jobs</b> — each job runs at its own times: every day, chosen weekdays, chosen days of the month (or the last day), or once. <b>Run now</b> starts a job immediately.<br><b>Week</b> — everything scheduled this week; holidays and Skip today are crossed out.<br><b>Holidays</b> — jobs skip these dates (paste a whole year's list at once).<br><b>Pause</b> stops every job; <b>Skip today</b> skips today only.<br><b>PC clock</b> — Chimera compares your PC's clock with SFA's at every login; fix the Windows clock if it warns you.<br><b>Run even when Chimera is closed</b> — Windows Task Scheduler wakes Chimera at each run time."],
  automation: ["SFA Automation", "<b>Report Profiles</b> — one per SFA report: paste its URL, press <b>Detect filters</b>, choose the values, save, then <b>Test</b>.<br><b>Run all now</b> downloads every enabled report in parallel tabs, imports them and builds the reports.<br><b>Run Settings</b> — how many tabs at once, retries, timeouts, which reports to build. Run times are on the <b>Schedules</b> screen.<br><b>WhatsApp Delivery</b> — link WhatsApp once, add rules for which chat gets which reports.<br><b>Run History</b> — every run, with per-report results and failure screenshots."],
  reports: ["Combined SKU + Invoice Report", "Route-wise per-outlet report anchored to the previous visit date, from the itinerary and sales data imported on the Import Data screen."],
  "import-data": ["Import Data", "Imports itinerary, territories, invoices, sales orders, outlet master and SKU sales files for the route reports. For the daily SKU/Pivot/Hourly reports use <b>Daily SFA Reports</b> instead — it reads raw SFA exports directly."],
  settings: ["Settings", "<b>SFA Connection</b> — the login Chimera uses for automation; <b>Test Connection</b> shows a screenshot.<br><b>Daily report rules</b> — first-bill time, gap highlight, targets, output style.<br><b>Manager groups</b> — which territories belong to which manager (import from SalesPulse)."],
  "outlet-updates": ["Outlet Updates", "<b>Today's Routes</b> — the route each working territory's rep works on the chosen day, read from the SFA monthly itinerary (<b>H</b> = holiday, <b>-</b> = no route). From the prepare time (23:59) Chimera works on tomorrow; if the PC was off, it catches up the next time Chimera runs.<br><b>Working Territories</b> — switch on the territories Chimera looks after; <b>Detect territories from SFA</b> lists them all.<br><b>Itinerary</b> on a card shows the whole month as SFA has it.<br><b>Rep sheets</b> — the route sheets reps fill (New Sequence, Top 15). Chimera matches each to its SFA route by Route Code, reads the route's outlets from SFA and shows a preview: the new visit order (numbered outlets first, blanks after in their current order) and Top 15 outlets set to Mandatory — change any outlet singly or in bulk. Approve to save sequence + Visit State in SFA (phones fixed to 10 characters on the way). Dry run and approval are in Settings. Coming next: sheets straight from the reps' WhatsApp group and Google Sheets, and Chimera's own branded sheet."],
  about: ["About & Support", "Who built Chimera and how to get in touch."],
};
document.getElementById("tb-help").addEventListener("click", () => {
  const [t, body] = HELP[SH.view] || ["Help", "No help for this screen yet."];
  openModal({ title: `Help — ${t}`, body: `<div style="line-height:1.65;font-size:14px;">${body}</div>`,
    footer: `<button class="btn-outline" onclick="switchView('about');closeModal()">Contact support</button><button class="btn-primary" onclick="closeModal()">Got it</button>` });
});

// ---------------------------------------------------------------- overview feed
async function loadOverviewFeed() {
  let f; try { f = await window.pywebview.api.get_overview_feed(); } catch (e) { return; }
  document.getElementById("ov-activity").innerHTML = f.events.length ? `<ul class="feed">${f.events.map(e => `
      <li><span class="notif-dot ${e.level}"></span><div><b>${esc(e.title)}</b><div class="hint" style="margin:2px 0 0;">${esc(e.text || "")}</div>
      <div class="hint" style="margin:2px 0 0;font-size:11px;">${esc(e.ago)}</div></div></li>`).join("")}</ul>`
    : `<p class="hint">No activity yet. Run an SFA download or generate reports and it will appear here.</p>`;
  const h = f.health;
  document.getElementById("ov-health").innerHTML = `<p class="hint" style="margin:0 0 6px;">Today (${esc(h.date)})</p>` + h.datasets.map(d => `
    <div class="health-row"><span><b>${esc(d.label)}</b>${d.generated_at ? `<div class="hint" style="margin:2px 0 0;font-size:11px;">SFA: ${esc(d.generated_at)}</div>` : ""}</span>
      <span style="text-align:right;">${d.loaded ? `<span class="chip chip-ok">${d.rows} rows</span><div class="hint" style="margin:2px 0 0;font-size:11px;">${esc(d.ago)}</div>` : '<span class="chip chip-missing">Not loaded</span>'}</span></div>`).join("");
}

// ---------------------------------------------------------------- preview before saving
const DR_MODE_HINT = {
  save: "Runs save (and send to WhatsApp) straight away.",
  preview_first: "The first run of each day waits for your approval; after you approve, later runs that day save and send by themselves.",
  preview: "Every run waits for your approval.",
};
const DR_SCHED_NOTE = " Scheduled jobs: approval is chosen per time in Schedules › Jobs › Edit (Ask me first / Sends automatically).";
async function loadDeliveryMode() {
  try {
    const s = await window.pywebview.api.get_report_settings();
    const m = s.delivery_mode || "save";
    document.querySelectorAll('input[name="dr-mode"]').forEach(r => { r.checked = r.value === m; });
    document.getElementById("dr-mode-hint").textContent = DR_MODE_HINT[m] +
      (m === "preview_first" ? (s.approved_today ? " Today: approved ✓." : " Today: not approved yet.") : "") + DR_SCHED_NOTE;
  } catch (e) {}
}
document.querySelectorAll('input[name="dr-mode"]').forEach(r => r.addEventListener("change", async () => {
  await window.pywebview.api.save_report_settings({ delivery_mode: r.value });
  loadDeliveryMode();
}));

async function loadPending() {
  let p = []; try { p = await window.pywebview.api.list_pending_reports(); } catch (e) { return; }
  const card = document.getElementById("dr-pending-card");
  card.style.display = p.length ? "" : "none";
  document.getElementById("dr-pending").innerHTML = p.map(r => `
    <div class="rule-card"><div><div class="who">Report date ${esc(r.report_date)} · run #${r.id}</div>
      <div class="what">${esc((r.started_at || "").replace("T", " "))} · ${esc(r.trigger)} · ${(r.summary.reports || []).filter(x => x.ok).map(x => esc(x.label)).join(", ")}</div></div>
      <button class="btn-primary btn-small" data-review="${r.id}">Review</button></div>`).join("");
}
document.getElementById("dr-pending").addEventListener("click", (e) => { const id = e.target.dataset.review; if (id) openPreview(Number(id)); });

// Wrap Generate so "Preview before saving" opens the review pop-up.
document.getElementById("dr-generate-btn").addEventListener("click", () => {
  // main.js handles the run; we watch the run history for a pending result.
  setTimeout(async function check(n = 0) {
    const btn = document.getElementById("dr-generate-btn");
    if (btn.disabled && n < 600) return setTimeout(() => check(n + 1), 500);
    const p = await window.pywebview.api.list_pending_reports();
    loadPending(); refreshBell();
    const mode = document.querySelector('input[name="dr-mode"]:checked');
    if (mode && mode.value === "preview" && p.length) openPreview(p[0].id);
  }, 400);
});

async function openPreview(runId) {
  const r = await window.pywebview.api.get_report_preview(runId);
  if (!r.ok) { openModal({ title: "Preview", body: `<div class="problem-box">${esc(r.message)}</div>` }); return; }
  SH.preview = r;
  const groups = {};
  r.items.forEach(it => { const k = it.label + (new Set(r.items.map(x => x.style)).size > 1 ? ` (${it.style})` : ""); (groups[k] = groups[k] || []).push(it); });
  const keys = Object.keys(groups);
  const pending = r.run.status === "pending_review";
  openModal({ title: `${pending ? "Review" : "View"} reports — ${r.run.report_date}`, size: "xl",
    body: `
      ${pending ? `<div class="ok-box" style="background:#fff6e5;color:#9a6a00;display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;">
          <span>These reports aren't saved yet. Check every page, then save or discard.</span>
          <span>Style: <span class="seg" id="pv-style">${["classic", "professional", "both"].map(st => `<button data-st="${st}" class="${(r.run.summary.style || "classic") === st ? "on" : ""}">${st[0].toUpperCase() + st.slice(1)}</button>`).join("")}</span></span></div>` : ""}
      <div class="tabs" id="pv-tabs">${keys.map((k, i) => `<button class="tab ${i === 0 ? "active" : ""}" data-k="${esc(k)}">${esc(k)} <span class="chip chip-grey">${groups[k].length}</span></button>`).join("")}</div>
      <div class="preview-grid" id="pv-grid"></div>
      ${(r.run.summary.reports || []).some(x => x.notes && x.notes.length) ? `<div class="section-title">Data notes</div><ul class="result-list">${[...new Set((r.run.summary.reports || []).flatMap(x => x.notes || []))].map(n => `<li>${esc(n)}</li>`).join("")}</ul>` : ""}`,
    footer: pending ? `<button class="btn-outline left" id="pv-discard">Discard</button>
             ${r.has_rules ? `<button class="btn-outline" id="pv-save">Save only</button><button class="btn-primary" id="pv-save-send">Approve &amp; send to WhatsApp</button>`
                           : `<button class="btn-primary" id="pv-save2">Approve &amp; save</button>`}`
           : `${r.run.folder ? openPathButton(r.run.folder, "Open folder") : ""}<button class="btn-outline" onclick="closeModal()">Close</button>` });
  const show = (k) => {
    SH.pvItems = groups[k];
    document.getElementById("pv-grid").innerHTML = groups[k].map((it, i) => `<figure data-i="${i}"><div class="thumb"><span class="spinner"></span></div><figcaption>${esc(it.page)}</figcaption></figure>`).join("");
    groups[k].forEach(async (it, i) => {
      if (!it.src) {
        const b64 = await window.pywebview.api.get_image_b64(it.path);
        it.src = b64 ? (it.rotated ? await uprightSrc(b64) : `data:image/png;base64,${b64}`) : null;
      }
      const fig = document.querySelector(`#pv-grid figure[data-i="${i}"] .thumb`);
      if (fig) fig.innerHTML = it.src ? `<img src="${it.src}">` : `<span class="hint">image missing</span>`;
    });
  };
  show(keys[0]);
  document.getElementById("pv-tabs").onclick = (e) => { const b = e.target.closest(".tab"); if (!b) return;
    document.querySelectorAll("#pv-tabs .tab").forEach(t => t.classList.toggle("active", t === b)); show(b.dataset.k); };
  document.getElementById("pv-grid").onclick = (e) => { const f = e.target.closest("figure"); if (f) openLightbox(Number(f.dataset.i)); };
  if (pending) {
    document.getElementById("pv-style").onclick = async (e) => {
      const st = e.target.dataset.st; if (!st || e.target.classList.contains("on")) return;
      document.querySelectorAll("#pv-style button").forEach(b => { b.disabled = true; });
      e.target.textContent = "Building…";
      AU.busyModal = true;
      const res = await window.pywebview.api.rebuild_report_run(runId, st);
      AU.busyModal = false;
      if (!res.ok) { openModal({ title: "Couldn't rebuild", body: `<div class="problem-box">${esc(res.message)}</div>`, footer: `<button class="btn-primary" onclick="closeModal()">OK</button>` }); return; }
      SH.autoPreviewed && SH.autoPreviewed.add(res.run_id);
      closeModal(true); openPreview(res.run_id); loadPending();
    };
    const act = async (send) => {
      AU.busyModal = true;
      const res = await window.pywebview.api.approve_report_run(runId, send);
      AU.busyModal = false;
      closeModal(true);
      let msg = res.ok ? `Saved to ${res.folder}.` : res.message;
      if (res.whatsapp) msg += res.whatsapp.ok ? " Sending to WhatsApp in the background — progress is under SFA Automation → WhatsApp Delivery, and the bell tells you when it's done."
                                               : ` WhatsApp: ${res.whatsapp.message}`;
      openModal({ title: res.ok ? "Reports saved" : "Couldn't save", body: `<p>${esc(msg)}</p>`,
        footer: `${res.folder ? openPathButton(res.folder, "Open folder") : ""}<button class="btn-primary" onclick="closeModal()">OK</button>` });
      loadPending(); refreshBell(); loadDeliveryMode(); if (typeof loadRuns === "function") loadRuns();
      if (send) startWaPoll();
    };
    if (document.getElementById("pv-save")) document.getElementById("pv-save").onclick = () => act(false);
    const s2 = document.getElementById("pv-save-send") || document.getElementById("pv-save2");
    s2.onclick = () => act(!!document.getElementById("pv-save-send"));
    document.getElementById("pv-discard").onclick = () => confirmModal("Discard preview", "Delete these generated reports? The SFA data stays imported; you can generate again any time.", "Discard", async () => {
      await window.pywebview.api.discard_report_run(runId); loadPending(); refreshBell(); if (typeof loadRuns === "function") loadRuns();
    }, true);
  }
}
function openLightbox(i) {
  const items = SH.pvItems; if (!items || !items[i]) return;
  closeLightbox();
  const lb = document.createElement("div"); lb.className = "lightbox";
  lb.innerHTML = `<span class="cap">${esc(items[i].page)} (${i + 1}/${items.length})</span>
    ${i > 0 ? '<button class="nav prev">‹</button>' : ""}${i < items.length - 1 ? '<button class="nav next">›</button>' : ""}
    <img src="${items[i].src || ""}">`;
  lb.onclick = (e) => { if (e.target.classList.contains("prev")) openLightbox(i - 1); else if (e.target.classList.contains("next")) openLightbox(i + 1); else closeLightbox(); };
  document.body.appendChild(lb); SH.lb = lb;
}
// Phone images of wide pages are turned 90° anticlockwise; show them upright in the preview.
function uprightSrc(b64) {
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => {
      const c = document.createElement("canvas"); c.width = img.height; c.height = img.width;
      const g = c.getContext("2d"); g.translate(c.width, 0); g.rotate(Math.PI / 2); g.drawImage(img, 0, 0);
      resolve(c.toDataURL("image/png"));
    };
    img.onerror = () => resolve(`data:image/png;base64,${b64}`);
    img.src = `data:image/png;base64,${b64}`;
  });
}
function closeLightbox() { if (SH.lb) { SH.lb.remove(); SH.lb = null; } }
document.addEventListener("keydown", (e) => { if (SH.lb && e.key === "Escape") { e.stopImmediatePropagation(); closeLightbox(); } }, true);

// ---------------------------------------------------------------- WhatsApp Delivery tab
function selectAutomationTab(t) { const b = document.querySelector(`#au-tabs [data-tab="${t}"]`); if (b) b.click(); }
window.selectAutomationTab = selectAutomationTab;
document.getElementById("au-tabs").addEventListener("click", (e) => { if (e.target.dataset.tab === "whatsapp") loadWhatsApp(); });

async function loadWhatsApp() {
  let w; try { w = await window.pywebview.api.get_whatsapp_overview(); } catch (e) {
    document.getElementById("wa-rules").innerHTML = `<div class="problem-box">${esc(String(e))}</div>`; return; }
  SH.wa = w;
  const chipEl = document.getElementById("wa-state-chip");
  chipEl.className = "chip " + (w.profile_exists ? "chip-ok" : "chip-grey");
  chipEl.textContent = w.profile_exists ? "Linked" : "Not linked";
  document.getElementById("wa-state-hint").textContent = w.profile_exists
    ? `${w.chats.length} chats known · ${w.rules.filter(r => r.enabled).length} active rule(s)` : "Press “Link WhatsApp” and scan the QR code with your phone.";
  document.getElementById("wa-auto").checked = w.settings.wa_auto_send === "1";
  document.getElementById("wa-show").checked = w.settings.wa_show_browser === "1";
  document.getElementById("wa-style").value = w.settings.wa_style;
  document.getElementById("wa-send-latest").disabled = !w.last_saved_run || !w.rules.some(r => r.enabled);
  const kindLabel = { sku: "SKU", pivot: "Pivot", hourly: "Hourly" };
  document.getElementById("wa-rules").innerHTML = w.rules.length ? w.rules.map(r => `
    <div class="rule-card ${r.enabled ? "" : "off"}"><div>
      <div class="who">${esc(r.chat_name)} ${r.enabled ? "" : '<span class="chip chip-grey">off</span>'}</div>
      <div class="what">${r.kinds.map(k => kindLabel[k]).join(" · ")} as ${{ images: "photos", pdf: "PDF files", both: "photos + PDF" }[r.send_as || "images"]}${(r.send_as || "images") !== "pdf" ? ` (${r.orientation || "portrait"})` : ""} — ${r.groups.includes("*") ? "all managers" : esc(r.groups.join(", "))}${r.caption ? ` · “${esc(r.caption)}”` : ""}</div>
      ${r.pre_message ? `<div class="what wa-pre" title="${esc(r.pre_message)}">&#128172; Message first: “${esc(r.pre_message.length > 90 ? r.pre_message.slice(0, 88) + "…" : r.pre_message)}”</div>` : ""}</div>
      <div class="row-actions"><button class="icon-action" data-wa-edit="${r.id}">Edit</button><button class="icon-action" data-wa-test="${esc(r.chat_name)}">Test</button><button class="icon-action danger" data-wa-del="${r.id}">✕</button></div></div>`).join("")
    : `<p class="hint">No rules yet. Example: send the SKU and Pivot pages for <b>Asela</b> to Asela's team group, and the Hourly report to the management group.</p>`;
  document.getElementById("wa-deliveries").innerHTML = w.deliveries.length ? `<table class="report-table"><thead><tr><th>When</th><th>Chat</th><th>Images</th><th>Status</th><th>Details</th></tr></thead><tbody>${w.deliveries.map(d => `
    <tr><td>${esc(d.sent_at.replace("T", " "))}</td><td>${esc(d.chat_name)}</td><td>${d.files}</td><td>${chip(d.status === "sent" ? "done" : d.status === "failed" ? "failed" : "cancelled")}</td><td style="font-size:12px;">${esc(d.message || "")}</td></tr>`).join("")}</tbody></table>`
    : `<p class="hint" style="padding:0 18px 18px;">Nothing sent yet.</p>`;
  if (w.live && w.live.active) startWaPoll(); else renderWaLive(w.live);
}

function renderWaLive(s) {
  const el = document.getElementById("wa-live");
  if (!s || (!s.active && !s.result)) { el.innerHTML = ""; return; }
  const title = { link: "Linking WhatsApp", chats: "Reading your chats", test: "Sending a test message", send: "Sending reports" }[s.action] || "WhatsApp";
  el.innerHTML = `<div class="card"><div class="card-header-row"><h3>${s.active ? '<span class="spinner"></span> ' : ""}${esc(title)}</h3>
      <span>${!s.active ? '<button class="btn-outline btn-small" id="wa-copy-log" title="Log plus the page details WhatsApp showed — paste it to get help">Copy log</button> ' : ""}${s.result ? chip(s.result.ok ? "done" : "failed") : ""}</span></div>
    ${s.result && s.result.message ? `<p class="${s.result.ok ? "hint" : "error"}">${esc(s.result.message)}</p>` : ""}
    <div class="log-box">${(s.log || []).map(x => `<div class="lv-${x.level}">${esc(x.t)} ${esc(x.msg)}</div>`).join("") || "Starting…"}</div></div>`;
}
document.addEventListener("click", async (e) => {
  if (e.target.id !== "wa-copy-log") return;
  copyText(await window.pywebview.api.get_whatsapp_log_text(), e.target);
});
function startWaPoll() {
  if (SH.waPoll) return;
  SH.waPoll = setInterval(async () => {
    let s; try { s = await window.pywebview.api.get_whatsapp_live(); } catch (e) { return; }
    renderWaLive(s);
    if (!s.active) { clearInterval(SH.waPoll); SH.waPoll = null; loadWhatsApp(); refreshBell(); }
  }, 1000);
}
async function waStart(promise) {
  const r = await promise;
  if (!r.ok) { document.getElementById("wa-live").innerHTML = `<div class="problem-box">${esc(r.message)}</div>`; return; }
  startWaPoll();
}
document.getElementById("wa-link-btn").addEventListener("click", () => waStart(window.pywebview.api.whatsapp_link()));
document.getElementById("wa-chats-btn").addEventListener("click", () => waStart(window.pywebview.api.whatsapp_load_chats()));
document.getElementById("wa-auto").addEventListener("change", (e) => window.pywebview.api.save_automation_settings({ wa_auto_send: e.target.checked ? "1" : "0" }));
document.getElementById("wa-show").addEventListener("change", (e) => window.pywebview.api.save_automation_settings({ wa_show_browser: e.target.checked ? "1" : "0" }));
document.getElementById("wa-style").addEventListener("change", (e) => window.pywebview.api.save_automation_settings({ wa_style: e.target.value }));
document.getElementById("wa-send-latest").addEventListener("click", async () => {
  const run = SH.wa.last_saved_run; if (!run) return;
  const plan = await window.pywebview.api.whatsapp_plan(run.id);
  const st = ((run.summary || {}).style || "classic");
  confirmModal("Send latest reports", `Report date <b>${esc(run.report_date)}</b> (run #${run.id}) · style <b>${esc(st[0].toUpperCase() + st.slice(1))}</b>
    <p class="hint">This sends reports that were already built. For another style, build them again (Daily Reports, or Run now) — or switch the style in the review pop-up before approving.</p><ul class="result-list">${plan.map(p => `<li><b>${esc(p.chat_name)}</b> — ${p.files.length} image(s)${p.files.length ? "" : " (nothing matches this rule)"}</li>`).join("")}</ul>`,
    "Send now", () => waStart(window.pywebview.api.whatsapp_send_run(run.id)));
});
document.getElementById("wa-rules").addEventListener("click", (e) => {
  const t = e.target;
  if (t.dataset.waEdit) openRuleModal(SH.wa.rules.find(r => r.id === Number(t.dataset.waEdit)));
  if (t.dataset.waTest) confirmModal("Send a test message", `Send a short test message to <b>${esc(t.dataset.waTest)}</b>?`, "Send test", () => waStart(window.pywebview.api.whatsapp_send_test(t.dataset.waTest)));
  if (t.dataset.waDel) confirmModal("Delete rule", "Delete this delivery rule?", "Delete", async () => { await window.pywebview.api.delete_whatsapp_rule(Number(t.dataset.waDel)); loadWhatsApp(); }, true);
});
document.getElementById("wa-add-rule").addEventListener("click", () => openRuleModal(null));

function openRuleModal(rule) {
  const w = SH.wa; const r = rule || { chat_name: "", kinds: ["sku", "pivot", "hourly"], groups: ["*"], caption: "{report} — {date}", enabled: true, send_as: "images", orientation: "portrait", pre_message: "" };
  const sendAs = r.send_as || "images", orient = r.orientation || "portrait";
  const all = r.groups.includes("*");
  openModal({ title: rule ? "Edit delivery rule" : "Add delivery rule", body: `
    <div id="wr-err"></div>
    <label class="field">Chat or group name (exactly as in WhatsApp)
      <div class="wa-pick"><input id="wr-chat" value="${esc(r.chat_name)}" autocomplete="off" placeholder="${w.chats.length ? "Search or pick a chat…" : "Press “Load my chats” first, or type the name"}">
        <button type="button" class="wa-pick-arrow" tabindex="-1" title="Show chats">&#9662;</button>
        <div class="wa-list" id="wr-chat-list" style="display:none;"></div></div></label>
    <div class="section-title">Reports to send</div>
    <div class="form-row">${[["sku", "SKU Report"], ["pivot", "Pivot Report"], ["hourly", "Hourly BC & SV"]].map(([k, l]) => `<label class="check"><input type="checkbox" class="wr-kind" value="${k}" ${r.kinds.includes(k) ? "checked" : ""}> ${l}</label>`).join("")}</div>
    <div class="section-title">Send as</div>
    <div class="radio-row" style="margin-bottom:6px;">
      <label class="radio"><input type="radio" name="wr-as" value="images" ${sendAs === "images" ? "checked" : ""}> Photos (Photos &amp; videos)</label>
      <label class="radio"><input type="radio" name="wr-as" value="pdf" ${sendAs === "pdf" ? "checked" : ""}> PDF files (Document)</label>
      <label class="radio"><input type="radio" name="wr-as" value="both" ${sendAs === "both" ? "checked" : ""}> Both</label></div>
    <p class="hint" style="margin:0 0 4px;">Photos can be limited to some managers below; a PDF is the whole report (every manager).
      Photos are sent report by report: SKU, then Pivot, then Hourly — each as its own message.</p>
    <div class="section-title">Photo orientation for this chat</div>
    <div class="radio-row" style="margin-bottom:6px;">
      <label class="radio"><input type="radio" name="wr-or" value="portrait" ${orient === "portrait" ? "checked" : ""}> Portrait (wide pages turned upright — easy on phones)</label>
      <label class="radio"><input type="radio" name="wr-or" value="landscape" ${orient === "landscape" ? "checked" : ""}> Landscape (pages as printed)</label></div>
    <div class="section-title">Managers</div>
    <div class="radio-row" style="margin-bottom:10px;"><label class="radio"><input type="radio" name="wr-g" value="all" ${all ? "checked" : ""}> All managers</label>
      <label class="radio"><input type="radio" name="wr-g" value="some" ${all ? "" : "checked"}> Only these:</label></div>
    <div class="form-row" id="wr-groups" style="${all ? "opacity:.45;pointer-events:none;" : ""}">${w.groups.map(g => `<label class="check"><input type="checkbox" class="wr-group" value="${esc(g)}" ${r.groups.includes(g) ? "checked" : ""}> ${esc(g)}</label>`).join("") || '<span class="hint">No manager groups set up.</span>'}</div>
    <label class="field">Message before the reports (optional)
      <textarea id="wr-pre" rows="3" maxlength="2000" placeholder="e.g. Good morning team, here are the reports for {date}.">${esc(r.pre_message || "")}</textarea>
      <span>Sent to this chat as its own message right before the reports, every time they go out. Leave empty for none.
        Use {date} (report date), {time} (send time) and {day} (today, e.g. Wednesday). New lines are kept.</span></label>
    <div class="wa-pre-preview" id="wr-pre-preview"></div>
    <label class="field">Caption (optional) <input id="wr-caption" value="${esc(r.caption)}"><span>Use {report}, {date} and {time}.</span></label>
    <label class="check" style="display:flex;gap:6px;margin-top:12px;font-size:14px;"><input type="checkbox" id="wr-enabled" ${r.enabled ? "checked" : ""}> Enabled</label>`,
    footer: `<button class="btn-outline" onclick="closeModal()">Cancel</button><button class="btn-primary" id="wr-save">Save rule</button>` });
  waChatPicker(document.getElementById("wr-chat"), document.getElementById("wr-chat-list"));
  const prePrev = () => { const t = document.getElementById("wr-pre").value.trim(), now = new Date();
    const d = new Date(now); d.setDate(d.getDate() - 1);
    document.getElementById("wr-pre-preview").innerHTML = t ? `<div class="wa-bubble">${esc(t.replaceAll("{date}", d.toISOString().slice(0, 10))
      .replaceAll("{time}", now.toTimeString().slice(0, 5)).replaceAll("{day}", now.toLocaleDateString("en-GB", { weekday: "long" })))}</div>
      <span class="hint">How it will look (example date)</span>` : ""; };
  document.getElementById("wr-pre").oninput = prePrev; prePrev();
  document.querySelectorAll('input[name="wr-g"]').forEach(x => x.onchange = () => {
    const some = document.querySelector('input[name="wr-g"]:checked').value === "some";
    document.getElementById("wr-groups").style.cssText = some ? "" : "opacity:.45;pointer-events:none;"; });
  document.getElementById("wr-save").onclick = async () => {
    const some = document.querySelector('input[name="wr-g"]:checked').value === "some";
    const groups = some ? [...document.querySelectorAll(".wr-group:checked")].map(c => c.value) : ["*"];
    if (some && !groups.length) { document.getElementById("wr-err").innerHTML = `<div class="problem-box">Tick at least one manager, or choose “All managers”.</div>`; return; }
    const res = await window.pywebview.api.save_whatsapp_rule({ id: rule ? rule.id : null, chat_name: document.getElementById("wr-chat").value,
      kinds: [...document.querySelectorAll(".wr-kind:checked")].map(c => c.value), groups,
      caption: document.getElementById("wr-caption").value, enabled: document.getElementById("wr-enabled").checked,
      send_as: document.querySelector('input[name="wr-as"]:checked').value,
      orientation: document.querySelector('input[name="wr-or"]:checked').value,
      pre_message: document.getElementById("wr-pre").value });
    if (!res.ok) { document.getElementById("wr-err").innerHTML = `<div class="problem-box">${esc(res.message)}</div>`; return; }
    closeModal(); loadWhatsApp();
  };
}

// ---------------------------------------------------------------- WhatsApp-style chat picker
const WA_SVG = {
  user: '<svg viewBox="0 0 212 212"><path fill="#dfe5e7" d="M106 0C47.5 0 0 47.5 0 106s47.5 106 106 106 106-47.5 106-106S164.5 0 106 0z"/><path fill="#fff" d="M173 165c-15 18-38 30-67 30s-52-12-67-30c6-15 22-27 46-33-14-7-23-21-23-37 0-23 19-42 44-42s44 19 44 42c0 16-9 30-23 37 24 6 40 18 46 33z"/></svg>',
  group: '<svg viewBox="0 0 212 212"><path fill="#dfe5e7" d="M106 0C47.5 0 0 47.5 0 106s47.5 106 106 106 106-47.5 106-106S164.5 0 106 0z"/><path fill="#fff" d="M102 89c0 15-12 27-27 27s-27-12-27-27 12-27 27-27 27 12 27 27zm62 0c0 15-12 27-27 27s-27-12-27-27 12-27 27-27 27 12 27 27zM30 158c4-18 23-31 45-31s41 13 45 31v10H30zm92 10v-10c0-11-4-21-11-29 8-1 17-2 26-2 22 0 41 13 45 31v10z"/></svg>',
  photo: '<svg viewBox="0 0 16 20" width="15" height="17"><path fill="currentColor" d="M13.8 4.2H2.2C1.5 4.2 1 4.7 1 5.4v9.2c0 .7.5 1.2 1.2 1.2h11.6c.7 0 1.2-.5 1.2-1.2V5.4c0-.7-.5-1.2-1.2-1.2zM3 13.6l2.6-3.4 1.9 2.3 2.6-3.4 3.4 4.5z"/></svg>',
  call: '<svg viewBox="0 0 24 24" width="15" height="15"><path fill="currentColor" d="M19.95 21q-3.1 0-6.13-1.35-3.02-1.35-5.5-3.83-2.47-2.47-3.82-5.5Q3.15 7.3 3.15 4.2q0-.45.3-.75t.75-.3H8.25q.35 0 .63.24.27.23.32.56l.65 3.5q.05.4-.03.68-.07.27-.27.47L7.1 11.1q.5.92 1.18 1.79.68.86 1.5 1.66.77.78 1.63 1.44.85.66 1.81 1.22l2.35-2.35q.22-.22.58-.33.37-.11.72-.06l3.45.7q.35.1.57.36.23.27.23.6v4.05q0 .45-.3.75t-.75.3z"/></svg>',
  mic: '<svg viewBox="0 0 24 24" width="14" height="14"><path fill="currentColor" d="M12 14a3 3 0 0 0 3-3V5a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.9V21h2v-3.1a7 7 0 0 0 6-6.9z"/></svg>',
  doc: '<svg viewBox="0 0 24 24" width="14" height="14"><path fill="currentColor" d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zm-1 7V3.5L18.5 9z"/></svg>',
};
SH.waChats = null;
function waIconFor(c) {
  const i = (c.icon || "").toLowerCase(), p = (c.preview || "").toLowerCase();
  if (/call|missed/.test(i) || /voice call|video call|missed/.test(p)) return WA_SVG.call;
  if (/ptt|mic/.test(i) || /^voice message/.test(p)) return WA_SVG.mic;
  if (/document/.test(i) || /\.pdf\b|document/.test(p)) return WA_SVG.doc;
  if (/image|photo|camera|video|gif|sticker/.test(i) || /^(~?[^:]{0,40}:\s*)?(photo|video|gif|sticker)\b/.test(p)) return WA_SVG.photo;
  return "";
}
function waHi(text, q) {
  if (!q) return esc(text); const i = text.toLowerCase().indexOf(q.toLowerCase());
  return i < 0 ? esc(text) : esc(text.slice(0, i)) + "<mark>" + esc(text.slice(i, i + q.length)) + "</mark>" + esc(text.slice(i + q.length));
}
function waRowHtml(c, q, on) {
  const av = c.photo ? `<img src="data:image/jpeg;base64,${c.photo}" alt="">` : (c.group ? WA_SVG.group : WA_SVG.user);
  const prev = c.preview || "";
  const pfx = prev.match(/^(~?[^:]{1,40}):\s*(.*)$/);   // "~Sadeepa: Cancel bill"
  const body = pfx ? `<span class="wa-who">${esc(pfx[1])}:</span> ${esc(pfx[2])}` : esc(prev);
  return `<div class="wa-row ${on ? "on" : ""}" data-v="${esc(c.name)}">
    <div class="wa-av">${av}</div>
    <div class="wa-mid"><div class="wa-top"><span class="wa-name">${waHi(c.name, q)}</span><span class="wa-time ${c.unread ? "unread" : ""}">${esc(c.time || "")}</span></div>
      <div class="wa-bot"><span class="wa-prev">${waIconFor(c)}<span class="wa-txt">${body || "&nbsp;"}</span></span>${c.unread ? `<span class="wa-badge">${esc(c.unread)}</span>` : ""}</div></div></div>`;
}
async function waChatPicker(input, list) {
  const arrow = input.parentElement.querySelector(".wa-pick-arrow");
  let chats = SH.waChats || (SH.wa && SH.wa.chats || []).map(n => ({ name: n }));
  const draw = () => {
    const q = input.value.trim(), ql = q.toLowerCase();
    const showAll = input.dataset.all === "1";
    const hits = chats.filter(c => showAll || !ql || c.name.toLowerCase().includes(ql))
      .sort((a, b) => ql && !showAll ? (b.name.toLowerCase().startsWith(ql) ? 1 : 0) - (a.name.toLowerCase().startsWith(ql) ? 1 : 0) : 0);
    list.innerHTML = hits.length ? hits.slice(0, 150).map((c, i) => waRowHtml(c, showAll ? "" : q, i === 0 && ql && !showAll)).join("")
      : `<div class="wa-none">${chats.length ? "No chat matches — this exact name will be used." : "No chats loaded yet — close this, press “Load my chats”, then try again. Or type the exact name."}</div>`;
    list.style.display = "";
  };
  const hide = () => { list.style.display = "none"; input.dataset.all = ""; };
  const pick = (v) => { input.value = v; hide(); };
  input.addEventListener("focus", () => { input.dataset.all = "1"; draw(); });
  input.addEventListener("input", () => { input.dataset.all = ""; draw(); });
  input.addEventListener("blur", () => setTimeout(hide, 150));
  arrow.addEventListener("mousedown", (e) => { e.preventDefault(); if (list.style.display === "none") { input.focus(); input.dataset.all = "1"; draw(); } else hide(); });
  list.addEventListener("mousedown", (e) => { const r = e.target.closest(".wa-row"); if (r) { e.preventDefault(); pick(r.dataset.v); } });
  input.addEventListener("keydown", (e) => {
    const rows = [...list.querySelectorAll(".wa-row")]; let i = rows.findIndex(x => x.classList.contains("on"));
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault(); if (list.style.display === "none") draw();
      const all = [...list.querySelectorAll(".wa-row")]; if (!all.length) return;
      i = e.key === "ArrowDown" ? Math.min(all.length - 1, i + 1) : Math.max(0, i - 1);
      all.forEach((x, k) => x.classList.toggle("on", k === i)); all[i].scrollIntoView({ block: "nearest" });
    } else if (e.key === "Enter") { if (list.style.display !== "none" && i >= 0) { e.preventDefault(); pick(rows[i].dataset.v); } }
    else if (e.key === "Escape" && list.style.display !== "none") { e.stopPropagation(); e.preventDefault(); hide(); }
  });
  try {   // photos + last messages (from the last "Load my chats")
    const r = await window.pywebview.api.get_whatsapp_chat_list();
    if (r && r.chats && r.chats.length) { SH.waChats = chats = r.chats; if (list.style.display !== "none") draw(); }
  } catch (e) {}
}

// ---------------------------------------------------------------- start-up
function apiConnected() {
  const api = window.pywebview && window.pywebview.api;
  return !!api && typeof api.get_dashboard_summary === "function" && typeof api.get_notifications === "function";
}
function showApiBanner() {
  if (document.getElementById("api-banner")) return;
  const d = document.createElement("div");
  d.id = "api-banner"; d.className = "problem-box";
  d.style.cssText = "margin:16px 32px 0;";
  d.innerHTML = `<b>Chimera's screen couldn't connect to its Python side</b>, so buttons won't work. Close Chimera and start it again.
    If it keeps happening, make sure every file from the latest update is in place (especially <code>backend\\api.py</code> and
    <code>backend\\app.py</code>) and send the log file <code>%USERPROFILE%\\Chimera\\logs\\chimera.log</code>.`;
  document.querySelector(".main-col").insertBefore(d, document.querySelector("main"));
}
window.addEventListener("pywebviewready", () => {
  if (!apiConnected()) { showApiBanner(); return; }
  loadOverviewFeed(); refreshBell();
  SH.notifTimer = setInterval(refreshBell, 30000);
});

// ---------------------------------------------------------------- "About Chimera" pop-up (sidebar logo)
const ICON = {
  report: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M8 13h8M8 17h5"/>',
  bot: '<rect x="3" y="8" width="18" height="12" rx="2"/><path d="M12 8V4M8 14h.01M16 14h.01"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  eye: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  chat: '<path d="M21 11.5a8.4 8.4 0 0 1-12.4 7.4L3 21l2.1-5.6A8.4 8.4 0 1 1 21 11.5z"/>',
  bell: '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.7 21a2 2 0 0 1-3.4 0"/>',
  table: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18M9 4v16"/>',
  route: '<circle cx="6" cy="19" r="2"/><circle cx="18" cy="5" r="2"/><path d="M8 19h7a3 3 0 0 0 0-6H9a3 3 0 0 1 0-6h7"/>',
  upload: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12"/>',
  swap: '<path d="M17 3l4 4-4 4M3 7h18M7 21l-4-4 4-4M21 17H3"/>',
  list: '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
  store: '<path d="M3 9l1-5h16l1 5M4 9v11h16V9M9 20v-6h6v6"/>',
  tool: '<path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.5 2.5-2.4-.6-.6-2.4z"/>',
};
const svg = (k) => `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${ICON[k]}</svg>`;
const FEATURES = [
  ["report", "Daily SFA Reports", "built", "SKU, Pivot and Hourly BC & SV reports straight from raw SFA exports — no manual merging; every file checked against its own banner."],
  ["eye", "Preview & approve", "built", "See every page before it's saved; then save, save & send, or discard. Scheduled runs wait for your approval."],
  ["bot", "SFA Automation", "beta", "One login, many parallel tabs: fills each report's filters, exports, validates and imports — then builds the reports."],
  ["clock", "Scheduler", "beta", "Run times, run days, pause and skip-today; keeps running through Windows Task Scheduler when Chimera is closed."],
  ["chat", "WhatsApp Delivery", "beta", "Sends each manager's pages to the right chats through your own WhatsApp, with captions and a delivery log."],
  ["bell", "Notifications & help", "built", "A bell for runs, approvals and deliveries, per-screen help, and recent activity on the Overview."],
  ["table", "Combined SKU + Invoice", "built", "Route-wise outlet report anchored to the previous visit date."],
  ["route", "Last Visit Sales Summary", "built", "Checks the rep really worked the previous visit day before building the report."],
  ["upload", "Import Data", "built", "Itinerary, territories, invoices, sales orders, outlet master and SKU sales, with real counts and timestamps."],
  ["swap", "Visit State Update", "planned", "Bulk Mandatory ↔ Optional changes in SFA, dry run first, with an audit log."],
  ["list", "Sequence Update", "planned", "Bulk outlet sequence changes in SFA using the same safe run pattern."],
  ["store", "Outlet Master & Maintenance", "planned", "Outlet records and housekeeping tools in one place."],
];
const FLOW = [["Log in once", "One SFA session"], ["Download", "Parallel tabs"], ["Check", "Every file validated"],
              ["Build", "SKU · Pivot · Hourly"], ["Review", "Preview & approve"], ["Deliver", "Folder + WhatsApp"]];

function openAppAbout() {
  const badge = { built: ["BUILT", "b-built"], beta: ["BETA", "b-beta"], planned: ["PLANNED", "b-planned"] };
  openModal({ title: "About Chimera", size: "lg app-about", body: `
    <div class="aa-hero">
      <div class="aa-logo"><img src="assets/chimera-logo.png" alt="Chimera"></div>
      <h1>C H I M E R A</h1>
      <p>A sales-operations desk for SFA — downloads the day's reports automatically, checks every file, builds the
         SKU, Pivot and Hourly reports, and delivers them, all from one desktop app.</p>
    </div>
    <div class="aa-body">
      <p class="aa-intro">Data lives on this PC in Chimera's own SQLite database &mdash; no server, no cloud account. Chimera works with
        SFA and WhatsApp through a browser, exactly as you would, and whether data arrives from an automatic download or a
        file you pick, it goes through the same checks, the same snapshot rules and the same run history &mdash; so the
        numbers stay consistent however they got there.</p>
      <div class="aa-title">How a run works</div>
      <div class="aa-flow">${FLOW.map(([t, s], i) => `<div class="aa-step"><span>${i + 1}</span><b>${t}</b><small>${s}</small></div>`).join("")}</div>
      <div class="aa-title">What's inside</div>
      <div class="aa-grid">${FEATURES.map(([ic, t, st, d]) => `
        <div class="aa-card ${st}"><div class="aa-card-head"><span class="aa-ic">${svg(ic)}</span><b>${t}</b><span class="aa-badge ${badge[st][1]}">${badge[st][0]}</span></div>
          <p>${d}</p></div>`).join("")}</div>
      <p class="aa-legend"><span class="aa-badge b-built">BUILT</span> in use &nbsp; <span class="aa-badge b-beta">BETA</span> built and tested, awaiting first live runs &nbsp; <span class="aa-badge b-planned">PLANNED</span> not active yet</p>
      <div class="aa-foot">Crafted by Binusha &middot; Full-Stack Software Engineer &middot; <button class="link-btn" onclick="closeModal();switchView('about')">About &amp; Support</button></div>
    </div>`, footer: "" });
}
document.querySelector(".sidebar-brand").addEventListener("click", openAppAbout);
document.querySelector(".sidebar-brand").setAttribute("title", "About Chimera");
