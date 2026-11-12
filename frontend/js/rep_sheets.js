// rep_sheets.js — Outlet Updates › Rep sheets: route sheets reps fill (New Sequence, Top 15), the preview against SFA,
// approval, and saving visit sequence + Visit State in SFA. Relies on openModal/closeModal/confirmModal/esc/API/chip
// (automation.js), copyText/fileName (main.js), openStopNow (shell.js), selectOuTab/renderOuBadge (outlet_updates.js).

const RS = { ov: null, filter: "all", poll: null, wasActive: false, monOpen: false, sheet: null, sel: new Set(), lf: "all", openId: null };
const RS_STATE = {
  received: ["chip-grey", "Read from the file"], ready: ["chip-warn", "Waiting for approval"], approved: ["chip-warn", "Approved — to save"],
  applying: ["chip-warn", "Saving in SFA"], done: ["chip-ok", "Saved in SFA"], partial: ["chip-missing", "Partly saved"],
  failed: ["chip-missing", "Failed"], rejected: ["chip-missing", "Refused"], discarded: ["chip-grey", "Discarded"],
  superseded: ["chip-grey", "Replaced by a newer sheet"],
};
const RS_OUT = { "": ["", ""], updated: ["chip-ok", "Saved"], would_update: ["chip-grey", "Would be saved"], failed: ["chip-missing", "Failed"],
                 not_found: ["chip-missing", "Not on route"], unchanged: ["chip-grey", "Already matched"], skipped: ["chip-grey", "Skipped"] };
const RS_SRC = { upload: "Uploaded", whatsapp: "WhatsApp", gsheet: "Google Sheet" };
const rsChip = (m, k) => { const c = m[k] || ["chip-grey", k]; return c[1] ? `<span class="chip ${c[0]}">${esc(c[1])}</span>` : ""; };
const rsWhen = (t) => t ? new Date(t).toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "—";
const RS_GROUP = { ready: ["ready"], queued: ["received", "approved", "applying"], done: ["done"], bad: ["rejected", "failed", "partial"], old: ["discarded", "superseded"] };

async function loadRepSheets() {
  if (typeof API().get_rep_sheets_overview !== "function") {
    document.getElementById("rs-table").innerHTML = `<div class="problem-box" style="margin:14px;">This tab needs the latest backend files
      (backend\\api.py, sequence_store.py, sequence_reader.py, sequence_automation.py). Replace them, then restart Chimera.</div>`;
    return;
  }
  try { RS.ov = await API().get_rep_sheets_overview(); } catch (e) { return; }
  const st = RS.ov.settings;
  document.getElementById("rs-need-approval").checked = st.seq_need_approval === "1";
  document.getElementById("rs-dry-run").checked = st.seq_dry_run === "1";
  document.getElementById("rs-routes-url").value = st.ou_routes_url;
  renderRepSheets();
  if (RS.ov.live_active) { RS.wasActive = true; rsStartPoll(); }
}
window.loadRepSheets = loadRepSheets;

function renderRepSheets() {
  const ov = RS.ov, c = ov.counts, n = (ks) => ks.reduce((a, k) => a + (c[k] || 0), 0);
  window.RS_WAITING = c.ready || 0;
  if (window.renderOuBadge) renderOuBadge();
  document.getElementById("ou-tc-sheets").textContent = c.ready ? c.ready : "";
  document.getElementById("rs-st-ready").textContent = c.ready || 0;
  document.getElementById("rs-st-queued").textContent = n(RS_GROUP.queued);
  document.getElementById("rs-st-done").textContent = c.done || 0;
  document.getElementById("rs-st-bad").textContent = n(RS_GROUP.bad);
  const st = ov.settings;
  document.getElementById("rs-mode").innerHTML =
    `<span class="chip ${st.seq_need_approval === "1" ? "chip-ok" : "chip-warn"}" title="Settings">${st.seq_need_approval === "1" ? "Asks before saving" : "Saves without asking"}</span>
     ${st.seq_dry_run === "1" ? `<span class="chip chip-warn" title="Settings › Dry run">DRY RUN — nothing is saved</span>` : ""}`;
  const r = ov.routes;
  document.getElementById("rs-routes-info").innerHTML = r.routes
    ? `<b>${r.routes.toLocaleString()}</b> active routes in <b>${r.territories}</b> territories &middot; updated ${esc(rsWhen(r.synced_at))}`
    : `<span class="error">Not read yet</span> &mdash; sheets can't be matched to SFA routes until the list is read. Click <b>Update from SFA</b>.`;
  const rows = ov.sheets.filter(s => RS.filter === "all" ? !RS_GROUP.old.includes(s.state) : RS_GROUP[RS.filter].includes(s.state));
  document.getElementById("rs-table").innerHTML = rows.length ? `<div class="am-scroll"><table class="report-table am-table"><thead><tr>
      <th>#</th><th>Received</th><th>Route</th><th>From</th><th class="num">Outlets</th><th class="num">Top 15</th><th class="num">Changes</th><th>Status</th><th></th></tr></thead><tbody>
      ${rows.map(s => `<tr><td>${s.id}</td><td>${esc(rsWhen(s.received_at))}</td>
        <td><b>${esc(s.route_name || s.file_name)}</b><div class="am-note">${esc([s.territory, s.route_code, s.rep].filter(Boolean).join(" · "))}</div></td>
        <td>${esc(RS_SRC[s.source] || s.source)}${s.sender ? `<div class="am-note">${esc(s.sender)}</div>` : ""}<div class="am-note" title="${esc(s.file_name)}">${esc(s.file_name.length > 34 ? s.file_name.slice(0, 32) + "…" : s.file_name)}</div></td>
        <td class="num">${s.counts.outlets || "—"}</td><td class="num">${s.counts.top || "—"}</td>
        <td class="num">${["received", "rejected"].includes(s.state) ? "—" : s.counts.changes}${s.counts.updated ? `<div class="am-note">${s.counts.updated} ${s.dry_run ? "checked" : "saved"}</div>` : ""}${s.counts.failed ? `<div class="am-note error">${s.counts.failed} failed</div>` : ""}</td>
        <td>${rsChip(RS_STATE, s.state)}${s.dry_run && s.state === "ready" ? ` <span class="chip chip-grey">dry run done</span>` : ""}${s.message ? `<div class="am-note">${esc(s.message)}</div>` : ""}</td>
        <td style="text-align:right;white-space:nowrap;">
          ${s.state === "ready" ? `<button class="btn-primary btn-small" data-rs-open="${s.id}">Review</button>` : `<button class="btn-outline btn-small" data-rs-open="${s.id}">Open</button>`}
        </td></tr>`).join("")}</tbody></table></div>`
    : `<p class="hint" style="padding:14px 18px 18px;">${RS.filter === "all" ? "No route sheets yet. Click <b>+ Add filled sheets</b> and pick the Excel files reps sent back." : "Nothing here."}</p>`;
  document.getElementById("rs-runs").innerHTML = ov.runs.length ? `<table class="report-table"><tbody>${ov.runs.slice(0, 6).map(x => `
      <tr><td style="white-space:nowrap;">${esc(rsWhen(x.started_at))}</td><td>${esc({ routes: "Route list", read: "Previews", apply: "SFA run" }[x.kind] || x.kind)}</td>
      <td style="font-size:12px;">${chip({ done: "success", failed: "failed", cancelled: "cancelled", running: "running", interrupted: "interrupted" }[x.status] || x.status)} ${esc(x.message || "")}</td>
      <td style="text-align:right;"><button class="icon-action" data-rs-runlog="${x.id}">Copy log</button></td></tr>`).join("")}</tbody></table>`
    : `<p class="hint">Nothing has run yet.</p>`;
}

// ---------------------------------------------------------------- adding sheets
async function rsAdd() {
  const r = await API().add_rep_sheets();
  if (!r.results) { if (r.message !== "No file selected.") openModal({ title: "Add sheets", body: `<div class="problem-box">${esc(r.message)}</div>`, footer: `<button class="btn-primary" onclick="closeModal()">OK</button>` }); return; }
  openModal({ title: "Route sheets added", size: "lg", body: `<div class="${r.ok ? "ok-box" : "problem-box"}">${esc(r.message)}</div>
      <table class="report-table"><thead><tr><th>File</th><th>Route</th><th>Result</th></tr></thead><tbody>${r.results.map(x => `
        <tr><td>${esc(x.file)}${x.tab ? `<div class="am-note">${esc(x.tab)}</div>` : ""}</td><td>${esc(x.route || "—")}</td>
          <td>${x.ok ? `<span class="chip chip-ok">Added</span>` : x.duplicate ? `<span class="chip chip-grey">Already received</span>` : `<span class="chip chip-missing">Refused</span>`}
            <div class="am-note">${esc(x.message)}</div></td></tr>`).join("")}</tbody></table>`,
    footer: r.started ? `<button class="btn-outline" onclick="closeModal()">Close</button><button class="btn-primary" id="rs-add-watch">Watch</button>`
                      : `<button class="btn-primary" onclick="closeModal()">OK</button>` });
  const w = document.getElementById("rs-add-watch"); if (w) w.onclick = () => { closeModal(); openRsMonitor(); };
  loadRepSheets(); if (r.started) rsStartPoll();
}

async function rsRun() {
  const r = await API().start_rep_sheets_run(false);
  if (!r.ok) { openModal({ title: "Couldn't start", body: `<div class="problem-box">${esc(r.message)}</div>`, footer: `<button class="btn-primary" onclick="closeModal()">OK</button>` }); return; }
  openRsMonitor();
}

// ---------------------------------------------------------------- the preview
async function openRepSheet(id) {
  const r = await API().get_rep_sheet(Number(id));
  if (!r.ok) { openModal({ title: "Sheet", body: `<div class="problem-box">${esc(r.message)}</div>`, footer: `<button class="btn-primary" onclick="closeModal()">OK</button>` }); return; }
  RS.sheet = r.sheet; RS.openId = r.sheet.id; RS.sel = new Set();
  openModal({ title: `${r.sheet.route_name || r.sheet.file_name} — sheet #${r.sheet.id}`, size: "xl", body: `<div id="rsp-body"></div>`, footer: `<div id="rsp-foot" style="display:flex;gap:8px;width:100%;"></div>`,
    onClose: () => { RS.openId = null; } });
  renderRepSheet();
}
window.openRepSheet = openRepSheet;

function rsLines() {
  const s = RS.sheet, f = RS.lf;
  return s.lines.filter(l => f === "all" ? true : f === "changes" ? (l.in_sfa && (String(l.new_seq) !== l.cur_seq || l.new_state !== l.cur_state))
    : f === "top" ? l.top : f === "notes" ? (l.remark || l.demand) : f === "problems" ? (l.in_sfa === false || l.outcome === "failed" || l.line === 0) : true);
}

function renderRepSheet() {
  const s = RS.sheet, sm = s.summary, editable = s.state === "ready", dry = RS.ov && RS.ov.settings.seq_dry_run === "1";
  const read = !!s.read_at;
  const lines = rsLines();
  const stateSel = (l) => !editable || !l.in_sfa ? `${esc(l.new_state || l.cur_state || "—")}`
    : `<select class="rs-vs ${l.new_state === "Mandatory" ? "mand" : ""}" data-rs-line="${l.id}">${["Mandatory", "Optional"].map(v => `<option ${v === (l.new_state || l.cur_state) ? "selected" : ""}>${v}</option>`).join("")}</select>`;
  const arrow = (a, b) => (a || "—") === (b || "—") ? `<span class="hint">${esc(a || "—")}</span>` : `<span class="hint">${esc(a || "—")}</span> → <b>${esc(b || "—")}</b>`;
  document.getElementById("rsp-body").innerHTML = `
    <div class="rsp-meta">
      <div><b>${esc(s.territory)}</b> · ${esc(s.route_code)} · rep ${esc(s.rep || "—")} · distributor ${esc(s.distributor || "—")}</div>
      <div class="hint">${esc(RS_SRC[s.source] || s.source)}${s.sender ? " by " + esc(s.sender) : ""} · ${esc(s.file_name)} · received ${esc(rsWhen(s.received_at))}${read ? " · SFA read " + esc(rsWhen(s.read_at)) : ""}</div>
      <div>${rsChip(RS_STATE, s.state)} ${s.dry_run ? `<span class="chip chip-grey">dry run</span>` : ""} <span class="hint">${esc(s.message || "")}</span></div>
    </div>
    ${s.problems.map(p => `<div class="${p.level === "error" ? "problem-box" : "warn-box"}">${esc(p.text)}</div>`).join("")}
    ${!read && s.state !== "rejected" ? `<div class="warn-box">SFA hasn't been read for this sheet yet, so the current sequence and Visit State aren't known. Click <b>Read SFA</b>.</div>` : ""}
    <div class="stat-cards rsp-stats">
      <div class="stat-card"><div class="stat-label">OUTLETS</div><div class="stat-value">${sm.outlets}</div><div class="stat-foot">${sm.in_file} in the sheet${sm.sfa_only ? ` · ${sm.sfa_only} only in SFA` : ""}</div></div>
      <div class="stat-card"><div class="stat-label">NEW ORDER</div><div class="stat-value">${read ? sm.seq_changes : "—"}</div><div class="stat-foot">sequence changes</div></div>
      <div class="stat-card"><div class="stat-label">VISIT STATE</div><div class="stat-value">${read ? sm.state_changes : "—"}</div><div class="stat-foot">${sm.top} Top 15 · ${read ? sm.mandatory + " Mandatory after" : ""}</div></div>
      <div class="stat-card ${sm.failed ? "am-bad" : ""}"><div class="stat-label">${s.dry_run ? "DRY RUN" : "SAVED IN SFA"}</div><div class="stat-value">${s.dry_run ? sm.would_update : sm.updated}</div><div class="stat-foot">${sm.failed ? sm.failed + " failed" : "outlets"}</div></div>
    </div>
    <div class="rsp-tools">
      <div class="seg" id="rsp-filter">${[["all", "All"], ["changes", "Changes"], ["top", "Top 15"], ["notes", "Remarks / demand"], ["problems", "Problems"]]
        .map(([k, v]) => `<button data-f="${k}" class="${RS.lf === k ? "on" : ""}">${v}</button>`).join("")}</div>
      ${editable ? `<span class="rsp-bulk"><span id="rsp-selcount" class="hint">${RS.sel.size} selected</span>
        <button class="btn-outline btn-small" data-rs-bulk="Mandatory">Set Mandatory</button><button class="btn-outline btn-small" data-rs-bulk="Optional">Set Optional</button>
        <button class="btn-outline btn-small" data-rs-bulk="reset" title="Top 15 → Mandatory, others as in SFA">Reset</button></span>` : ""}
    </div>
    <div class="am-scroll rsp-scroll"><table class="report-table am-table rsp-table"><thead><tr>
      ${editable ? `<th><input type="checkbox" id="rsp-all"></th>` : ""}<th class="num">New</th><th>Outlet</th><th class="num">Rep's</th><th>Top 15</th>
      <th>Sequence</th><th>Visit State</th>${editable ? "" : "<th>After</th>"}<th>Remark / demand</th><th>Result</th></tr></thead><tbody>
      ${lines.map(l => `<tr class="${l.in_sfa === false ? "rs-off" : ""}">
        ${editable ? `<td>${l.in_sfa ? `<input type="checkbox" data-rs-sel="${l.id}" ${RS.sel.has(l.id) ? "checked" : ""}>` : ""}</td>` : ""}
        <td class="num"><b>${l.new_seq ?? "—"}</b></td>
        <td><b class="mono">${esc(l.outlet_code)}</b><div class="am-note">${esc(l.outlet_name || "")}${l.line === 0 ? " · not in the sheet" : ""}</div></td>
        <td class="num">${esc(l.rep_seq || "—")}</td>
        <td>${l.top ? `<span class="chip chip-ok" title="Marked '${esc(l.top_mark)}'">${l.top_rank ? "#" + l.top_rank : "✓"}</span>` : ""}</td>
        <td>${l.in_sfa ? arrow(l.cur_seq, String(l.new_seq ?? "")) : "—"}</td>
        <td>${editable ? `${l.in_sfa ? `<span class="hint">${esc(l.cur_state || "—")}</span> → ` : ""}${stateSel(l)}${l.state_by === "you" ? ` <span class="chip chip-grey" title="You set this">you</span>` : ""}`
                       : (l.in_sfa ? esc(l.cur_state || "—") : "—")}</td>
        ${editable ? "" : `<td>${l.in_sfa ? esc(l.new_state || "—") : ""}</td>`}
        <td>${l.remark ? `<div>${esc(l.remark)}</div>` : ""}${l.demand ? `<div class="am-note">Demand: ${esc(l.demand)}</div>` : ""}</td>
        <td>${rsChip(RS_OUT, l.outcome)}${l.message ? `<div class="am-note">${esc(l.message)}</div>` : ""}</td></tr>`).join("")
        || `<tr><td colspan="10" class="hint am-empty">Nothing matches this filter.</td></tr>`}</tbody></table></div>`;
  const canApprove = editable && !s.problems.some(p => p.level === "error");
  const canReread = ["ready", "failed", "received"].includes(s.state) && !s.problems.some(p => p.level === "error" && !p.from_sfa);
  document.getElementById("rsp-foot").innerHTML = `
    <button class="btn-outline" id="rsp-export">Export to Excel</button>
    ${["received", "ready", "approved", "failed", "rejected"].includes(s.state) ? `<button class="btn-outline" id="rsp-discard">Discard</button>` : ""}
    ${canReread ? `<button class="btn-outline" id="rsp-reread">${read ? "Read SFA again" : "Read SFA"}</button>` : ""}
    <span style="flex:1"></span>
    ${canApprove ? `<button class="btn-primary" id="rsp-approve">${dry ? "Approve — dry run (nothing saved)" : `Approve &amp; save ${sm.changes} outlet(s) in SFA`}</button>`
                 : `<button class="btn-primary" onclick="closeModal()">Close</button>`}`;
  const on = (id, fn) => { const e = document.getElementById(id); if (e) e.onclick = fn; };
  on("rsp-export", async (e) => { const x = await API().export_rep_sheet(s.id); if (x.ok) API().open_path(x.path); else e.target.textContent = x.message; });
  on("rsp-discard", () => confirmModal("Discard sheet", `Discard sheet #${s.id} (${esc(s.route_name || s.file_name)})? Nothing is changed in SFA.`, "Discard",
    async () => { const x = await API().discard_rep_sheet(s.id); if (!x.ok) alertBox(x.message); loadRepSheets(); }, true));
  on("rsp-reread", async () => { const x = await API().reread_rep_sheet(s.id); if (!x.ok) { alertBox(x.message); return; } closeModal(); loadRepSheets(); openRsMonitor(); });
  on("rsp-approve", () => rsApprove(s));
  const all = document.getElementById("rsp-all");
  if (all) all.onchange = () => { lines.filter(l => l.in_sfa).forEach(l => all.checked ? RS.sel.add(l.id) : RS.sel.delete(l.id)); renderRepSheet(); };
}

function alertBox(msg) { openModal({ title: "Chimera", body: `<div class="problem-box">${esc(msg)}</div>`, footer: `<button class="btn-primary" onclick="closeModal()">OK</button>` }); }

async function rsSetStates(ids, state) {
  const r = await API().set_rep_sheet_states(RS.sheet.id, ids, state);
  if (!r.ok) { alertBox(r.message); return; }
  const x = await API().get_rep_sheet(RS.sheet.id); RS.sheet = x.sheet; renderRepSheet();
}

function rsApprove(s) {
  const sm = s.summary, dry = RS.ov.settings.seq_dry_run === "1";
  const toMand = s.lines.filter(l => l.in_sfa && l.new_state === "Mandatory" && l.cur_state !== "Mandatory").length;
  const toOpt = s.lines.filter(l => l.in_sfa && l.new_state === "Optional" && l.cur_state !== "Optional").length;
  confirmModal(dry ? "Approve — dry run" : "Save in SFA",
    `${dry ? "<b>Dry run is on</b>: Chimera opens and checks every outlet but doesn't press UPDATE — nothing changes in SFA.<br><br>" : ""}
     <b>${esc(s.route_name)}</b> (${esc(s.route_code)}): ${sm.changes} outlet(s) — ${sm.seq_changes} new sequence number(s), ${toMand} to Mandatory, ${toOpt} to Optional.
     Phone fields that aren't 10 characters are fixed on the way.`,
    dry ? "Run the dry run" : "Save in SFA", async () => {
      const r = await API().approve_rep_sheet([s.id], true);
      if (!r.ok) { alertBox(r.message); return; }
      loadRepSheets(); if (r.started) openRsMonitor(); });
}

// ---------------------------------------------------------------- routes
async function rsRoutesView() {
  const rows = await API().list_sfa_routes(null);
  const draw = (q) => { const ql = q.toLowerCase(), list = rows.filter(r => !ql || `${r.territory} ${r.name} ${r.code}`.toLowerCase().includes(ql));
    document.getElementById("rsr-list").innerHTML = `<table class="report-table am-table"><thead><tr><th>Territory</th><th>Route code</th><th>Route</th><th>Area</th></tr></thead><tbody>
      ${list.slice(0, 400).map(r => `<tr><td>${esc(r.territory)}</td><td class="mono">${esc(r.code)}</td><td>${esc(r.name)}</td><td>${esc(r.area)}</td></tr>`).join("")}</tbody></table>
      <p class="hint">${list.length.toLocaleString()} route(s)${list.length > 400 ? " — first 400 shown, search to narrow" : ""}.</p>`; };
  openModal({ title: "SFA's active routes", size: "lg", body: `<input type="search" id="rsr-q" class="am-search" placeholder="Search territory, route or code" style="width:100%;margin-bottom:10px;"><div class="am-scroll" id="rsr-list"></div>`,
    footer: `<button class="btn-primary" onclick="closeModal()">Close</button>` });
  document.getElementById("rsr-q").oninput = (e) => draw(e.target.value); draw("");
}

// ---------------------------------------------------------------- live monitor
function openRsMonitor() {
  RS.monOpen = true;
  openModal({ title: "Outlet updates in SFA", size: "xl", body: `<div id="rsm-body"><span class="spinner"></span> Starting…</div>`,
    footer: `<button class="btn-stop left" id="rsm-stop">&#9632; Stop now</button><button class="btn-outline" id="rsm-min">Minimise</button>`,
    onClose: () => { RS.monOpen = false; } });
  document.getElementById("rsm-min").onclick = () => closeModal();
  document.getElementById("rsm-stop").onclick = () => openStopNow();
  rsStartPoll();
}
window.openRsMonitor = openRsMonitor;

function renderRsMonitor(s) {
  const el = document.getElementById("rsm-body"); if (!el) return;
  const lanes = s.lanes || [], labels = s.step_labels || {};
  const icon = (st) => st === "done" ? "✓" : st === "failed" ? "!" : st === "skipped" ? "–" : "";
  const stChip = { done: ["chip-ok", "Done"], failed: ["chip-missing", "Failed"], running: ["chip-warn", "Working"], queued: ["chip-grey", "Queued"], cancelled: ["chip-grey", "Stopped"] };
  const fin = lanes.filter(l => ["done", "failed", "cancelled"].includes(l.status)).length;
  el.innerHTML = `<div style="display:flex;justify-content:space-between;align-items:center;">
      <div><b>${s.kind === "routes" ? "Reading SFA's route list (View Route)" : { logging_in: "Logging in to SFA…", working: "Working through the route sheets…", done: "Finished" }[s.phase] || "Starting…"}</b>
        <span class="hint">· ${esc(s.trigger || "")}${s.run_id ? ` · run #${s.run_id}` : ""}${s.dry_run && s.kind !== "routes" ? " · DRY RUN — nothing is saved" : ""}</span></div>
      <div>${s.result ? chip({ done: "success", failed: "failed", cancelled: "cancelled", busy: "busy" }[s.result.status] || s.result.status) : ""}</div></div>
    <div class="progress"><div style="width:${s.active ? Math.min(99, Math.round(100 * fin / (lanes.length || 1))) : 100}%"></div></div>
    ${s.result && s.result.message ? `<div class="${s.result.ok ? "ok-box" : "problem-box"}">${esc(s.result.message)}</div>` : ""}
    <div class="lanes">${lanes.map(l => { const c = stChip[l.status] || ["chip-grey", l.status], st = Object.keys(l.steps || {});
      return `<div class="lane ${l.status === "failed" ? "failed" : l.status === "done" ? "done" : ""}">
        <div><div class="lane-name">${esc(l.name)}</div><div class="lane-msg">${esc(l.message || "")}</div></div>
        <div class="stepper">${st.map(x => `<div class="st ${l.steps[x]}"><span class="dot">${icon(l.steps[x])}</span>${esc(labels[x] || x)}</div>`).join("")}</div>
        <div class="lane-meta"><span class="chip ${c[0]}">${esc(c[1])}</span><div>${l.elapsed ? l.elapsed + "s" : ""}</div></div></div>`; }).join("")}</div>
    <div class="section-title">Log <button class="btn-outline btn-small" id="rsm-copy">Copy log</button></div>
    <div class="log-box">${(s.log || []).slice(-200).map(x => `<div class="lv-${x.level}">${esc(x.t)} ${x.lane ? `<span class="who">[${esc(x.lane)}]</span> ` : ""}${esc(x.msg)}</div>`).join("")}</div>`;
  const stop = document.getElementById("rsm-stop"); if (stop) stop.style.display = s.active ? "" : "none";
  const min = document.getElementById("rsm-min"); if (min) min.textContent = s.active ? "Minimise" : "Close";
  const cp = document.getElementById("rsm-copy"); if (cp) cp.onclick = async (e) => copyText(await API().get_rep_sheets_log_text(null), e.target);
}

function rsStartPoll() {
  if (RS.poll) return;
  const tick = async () => {
    let s; try { s = await API().get_rep_sheets_live(); } catch (e) { return; }
    onRepSheetsLive(s);
    if (!s.active) { clearInterval(RS.poll); RS.poll = null; }
  };
  RS.poll = setInterval(tick, 1000); tick();
}

function onRepSheetsLive(s) {
  if (RS.monOpen) renderRsMonitor(s);
  if (s.active) { RS.wasActive = true; if (!RS.poll) rsStartPoll(); return; }
  if (!RS.wasActive) return;
  RS.wasActive = false;
  loadRepSheets();
  if (typeof refreshBell === "function") refreshBell();
}
window.onRepSheetsLive = onRepSheetsLive;

// ---------------------------------------------------------------- events
(function wireRepSheets() {
  const panel = document.getElementById("ou-panel-sheets"); if (!panel) return;
  document.getElementById("rs-add").onclick = rsAdd;
  document.getElementById("rs-run").onclick = rsRun;
  document.getElementById("rs-routes-sync").onclick = async () => {
    const r = await API().start_routes_sync(false);
    if (!r.ok) { alertBox(r.message); return; } openRsMonitor(); };
  document.getElementById("rs-routes-import").onclick = async () => {
    const r = await API().import_routes_excel();
    if (r.message === "No file selected.") return;
    openModal({ title: "Route list", body: `<div class="${r.ok ? "ok-box" : "problem-box"}">${esc(r.message)}</div>`, footer: `<button class="btn-primary" onclick="closeModal()">OK</button>` });
    loadRepSheets(); };
  document.getElementById("rs-routes-view").onclick = rsRoutesView;
  document.getElementById("rs-filter").addEventListener("click", (e) => {
    const f = e.target.dataset.f; if (!f) return; RS.filter = f;
    document.querySelectorAll("#rs-filter button").forEach(b => b.classList.toggle("on", b.dataset.f === f)); renderRepSheets(); });
  panel.addEventListener("click", async (e) => {
    const b = e.target.closest("button"); if (!b) return;
    if (b.dataset.rsOpen) openRepSheet(b.dataset.rsOpen);
    if (b.dataset.rsRunlog) copyText(await API().get_rep_sheets_log_text(Number(b.dataset.rsRunlog)), b);
  });
  document.getElementById("rs-mode").addEventListener("click", () => selectOuTab("settings"));
  // the preview lives in the shared modal
  document.getElementById("modal-body").addEventListener("change", (e) => {
    if (!RS.openId) return;
    if (e.target.dataset.rsLine) rsSetStates([Number(e.target.dataset.rsLine)], e.target.value);
    if (e.target.dataset.rsSel) { const id = Number(e.target.dataset.rsSel); e.target.checked ? RS.sel.add(id) : RS.sel.delete(id);
      const c = document.getElementById("rsp-selcount"); if (c) c.textContent = `${RS.sel.size} selected`; }
  });
  document.getElementById("modal-body").addEventListener("click", (e) => {
    if (!RS.openId) return;
    const b = e.target.closest("button"); if (!b) return;
    if (b.dataset.rsBulk) { if (!RS.sel.size) { b.blur(); const c = document.getElementById("rsp-selcount"); if (c) c.innerHTML = `<span class="error">Tick outlets first</span>`; return; }
      rsSetStates([...RS.sel], b.dataset.rsBulk).then(() => { RS.sel = new Set(); renderRepSheet(); }); }
    if (b.closest("#rsp-filter") && b.dataset.f) { RS.lf = b.dataset.f; renderRepSheet(); }
  });
})();

// the sidebar badge counts sheets waiting for approval from the start, not only once Outlet Updates is opened
window.addEventListener("pywebviewready", () => {
  if (typeof API().get_rep_sheets_overview !== "function") return;
  API().get_rep_sheets_overview().then(ov => {
    RS.ov = ov; window.RS_WAITING = ov.counts.ready || 0;
    if (window.renderOuBadge) renderOuBadge();
    if (ov.live_active) { RS.wasActive = true; rsStartPoll(); }
  }).catch(() => {});
});
