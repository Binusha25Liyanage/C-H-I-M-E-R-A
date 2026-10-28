// schedules.js — Schedules screen (jobs, week view, holiday calendar, firing
// log) and the top-bar clock. Relies on openModal/closeModal/confirmModal,
// esc() and API() from automation.js, and localToday() from main.js.

const SC = { ov: null, week: 0, tab: "jobs", draft: null };
const SC_DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const SC_TYPE_CLASS = { sfa_downloads: "wk-dl", master_sync: "wk-md", visit_state: "wk-vs", sequence: "wk-sq" };

function scFmtDate(iso, opts) { return new Date(iso).toLocaleDateString("en-GB", opts || { weekday: "short", day: "2-digit", month: "short" }); }
function scTime(iso) { return iso.slice(11, 16); }

async function loadSchedules(week) {
  if (week !== undefined) SC.week = week;
  try {
    if (typeof API().get_schedule_overview !== "function") throw new Error("OUTDATED");
    SC.ov = await API().get_schedule_overview(SC.week);
  } catch (err) {
    const outdated = String(err).includes("OUTDATED") || String(err).includes("not a function");
    document.getElementById("sc-jobs").innerHTML = `<div class="problem-box" style="margin:14px 18px;">${outdated
      ? "The Schedules screen needs the latest backend files (backend\\api.py, scheduler.py, schedule_store.py). Replace them, then restart Chimera."
      : "Couldn't load schedules: " + esc(String(err))}</div>`;
    return;
  }
  renderScTop(); renderScSwitches(); renderScJobs(); renderScWeek(); renderScHolidays(); renderScFires();
}
window.loadSchedules = loadSchedules;

// ---------------------------------------------------------------- top
function clockText(c) {
  if (c.offset_s == null) return ["Not checked", "chip-grey"];
  if (c.level === "ok") return ["Matches SFA", "chip-ok"];
  return [`${Math.abs(c.offset_s) >= 60 ? Math.round(Math.abs(c.offset_s) / 60) + " min" : Math.abs(c.offset_s) + " s"} ${c.offset_s > 0 ? "behind" : "ahead"}`, c.level === "bad" ? "chip-missing" : "chip-warn"];
}
function renderScTop() {
  const o = SC.ov, c = o.controls;
  const paused = c.paused === "1", skipping = c.skip_date === o.today;
  document.getElementById("sc-next").textContent = paused ? "Paused" : (o.next ? o.next.label : "Nothing scheduled");
  document.getElementById("sc-next-foot").textContent = paused ? "turn automation back on to resume" : (o.next ? o.next.job : "add a job below");
  document.getElementById("sc-state").textContent = paused ? "Paused" : skipping ? "Skipping today" : "Running";
  document.getElementById("sc-state-foot").textContent = `${o.jobs.filter(j => j.enabled).length} of ${o.jobs.length} job(s) on`;
  const [ct, cc] = clockText(o.clock);
  document.getElementById("sc-clock").innerHTML = `${esc(o.now.slice(11, 16))} <span class="chip ${cc}" style="vertical-align:middle;">${esc(ct)}</span>`;
  document.getElementById("sc-clock-foot").textContent = o.clock.checked_at ? `${o.clock.tz} · checked ${o.clock.checked_at.replace("T", " ").slice(0, 16)}` : o.clock.tz;
  const nh = o.holidays.find(h => h.day >= o.today);
  document.getElementById("sc-hol").textContent = nh ? scFmtDate(nh.day) : "None set";
  document.getElementById("sc-hol-foot").textContent = nh ? nh.name : "add them on the Holidays tab";

  document.getElementById("sc-paused-switch").checked = paused;
  document.getElementById("sc-paused-label").textContent = paused ? "Paused" : "Running";
  const chipEl = document.getElementById("sc-state-chip");
  chipEl.className = "chip " + (paused ? "chip-grey" : skipping ? "chip-warn" : "chip-ok");
  chipEl.textContent = paused ? "Paused" : skipping ? "Skipping today" : "Running on schedule";
  document.getElementById("sc-state-hint").textContent = paused ? "No scheduled job will run." : skipping ? "Nothing runs today; back to normal tomorrow." : (o.next ? `Next: ${o.next.job} · ${o.next.label}` : "");
  document.getElementById("sc-skip-btn").textContent = skipping ? "Undo skip today" : "Skip today";
  document.getElementById("sc-clock-warning").innerHTML = ["warn", "bad"].includes(o.clock.level)
    ? `<div class="${o.clock.level === "bad" ? "problem-box" : "warn-box"}">${esc(o.clock.message)}</div>` : "";

  const ts = document.getElementById("sc-task-switch");
  ts.checked = c.use_task_scheduler === "1"; ts.disabled = !o.is_windows;
  document.getElementById("sc-task-times").textContent = o.is_windows
    ? (o.task_times.length ? `Times registered: ${o.task_times.join(", ")}.` : "") : "Available when Chimera runs on Windows.";
}

async function scSave(values) {
  const r = await API().save_automation_settings(values);
  let html = `<p class="${r.ok ? "hint" : "error"}">${esc(r.message)}</p>`;
  if (r.task_sync) html += `<ul class="result-list">${r.task_sync.messages.map(m => `<li>${esc(m)}</li>`).join("")}</ul>`;
  document.getElementById("sc-task-result").innerHTML = html;
  await loadSchedules(); refreshClock();
}
document.getElementById("sc-paused-switch").addEventListener("change", (e) => scSave({ paused: e.target.checked ? "1" : "0" }));
document.getElementById("sc-skip-btn").addEventListener("click", () => scSave({ skip_date: SC.ov.controls.skip_date === SC.ov.today ? "" : SC.ov.today }));
document.getElementById("sc-task-switch").addEventListener("change", (e) => scSave({ use_task_scheduler: e.target.checked ? "1" : "0" }));
document.getElementById("sc-clock-btn").addEventListener("click", async (e) => {
  const b = e.target; b.disabled = true; b.textContent = "Checking…";
  const r = await API().check_clock();
  b.disabled = false; b.textContent = "Check clock with SFA";
  if (!r.ok) document.getElementById("sc-clock-warning").innerHTML = `<div class="problem-box">${esc(r.message)}</div>`;
  else { await loadSchedules(); if (r.level === "ok") document.getElementById("sc-clock-warning").innerHTML = `<div class="ok-box">${esc(r.message)}</div>`; }
  refreshClock();
});

// ---------------------------------------------------------------- per-process switches
const SC_SWITCHES = [
  ["auto_sfa_downloads", "SFA report downloads", "Scheduled download jobs run at their times."],
  ["auto_build_reports", "Build reports after downloading", "SKU, Pivot and Hourly are built when a download finishes."],
  ["wa_auto_send", "Send to WhatsApp automatically", "Reports go to the chats in your delivery rules once saved or approved."],
  ["auto_master_sync", "Master data sync", "Itinerary, Territories and Outlet Master from SFA.", true],
  ["auto_visit_state", "Visit state update", "Mandatory / Optional route changes in SFA.", true],
  ["auto_sequence", "Sequence update", "Outlet visit order in SFA.", true],
];
function renderScSwitches() {
  const sw = SC.ov.switches, paused = SC.ov.controls.paused === "1";
  document.getElementById("sc-switches").innerHTML = SC_SWITCHES.map(([k, label, hint, soon]) => `
    <div class="auto-item ${soon ? "soon" : ""} ${!soon && sw[k] === "1" && !paused ? "on" : ""}">
      <div><b>${esc(label)}</b>${soon ? ' <em class="nav-soon dark">Soon</em>' : ""}<p class="hint">${esc(hint)}</p></div>
      <label class="switch2"><input type="checkbox" data-switch="${k}" ${!soon && sw[k] === "1" ? "checked" : ""} ${soon ? "disabled" : ""}><span></span></label>
    </div>`).join("");
}
document.getElementById("sc-switches").addEventListener("change", async (e) => {
  const k = e.target.dataset.switch; if (!k) return;
  await API().save_automation_settings({ [k]: e.target.checked ? "1" : "0" });
  await loadSchedules(); refreshClock();
});

// ---------------------------------------------------------------- jobs
function renderScJobs() {
  const jobs = SC.ov.jobs;
  document.getElementById("sc-jobs").innerHTML = jobs.length ? `<table class="report-table"><thead><tr>
      <th style="width:70px">On</th><th>Job</th><th>When</th><th>Next run</th><th>Last fired</th><th style="width:250px"></th></tr></thead><tbody>${jobs.map(j => {
      const lf = j.last_fire;
      const ok = lf && /^(success|done)/.test(lf.outcome || "");
      return `<tr>
        <td><label class="switch2"><input type="checkbox" data-toggle="${j.id}" ${j.enabled ? "checked" : ""}><span></span></label></td>
        <td><b>${esc(j.name)}</b><div class="hint" style="font-size:12px;">${esc(j.type_label)}${j.options.report_day === "yesterday" ? " · for yesterday" : ""}${j.options.profile_ids && j.options.profile_ids.length ? ` · ${j.options.profile_ids.length} report(s)` : ""}${j.skip_holidays ? "" : " · runs on holidays"}</div></td>
        <td>${esc(j.summary)}</td>
        <td>${j.enabled ? esc(j.next_label || "—") : '<span class="hint">off</span>'}</td>
        <td style="font-size:12px;">${lf ? `${esc(lf.day)} ${esc(lf.slot)}<div class="${ok ? "hint" : "error"}">${esc((lf.outcome || "started").slice(0, 70))}</div>` : '<span class="hint">never</span>'}</td>
        <td style="text-align:right;white-space:nowrap;"><button class="icon-action" data-run="${j.id}">Run now</button>
          <button class="icon-action" data-edit="${j.id}">Edit</button><button class="icon-action danger" data-del="${j.id}">Delete</button></td></tr>`; }).join("")}</tbody></table>`
    : `<p class="hint" style="padding:18px;">No scheduled jobs. Add one to run SFA downloads automatically.</p>`;
}
document.getElementById("sc-jobs").addEventListener("click", async (e) => {
  const t = e.target;
  if (t.dataset.edit) openJobEditor(SC.ov.jobs.find(j => String(j.id) === t.dataset.edit));
  if (t.dataset.del) {
    const j = SC.ov.jobs.find(x => String(x.id) === t.dataset.del);
    confirmModal("Delete job", `Delete “${esc(j.name)}”? Its schedule stops; past runs stay in the history.`, "Delete", async () => { await API().delete_schedule_job(j.id); loadSchedules(); refreshClock(); }, true);
  }
  if (t.dataset.run) {
    const j = SC.ov.jobs.find(x => String(x.id) === t.dataset.run);
    const r = await API().run_schedule_job_now(j.id);
    if (!r.ok) { confirmModal("Couldn't start", esc(r.message), "OK", async () => {}); return; }
    if (typeof openMonitor === "function") openMonitor();
  }
});
document.getElementById("sc-jobs").addEventListener("change", async (e) => {
  const id = e.target.dataset.toggle; if (!id) return;
  await API().set_schedule_job_enabled(Number(id), e.target.checked); loadSchedules(); refreshClock();
});
document.getElementById("sc-add").addEventListener("click", () => openJobEditor(null));

// ---------------------------------------------------------------- job editor
function openJobEditor(job) {
  const types = SC.ov.job_types;
  SC.draft = job ? JSON.parse(JSON.stringify(job)) : {
    name: "", job_type: "sfa_downloads", frequency: "weekly", times_list: ["10:00"], weekdays: "1111110",
    month_days_list: ["1"], once_date: SC.ov.today, enabled: true, skip_holidays: true, options: { report_day: "today", profile_ids: [] } };
  const d = SC.draft;
  d.options = d.options || {}; d.options.profile_ids = d.options.profile_ids || [];
  openModal({ title: job ? `Edit job — ${job.name}` : "New scheduled job", size: "lg", body: `
    <div class="grid-2">
      <label class="field">Job name <input type="text" id="je-name" value="${esc(d.name)}" placeholder="e.g. Morning downloads"></label>
      <label class="field">What to run <select id="je-type">${Object.entries(types).map(([k, t]) =>
        `<option value="${k}" ${k === d.job_type ? "selected" : ""} ${t.available ? "" : "disabled"}>${esc(t.label)}${t.available ? "" : " — coming soon"}</option>`).join("")}</select></label>
    </div>
    <p class="hint" id="je-type-hint" style="margin:6px 0 0;"></p>
    <div class="section-title">How often</div>
    <div class="seg seg-lg" id="je-freq">${["daily", "weekly", "monthly", "once"].map(f => `<button data-f="${f}" class="${d.frequency === f ? "on" : ""}">${f[0].toUpperCase() + f.slice(1)}</button>`).join("")}</div>
    <div id="je-freq-body" style="margin-top:12px;"></div>
    <div class="section-title">At these times (24-hour)</div>
    <div id="je-times" class="time-chips"></div>
    <div class="form-row" style="margin-top:10px;"><label>Add a time <input type="time" id="je-time-input"></label><button class="btn-outline" id="je-time-add">Add time</button></div>
    <div id="je-opts"></div>
    <div class="toggle-row" style="margin-top:14px;"><div><b>Skip holidays</b><p class="hint">Don't run on dates in the holiday calendar.</p></div>
      <label class="switch2"><input type="checkbox" id="je-hol" ${d.skip_holidays ? "checked" : ""}><span></span></label></div>
    <div class="toggle-row"><div><b>Job is on</b></div>
      <label class="switch2"><input type="checkbox" id="je-on" ${d.enabled ? "checked" : ""}><span></span></label></div>
    <div id="je-error"></div>`,
    footer: `<button class="btn-outline" id="je-cancel">Cancel</button><button class="btn-primary" id="je-save">Save job</button>` });
  const typeHint = () => { document.getElementById("je-type-hint").textContent = (types[d.job_type] || {}).hint || ""; };
  typeHint(); renderFreqBody(); renderJeTimes(); renderJeOpts();
  document.getElementById("je-type").onchange = (e) => { d.job_type = e.target.value; typeHint(); renderJeOpts(); };
  document.getElementById("je-freq").onclick = (e) => {
    const f = e.target.dataset.f; if (!f) return; d.frequency = f;
    document.querySelectorAll("#je-freq button").forEach(b => b.classList.toggle("on", b.dataset.f === f)); renderFreqBody(); };
  document.getElementById("je-time-add").onclick = () => {
    const v = document.getElementById("je-time-input").value; if (!v) return;
    if (!d.times_list.includes(v)) d.times_list.push(v); d.times_list.sort(); renderJeTimes(); };
  document.getElementById("je-times").onclick = (e) => {
    const t = e.target.dataset.rm; if (!t) return; d.times_list = d.times_list.filter(x => x !== t); renderJeTimes(); };
  document.getElementById("je-cancel").onclick = () => closeModal();
  document.getElementById("je-save").onclick = saveJobDraft;
}
function renderFreqBody() {
  const d = SC.draft, el = document.getElementById("je-freq-body");
  if (d.frequency === "daily") el.innerHTML = `<p class="hint">Every day, including weekends (holidays are still skipped if that's on).</p>`;
  if (d.frequency === "weekly") {
    el.innerHTML = `<div class="day-pills" id="je-days">${SC_DAYS.map((n, i) => `<button class="day-pill ${d.weekdays[i] === "1" ? "on" : ""}" data-day="${i}">${n}</button>`).join("")}</div>`;
    document.getElementById("je-days").onclick = (e) => {
      const i = e.target.dataset.day; if (i === undefined) return;
      const w = d.weekdays.split(""); w[i] = w[i] === "1" ? "0" : "1"; d.weekdays = w.join(""); renderFreqBody(); };
  }
  if (d.frequency === "monthly") {
    const sel = new Set(d.month_days_list);
    el.innerHTML = `<div class="month-grid" id="je-mdays">${Array.from({ length: 31 }, (_, i) => String(i + 1)).concat(["last"]).map(x =>
      `<button class="${sel.has(x) ? "on" : ""} ${x === "last" ? "wide" : ""}" data-md="${x}">${x === "last" ? "Last day" : x}</button>`).join("")}</div>
      <p class="hint" style="margin-top:8px;">Days 29&ndash;31 run on the last day in shorter months (e.g. 31 &rarr; 30 Nov, 28 Feb).</p>`;
    document.getElementById("je-mdays").onclick = (e) => {
      const x = e.target.dataset.md; if (!x) return;
      sel.has(x) ? sel.delete(x) : sel.add(x); d.month_days_list = [...sel]; renderFreqBody(); };
  }
  if (d.frequency === "once") {
    el.innerHTML = `<label class="field" style="max-width:220px;">Date <input type="date" id="je-once" value="${esc(d.once_date || SC.ov.today)}" min="${SC.ov.today}"></label>`;
    document.getElementById("je-once").onchange = (e) => { d.once_date = e.target.value; };
  }
}
function renderJeTimes() {
  document.getElementById("je-times").innerHTML = SC.draft.times_list.map(t => `<span class="time-chip">${esc(t)}<button data-rm="${esc(t)}" title="Remove">×</button></span>`).join("")
    || '<span class="empty-note">No times yet — add at least one.</span>';
}
function renderJeOpts() {
  const d = SC.draft, el = document.getElementById("je-opts");
  if (d.job_type !== "sfa_downloads") { el.innerHTML = ""; return; }
  const all = !d.options.profile_ids.length;
  el.innerHTML = `<div class="section-title">SFA downloads</div>
    <div class="grid-2">
      <label class="field">Report date <select id="je-rday">
        <option value="today" ${d.options.report_day !== "yesterday" ? "selected" : ""}>Today (the day it runs)</option>
        <option value="yesterday" ${d.options.report_day === "yesterday" ? "selected" : ""}>Yesterday (e.g. an early-morning catch-up)</option></select></label>
      <label class="field">Reports <select id="je-pmode"><option value="all" ${all ? "selected" : ""}>All enabled report profiles</option>
        <option value="pick" ${all ? "" : "selected"}>Only the ones I choose</option></select></label>
    </div>
    <div class="detect-list" id="je-profiles" style="margin-top:10px;${all ? "display:none;" : ""}">${SC.ov.profiles.map(p =>
      `<label><input type="checkbox" class="je-p" value="${p.id}" ${d.options.profile_ids.includes(p.id) ? "checked" : ""}><b>${esc(p.name)}</b>${p.enabled ? "" : '<span class="muted">profile is off</span>'}</label>`).join("")}</div>`;
  document.getElementById("je-rday").onchange = (e) => { d.options.report_day = e.target.value; };
  document.getElementById("je-pmode").onchange = (e) => {
    document.getElementById("je-profiles").style.display = e.target.value === "pick" ? "" : "none";
    if (e.target.value === "all") { d.options.profile_ids = []; document.querySelectorAll(".je-p").forEach(c => { c.checked = false; }); } };
  document.getElementById("je-profiles").onchange = () => { d.options.profile_ids = [...document.querySelectorAll(".je-p:checked")].map(c => Number(c.value)); };
}
async function saveJobDraft() {
  const d = SC.draft;
  if (document.getElementById("je-pmode") && document.getElementById("je-pmode").value === "pick" && !d.options.profile_ids.length) {
    document.getElementById("je-error").innerHTML = `<div class="problem-box">Choose at least one report, or pick “All enabled report profiles”.</div>`; return;
  }
  const payload = { id: d.id, name: document.getElementById("je-name").value, job_type: d.job_type, frequency: d.frequency,
    times: d.times_list, weekdays: d.weekdays, month_days: d.month_days_list, once_date: d.once_date,
    enabled: document.getElementById("je-on").checked, skip_holidays: document.getElementById("je-hol").checked, options: d.options };
  const r = await API().save_schedule_job(payload);
  if (!r.ok) { document.getElementById("je-error").innerHTML = `<div class="problem-box">${esc(r.message)}</div>`; return; }
  closeModal(true);
  await loadSchedules(); refreshClock();
  if (r.task_sync && !r.task_sync.ok) document.getElementById("sc-task-result").innerHTML = `<ul class="result-list">${r.task_sync.messages.map(m => `<li>${esc(m)}</li>`).join("")}</ul>`;
}

// ---------------------------------------------------------------- week
function renderScWeek() {
  const o = SC.ov, start = new Date(o.week_start + "T00:00:00");
  const end = new Date(start); end.setDate(end.getDate() + 6);
  document.getElementById("sc-week-title").textContent = SC.week === 0 ? "This week" : `${scFmtDate(start, { day: "2-digit", month: "short" })} – ${scFmtDate(end, { day: "2-digit", month: "short", year: "numeric" })}`;
  const now = o.now;
  const cols = SC_DAYS.map((n, i) => {
    const d = new Date(start); d.setDate(d.getDate() + i);
    const iso = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    const hol = o.week_holidays[iso];
    const items = o.week.filter(x => x.at.slice(0, 10) === iso);
    return `<div class="wk-col ${iso === o.today ? "today" : ""}">
      <div class="wk-head"><b>${n}</b> ${d.getDate()} ${scFmtDate(d, { month: "short" })}${hol ? `<div class="wk-hol" title="${esc(hol)}">${esc(hol)}</div>` : ""}</div>
      ${items.map(x => {
        const skipped = x.holiday || x.skipped_today || x.switched_off || o.controls.paused === "1";
        return `<div class="wk-item ${SC_TYPE_CLASS[x.type] || ""} ${x.at <= now ? "past" : ""} ${skipped ? "skipped" : ""}" title="${esc(x.job)} at ${esc(x.slot)}${x.holiday ? " — skipped (holiday)" : ""}">
          <b>${esc(x.slot)}</b> ${esc(x.job)}</div>`; }).join("") || '<div class="wk-empty">—</div>'}
    </div>`; }).join("");
  document.getElementById("sc-week").innerHTML = cols;
}
document.getElementById("sc-week-prev").addEventListener("click", () => loadSchedules(SC.week - 1));
document.getElementById("sc-week-next").addEventListener("click", () => loadSchedules(SC.week + 1));
document.getElementById("sc-week-now").addEventListener("click", () => loadSchedules(0));

// ---------------------------------------------------------------- holidays
function renderScHolidays() {
  const list = SC.ov.holidays, byYear = {};
  list.forEach(h => { (byYear[h.day.slice(0, 4)] = byYear[h.day.slice(0, 4)] || []).push(h); });
  document.getElementById("sc-hol-list").innerHTML = list.length ? Object.keys(byYear).sort().map(y => `
    <div class="section-title" style="padding:0 18px;">${y} <span class="hint">(${byYear[y].length})</span></div>
    <table class="report-table compact"><tbody>${byYear[y].map(h => `<tr class="${h.day < SC.ov.today ? "row-past" : ""}">
      <td style="width:150px;">${esc(scFmtDate(h.day, { weekday: "short", day: "2-digit", month: "short" }))}</td><td>${esc(h.name)}</td>
      <td style="text-align:right;"><button class="icon-action danger" data-hdel="${esc(h.day)}">Remove</button></td></tr>`).join("")}</tbody></table>`).join("")
    : `<p class="hint" style="padding:18px;">No holidays yet.</p>`;
}
async function addHolidayText(text, clear) {
  const r = await API().add_holidays(text);
  document.getElementById("sc-hol-result").innerHTML = `<p class="${r.added ? "hint" : "error"}">${esc(r.message)}</p>`
    + (r.problems.length ? `<ul class="result-list">${r.problems.map(p => `<li class="error">${esc(p)}</li>`).join("")}</ul>` : "");
  if (r.added) { clear(); await loadSchedules(); refreshClock(); }
}
document.getElementById("sc-hol-add").addEventListener("click", () => {
  const d = document.getElementById("sc-hol-date").value, n = document.getElementById("sc-hol-name").value;
  if (!d) { document.getElementById("sc-hol-result").innerHTML = `<p class="error">Choose a date.</p>`; return; }
  addHolidayText(`${d} ${n}`, () => { document.getElementById("sc-hol-date").value = ""; document.getElementById("sc-hol-name").value = ""; });
});
document.getElementById("sc-hol-paste-btn").addEventListener("click", () => {
  const t = document.getElementById("sc-hol-paste").value; if (!t.trim()) return;
  addHolidayText(t, () => { document.getElementById("sc-hol-paste").value = ""; });
});
document.getElementById("sc-hol-list").addEventListener("click", async (e) => {
  const d = e.target.dataset.hdel; if (!d) return;
  await API().delete_holiday(d); loadSchedules(); refreshClock();
});

// ---------------------------------------------------------------- firing log
function renderScFires() {
  const f = SC.ov.fires, names = Object.fromEntries(SC.ov.jobs.map(j => [j.id, j.name]));
  document.getElementById("sc-fires").innerHTML = f.length ? `<table class="report-table"><thead><tr><th>Started</th><th>Job</th><th>Slot</th><th>By</th><th>Outcome</th></tr></thead><tbody>${f.map(x => `
    <tr><td>${esc(x.fired_at.replace("T", " "))}</td><td>${esc(names[x.job_id] || `Job #${x.job_id} (deleted)`)}</td><td>${esc(x.day)} ${esc(x.slot)}</td>
      <td>${x.trigger === "scheduled" ? "Schedule" : esc(x.trigger)}</td><td style="font-size:12px;">${esc(x.outcome || "running…")}</td></tr>`).join("")}</tbody></table>`
    : `<p class="hint" style="padding:18px;">Nothing has run on a schedule yet. Each scheduled start is listed here, with its result.</p>`;
}

// ---------------------------------------------------------------- tabs
document.getElementById("sc-tabs").addEventListener("click", (e) => {
  const t = e.target.dataset.tab; if (!t) return; SC.tab = t;
  document.querySelectorAll("#sc-tabs .tab").forEach(b => b.classList.toggle("active", b.dataset.tab === t));
  ["jobs", "week", "holidays", "log"].forEach(x => { document.getElementById("sc-panel-" + x).style.display = x === t ? "block" : "none"; });
});

// ---------------------------------------------------------------- top-bar clock
const CLK = { info: null };
function tickClock() {
  const n = new Date();
  document.getElementById("tb-clock-time").textContent = n.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
}
async function refreshClock() {
  try { CLK.info = await API().get_clock(); } catch (e) { return; }
  const c = CLK.info;
  const dot = document.getElementById("tb-clock-dot");
  dot.className = "clk-dot " + ({ ok: "ok", warn: "warn", bad: "bad" }[c.level] || "");
  document.getElementById("tb-clock-next").textContent = c.paused ? "Automation paused" : c.next ? `Next: ${c.next.job} · ${c.next.label}` : "Nothing scheduled";
  document.getElementById("tb-clock").title = `${c.message}\nClick to open Schedules.`;
}
window.refreshClock = refreshClock;
tickClock(); setInterval(tickClock, 1000);
window.addEventListener("pywebviewready", () => { refreshClock(); setInterval(refreshClock, 60000); });
