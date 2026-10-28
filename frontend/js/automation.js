// automation.js — SFA Automation screen: report profiles, pop-up editors,
// test runs, live run monitor, schedule & control, run history.
// Relies on escapeHtml(), localToday(), openPathButton() from main.js.

const AU = { ov: null, draft: null, poll: null, monitorOpen: false, testOpen: false, busyModal: false };
const API = () => window.pywebview.api;
const STEP_ORDER = ["login", "open", "filters", "search", "export", "validate", "import"];
const STEP_LABEL = { login: "Log in", open: "Open", filters: "Filters", search: "Search", export: "Export", validate: "Validate", import: "Import" };
const STATUS_CHIP = {
  done: ["chip-ok", "Done"], empty: ["chip-ok", "No data"], failed: ["chip-missing", "Failed"], retrying: ["chip-warn", "Retrying"],
  running: ["chip-warn", "Running"], queued: ["chip-grey", "Queued"], cancelled: ["chip-grey", "Cancelled"],
  success: ["chip-ok", "Success"], partial: ["chip-warn", "Partial"], interrupted: ["chip-grey", "Interrupted"], busy: ["chip-grey", "Busy"],
};
const chip = (status) => { const c = STATUS_CHIP[status] || ["chip-grey", status || "—"]; return `<span class="chip ${c[0]}">${escapeHtml(c[1])}</span>`; };
const esc = (s) => escapeHtml(s == null ? "" : s);

// ---------------------------------------------------------------- pop-ups
function openModal({ title, body, footer = "", size = "", onClose = null }) {
  const m = document.getElementById("modal");
  m.className = "modal " + size;
  document.getElementById("modal-title").textContent = title;
  document.getElementById("modal-body").innerHTML = body;
  document.getElementById("modal-footer").innerHTML = footer;
  document.getElementById("modal-backdrop").style.display = "flex";
  AU.onClose = onClose;
}
function closeModal(force = false) {
  if (AU.busyModal && !force) return;
  document.getElementById("modal-backdrop").style.display = "none";
  const cb = AU.onClose; AU.onClose = null;
  if (cb) cb();
}
document.getElementById("modal-x").addEventListener("click", () => closeModal());
document.getElementById("modal-backdrop").addEventListener("mousedown", (e) => { if (e.target.id === "modal-backdrop") closeModal(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && document.getElementById("modal-backdrop").style.display === "flex") closeModal(); });
function confirmModal(title, text, okLabel, onOk, danger = false) {
  openModal({ title, body: `<p>${text}</p>`,
    footer: `<button class="btn-outline" data-act="cancel">Cancel</button><button class="btn-primary" data-act="ok" ${danger ? 'style="background:#B5121B"' : ""}>${esc(okLabel)}</button>` });
  const f = document.getElementById("modal-footer");
  f.querySelector('[data-act="cancel"]').onclick = () => closeModal();
  f.querySelector('[data-act="ok"]').onclick = async () => { closeModal(); await onOk(); };
}

// ---------------------------------------------------------------- load
async function loadAutomation() {
  try {
    if (typeof API().get_automation_overview !== "function")
      throw new Error("OUTDATED");
    AU.ov = await API().get_automation_overview();
  } catch (err) {
    const outdated = String(err).includes("OUTDATED") || String(err).includes("not a function");
    document.getElementById("au-profile-rows").innerHTML = `<tr><td colspan="8"><div class="problem-box" style="margin:6px 0;">${outdated
      ? "Chimera's screens are newer than its backend: <b>backend\\api.py</b> (and the other backend files from the latest update) still need replacing. Replace them, then restart Chimera."
      : "Couldn't load SFA Automation: " + esc(String(err))}</div></td></tr>`;
    return;
  }
  renderStats(); renderProfiles(); renderSchedule(); loadHistory();
  if (AU.ov.live_active) startPolling();
}
window.loadAutomation = loadAutomation;

function renderStats() {
  const ov = AU.ov, en = ov.profiles.filter(p => p.enabled);
  const broken = en.filter(p => p.problems.length).length;
  document.getElementById("au-stat-enabled").textContent = `${en.length}/${ov.profiles.length}`;
  document.getElementById("au-stat-enabled-foot").innerHTML = broken ? `<span class="error">${broken} need attention</span>` : "ready to run";
  const lr = ov.last_run;
  document.getElementById("au-stat-last").innerHTML = lr ? `${esc(lr.started_at.slice(11, 16))} ${chip(lr.status)}` : "Never";
  document.getElementById("au-stat-last-foot").textContent = lr ? `${lr.report_date} · ${lr.message || lr.trigger}` : "no runs yet";
  document.getElementById("au-stat-next").textContent = ov.settings.paused === "1" ? "Paused" : (ov.next_run || "—");
  document.getElementById("au-stat-next-foot").textContent = ov.settings.paused === "1" ? "resume on the Schedules screen" : (ov.next_job || "nothing scheduled");
  document.getElementById("au-stat-tabs").textContent = ov.settings.parallel_tabs;
  document.getElementById("au-login-warning").innerHTML = ov.login_ready ? "" :
    `<div class="problem-box">SFA login isn't set up yet — Chimera can't download anything until it is.
      <button class="link-btn" onclick="switchView('settings')">Open Settings → SFA Connection</button></div>`;
}

function filterSummary(p) {
  const parts = p.filters.filter(f => f.type === "date" || (f.value && f.value.length)).map(f => {
    let v = f.type === "date" ? ({ report_date: "report date", today: "today", yesterday: "yesterday" }[f.date_mode] || f.value)
          : Array.isArray(f.value) ? f.value.join(", ") : f.value;
    return `${esc(f.label)} = <b>${esc(v)}</b>`;
  });
  return parts.length ? parts.join("<br>") : '<span class="empty-note">no filters</span>';
}

function lastResultFor(pid) {
  const lr = AU.ov.last_run;
  if (!lr) return "";
  const lane = (lr.lanes || []).find(l => l.profile_id === pid);
  return lane ? `${chip(lane.status)}<div class="p-desc">${esc(lr.started_at.slice(11, 16))}${lane.rows != null ? ` · ${lane.rows} rows` : ""}</div>` : "";
}

function renderProfiles() {
  const rows = AU.ov.profiles;
  const body = document.getElementById("au-profile-rows");
  if (!rows.length) { body.innerHTML = `<tr><td colspan="8" class="hint">No report profiles yet — click “+ Add report”.</td></tr>`; return; }
  body.innerHTML = rows.map((p, i) => `
    <tr data-id="${p.id}">
      <td><div class="order-btns"><button data-act="up" ${i === 0 ? "disabled" : ""} title="Move up">▲</button><button data-act="down" ${i === rows.length - 1 ? "disabled" : ""} title="Move down">▼</button></div></td>
      <td><label class="switch2"><input type="checkbox" data-act="enable" ${p.enabled ? "checked" : ""}><span></span></label></td>
      <td><div class="p-name">${esc(p.name)}</div>${p.problems.length ? `<div class="p-desc error">${esc(p.problems[0])}</div>` : (p.description ? `<div class="p-desc">${esc(p.description)}</div>` : "")}</td>
      <td class="url-cell">${p.url ? `<span class="mono">${esc(p.url)}</span>` : '<span class="chip chip-missing">URL needed</span>'}</td>
      <td><span class="chip chip-blue">${esc(p.feeds_label)}</span></td>
      <td style="font-size:12px;">${filterSummary(p)}</td>
      <td>${lastResultFor(p.id)}</td>
      <td><div class="row-actions">
        <button class="icon-action" data-act="edit">Edit</button>
        <button class="icon-action" data-act="test" ${p.problems.length ? "disabled title='Fix the profile first'" : ""}>Test</button>
        <button class="icon-action" data-act="dup" title="Duplicate">Copy</button>
        <button class="icon-action danger" data-act="del" title="Delete">✕</button>
      </div></td>
    </tr>`).join("");
}

document.getElementById("au-profile-rows").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-act]"); if (!btn) return;
  const tr = btn.closest("tr"); const id = Number(tr.dataset.id);
  const p = AU.ov.profiles.find(x => x.id === id);
  const act = btn.dataset.act;
  if (act === "edit") openProfileEditor(p);
  if (act === "test") openTestModal(p);
  if (act === "dup") { const r = await API().duplicate_automation_profile(id); if (r.ok) { await loadAutomation(); openProfileEditor(r.profile); } }
  if (act === "del") confirmModal("Delete report profile", `Delete <b>${esc(p.name)}</b>? This can't be undone.`, "Delete", async () => { await API().delete_automation_profile(id); loadAutomation(); }, true);
  if (act === "up" || act === "down") {
    const ids = AU.ov.profiles.map(x => x.id); const i = ids.indexOf(id); const j = act === "up" ? i - 1 : i + 1;
    [ids[i], ids[j]] = [ids[j], ids[i]]; await API().reorder_automation_profiles(ids); loadAutomation();
  }
});
document.getElementById("au-profile-rows").addEventListener("change", async (e) => {
  if (e.target.dataset.act !== "enable") return;
  const id = Number(e.target.closest("tr").dataset.id);
  await API().set_automation_profile_enabled(id, e.target.checked); loadAutomation();
});

// ---------------------------------------------------------------- profile editor (pop-up)
const BLANK_PROFILE = { id: null, name: "", url: "", feeds_into: "download_only", enabled: true, description: "",
  filters: [{ label: "Date From", type: "date", value: "", date_mode: "report_date", required: true, options: [] },
            { label: "Date To", type: "date", value: "", date_mode: "report_date", required: true, options: [] }],
  search_text: "SEARCH", export_text: "Export", wait_seconds: 60, allow_empty: true };

function openProfileEditor(p) {
  AU.draft = JSON.parse(JSON.stringify(p || BLANK_PROFILE));
  AU.draft.filters.forEach(f => { f.options = f.options || []; });
  const feeds = Object.entries(AU.ov.feeds).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
  openModal({
    title: p ? `Edit report — ${p.name}` : "Add report", size: "lg",
    body: `
      <div id="pe-problems"></div>
      <div class="section-title">Basics</div>
      <div class="grid-2">
        <label class="field">Report name <input id="pe-name" placeholder="e.g. Outlet Wise SKU - Van Selling"></label>
        <label class="field">Feeds into <select id="pe-feeds">${feeds}</select></label>
        <label class="field full">Navigation URL <input id="pe-url" class="mono" placeholder="https://lakmee.salespro.lk/it/reports/..."></label>
        <label class="field full">Description (optional) <input id="pe-desc"></label>
      </div>
      <div class="section-title">Filters
        <span><button class="btn-outline btn-small" id="pe-detect">✦ Detect filters from page</button>
              <button class="btn-outline btn-small" id="pe-add-filter">+ Add filter</button></span></div>
      <p class="hint" style="margin-top:-4px;">Labels must match the SFA screen exactly. Leave a value empty to keep SFA's “Not Selected”.
        Dates follow the run, so scheduled runs always use the right day.</p>
      <div id="pe-detect-area"></div>
      <table class="filter-table"><thead><tr><th style="width:24%">Filter label</th><th style="width:15%">Type</th><th>Value</th><th style="width:70px">Required</th><th style="width:76px"></th></tr></thead>
        <tbody id="pe-filters"></tbody></table>
      <div class="section-title">After filters</div>
      <div class="grid-2">
        <label class="field">Search button text <input id="pe-search"></label>
        <label class="field">Export button text <input id="pe-export"><span>If Export opens a menu, write both: <code>Export &gt; Excel</code></span></label>
        <label class="field">Wait for results (seconds) <input type="number" min="5" max="600" id="pe-wait"></label>
        <label class="field" style="justify-content:flex-end;"><span><input type="checkbox" id="pe-empty"> An empty report (no records) is OK</span></label>
      </div>
      <div class="section-title">Checks Chimera will make on the downloaded file</div>
      <ul class="check-list" id="pe-checks"></ul>`,
    footer: `<label class="left check" style="display:flex;gap:6px;align-items:center;font-size:14px;"><input type="checkbox" id="pe-enabled"> Enabled</label>
             <button class="btn-outline" id="pe-cancel">Cancel</button><button class="btn-primary" id="pe-save">Save profile</button>`,
  });
  const d = AU.draft;
  document.getElementById("pe-name").value = d.name; document.getElementById("pe-feeds").value = d.feeds_into;
  document.getElementById("pe-url").value = d.url; document.getElementById("pe-desc").value = d.description || "";
  document.getElementById("pe-search").value = d.search_text; document.getElementById("pe-export").value = d.export_text;
  document.getElementById("pe-wait").value = d.wait_seconds; document.getElementById("pe-empty").checked = !!d.allow_empty;
  document.getElementById("pe-enabled").checked = !!d.enabled;
  renderFilterRows(); renderChecks();
  ["pe-name", "pe-feeds", "pe-url"].forEach(id => document.getElementById(id).addEventListener("input", renderChecks));
  document.getElementById("pe-add-filter").onclick = () => { d.filters.push({ label: "", type: "dropdown", value: "", date_mode: "", required: false, options: [] }); renderFilterRows(); };
  document.getElementById("pe-detect").onclick = runDetect;
  document.getElementById("pe-cancel").onclick = () => closeModal();
  document.getElementById("pe-save").onclick = saveDraft;
}

function readBasics() {
  const d = AU.draft;
  d.name = document.getElementById("pe-name").value; d.feeds_into = document.getElementById("pe-feeds").value;
  d.url = document.getElementById("pe-url").value.trim(); d.description = document.getElementById("pe-desc").value;
  d.search_text = document.getElementById("pe-search").value; d.export_text = document.getElementById("pe-export").value;
  d.wait_seconds = Number(document.getElementById("pe-wait").value); d.allow_empty = document.getElementById("pe-empty").checked;
  d.enabled = document.getElementById("pe-enabled").checked;
}

function valueEditor(f, i) {
  if (f.type === "date") {
    const modes = [["report_date", "Report date"], ["today", "Today"], ["yesterday", "Yesterday"], ["fixed", "Fixed"]];
    return `<div class="seg">${modes.map(([k, l]) => `<button type="button" data-i="${i}" data-mode="${k}" class="${(f.date_mode || "report_date") === k ? "on" : ""}">${l}</button>`).join("")}</div>
      ${f.date_mode === "fixed" ? `<input type="date" data-i="${i}" data-k="value" value="${esc(f.value)}" style="margin-top:6px;">` : ""}`;
  }
  if (f.type === "toggle") {
    return `<select data-i="${i}" data-k="value"><option value="on" ${f.value !== "off" ? "selected" : ""}>On</option><option value="off" ${f.value === "off" ? "selected" : ""}>Off</option></select>`;
  }
  if (f.type === "multiselect") {
    const vals = Array.isArray(f.value) ? f.value : (f.value ? [f.value] : []);
    const opts = (f.options || []).filter(o => !vals.includes(o));
    const adder = opts.length ? `<select data-i="${i}" data-k="ms-add"><option value="">+ add a value…</option>${opts.map(o => `<option>${esc(o)}</option>`).join("")}</select>`
                              : `<input data-i="${i}" data-k="ms-add-text" placeholder="type a value and press Enter">`;
    return `${adder}<div class="ms-chips">${vals.map((v, j) => `<span class="ms-chip">${esc(v)}<button type="button" data-i="${i}" data-rm="${j}">×</button></span>`).join("") || '<span class="empty-note">left empty (Not Selected)</span>'}</div>`;
  }
  if (f.type === "dropdown" && f.options && f.options.length) {
    const opts = f.options.includes(f.value) || !f.value ? f.options : [f.value].concat(f.options);
    return `<select data-i="${i}" data-k="value"><option value="">— leave empty (Not Selected) —</option>${opts.map(o => `<option ${o === f.value ? "selected" : ""}>${esc(o)}</option>`).join("")}</select>`;
  }
  return `<input data-i="${i}" data-k="value" value="${esc(Array.isArray(f.value) ? f.value.join(", ") : f.value)}" placeholder="leave empty = Not Selected">`;
}

function renderFilterRows() {
  const d = AU.draft, tb = document.getElementById("pe-filters");
  const types = AU.ov.filter_types.map(t => `<option value="${t}">${{ dropdown: "Dropdown", multiselect: "Multi-select", date: "Date", text: "Text", toggle: "Toggle" }[t]}</option>`).join("");
  tb.innerHTML = d.filters.map((f, i) => `
    <tr>
      <td><input data-i="${i}" data-k="label" value="${esc(f.label)}" placeholder="e.g. Report Type"></td>
      <td><select data-i="${i}" data-k="type">${types}</select></td>
      <td>${valueEditor(f, i)}</td>
      <td style="text-align:center;"><input type="checkbox" data-i="${i}" data-k="required" ${f.required ? "checked" : ""} style="width:auto;"></td>
      <td><div class="row-actions"><button type="button" class="icon-action" data-mv="${i}:-1" ${i === 0 ? "disabled" : ""}>▲</button><button type="button" class="icon-action" data-mv="${i}:1" ${i === d.filters.length - 1 ? "disabled" : ""}>▼</button><button type="button" class="icon-action danger" data-del="${i}">✕</button></div></td>
    </tr>`).join("") || `<tr><td colspan="5" class="hint">No filters — the report will be exported with SFA's defaults.</td></tr>`;
  tb.querySelectorAll('select[data-k="type"]').forEach(s => { s.value = d.filters[Number(s.dataset.i)].type; });
  renderChecks();
}

document.addEventListener("input", (e) => {
  const el = e.target; if (!el.closest || !el.closest("#pe-filters") || el.dataset.k === undefined) return;
  const f = AU.draft.filters[Number(el.dataset.i)];
  if (el.dataset.k === "label") { f.label = el.value; renderChecks(); }
  if (el.dataset.k === "value" && el.tagName === "INPUT" && el.type !== "date") { f.value = el.value; renderChecks(); }
});
document.addEventListener("change", (e) => {
  const el = e.target; if (!el.closest || !el.closest("#pe-filters")) return;
  const f = AU.draft.filters[Number(el.dataset.i)]; const k = el.dataset.k;
  if (k === "type") { f.type = el.value; f.value = f.type === "multiselect" ? [] : (f.type === "toggle" ? "on" : ""); f.date_mode = f.type === "date" ? "report_date" : ""; renderFilterRows(); }
  else if (k === "value") { f.value = el.value; renderChecks(); if (el.type === "date") renderChecks(); }
  else if (k === "required") f.required = el.checked;
  else if (k === "ms-add" && el.value) { f.value = (Array.isArray(f.value) ? f.value : []).concat([el.value]); renderFilterRows(); }
});
document.addEventListener("keydown", (e) => {
  const el = e.target; if (!el.closest || !el.closest("#pe-filters") || el.dataset.k !== "ms-add-text" || e.key !== "Enter") return;
  e.preventDefault(); const f = AU.draft.filters[Number(el.dataset.i)]; const v = el.value.trim();
  if (v) { f.value = (Array.isArray(f.value) ? f.value : []).concat([v]); renderFilterRows(); }
});
document.addEventListener("click", (e) => {
  const el = e.target; if (!el.closest || !el.closest("#pe-filters")) return;
  const d = AU.draft;
  if (el.dataset.mode) { const f = d.filters[Number(el.dataset.i)]; f.date_mode = el.dataset.mode; if (f.date_mode !== "fixed") f.value = ""; renderFilterRows(); }
  if (el.dataset.rm !== undefined) { const f = d.filters[Number(el.dataset.i)]; f.value.splice(Number(el.dataset.rm), 1); renderFilterRows(); }
  if (el.dataset.del !== undefined) { d.filters.splice(Number(el.dataset.del), 1); renderFilterRows(); }
  if (el.dataset.mv) { const [i, dir] = el.dataset.mv.split(":").map(Number); const j = i + dir; [d.filters[i], d.filters[j]] = [d.filters[j], d.filters[i]]; renderFilterRows(); }
});

function renderChecks() {
  const ul = document.getElementById("pe-checks"); if (!ul) return;
  const feeds = document.getElementById("pe-feeds").value;
  const items = [];
  if (feeds !== "download_only") items.push(`The file is a <b>${esc(AU.ov.feeds[feeds])}</b> export`);
  AU.draft.filters.forEach(f => {
    if (!f.label) return;
    if (f.type === "date") items.push(`<b>${esc(f.label)}</b> in the file banner = ${{ report_date: "the run's report date", today: "today", yesterday: "yesterday", fixed: esc(f.value || "(pick a date)") }[f.date_mode || "report_date"]}`);
    else if ((f.type === "dropdown" || f.type === "text") && f.value) items.push(`<b>${esc(f.label)}</b> in the file banner = “${esc(f.value)}”`);
  });
  items.push("Banner rows and the totals row are removed; numbers stored as text are converted");
  ul.innerHTML = items.map(t => `<li><span class="tick">✓</span><span>${t}</span></li>`).join("");
  // live problems
  const probs = [];
  const url = document.getElementById("pe-url").value.trim();
  if (!document.getElementById("pe-name").value.trim()) probs.push("Give the report a name.");
  if (!url) probs.push("Paste the report's navigation URL.");
  else if (!/^https?:\/\//.test(url)) probs.push("The URL must start with https://");
  AU.draft.filters.forEach(f => { if (!f.label.trim()) probs.push("A filter row has no label."); });
  document.getElementById("pe-problems").innerHTML = probs.length ? `<div class="problem-box">${probs.map(esc).join("<br>")}</div>` : "";
}

async function saveDraft() {
  readBasics();
  const btn = document.getElementById("pe-save"); btn.disabled = true;
  const r = await API().save_automation_profile(AU.draft);
  btn.disabled = false;
  if (!r.ok) { document.getElementById("pe-problems").innerHTML = `<div class="problem-box">${esc(r.message)}</div>`; document.getElementById("modal-body").scrollTop = 0; return; }
  closeModal(true); await loadAutomation();
}

async function runDetect() {
  readBasics();
  const area = document.getElementById("pe-detect-area");
  if (!/^https?:\/\//.test(AU.draft.url)) { area.innerHTML = `<div class="problem-box">Paste the report's URL first.</div>`; return; }
  area.innerHTML = `<div class="ok-box" style="background:#f6f6f7;color:#444;"><span class="spinner"></span> &nbsp;Logging in to SFA and reading the filters on that page… this takes about 20–40 seconds. Nothing in SFA is changed.</div>`;
  AU.busyModal = true; document.getElementById("pe-detect").disabled = true;
  let r;
  try { r = await API().detect_profile_filters(AU.draft.url, false); }
  catch (err) { r = { ok: false, message: String(err) }; }
  AU.busyModal = false; document.getElementById("pe-detect").disabled = false;
  if (!r.ok) { area.innerHTML = `<div class="problem-box">${esc(r.message)}</div>`; return; }
  const have = new Set(AU.draft.filters.map(f => f.label.toLowerCase()));
  area.innerHTML = `
    <div class="ok-box">${esc(r.message)} Tick the ones this report should set, then “Apply”. Options seen on the page become pick-lists.</div>
    <div class="detect-list">${r.fields.map((f, i) => `
      <label><input type="checkbox" data-di="${i}" ${have.has(f.label.toLowerCase()) || f.type === "date" ? "checked" : ""}>
        <b>${esc(f.label)}</b> <span class="chip chip-grey">${esc(f.type)}</span>
        <span class="muted">${f.options.length ? esc(f.options.slice(0, 4).join(", ")) + (f.options.length > 4 ? ` … (${f.options.length})` : "") : (f.type === "toggle" ? (f.toggled ? "currently on" : "currently off") : "")}</span></label>`).join("")}
    </div>
    <div class="form-row" style="margin-top:10px;"><button class="btn-primary btn-small" id="pe-detect-apply">Apply</button>
      <button class="btn-outline btn-small" id="pe-detect-close">Dismiss</button>
      ${r.screenshot ? `<button class="btn-outline btn-small" id="pe-detect-shot">View page screenshot</button>` : ""}</div>`;
  document.getElementById("pe-detect-close").onclick = () => { area.innerHTML = ""; };
  if (r.screenshot) document.getElementById("pe-detect-shot").onclick = () => { const w = document.createElement("img"); w.src = "data:image/jpeg;base64," + r.screenshot; w.style.cssText = "max-width:100%;border:1px solid #eee;border-radius:8px;margin-top:10px;"; area.appendChild(w); };
  document.getElementById("pe-detect-apply").onclick = () => {
    area.querySelectorAll("input[data-di]:checked").forEach(cb => {
      const f = r.fields[Number(cb.dataset.di)];
      const existing = AU.draft.filters.find(x => x.label.toLowerCase() === f.label.toLowerCase());
      if (existing) { existing.type = f.type; existing.options = f.options || []; if (f.type === "date" && !existing.date_mode) existing.date_mode = "report_date"; }
      else AU.draft.filters.push({ label: f.label, type: f.type, value: f.type === "multiselect" ? [] : (f.type === "toggle" ? (f.toggled ? "on" : "off") : ""),
                                   date_mode: f.type === "date" ? "report_date" : "", required: f.type === "date", options: f.options || [] });
    });
    // keep the page's order
    const order = r.fields.map(f => f.label.toLowerCase());
    AU.draft.filters.sort((a, b) => (order.indexOf(a.label.toLowerCase()) + 1 || 999) - (order.indexOf(b.label.toLowerCase()) + 1 || 999));
    area.innerHTML = `<div class="ok-box">Filters updated — choose the values below, then save.</div>`;
    renderFilterRows();
  };
}

// ---------------------------------------------------------------- live monitor
function stepper(lane) {
  return `<div class="stepper">${STEP_ORDER.map(s => {
    const st = lane.steps[s];
    const icon = st === "done" ? "✓" : st === "failed" ? "!" : st === "skipped" ? "–" : "";
    return `<div class="st ${st}"><span class="dot">${icon}</span>${STEP_LABEL[s]}</div>`; }).join("")}</div>`;
}
function laneHtml(l) {
  const secs = l.elapsed ? `${l.elapsed}s` : "";
  return `<div class="lane ${l.status}">
    <div><div class="lane-name">${esc(l.name)}</div><div class="lane-msg">${esc(l.message || (l.status === "queued" ? "Waiting for a free tab…" : ""))}</div></div>
    ${stepper(l)}
    <div class="lane-meta">${chip(l.status)}${l.max_attempts > 1 && l.attempt > 1 ? `<div>try ${l.attempt}/${l.max_attempts}</div>` : ""}
      <div>${l.rows != null ? `${l.rows} rows` : ""} ${secs}</div>
      ${l.file ? `<div>${openPathButton(l.file, "file")}</div>` : ""}${l.screenshot && l.status === "failed" ? `<div>${openPathButton(l.screenshot, "screenshot")}</div>` : ""}</div>
  </div>`;
}
function logHtml(log) {
  return log.slice(-150).map(x => `<div class="lv-${x.level}">${esc(x.t)} ${x.lane ? `<span class="who">[${esc(x.lane)}]</span> ` : ""}${esc(x.msg)}</div>`).join("");
}

function openRunNowModal() {
  const en = AU.ov.profiles;
  openModal({ title: "Run SFA downloads now", body: `
      <div class="grid-2">
        <label class="field">Report date <input type="date" id="rn-date" value="${localToday()}"></label>
        <label class="field" style="justify-content:flex-end;"><span><input type="checkbox" id="rn-show"> Show the browser while it runs</span></label>
      </div>
      <div class="section-title" style="margin-top:16px;">Reports to download</div>
      <div class="detect-list">${en.map(p => `<label><input type="checkbox" class="rn-p" value="${p.id}" ${p.enabled && !p.problems.length ? "checked" : ""} ${p.problems.length ? "disabled" : ""}>
        <b>${esc(p.name)}</b><span class="muted">${p.problems.length ? `<span class="error">${esc(p.problems[0])}</span>` : esc(p.feeds_label)}</span></label>`).join("")}</div>
      <p class="hint" style="margin-top:10px;">Up to ${esc(AU.ov.settings.parallel_tabs)} tabs at once. Afterwards the ${esc(AU.ov.settings.generate_list.join(", ") || "no")} report(s) are built${AU.ov.settings.wa_auto_send === "1" ? " and sent to WhatsApp" : ""}.</p>`,
    footer: `<button class="btn-outline" id="rn-cancel">Cancel</button><button class="btn-primary" id="rn-go">▶ Start</button>` });
  document.getElementById("rn-cancel").onclick = () => closeModal();
  document.getElementById("rn-go").onclick = async () => {
    const ids = [...document.querySelectorAll(".rn-p:checked")].map(c => Number(c.value));
    if (!ids.length) return;
    const r = await API().start_automation_run(document.getElementById("rn-date").value, ids, document.getElementById("rn-show").checked);
    if (!r.ok) { document.getElementById("modal-body").insertAdjacentHTML("afterbegin", `<div class="problem-box">${esc(r.message)}</div>`); return; }
    closeModal(true); openMonitor();
  };
}
function needOverview(fn) {
  return () => {
    if (AU.ov) return fn();
    openModal({ title: "SFA Automation isn't ready", body: `<div class="problem-box">The automation data couldn't be loaded — see the message on the screen (usually a backend file from the latest update is missing). Restart Chimera after replacing the files.</div>`,
      footer: `<button class="btn-primary" onclick="closeModal()">OK</button>` });
  };
}
document.getElementById("au-run-btn").addEventListener("click", needOverview(() => openRunNowModal()));
document.getElementById("au-add-btn").addEventListener("click", needOverview(() => openProfileEditor(null)));
document.getElementById("au-live-open").addEventListener("click", openMonitor);

function openMonitor() {
  AU.monitorOpen = true;
  openModal({ title: "Live run", size: "xl", body: `<div id="mon-body"><span class="spinner"></span> Starting…</div>`,
    footer: `<button class="btn-outline left" id="mon-cancel">Cancel run</button><button class="btn-outline" id="mon-min">Minimise</button>`,
    onClose: () => { AU.monitorOpen = false; } });
  document.getElementById("mon-min").onclick = () => closeModal();
  document.getElementById("mon-cancel").onclick = async () => { await API().cancel_automation_run(); };
  startPolling();
}

function renderMonitor(s) {
  const el = document.getElementById("mon-body"); if (!el) return;
  const lanes = s.lanes || [];
  const total = lanes.length * STEP_ORDER.length || 1;
  const done = lanes.reduce((a, l) => a + STEP_ORDER.filter(x => ["done", "skipped"].includes(l.steps[x]) || (["failed", "cancelled", "empty"].includes(l.status))).length, 0);
  const pct = s.active ? Math.min(99, Math.round(100 * done / total)) : 100;
  const phase = { starting: "Starting…", logging_in: "Logging in to SFA…", downloading: "Downloading reports…", generating: "Building reports…", sending: "Sending to WhatsApp…", done: "Finished" }[s.phase] || s.phase;
  let gen = "";
  const rr = s.result && s.result.report_run;
  if (rr) gen = `<div class="section-title">Reports built</div><ul class="result-list">${rr.reports.map(r => r.ok
      ? `<li>${chip("done")} <b>${esc(r.label)}</b> — ${(r.outputs || [{ pdf: r.pdf }]).map(o => openPathButton(o.pdf, o.style ? `PDF (${o.style})` : "PDF")).join(" · ")} · ${r.images.length} WhatsApp image(s)</li>`
      : `<li>${chip("failed")} <b>${esc(r.label)}</b> — ${esc(r.reason)}</li>`).join("")}</ul>${rr.folder ? `<p class="hint">${openPathButton(rr.folder, "Open output folder")}</p>` : ""}`;
  el.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;">
      <div><b>${esc(phase)}</b> <span class="hint">· report date ${esc(s.report_date || "")} · ${esc(s.trigger || "")}${s.slot ? " " + esc(s.slot) : ""}${s.run_id ? ` · run #${s.run_id}` : ""}${s.data_bytes != null ? ` · data ${fmtBytes(s.data_bytes)}${s.data_saver ? ` (data saver on${s.blocked ? `, ${s.blocked} skipped` : ""})` : ""}` : ""}</span></div>
      <div>${s.result ? chip(s.result.status) : ""}</div></div>
    <div class="progress"><div style="width:${pct}%"></div></div>
    ${s.result && s.result.message && !s.result.ok ? `<div class="problem-box">${esc(s.result.message)}</div>` : ""}
    <div class="lanes">${lanes.map(laneHtml).join("")}</div>
    ${gen}
    <div class="section-title">Log</div><div class="log-box" id="mon-log">${logHtml(s.log || [])}</div>`;
  const lg = document.getElementById("mon-log"); lg.scrollTop = lg.scrollHeight;
  const c = document.getElementById("mon-cancel"); if (c) c.style.display = s.active ? "" : "none";
  const m = document.getElementById("mon-min"); if (m) m.textContent = s.active ? "Minimise" : "Close";
}

function startPolling() {
  if (AU.poll) return;
  const tick = async () => {
    let s; try { s = await API().get_live_run(); } catch (e) { return; }
    const banner = document.getElementById("au-live-banner");
    banner.style.display = s.active && !s.test ? "flex" : "none";
    if (s.active) document.getElementById("au-live-text").textContent =
      `${s.trigger === "scheduled" ? "Scheduled" : "Manual"} run in progress — ${(s.lanes || []).filter(l => ["done", "empty"].includes(l.status)).length}/${(s.lanes || []).length} reports done`;
    document.getElementById("au-cancel-btn").disabled = !s.active;
    if (AU.monitorOpen) renderMonitor(s);
    if (AU.testOpen) renderTest(s);
    if (!s.active && !AU.monitorOpen && !AU.testOpen) { clearInterval(AU.poll); AU.poll = null; loadAutomation(); }
    if (!s.active && AU.wasActive) { AU.wasActive = false; loadAutomationQuiet(); }
    if (s.active) AU.wasActive = true;
  };
  AU.poll = setInterval(tick, 1000); tick();
}
async function loadAutomationQuiet() { try { AU.ov = await API().get_automation_overview(); renderStats(); renderProfiles(); loadHistory(); } catch (e) {} }

// ---------------------------------------------------------------- test run (pop-up)
function openTestModal(p) {
  AU.testOpen = true; AU.testProfile = p;
  openModal({ title: `Test run — ${p.name}`, size: "lg", body: `
      <div class="grid-2">
        <label class="field">Run for date <input type="date" id="tr-date" value="${localToday()}"></label>
        <label class="field" style="justify-content:flex-end;"><span><input type="checkbox" id="tr-show" checked> Show the browser window</span></label>
      </div>
      <p class="hint">A test run logs in, fills the filters, exports and checks the file — but never imports anything into Chimera. Screenshots are kept for every step.</p>
      <div id="tr-body"></div>`,
    footer: `<button class="btn-outline" id="tr-close">Close</button><button class="btn-primary" id="tr-go">▶ Run test</button>`,
    onClose: () => { AU.testOpen = false; } });
  document.getElementById("tr-close").onclick = () => closeModal();
  document.getElementById("tr-go").onclick = async () => {
    const r = await API().start_profile_test(p.id, document.getElementById("tr-date").value, document.getElementById("tr-show").checked);
    if (!r.ok) { document.getElementById("tr-body").innerHTML = `<div class="problem-box">${esc(r.message)}</div>`; return; }
    document.getElementById("tr-go").disabled = true; startPolling();
  };
}
function renderTest(s) {
  const el = document.getElementById("tr-body"); if (!el || !s.test) return;
  const l = (s.lanes || [])[0]; if (!l) { el.innerHTML = `<span class="spinner"></span> Starting…`; return; }
  const checks = (l.checks || []).map(c => `<li><span class="${c.ok ? "tick" : "cross"}">${c.ok ? "✓" : "✗"}</span><span><b>${esc(c.what)}</b>: file says “${esc(c.got)}”, expected “${esc(c.expect)}”</span></li>`).join("");
  el.innerHTML = `${laneHtml(l)}
    ${checks ? `<div class="section-title">File checks</div><ul class="check-list">${checks}</ul>` : ""}
    ${(l.shots || []).length ? `<div class="section-title">Screenshots</div><div class="shots">${l.shots.map(x => `<figure><img src="data:image/jpeg;base64,${x.img}" onclick="this.requestFullscreen && this.requestFullscreen()"><figcaption>${esc(x.step.replace(/_/g, " "))}</figcaption></figure>`).join("")}</div>` : ""}
    <div class="section-title">Log</div><div class="log-box">${logHtml(s.log || [])}</div>`;
  const go = document.getElementById("tr-go"); if (go) { go.disabled = !!s.active; go.textContent = s.active ? "Running…" : "▶ Run test again"; }
}

// ---------------------------------------------------------------- run settings
const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
function renderSchedule() {
  const s = AU.ov.settings;
  const paused = s.paused === "1", skipping = s.skip_date === localToday();
  document.getElementById("au-state-chip").className = "chip " + (paused ? "chip-grey" : skipping ? "chip-warn" : "chip-ok");
  document.getElementById("au-state-chip").textContent = paused ? "Paused" : skipping ? "Skipping today" : "Running on schedule";
  document.getElementById("au-state-hint").textContent = paused ? "No scheduled runs will happen." : (AU.ov.next_run ? `Next: ${AU.ov.next_job ? AU.ov.next_job + " · " : ""}${AU.ov.next_run}` : "Nothing scheduled.");
  document.getElementById("au-parallel").value = s.parallel_tabs; document.getElementById("au-parallel-val").textContent = s.parallel_tabs; parallelWarn(s.parallel_tabs);
  document.getElementById("au-retries").value = s.retries; document.getElementById("au-timeout").value = s.page_timeout_seconds;
  document.getElementById("au-headless").checked = s.headless === "1";
  document.querySelectorAll(".au-gen").forEach(c => { c.checked = s.generate_list.includes(c.value); });
}
async function saveAuto(values, resultId) {
  const r = await API().save_automation_settings(values);
  const el = resultId && document.getElementById(resultId);
  if (el) {
    let html = `<p class="${r.ok ? "hint" : "error"}">${esc(r.message)}</p>`;
    if (r.task_sync) html += `<ul class="result-list">${r.task_sync.messages.map(m => `<li>${esc(m)}</li>`).join("")}</ul>`;
    el.innerHTML = html;
  }
  if (r.ok) await loadAutomationQuiet().then(renderSchedule);
  return r;
}
document.getElementById("au-cancel-btn").addEventListener("click", () => confirmModal("Cancel current run", "Stop the run that's in progress? Reports already downloaded stay imported.", "Cancel run", async () => { await API().cancel_automation_run(); }, true));
function parallelWarn(v) {
  document.getElementById("au-parallel-warn").innerHTML = Number(v) > 8
    ? `<span style="color:#9a6a00;">${v} tabs is heavy: each tab is a full SFA page (~150–250 MB of memory). Use it only if the PC and SFA stay responsive; reports that fail retry on their own.</span>`
    : "Each enabled report gets its own tab; extra reports wait for a free tab.";
}
document.getElementById("au-parallel").addEventListener("input", (e) => { document.getElementById("au-parallel-val").textContent = e.target.value; parallelWarn(e.target.value); });
document.getElementById("au-behaviour-save").addEventListener("click", () => saveAuto({
  parallel_tabs: document.getElementById("au-parallel").value, retries: document.getElementById("au-retries").value,
  page_timeout_seconds: document.getElementById("au-timeout").value, headless: document.getElementById("au-headless").checked ? "1" : "0",
  generate_kinds: [...document.querySelectorAll(".au-gen:checked")].map(c => c.value).join(","),
}, "au-behaviour-result"));

// ---------------------------------------------------------------- history
async function loadHistory() {
  const el = document.getElementById("au-history");
  let runs = []; try { runs = await API().get_automation_runs(25); } catch (e) { return; }
  AU.runs = runs;
  el.innerHTML = runs.length ? `<table class="report-table"><thead><tr><th>#</th><th>Started</th><th>Report date</th><th>Trigger</th><th>Status</th><th>Reports</th><th>Data</th><th>Summary</th><th></th></tr></thead><tbody>${runs.map(r => `
    <tr><td>${r.id}</td><td>${esc((r.started_at || "").replace("T", " "))}</td><td>${esc(r.report_date)}</td><td>${esc(r.trigger)}${r.slot ? " " + esc(r.slot) : ""}</td><td>${chip(r.status)}</td>
      <td style="font-size:12px;">${(r.lanes || []).map(l => `${["done", "empty"].includes(l.status) ? "✓" : "✗"} ${esc(l.name)}`).join("<br>")}</td>
      <td style="font-size:12px;white-space:nowrap;">${r.data_bytes != null ? fmtBytes(r.data_bytes) + (r.data_saver ? ' <span class="hint" title="Data saver was on">· saver</span>' : "") : '<span class="hint">—</span>'}</td>
      <td style="font-size:12px;">${esc(r.message || "")}</td><td><button class="icon-action" data-run="${r.id}">Details</button></td></tr>`).join("")}</tbody></table>`
    : `<p class="hint" style="padding:18px;">No automation runs yet.</p>`;
}
document.getElementById("au-history").addEventListener("click", (e) => {
  const id = e.target.dataset.run; if (!id) return;
  const r = AU.runs.find(x => String(x.id) === id);
  openModal({ title: `Run #${r.id} — ${r.report_date}`, size: "lg",
    body: `<p class="hint">${esc((r.started_at || "").replace("T", " "))} → ${esc((r.finished_at || "").replace("T", " "))} · ${esc(r.trigger)}${r.slot ? " " + esc(r.slot) : ""} · ${chip(r.status)}</p>
           <div class="lanes">${(r.lanes || []).map(laneHtml).join("") || "<p class='hint'>No reports in this run.</p>"}</div>`,
    footer: `<button class="btn-outline" onclick="closeModal()">Close</button>` });
});

// ---------------------------------------------------------------- tabs
document.getElementById("au-tabs").addEventListener("click", (e) => {
  const t = e.target.dataset.tab; if (!t) return;
  document.querySelectorAll("#au-tabs .tab").forEach(b => b.classList.toggle("active", b.dataset.tab === t));
  ["profiles", "schedule", "whatsapp", "history"].forEach(x => { document.getElementById("au-panel-" + x).style.display = x === t ? "block" : "none"; });
  if (t === "history") loadHistory();
});
