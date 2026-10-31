// loadings.js — Distributor Loadings: distributors (filters + Daily / On request),
// prepare (read-only) runs, the Approve Loadings pop-up (one by one or many at
// once) and confirm runs. Relies on openModal/closeModal/esc/API/chip from
// automation.js, copyText/escapeHtml/localToday from main.js.

const LD = { ov: null, sel: new Set(), filter: "all", q: "", poll: null, wasActive: false, monOpen: false,
             approve: null, approveSel: new Set(), current: null, draft: null };
const LD_STEP_LABEL = { login: "Log in", open: "Open", filters: "Filters", search: "Search", find: "Find pending",
                        read: "Read loading", check: "Check", confirm: "Confirm", verify: "Verify" };
const LD_STATE = {
  pending_approval: ["chip-warn", "Waiting for approval"], nothing_pending: ["chip-grey", "Nothing pending"],
  failed: ["chip-missing", "Failed"], confirming: ["chip-warn", "Confirming"], confirmed: ["chip-ok", "Confirmed"],
  confirm_failed: ["chip-missing", "Confirm failed"], changed: ["chip-missing", "Changed in SFA"],
  skipped: ["chip-grey", "Skipped"], superseded: ["chip-grey", "Replaced"],
};
const ldChip = (st) => { const c = LD_STATE[st] || ["chip-grey", st || "—"]; return `<span class="chip ${c[0]}">${esc(c[1])}</span>`; };
const ldNum = (v) => { const n = Number(String(v || "").replace(/,/g, "")); return isNaN(n) ? 0 : n; };
const ldFmt = (n) => Number(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

// ---------------------------------------------------------------- load + list
async function loadLoadings() {
  try {
    if (typeof API().get_loadings_overview !== "function") throw new Error("OUTDATED");
    LD.ov = await API().get_loadings_overview();
  } catch (err) {
    const outdated = String(err).includes("OUTDATED") || String(err).includes("not a function");
    document.getElementById("ld-table").innerHTML = `<div class="problem-box" style="margin:14px 18px;">${outdated
      ? "This screen needs the latest backend files (backend\\api.py, loading_store.py, loading_automation.py). Replace them, then restart Chimera."
      : "Couldn't load Distributor Loadings: " + esc(String(err))}</div>`;
    return;
  }
  renderLdTop(); renderLdTable(); renderLdSettings(); renderLdRuns();
  if (LD.ov.live_active) ldStartPoll();
}
window.loadLoadings = loadLoadings;

function renderLdTop() {
  const s = LD.ov.stats;
  document.getElementById("ld-st-daily").innerHTML = `${s.daily}<span class="stat-of"> / ${s.total}</span>`;
  document.getElementById("ld-st-daily-foot").textContent = s.daily ? "prepared by the morning schedule" : "set distributors to Daily below";
  document.getElementById("ld-st-pending").textContent = s.pending;
  document.getElementById("ld-st-confirmed").textContent = s.confirmed_today;
  document.getElementById("ld-st-nothing").textContent = s.nothing_today;
  document.getElementById("ld-approve-banner").innerHTML = s.pending ? `<div class="approve-banner">
      <span><b>${s.pending} loading(s) are waiting for your approval.</b> They were read from SFA but are NOT confirmed.</span>
      <button class="btn-primary" onclick="openApproveLoadings()">Review &amp; approve</button></div>` : "";
  const badge = document.getElementById("nav-load-badge");
  badge.textContent = s.pending; badge.style.display = s.pending ? "" : "none";
}

function ldFiltered() {
  const q = LD.q.toLowerCase();
  return LD.ov.distributors.filter(d => (LD.filter === "all" || d.frequency === LD.filter)
    && (!q || d.name.toLowerCase().includes(q) || (d.code || "").toLowerCase().includes(q) || (d.area || "").toLowerCase().includes(q)));
}

function ldFilterSummary(d) {
  const shown = d.filters.filter(f => f.type !== "date").map(f => `${f.label}: ${Array.isArray(f.value) ? f.value.join(", ") : f.value || "—"}`);
  const dates = d.filters.filter(f => f.type === "date").map(f => `${f.label}: ${f.date_mode === "fixed" ? f.value : (f.date_mode || "today").replace("_", " ")}`);
  return shown.concat(dates).join(" · ") || "No filters yet";
}

function renderLdTable() {
  const rows = ldFiltered();
  document.getElementById("ld-count").textContent = `${rows.length} of ${LD.ov.distributors.length} distributor(s)`;
  const all = rows.length && rows.every(d => LD.sel.has(d.id));
  document.getElementById("ld-table").innerHTML = LD.ov.distributors.length ? `<table class="report-table ld-table"><thead><tr>
      <th style="width:40px"><input type="checkbox" id="ld-all" ${all ? "checked" : ""}></th><th style="width:190px">Loading</th><th>Distributor</th>
      <th>Filters</th><th style="width:200px">Last result</th><th style="width:250px"></th></tr></thead><tbody>${rows.map(d => {
        const last = d.last;
        return `<tr class="${d.problems.length ? "row-warn" : ""}">
          <td><input type="checkbox" class="ld-sel" value="${d.id}" ${LD.sel.has(d.id) ? "checked" : ""}></td>
          <td><label class="radio sm"><input type="radio" name="ld-f-${d.id}" data-freq="daily" data-id="${d.id}" ${d.frequency === "daily" ? "checked" : ""}> Daily</label>
              <label class="radio sm"><input type="radio" name="ld-f-${d.id}" data-freq="request" data-id="${d.id}" ${d.frequency === "request" ? "checked" : ""}> On request</label></td>
          <td><b>${esc(d.name)}</b>${d.code ? `<div class="hint" style="font-size:12px;">${esc(d.code)}</div>` : ""}${d.area ? `<div class="hint" style="font-size:12px;">${esc(d.area)}</div>` : ""}</td>
          <td style="font-size:12px;">${d.problems.length ? `<span class="error">${esc(d.problems[0])}</span>` : esc(ldFilterSummary(d))}</td>
          <td style="font-size:12px;">${last ? `${ldChip(last.state)}<div class="hint" style="font-size:11px;margin-top:3px;">${esc((last.at || "").replace("T", " ").slice(0, 16))}${last.loading_number ? " · " + esc(last.loading_number) : ""}</div>` : '<span class="hint">never</span>'}</td>
          <td style="text-align:right;white-space:nowrap;"><button class="icon-action" data-ld-edit="${d.id}">Edit</button>
            <button class="icon-action" data-ld-test="${d.id}" title="Read this distributor's pending loading now (never confirms)">Prepare</button>
            <button class="icon-action" data-ld-copy="${d.id}">Copy</button><button class="icon-action danger" data-ld-del="${d.id}">✕</button></td></tr>`; }).join("")}</tbody></table>`
    : `<div class="empty-state"><b>No distributors yet.</b><p class="hint">Add your first distributor: its View Loadings filters (Distributor, Territory…) and whether it needs a loading every day.</p>
        <button class="btn-primary" onclick="openDistributorEditor(null)">+ Add distributor</button></div>`;
  renderLdBulk();
}

function renderLdBulk() {
  const n = LD.sel.size, el = document.getElementById("ld-bulk");
  el.style.display = n ? "flex" : "none";
  el.innerHTML = `<b>${n} selected</b>
    <button class="btn-primary btn-small" id="ld-prep-sel">&#9654; Prepare selected now</button>
    <button class="btn-outline btn-small" data-bulk-freq="daily">Set as Daily</button>
    <button class="btn-outline btn-small" data-bulk-freq="request">Set as On request</button>
    <button class="link-btn" id="ld-desel">Clear selection</button>
    <span class="hint">${Math.min(n, Number(LD.ov.settings.loading_tabs))} at a time; the rest wait for a free tab.</span>`;
}

function renderLdSettings() {
  const s = LD.ov.settings;
  document.getElementById("ld-url").value = s.loading_url;
  document.getElementById("ld-tabs").value = s.loading_tabs; document.getElementById("ld-tabs-val").textContent = s.loading_tabs;
  document.getElementById("ld-jobs").innerHTML = LD.ov.jobs.length
    ? `<ul class="result-list">${LD.ov.jobs.map(j => `<li>${j.enabled ? "✓" : "○"} <b>${esc(j.name)}</b> — ${esc(j.summary)}</li>`).join("")}</ul>`
    : `<p class="hint">No morning schedule yet — Daily distributors are only prepared when you press "Prepare today's loadings".</p>`;
}

function renderLdRuns() {
  const r = LD.ov.runs;
  document.getElementById("ld-runs").innerHTML = r.length ? `<table class="report-table"><thead><tr><th>#</th><th>Started</th><th>Run</th><th>By</th><th>Result</th><th></th></tr></thead><tbody>${r.map(x => `
      <tr><td>${x.id}</td><td>${esc(x.started_at.replace("T", " "))}</td><td>${x.kind === "prepare" ? "Prepare (read only)" : "Confirm"}</td><td>${esc(x.trigger)}</td>
        <td style="font-size:12px;">${chip({ done: "success", failed: "failed", cancelled: "cancelled", running: "running" }[x.status] || x.status)} ${esc(x.message || "")}</td>
        <td style="text-align:right;"><button class="icon-action" data-ld-run="${x.id}">Details</button></td></tr>`).join("")}</tbody></table>`
    : `<p class="hint" style="padding:18px;">Nothing has run yet.</p>`;
}

// list events
document.getElementById("ld-search").addEventListener("input", (e) => { LD.q = e.target.value; renderLdTable(); });
document.getElementById("ld-freq-filter").addEventListener("click", (e) => {
  const f = e.target.dataset.f; if (!f) return; LD.filter = f;
  document.querySelectorAll("#ld-freq-filter button").forEach(b => b.classList.toggle("on", b.dataset.f === f)); renderLdTable(); });
document.getElementById("ld-table").addEventListener("change", async (e) => {
  const t = e.target;
  if (t.id === "ld-all") { ldFiltered().forEach(d => t.checked ? LD.sel.add(d.id) : LD.sel.delete(d.id)); renderLdTable(); return; }
  if (t.classList.contains("ld-sel")) { t.checked ? LD.sel.add(Number(t.value)) : LD.sel.delete(Number(t.value)); renderLdBulk(); return; }
  if (t.dataset.freq) { await API().set_distributor_frequency([Number(t.dataset.id)], t.dataset.freq); loadLoadings(); }
});
document.getElementById("ld-table").addEventListener("click", async (e) => {
  const t = e.target;
  const find = (id) => LD.ov.distributors.find(d => String(d.id) === id);
  if (t.dataset.ldEdit) openDistributorEditor(find(t.dataset.ldEdit));
  if (t.dataset.ldCopy) { const r = await API().duplicate_distributor(Number(t.dataset.ldCopy)); if (r.ok) { await loadLoadings(); openDistributorEditor(r.distributor); } }
  if (t.dataset.ldDel) { const d = find(t.dataset.ldDel);
    confirmModal("Delete distributor", `Delete “${esc(d.name)}” from Chimera? Nothing changes in SFA.`, "Delete", async () => { await API().delete_distributor(d.id); LD.sel.delete(d.id); loadLoadings(); }, true); }
  if (t.dataset.ldTest) startLdPrepare([Number(t.dataset.ldTest)]);
});
document.getElementById("ld-bulk").addEventListener("click", async (e) => {
  const t = e.target;
  if (t.id === "ld-prep-sel") startLdPrepare([...LD.sel]);
  if (t.id === "ld-desel") { LD.sel.clear(); renderLdTable(); }
  if (t.dataset.bulkFreq) { await API().set_distributor_frequency([...LD.sel], t.dataset.bulkFreq); loadLoadings(); }
});
document.getElementById("ld-add").addEventListener("click", () => openDistributorEditor(null));
document.getElementById("ld-prepare-daily").addEventListener("click", () => {
  const n = LD.ov ? LD.ov.stats.daily : 0;
  if (!n) { openModal({ title: "No Daily distributors", body: `<p>Set the distributors that need a loading every day to <b>Daily</b> (radio buttons in the list), or select some and press “Prepare selected now”.</p>`,
                        footer: `<button class="btn-primary" onclick="closeModal()">OK</button>` }); return; }
  startLdPrepare(null);
});
document.getElementById("ld-tabs").addEventListener("input", (e) => { document.getElementById("ld-tabs-val").textContent = e.target.value; });
document.getElementById("ld-tabs").addEventListener("change", async (e) => {
  const r = await API().save_loading_settings({ loading_tabs: e.target.value });
  document.getElementById("ld-settings-result").innerHTML = `<p class="${r.ok ? "hint" : "error"}">${r.ok ? `Up to ${e.target.value} distributors at a time.` : esc(r.message)}</p>`;
});
document.getElementById("ld-url-save").addEventListener("click", async () => {
  const r = await API().save_loading_settings({ loading_url: document.getElementById("ld-url").value.trim() });
  document.getElementById("ld-settings-result").innerHTML = `<p class="${r.ok ? "hint" : "error"}">${r.ok ? "View Loadings URL saved." : esc(r.message)}</p>`;
});
document.getElementById("ld-add-job").addEventListener("click", async () => {
  const r = await API().save_schedule_job({ name: "Morning distributor loadings", job_type: "loadings", frequency: "weekly",
                                            times: ["07:30"], weekdays: "1111110", enabled: true, skip_holidays: true, options: {} });
  if (r.ok) {   // show the new job in Schedules so the time can be changed
    switchView("schedules");
    let tries = 0; const t = setInterval(() => {
      tries++; const job = (typeof SC !== "undefined" && SC.ov) ? SC.ov.jobs.find(x => x.id === (r.job || {}).id) : null;
      if (job) { clearInterval(t); openJobEditor(job); } else if (tries > 20) clearInterval(t); }, 250);
  }
  else openModal({ title: "Couldn't add the schedule", body: `<div class="problem-box">${esc(r.message)}</div>`, footer: `<button class="btn-primary" onclick="closeModal()">OK</button>` });
});
document.getElementById("ld-runs").addEventListener("click", async (e) => {
  const id = e.target.dataset.ldRun; if (!id) return;
  const r = await API().get_loading_items(Number(id));
  openModal({ title: `Loading run #${id}`, size: "lg", body: r.items.length ? `<table class="report-table compact"><thead><tr><th>Distributor</th><th>Loading</th><th>Result</th><th>Details</th></tr></thead><tbody>${r.items.map(it => `
      <tr><td><b>${esc(it.distributor_name)}</b></td><td>${esc(it.loading_number || "—")}</td><td>${ldChip(it.state)}</td><td style="font-size:12px;">${esc(it.message || "")}</td></tr>`).join("")}</tbody></table>`
      : `<p class="hint">No distributors in this run.</p>`,
    footer: `<button class="btn-outline left" id="ld-run-copy">Copy log</button><button class="btn-outline" onclick="closeModal()">Close</button>` });
  document.getElementById("ld-run-copy").onclick = async (ev) => copyText(await API().get_loading_log_text(Number(id)), ev.target);
});

// ---------------------------------------------------------------- distributor editor
function openDistributorEditor(d) {
  const types = (LD.ov && LD.ov.filter_types) || ["dropdown", "multiselect", "date", "text", "toggle"];
  LD.draft = d ? JSON.parse(JSON.stringify(d)) : { name: "", code: "", area: "", frequency: "daily", notes: "", filters: [] };
  const others = (LD.ov ? LD.ov.distributors : []).filter(x => !d || x.id !== d.id);
  openModal({ title: d ? `Edit distributor — ${d.name}` : "Add distributor", size: "lg", body: `
    <div id="de-err"></div>
    <div class="grid-2">
      <label class="field">Distributor name <input id="de-name" value="${esc(LD.draft.name)}" placeholder="as you call it"></label>
      <label class="field">SFA code (optional) <input id="de-code" value="${esc(LD.draft.code)}"></label>
      <label class="field">Area / territory (optional) <input id="de-area" value="${esc(LD.draft.area)}"></label>
      <div class="field">Loading
        <div class="radio-row" style="margin:0;"><label class="radio"><input type="radio" name="de-freq" value="daily" ${LD.draft.frequency === "daily" ? "checked" : ""}> Daily (every morning run)</label>
          <label class="radio"><input type="radio" name="de-freq" value="request" ${LD.draft.frequency === "request" ? "checked" : ""}> Only when I choose</label></div></div>
    </div>
    <div class="section-title">View Loadings filters for this distributor
      <span><button class="btn-outline btn-small" id="de-detect">Detect filters</button>
      ${others.length ? `<select id="de-copy-from" class="btn-small"><option value="">Copy filters from…</option>${others.map(o => `<option value="${o.id}">${esc(o.name)}</option>`).join("")}</select>` : ""}</span></div>
    <p class="hint" style="margin:-4px 0 8px;">Set at least <b>Distributor</b>. Dates: “today” means the day the run happens.</p>
    <div id="de-detect-msg"></div>
    <table class="report-table compact filter-table"><thead><tr><th style="width:24%">Filter (label on the page)</th><th style="width:16%">Type</th><th>Value</th><th style="width:40px"></th></tr></thead>
      <tbody id="de-filters"></tbody></table>
    <button class="link-btn" id="de-add-filter" style="margin-top:8px;">+ Add a filter row</button>
    <label class="field" style="margin-top:12px;">Notes <input id="de-notes" value="${esc(LD.draft.notes || "")}"></label>`,
    footer: `<button class="btn-outline" id="de-cancel">Cancel</button><button class="btn-primary" id="de-save">Save distributor</button>` });
  const draw = () => {
    document.getElementById("de-filters").innerHTML = LD.draft.filters.map((f, i) => `<tr data-i="${i}">
      <td><input class="fi-label" value="${esc(f.label)}"></td>
      <td><select class="fi-type">${types.map(t => `<option ${t === f.type ? "selected" : ""}>${t}</option>`).join("")}</select></td>
      <td>${ldValueInput(f)}</td><td><button class="icon-action danger fi-del" title="Remove">✕</button></td></tr>`).join("")
      || `<tr><td colspan="4" class="hint">No filters yet — press “Detect filters” (Chimera opens View Loadings and lists them), or add rows by hand.</td></tr>`;
  };
  const sync = () => document.querySelectorAll("#de-filters tr[data-i]").forEach(tr => {
    const f = LD.draft.filters[Number(tr.dataset.i)];
    f.label = tr.querySelector(".fi-label").value.trim(); f.type = tr.querySelector(".fi-type").value;
    const v = tr.querySelector(".fi-value"), m = tr.querySelector(".fi-mode");
    if (f.type === "multiselect") f.value = [...tr.querySelectorAll(".fi-ms:checked")].map(c => c.value).concat(v && v.value ? v.value.split(",").map(x => x.trim()).filter(Boolean) : []);
    else if (v) f.value = v.value;
    if (m) f.date_mode = m.value;
  });
  draw();
  ldWirePickers(document.getElementById("de-filters"));
  document.getElementById("de-filters").addEventListener("change", (e) => {
    sync(); if (e.target.classList.contains("fi-type") || e.target.classList.contains("fi-mode")) draw(); });
  document.getElementById("de-filters").addEventListener("click", (e) => {
    if (!e.target.classList.contains("fi-del")) return; sync();
    LD.draft.filters.splice(Number(e.target.closest("tr").dataset.i), 1); draw(); });
  document.getElementById("de-add-filter").onclick = () => { sync(); LD.draft.filters.push({ label: "", type: "dropdown", value: "", options: [] }); draw(); };
  const cf = document.getElementById("de-copy-from");
  if (cf) cf.onchange = () => { const o = others.find(x => String(x.id) === cf.value); if (!o) return;
    LD.draft.filters = JSON.parse(JSON.stringify(o.filters)); draw(); cf.value = "";
    document.getElementById("de-detect-msg").innerHTML = `<div class="ok-box">Copied ${o.filters.length} filter(s) from ${esc(o.name)} — now change the Distributor value.</div>`; };
  document.getElementById("de-detect").onclick = async (e) => {
    sync(); e.target.disabled = true; e.target.textContent = "Opening View Loadings…";
    document.getElementById("de-detect-msg").innerHTML = `<p class="hint"><span class="spinner"></span> Logging in to SFA and reading the filters (nothing is changed)…</p>`;
    const r = await API().detect_loading_filters(document.getElementById("ld-url").value.trim() || null);
    e.target.disabled = false; e.target.textContent = "Detect filters";
    if (!r.ok) { document.getElementById("de-detect-msg").innerHTML = `<div class="problem-box">${esc(r.message)}</div>`; return; }
    const keep = Object.fromEntries(LD.draft.filters.map(f => [f.label.toLowerCase(), f]));
    LD.draft.filters = r.fields.map(f => {
      const old = keep[f.label.toLowerCase()];
      const type = f.type === "date" ? "date" : f.type || "dropdown";
      return { label: f.label, type, options: f.options || f.native_options || [], value: old ? old.value : (type === "multiselect" ? [] : ""),
               date_mode: type === "date" ? (old && old.date_mode) || "today" : "" };
    });
    // empty optional filters are fine; Distributor is the one that matters
    draw();
    document.getElementById("de-detect-msg").innerHTML = `<div class="ok-box">${esc(r.message)} Choose the values for this distributor (leave the others empty to ignore them).</div>`;
  };
  document.getElementById("de-cancel").onclick = () => closeModal();
  document.getElementById("de-save").onclick = async () => {
    sync();
    const payload = { id: d ? d.id : null, name: document.getElementById("de-name").value, code: document.getElementById("de-code").value,
      area: document.getElementById("de-area").value, notes: document.getElementById("de-notes").value,
      frequency: document.querySelector('input[name="de-freq"]:checked').value,
      filters: LD.draft.filters.filter(f => f.label && (f.type === "date" || (Array.isArray(f.value) ? f.value.length : String(f.value || "").trim()))) };
    const r = await API().save_distributor(payload);
    if (!r.ok) { document.getElementById("de-err").innerHTML = `<div class="problem-box">${esc(r.message)}</div>`; document.getElementById("modal-body").scrollTop = 0; return; }
    closeModal(true); loadLoadings();
  };
}
window.openDistributorEditor = openDistributorEditor;

// ---------------------------------------------------------------- type-to-search pickers
const ldChipsHtml = (vals) => vals.length ? vals.map(v => `<span class="ms-chip">${esc(v)}<button type="button" data-unpick="${esc(v)}" title="Remove">✕</button></span>`).join("")
  : `<span class="hint" style="font-size:12px;">Nothing ticked — leave empty to ignore this filter.</span>`;
const ldHi = (text, q) => { if (!q) return esc(text); const i = text.toLowerCase().indexOf(q.toLowerCase());
  return i < 0 ? esc(text) : esc(text.slice(0, i)) + "<mark>" + esc(text.slice(i, i + q.length)) + "</mark>" + esc(text.slice(i + q.length)); };

function ldComboShow(combo, q) {
  const opts = JSON.parse(combo.querySelector(".combo-opts").textContent);
  const ql = (q || "").toLowerCase();
  const hits = opts.filter(o => !ql || o.toLowerCase().includes(ql))
    .sort((a, b) => (b.toLowerCase().startsWith(ql) ? 1 : 0) - (a.toLowerCase().startsWith(ql) ? 1 : 0)).slice(0, 80);
  const list = combo.querySelector(".combo-list");
  list.innerHTML = hits.length ? hits.map((o, i) => `<div class="combo-item ${i === 0 && ql ? "on" : ""}" data-v="${esc(o)}">${ldHi(o, q)}</div>`).join("")
    : `<div class="combo-none">No match — this exact text will be typed into SFA.</div>`;
  list.style.display = "";
}
function ldComboPick(combo, v) {
  const inp = combo.querySelector(".combo-in"); inp.value = v;
  combo.querySelector(".combo-list").style.display = "none";
  combo.querySelector(".combo-clear").style.display = v ? "" : "none";
  inp.dispatchEvent(new Event("change", { bubbles: true }));
}
function ldMsRefresh(pick) {
  const vals = [...pick.querySelectorAll(".fi-ms:checked")].map(c => c.value);
  pick.querySelector(".ms-chips").innerHTML = ldChipsHtml(vals);
}
function ldWirePickers(root) {
  root.addEventListener("focusin", (e) => { if (e.target.classList.contains("combo-in")) ldComboShow(e.target.closest(".combo"), ""); });
  root.addEventListener("input", (e) => {
    const t = e.target;
    if (t.classList.contains("combo-in")) { ldComboShow(t.closest(".combo"), t.value); t.closest(".combo").querySelector(".combo-clear").style.display = t.value ? "" : "none"; }
    if (t.classList.contains("ms-search")) {
      const pick = t.closest(".ms-pick"), q = t.value.toLowerCase(); let n = 0;
      pick.querySelectorAll(".ms-opt").forEach(l => { const show = !q || l.textContent.toLowerCase().includes(q); l.style.display = show ? "" : "none"; n += show;
        l.querySelector("span").innerHTML = ldHi(l.querySelector("input").value, t.value); });
      pick.querySelector(".ms-empty").style.display = n ? "none" : "";
    }
  });
  root.addEventListener("keydown", (e) => {
    const t = e.target;
    if (t.classList.contains("combo-in")) {
      const combo = t.closest(".combo"), list = combo.querySelector(".combo-list"), items = [...list.querySelectorAll(".combo-item")];
      let i = items.findIndex(x => x.classList.contains("on"));
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault(); if (list.style.display === "none") ldComboShow(combo, t.value);
        const all = [...list.querySelectorAll(".combo-item")]; if (!all.length) return;
        i = e.key === "ArrowDown" ? Math.min(all.length - 1, i + 1) : Math.max(0, i - 1);
        all.forEach((x, k) => x.classList.toggle("on", k === i)); all[i].scrollIntoView({ block: "nearest" });
      } else if (e.key === "Enter") { e.preventDefault(); if (i >= 0) ldComboPick(combo, items[i].dataset.v); else list.style.display = "none"; }
      else if (e.key === "Escape") { e.stopPropagation(); list.style.display = "none"; }
    }
    if (t.classList.contains("ms-search") && e.key === "Enter") {   // Enter ticks the only / first match
      e.preventDefault(); const pick = t.closest(".ms-pick");
      const first = [...pick.querySelectorAll(".ms-opt")].find(l => l.style.display !== "none");
      if (first) { const c = first.querySelector("input"); c.checked = true; c.dispatchEvent(new Event("change", { bubbles: true })); t.select(); }
    }
  });
  root.addEventListener("mousedown", (e) => {   // mousedown so the pick happens before the input loses focus
    const it = e.target.closest(".combo-item");
    if (it) { e.preventDefault(); ldComboPick(it.closest(".combo"), it.dataset.v); }
  });
  root.addEventListener("focusout", (e) => {
    if (e.target.classList.contains("combo-in")) setTimeout(() => { const l = e.target.closest(".combo"); if (l) l.querySelector(".combo-list").style.display = "none"; }, 120);
  });
  root.addEventListener("click", (e) => {
    const t = e.target;
    if (t.classList.contains("combo-clear")) { ldComboPick(t.closest(".combo"), ""); t.closest(".combo").querySelector(".combo-in").focus(); }
    const pick = t.closest(".ms-pick"); if (!pick) return;
    const fire = () => { ldMsRefresh(pick); pick.dispatchEvent(new Event("change", { bubbles: true })); };
    if (t.dataset.unpick !== undefined) { const c = [...pick.querySelectorAll(".fi-ms")].find(x => x.value === t.dataset.unpick); if (c) c.checked = false; fire(); }
    if (t.classList.contains("ms-all")) { pick.querySelectorAll(".ms-opt").forEach(l => { if (l.style.display !== "none") l.querySelector("input").checked = true; }); fire(); }
    if (t.classList.contains("ms-none")) { pick.querySelectorAll(".fi-ms").forEach(c => { c.checked = false; }); fire(); }
  });
  root.addEventListener("change", (e) => { if (e.target.classList.contains("fi-ms")) ldMsRefresh(e.target.closest(".ms-pick")); });
}

function ldValueInput(f) {
  const opts = f.options || [];
  if (f.type === "date") {
    const modes = [["today", "Today (run day)"], ["yesterday", "Yesterday"], ["report_date", "Run date"], ["fixed", "A fixed date"]];
    return `<select class="fi-mode">${modes.map(([v, l]) => `<option value="${v}" ${(f.date_mode || "today") === v ? "selected" : ""}>${l}</option>`).join("")}</select>
      ${f.date_mode === "fixed" ? `<input type="date" class="fi-value" value="${esc(f.value || "")}">` : ""}`;
  }
  if (f.type === "toggle") return `<select class="fi-value"><option value="on" ${f.value === "on" ? "selected" : ""}>On</option><option value="off" ${f.value !== "on" ? "selected" : ""}>Off</option></select>`;
  if (f.type === "multiselect") {
    const cur = Array.isArray(f.value) ? f.value : [];
    if (!opts.length) return `<input class="fi-value" value="${esc(cur.join(", "))}" placeholder="values, separated by commas">`;
    const extra = cur.filter(v => !opts.includes(v));   // kept values SFA no longer lists
    return `<div class="ms-pick">
      <div class="ms-chips">${ldChipsHtml(cur)}</div>
      <div class="ms-tools"><input type="search" class="ms-search" placeholder="Type to search ${opts.length} option(s)…" autocomplete="off">
        <button type="button" class="link-btn ms-all">Tick shown</button><button type="button" class="link-btn ms-none">Clear</button></div>
      <div class="ms-box">${opts.concat(extra).map(o => `<label class="ms-opt"><input type="checkbox" class="fi-ms" value="${esc(o)}" ${cur.includes(o) ? "checked" : ""}><span>${esc(o)}</span></label>`).join("")}
        <div class="ms-empty hint" style="display:none;">No match.</div></div></div>`;
  }
  if (f.type === "dropdown" && opts.length)
    return `<div class="combo"><input class="fi-value combo-in" value="${esc(f.value || "")}" placeholder="— leave empty, or type to search ${opts.length} option(s)" autocomplete="off">
      <button type="button" class="combo-clear" title="Clear" ${f.value ? "" : 'style="display:none"'}>✕</button>
      <div class="combo-list" style="display:none;"></div><script type="application/json" class="combo-opts">${JSON.stringify(opts).replace(/</g, "\\u003c")}</script></div>`;
  return `<input class="fi-value" value="${esc(f.value || "")}" placeholder="exactly as shown in SFA">`;
}

// ---------------------------------------------------------------- runs + live window
async function startLdPrepare(ids) {
  const r = await API().start_loading_prepare(ids && ids.length ? ids : null, false);
  if (!r.ok) { openModal({ title: "Couldn't start", body: `<div class="problem-box">${esc(r.message)}</div>`, footer: `<button class="btn-primary" onclick="closeModal()">OK</button>` }); return; }
  openLdMonitor("Preparing loadings (read only)");
}

function openLdMonitor(title) {
  LD.monOpen = true;
  openModal({ title, size: "xl", body: `<div id="ldm-body"><span class="spinner"></span> Starting…</div>`,
    footer: `<button class="btn-stop left" id="ldm-stop">&#9632; Stop now</button><button class="btn-outline" id="ldm-min">Minimise</button>`,
    onClose: () => { LD.monOpen = false; } });
  document.getElementById("ldm-min").onclick = () => closeModal();
  document.getElementById("ldm-stop").onclick = () => openStopNow();
  ldStartPoll();
}

function ldLaneHtml(l, steps) {
  const icon = (st) => st === "done" ? "✓" : st === "failed" ? "!" : st === "skipped" ? "–" : "";
  const statusChip = { ready: ["chip-warn", "Waiting for approval"], nothing: ["chip-grey", "Nothing pending"], confirmed: ["chip-ok", "Confirmed"],
                       changed: ["chip-missing", "Changed — not confirmed"], failed: ["chip-missing", "Failed"], running: ["chip-warn", "Running"],
                       queued: ["chip-grey", "Queued"], cancelled: ["chip-grey", "Stopped"] }[l.status] || ["chip-grey", l.status];
  return `<div class="lane ${["failed", "changed"].includes(l.status) ? "failed" : ["ready", "confirmed", "nothing"].includes(l.status) ? "done" : ""}">
    <div><div class="lane-name">${esc(l.name)}</div><div class="lane-msg">${esc(l.message || (l.status === "queued" ? "Waiting for a free tab…" : ""))}</div></div>
    <div class="stepper">${steps.map(s => `<div class="st ${l.steps[s]}"><span class="dot">${icon(l.steps[s])}</span>${LD_STEP_LABEL[s] || s}</div>`).join("")}</div>
    <div class="lane-meta"><span class="chip ${statusChip[0]}">${esc(statusChip[1])}</span><div>${l.elapsed ? l.elapsed + "s" : ""}</div>
      ${l.screenshot ? `<div>${openPathButton(l.screenshot, "screenshot")}</div>` : ""}</div></div>`;
}

function renderLdMonitor(s) {
  const el = document.getElementById("ldm-body"); if (!el) return;
  const lanes = s.lanes || [], steps = s.steps || [];
  const total = lanes.length * steps.length || 1;
  const done = lanes.reduce((a, l) => a + steps.filter(x => ["done", "skipped"].includes(l.steps[x]) || ["failed", "cancelled", "nothing", "changed"].includes(l.status)).length, 0);
  const phase = { logging_in: "Logging in to SFA…", preparing: "Reading pending loadings…", confirming: "Confirming approved loadings…", done: "Finished" }[s.phase] || "Starting…";
  el.innerHTML = `<div style="display:flex;justify-content:space-between;align-items:center;">
      <div><b>${esc(phase)}</b> <span class="hint">· ${s.kind === "confirm" ? "confirm" : "prepare (read only)"} · ${esc(s.trigger || "")}${s.run_id ? ` · run #${s.run_id}` : ""}</span></div>
      <div>${s.result ? chip({ done: "success", failed: "failed", cancelled: "cancelled", busy: "busy" }[s.result.status] || s.result.status) : ""}</div></div>
    <div class="progress"><div style="width:${s.active ? Math.min(99, Math.round(100 * done / total)) : 100}%"></div></div>
    ${s.result && s.result.message ? `<div class="${s.result.ok ? "ok-box" : "problem-box"}">${esc(s.result.message)}${/already in progress/i.test(s.result.message) ? ` <button class="btn-stop btn-small" onclick="openStopNow()">&#9632; Stop it now</button>` : ""}</div>` : ""}
    <div class="lanes">${lanes.map(l => ldLaneHtml(l, steps)).join("")}</div>
    <div class="section-title">Log <button class="btn-outline btn-small" id="ldm-copy">Copy log</button></div>
    <div class="log-box">${(s.log || []).slice(-150).map(x => `<div class="lv-${x.level}">${esc(x.t)} ${x.lane ? `<span class="who">[${esc(x.lane)}]</span> ` : ""}${esc(x.msg)}</div>`).join("")}</div>`;
  const stop = document.getElementById("ldm-stop"); if (stop) stop.style.display = s.active ? "" : "none";
  const min = document.getElementById("ldm-min"); if (min) min.textContent = s.active ? "Minimise" : "Close";
  const cp = document.getElementById("ldm-copy"); if (cp) cp.onclick = async (e) => copyText(await API().get_loading_log_text(null), e.target);
}

function ldStartPoll() {
  if (LD.poll) return;
  const tick = async () => {
    let s; try { s = await API().get_loading_live(); } catch (e) { return; }
    onLoadingLive(s);
    if (!s.active) { clearInterval(LD.poll); LD.poll = null; }
  };
  LD.poll = setInterval(tick, 1000); tick();
}

// called every second while a run is live (and by the top bar's 3-second check)
function onLoadingLive(s) {
  if (LD.monOpen) renderLdMonitor(s);
  if (s.active) { LD.wasActive = true; LD.lastKind = s.kind; if (!LD.poll) ldStartPoll(); return; }
  if (!LD.wasActive) return;
  LD.wasActive = false;
  if (document.getElementById("view-loadings").style.display !== "none") loadLoadings();
  else API().get_loadings_overview().then(ov => { LD.ov = ov; renderLdTop(); }).catch(() => {});
  if (typeof refreshBell === "function") refreshBell();
  const ready = (s.lanes || []).filter(l => l.status === "ready").length;
  if (s.kind === "prepare" && ready) {
    const modalOpen = document.getElementById("modal-backdrop").style.display === "flex";
    if (!modalOpen || LD.monOpen) { if (LD.monOpen) closeModal(true); setTimeout(openApproveLoadings, 300); }
  }
}
window.onLoadingLive = onLoadingLive;
window.openLdMonitor = openLdMonitor;

// ---------------------------------------------------------------- Approve Loadings
async function openApproveLoadings() {
  let r; try { r = await API().get_pending_loadings(); } catch (e) { return; }
  LD.approve = r.items;
  if (!r.items.length) { openModal({ title: "Approve loadings", body: `<p>No loadings are waiting for approval.</p>`, footer: `<button class="btn-primary" onclick="closeModal()">OK</button>` }); return; }
  LD.approveSel = new Set(r.items.map(i => i.id));
  LD.current = r.items[0].id;
  openModal({ title: "Approve loadings", size: "xl", body: `
    <div class="approve-banner"><span><b>${r.items.length} loading(s) were read from SFA but are NOT confirmed.</b> Check each one, then confirm it — or tick several and confirm them together.</span></div>
    <div class="approve-grid"><div class="approve-list" id="ap-list"></div><div class="approve-detail" id="ap-detail"></div></div>`,
    footer: `<button class="btn-outline left" id="ap-skip-sel">Skip selected</button>
             <span class="hint" id="ap-count"></span>
             <button class="btn-primary" id="ap-confirm-sel">Confirm selected</button>` });
  renderApprove();
  document.getElementById("ap-list").addEventListener("click", (e) => {
    if (e.target.matches("input")) return;
    const row = e.target.closest("[data-item]"); if (!row) return;
    LD.current = Number(row.dataset.item); renderApprove(); });
  document.getElementById("ap-list").addEventListener("change", (e) => {
    const id = Number(e.target.value); e.target.checked ? LD.approveSel.add(id) : LD.approveSel.delete(id); renderApprove(); });
  document.getElementById("ap-skip-sel").onclick = () => { if (LD.approveSel.size) askSkip([...LD.approveSel]); };
  document.getElementById("ap-confirm-sel").onclick = () => { if (LD.approveSel.size) askConfirm([...LD.approveSel]); };
}
window.openApproveLoadings = openApproveLoadings;

function renderApprove() {
  const items = LD.approve;
  document.getElementById("ap-list").innerHTML = `<label class="ap-all"><input type="checkbox" id="ap-all" ${items.every(i => LD.approveSel.has(i.id)) ? "checked" : ""}> Select all</label>` +
    items.map(it => `<div class="ap-item ${it.id === LD.current ? "on" : ""}" data-item="${it.id}">
      <input type="checkbox" value="${it.id}" ${LD.approveSel.has(it.id) ? "checked" : ""}>
      <div><b>${esc(it.distributor_name)}</b><div class="hint" style="font-size:12px;">${esc(it.loading_number)} · ${it.products.length} products</div>
        <div class="hint" style="font-size:12px;">Qty ${esc(it.totals.qty || "—")} · Amount ${esc(it.totals.amount || "—")}</div></div></div>`).join("");
  document.getElementById("ap-all").onchange = (e) => { items.forEach(i => e.target.checked ? LD.approveSel.add(i.id) : LD.approveSel.delete(i.id)); renderApprove(); };
  const it = items.find(i => i.id === LD.current) || items[0];
  const cols = [["product", "Product"], ["code", "Code"], ["price", "Price"], ["stock", "Available stock"], ["qty", "Accepted qty"], ["amount", "Amount"]];
  document.getElementById("ap-detail").innerHTML = it ? `
    <div class="card-header-row"><div><h3 style="margin:0;">${esc(it.distributor_name)}</h3>
      <div class="hint">Loading <b>${esc(it.loading_number)}</b> · ${esc(it.rep)} · ${esc(it.loading_date)} ${esc(it.loading_time)} · read ${esc((it.prepared_at || "").replace("T", " ").slice(0, 16))}</div></div>
      <div style="white-space:nowrap;"><button class="btn-outline btn-small" id="ap-skip-one">Skip</button>
        <button class="btn-primary btn-small" id="ap-confirm-one">Confirm this loading</button></div></div>
    <table class="report-table compact ap-products"><thead><tr>${cols.map(c => `<th>${c[1]}</th>`).join("")}</tr></thead><tbody>
      ${it.products.map(p => `<tr class="${ldNum(p.qty) ? "" : "row-zero"}">${cols.map(c => `<td>${esc(p[c[0]] || "")}</td>`).join("")}</tr>`).join("")}
      <tr class="ap-total"><td colspan="4"><b>Total</b></td><td><b>${esc(it.totals.qty || "")}</b></td><td><b>${esc(it.totals.amount || "")}</b></td></tr></tbody></table>
    <p class="hint">Rows with 0 accepted quantity are shown faded. Chimera doesn't change any quantity — it presses Confirm on the page exactly as SFA shows it,
      and stops if anything has changed since you approved.</p>
    ${it.screenshot ? `<div class="section-title">SFA page as read</div><div class="ap-shot" id="ap-shot"><span class="spinner"></span></div>` : ""}` : "";
  document.getElementById("ap-count").textContent = `${LD.approveSel.size} of ${items.length} selected`;
  document.getElementById("ap-confirm-sel").textContent = `Confirm selected (${LD.approveSel.size})`;
  document.getElementById("ap-confirm-sel").disabled = !LD.approveSel.size;
  if (it) {
    document.getElementById("ap-confirm-one").onclick = () => askConfirm([it.id]);
    document.getElementById("ap-skip-one").onclick = () => askSkip([it.id]);
    if (it.screenshot) API().get_image_b64(it.screenshot).then(b64 => {
      const box = document.getElementById("ap-shot"); if (box) box.innerHTML = b64 ? `<img src="data:image/png;base64,${b64}">` : `<span class="hint">Screenshot not available.</span>`; });
  }
}

function askSkip(ids) {
  confirmModal("Skip loadings", `Leave ${ids.length} loading(s) unconfirmed in SFA? You can prepare them again later.`, "Skip", async () => {
    await API().skip_loadings(ids); refreshBell(); loadLoadings(); openApproveLoadings(); });
}

function askConfirm(ids) {
  const items = LD.approve.filter(i => ids.includes(i.id));
  const qty = items.reduce((a, i) => a + ldNum(i.totals.qty), 0), amt = items.reduce((a, i) => a + ldNum(i.totals.amount), 0);
  openModal({ title: `Confirm ${items.length} loading(s) in SFA?`, body: `
    <div class="problem-box" style="background:#fff6e5;color:#9a6a00;">Chimera will press <b>Confirm</b> and <b>Yes</b> on these loadings in SFA. This can't be undone from Chimera.</div>
    <div class="stat-cards three"><div class="stat-card"><div class="stat-label">DISTRIBUTORS</div><div class="stat-value">${items.length}</div></div>
      <div class="stat-card"><div class="stat-label">TOTAL QUANTITY</div><div class="stat-value stat-value-small">${ldFmt(qty)}</div></div>
      <div class="stat-card"><div class="stat-label">TOTAL AMOUNT</div><div class="stat-value stat-value-small">${ldFmt(amt)}</div></div></div>
    <ul class="result-list">${items.map(i => `<li><b>${esc(i.distributor_name)}</b> — ${esc(i.loading_number)} · amount ${esc(i.totals.amount || "—")}</li>`).join("")}</ul>
    <label class="check" style="margin-top:10px;"><input type="checkbox" id="ac-ok"> I've checked these loadings</label>`,
    footer: `<button class="btn-outline" id="ac-cancel">Back</button><button class="btn-primary" id="ac-go" disabled>Yes, confirm in SFA</button>` });
  document.getElementById("ac-ok").onchange = (e) => { document.getElementById("ac-go").disabled = !e.target.checked; };
  document.getElementById("ac-cancel").onclick = () => openApproveLoadings();
  document.getElementById("ac-go").onclick = async () => {
    const r = await API().start_loading_confirm(ids, false);
    if (!r.ok) { document.getElementById("modal-body").insertAdjacentHTML("afterbegin", `<div class="problem-box">${esc(r.message)}</div>`); return; }
    closeModal(true); openLdMonitor("Confirming approved loadings");
  };
}

window.addEventListener("pywebviewready", () => {
  API().get_loadings_overview().then(ov => { LD.ov = ov; renderLdTop(); if (ov.live_active) { LD.wasActive = true; ldStartPoll(); } }).catch(() => {});
});
