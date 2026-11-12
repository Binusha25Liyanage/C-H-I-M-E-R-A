// outlet_updates.js — Outlet Updates, step 1: working territories, the SFA monthly itinerary of each,
// and Today's Routes (which route each territory's rep works on a day). Read only towards SFA.
// Relies on openModal/closeModal/confirmModal/esc/API/chip (automation.js), copyText (main.js),
// openStopNow/waChatPicker/refreshBell (shell.js).

const OU = { ov: null, day: null, filter: "all", tFilter: "on", tq: "", poll: null, wasActive: false, monOpen: false };
const OU_DAY = (iso) => new Date(iso + "T00:00:00");
const OU_ISO = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const OU_ADD = (iso, n) => { const d = OU_DAY(iso); d.setDate(d.getDate() + n); return OU_ISO(d); };
const OU_LABEL = (iso, long) => OU_DAY(iso).toLocaleDateString("en-GB", long ? { weekday: "long", day: "2-digit", month: "long", year: "numeric" }
                                                                           : { weekday: "short", day: "2-digit", month: "short" });
const OU_MONTH = (mk) => new Date(mk + "-01T00:00:00").toLocaleDateString("en-GB", { month: "long", year: "numeric" });
const OU_STATE = {
  route: ["chip-ok", "Route"], holiday: ["chip-warn", "Holiday (H)"], no_route: ["chip-grey", "No route (-)"],
  missing: ["chip-missing", "No itinerary in SFA"], failed: ["chip-missing", "Couldn't read"], not_read: ["chip-grey", "Not read yet"],
};
const ouChip = (st) => { const c = OU_STATE[st] || ["chip-grey", st]; return `<span class="chip ${c[0]}">${esc(c[1])}</span>`; };
const OU_STEP_LABEL = { login: "Log in", open: "Open", filters: "Filters", search: "Search", calendar: "Open calendar", read: "Read days" };

// ---------------------------------------------------------------- load
async function loadOutletUpdates(day) {
  try {
    if (typeof API().get_outlet_updates_overview !== "function") throw new Error("OUTDATED");
    OU.ov = await API().get_outlet_updates_overview(day || OU.day || null);
  } catch (err) {
    const outdated = String(err).includes("OUTDATED") || String(err).includes("not a function");
    document.getElementById("ou-board").innerHTML = `<div class="problem-box">${outdated
      ? "This screen needs the latest backend files (backend\\api.py, outlet_store.py, itinerary_automation.py). Replace them, then restart Chimera."
      : "Couldn't load Outlet Updates: " + esc(String(err))}</div>`;
    return;
  }
  OU.day = OU.ov.day;
  renderOuDates(); renderOuBoard(); renderOuTerritories(); renderOuSettings(); renderOuRuns(); renderOuBadge();
  if (window.loadRepSheets) window.loadRepSheets();
  if (OU.ov.live_active) ouStartPoll();
}
window.loadOutletUpdates = loadOutletUpdates;

function renderOuBadge() {
  const b = document.getElementById("nav-ou-badge"); if (!b) return;
  const t = OU.ov ? OU.ov.board.filter(x => ["missing", "failed"].includes(x.state)).length : 0, w = window.RS_WAITING || 0;
  b.textContent = t + w; b.style.display = t + w ? "" : "none";
  b.title = [t ? `${t} territory(ies) need attention` : "", w ? `${w} rep sheet(s) waiting for your approval` : ""].filter(Boolean).join(" · ");
}

function renderOuDates() {
  const o = OU.ov, d = o.day;
  document.getElementById("ou-h-time").textContent = o.settings.ou_prepare_time;
  const tag = (iso) => iso === o.today ? '<em class="ou-tag">TODAY</em>' : iso === o.target_day && o.target_day !== o.today ? '<em class="ou-tag next">PREPARING</em>' : "";
  document.getElementById("ou-dates").innerHTML = `
    <button class="btn-outline btn-small" data-ou-day="${OU_ADD(d, -1)}">&#8249; ${esc(OU_LABEL(OU_ADD(d, -1)))}</button>
    <div class="ou-day-now"><b>${esc(OU_LABEL(d, true))}</b> ${tag(d)}</div>
    <button class="btn-outline btn-small" data-ou-day="${OU_ADD(d, 1)}">${esc(OU_LABEL(OU_ADD(d, 1)))} &#8250;</button>
    ${d !== o.target_day ? `<button class="link-btn" data-ou-day="${o.target_day}">Back to ${o.target_day === o.today ? "today" : "the day being prepared"}</button>` : ""}`;
  const due = o.due.length, total = o.working_count;
  const tgt = OU_LABEL(o.target_day, false);
  document.getElementById("ou-prepared").innerHTML = !total ? `<span class="hint">Switch on working territories to start.</span>`
    : due ? `<span class="chip chip-warn">Preparing ${esc(tgt)}: ${due} of ${total} itinerary(ies) still to read</span>
             <span class="hint">${o.settings.auto_outlet_updates === "1" && o.settings.paused !== "1" ? "Chimera reads them by itself in a moment." :
               o.settings.paused === "1" ? "Automation is paused (Schedules) — read them now with the button above." : "Automatic preparing is off — read them now."}</span>`
    : `<span class="chip chip-ok">&#10003; ${esc(tgt)} is prepared</span><span class="hint">every working territory's itinerary has been read.</span>`;
}

function ouBoardRows() {
  const f = OU.filter;
  return OU.ov.board.filter(b => f === "all" || (f === "route" && b.state === "route") ||
    (f === "attn" && ["missing", "failed", "not_read"].includes(b.state)) || (f === "off" && ["holiday", "no_route"].includes(b.state)));
}

function renderOuBoard() {
  const o = OU.ov, c = o.counts;
  document.getElementById("ou-board-day").textContent = OU_LABEL(o.day, true);
  document.getElementById("ou-st-working").innerHTML = `${o.working_count}<span class="stat-of"> / ${o.territories.length}</span>`;
  document.getElementById("ou-st-working-foot").textContent = o.territories.length ? "switched on in Working Territories" : "detect or add territories";
  document.getElementById("ou-st-routes").textContent = c.route || 0;
  document.getElementById("ou-st-off").textContent = (c.holiday || 0) + (c.no_route || 0);
  const attn = (c.missing || 0) + (c.failed || 0) + (c.not_read || 0);
  document.getElementById("ou-st-attn").textContent = attn;
  document.getElementById("ou-st-attn-foot").textContent = attn ? [c.missing && `${c.missing} missing in SFA`, c.failed && `${c.failed} failed`,
    c.not_read && `${c.not_read} not read`].filter(Boolean).join(" · ") : "nothing to fix";
  const rows = ouBoardRows();
  document.getElementById("ou-board").innerHTML = !o.working_count
    ? `<div class="empty-state"><b>No working territories yet.</b><p class="hint">Detect the territories from SFA, then switch on the ones Chimera should look after.</p>
         <button class="btn-primary" onclick="selectOuTab('territories')">Set up working territories</button></div>`
    : rows.length ? rows.map(b => `
      <div class="ou-card st-${b.state}">
        <div class="ou-card-top"><div><div class="ou-terr">${esc(b.territory)}</div>
          <div class="hint">${b.rep ? esc(b.rep) : "Rep: read from the itinerary"}${b.distributor ? " · " + esc(b.distributor) : ""}</div></div>
          ${b.badge ? `<span class="ou-badge ${b.badge === "H" ? "h" : "wd"}">${esc(b.badge)}</span>` : ""}</div>
        <div class="ou-route">${b.state === "route" ? esc(b.route)
          : `<span class="ou-noroute">${{ holiday: "Holiday — no route", no_route: "No route set (-)", missing: "No itinerary in SFA yet",
               failed: "Couldn't read the itinerary", not_read: "Itinerary not read yet" }[b.state] || ""}</span>`}</div>
        <div class="ou-card-msg">${ouChip(b.state)} ${b.message ? `<span class="hint">${esc(b.message)}</span>` : ""}</div>
        <div class="ou-card-foot"><span class="hint">${b.read_at ? "Read " + esc(b.read_at.replace("T", " ").slice(0, 16)) : ""}</span>
          <span><button class="link-btn" data-ou-cal="${esc(b.territory)}">Itinerary</button>
          <button class="link-btn" data-ou-read="${esc(b.territory)}">Read again</button></span></div>
      </div>`).join("")
    : `<p class="hint" style="padding:10px 2px;">Nothing matches this filter.</p>`;
}

function renderOuTerritories() {
  const o = OU.ov, q = OU.tq.toLowerCase();
  document.getElementById("ou-tc-terr").textContent = o.working_count ? o.working_count : "";
  const rows = o.territories.filter(t => (OU.tFilter === "all" || (OU.tFilter === "on") === t.enabled)
    && (!q || [t.territory, t.rep, t.code, t.area, t.region, t.supervisor_area].some(x => (x || "").toLowerCase().includes(q))));
  document.getElementById("ou-t-count").textContent = `${o.working_count} of ${o.territories.length} working`;
  const ms = o.months;
  document.getElementById("ou-t-table").innerHTML = o.territories.length ? (rows.length ? `<table class="report-table ou-t-table"><thead><tr>
      <th style="width:70px">Working</th><th>Territory</th><th>Rep (from itinerary)</th><th>Manager</th><th>WhatsApp group</th>
      <th>Itinerary</th><th style="width:230px"></th></tr></thead><tbody>${rows.map(t => {
        const st = o.month_status[t.territory] || {};
        return `<tr class="${t.enabled ? "" : "row-off"}">
          <td><label class="switch2"><input type="checkbox" data-ou-on="${esc(t.territory)}" ${t.enabled ? "checked" : ""}><span></span></label></td>
          <td><b>${esc(t.territory)}</b>${t.sfa_status ? ` <span class="chip ${t.sfa_status.startsWith("ACTIVE") ? "chip-ok" : "chip-grey"}">${t.sfa_status.startsWith("ACTIVE") ? "Active" : "Inactive"} in SFA</span>` : ""}
            ${t.code || t.area ? `<div class="hint" style="font-size:12px;">${esc([t.code, t.area, t.region].filter(Boolean).join(" · "))}</div>` : ""}
            ${t.seen_in_sfa ? "" : '<div class="error" style="font-size:11px;">Not in SFA\'s list any more</div>'}</td>
          <td>${t.rep ? esc(t.rep) : '<span class="hint">—</span>'}${t.distributor ? `<div class="hint" style="font-size:12px;">${esc(t.distributor)}</div>` : ""}</td>
          <td>${t.manager ? esc(t.manager) : '<span class="hint">—</span>'}</td>
          <td>${t.wa_chat ? esc(t.wa_chat) : '<span class="hint">—</span>'}</td>
          <td style="white-space:nowrap;">${ms.map(m => { const s = st[m];
            return `<span class="chip ${s === "ok" ? "chip-ok" : s === "missing" ? "chip-missing" : s === "failed" ? "chip-missing" : "chip-grey"}" title="${esc(OU_MONTH(m))}: ${esc(s || "not read")}">${esc(OU_MONTH(m).slice(0, 3))} ${s === "ok" ? "✓" : s === "missing" ? "missing" : s === "failed" ? "!" : "—"}</span>`; }).join(" ")}</td>
          <td style="text-align:right;white-space:nowrap;"><button class="icon-action" data-ou-cal="${esc(t.territory)}">Calendar</button>
            <button class="icon-action" data-ou-read="${esc(t.territory)}" ${t.enabled ? "" : "disabled"}>Read</button>
            <button class="icon-action" data-ou-edit="${esc(t.territory)}">Edit</button>
            <button class="icon-action danger" data-ou-del="${esc(t.territory)}" title="Remove from Chimera">✕</button></td></tr>`; }).join("")}</tbody></table>`
      : `<p class="hint" style="padding:16px 18px;">Nothing matches.</p>`)
    : `<div class="empty-state"><b>No territories yet.</b><p class="hint">Chimera reads every territory from SFA's View Territory page (code, area, Active/Inactive). New territories are added switched off.</p>
         <button class="btn-primary" id="ou-detect-empty">Detect territories from SFA</button> <button class="btn-outline" onclick="ouAddTerritory()">+ Add by name</button></div>`;
  const de = document.getElementById("ou-detect-empty"); if (de) de.onclick = ouDetect;
}

function renderOuSettings() {
  const s = OU.ov.settings;
  document.getElementById("ou-auto").checked = s.auto_outlet_updates === "1";
  document.getElementById("ou-prepare-time").value = s.ou_prepare_time;
  document.getElementById("ou-tabs-range").value = s.ou_tabs; document.getElementById("ou-tabs-val").textContent = s.ou_tabs;
  document.getElementById("ou-itin-url").value = s.ou_itinerary_url;
  document.getElementById("ou-outlets-url").value = s.ou_outlets_url;
  document.getElementById("ou-terr-url").value = s.ou_territory_url || "";
}

function renderOuRuns() {
  const r = OU.ov.runs;
  document.getElementById("ou-runs").innerHTML = r.length ? `<table class="report-table"><thead><tr><th>#</th><th>Started</th><th>For</th><th>By</th><th>Result</th><th></th></tr></thead><tbody>${r.map(x => `
      <tr><td>${x.id}</td><td>${esc(x.started_at.replace("T", " "))}</td><td>${x.target_day ? esc(OU_LABEL(x.target_day)) : ""}</td><td>${esc(x.trigger)}</td>
        <td style="font-size:12px;">${chip({ done: "success", failed: "failed", cancelled: "cancelled", running: "running", interrupted: "interrupted" }[x.status] || x.status)} ${esc(x.message || "")}</td>
        <td style="text-align:right;"><button class="icon-action" data-ou-runlog="${x.id}">Copy log</button></td></tr>`).join("")}</tbody></table>`
    : `<p class="hint" style="padding:12px 18px 18px;">No itinerary has been read yet.</p>`;
}

function selectOuTab(t) {
  document.querySelectorAll("#ou-tabs .tab").forEach(b => b.classList.toggle("active", b.dataset.tab === t));
  ["board", "territories", "sheets", "settings"].forEach(x => { document.getElementById("ou-panel-" + x).style.display = x === t ? "block" : "none"; });
  if (t === "sheets" && window.loadRepSheets) window.loadRepSheets();
}
window.selectOuTab = selectOuTab;
window.renderOuBadge = renderOuBadge;

// ---------------------------------------------------------------- events
document.getElementById("ou-tabs").addEventListener("click", (e) => { const t = e.target.closest(".tab"); if (t) selectOuTab(t.dataset.tab); });
document.getElementById("view-outlet-updates").addEventListener("click", async (e) => {
  const t = e.target.closest("button"); if (!t) return;
  if (t.dataset.ouDay) { OU.day = t.dataset.ouDay; loadOutletUpdates(OU.day); }
  if (t.dataset.ouCal) openItineraryCalendar(t.dataset.ouCal, (OU.day || OU.ov.target_day).slice(0, 7));
  if (t.dataset.ouRead) startOuRead([t.dataset.ouRead], null);
  if (t.dataset.ouEdit) openOuTerritoryEditor(t.dataset.ouEdit);
  if (t.dataset.ouDel) { const n = t.dataset.ouDel;
    confirmModal("Remove territory", `Remove <b>${esc(n)}</b> from Chimera? Its itinerary copy is kept; nothing changes in SFA. Detect territories adds it back (switched off).`,
      "Remove", async () => { await API().remove_outlet_territory(n); loadOutletUpdates(); }, true); }
  if (t.dataset.ouRunlog) copyText(await API().get_itinerary_log_text(Number(t.dataset.ouRunlog)), t);
});
document.getElementById("ou-board-filter").addEventListener("click", (e) => {
  const f = e.target.dataset.f; if (!f) return; OU.filter = f;
  document.querySelectorAll("#ou-board-filter button").forEach(b => b.classList.toggle("on", b.dataset.f === f)); renderOuBoard(); });
document.getElementById("ou-t-filter").addEventListener("click", (e) => {
  const f = e.target.dataset.f; if (!f) return; OU.tFilter = f;
  document.querySelectorAll("#ou-t-filter button").forEach(b => b.classList.toggle("on", b.dataset.f === f)); renderOuTerritories(); });
document.getElementById("ou-t-search").addEventListener("input", (e) => { OU.tq = e.target.value; renderOuTerritories(); });
document.getElementById("ou-t-table").addEventListener("change", async (e) => {
  const n = e.target.dataset.ouOn; if (!n) return;
  await API().set_outlet_territories_enabled([n], e.target.checked); loadOutletUpdates();
});
document.getElementById("ou-detect").addEventListener("click", () => ouDetect());
document.getElementById("ou-add-terr").addEventListener("click", () => ouAddTerritory());
document.getElementById("ou-read-now").addEventListener("click", () => {
  if (!OU.ov || !OU.ov.working_count) { selectOuTab("territories"); return; }
  startOuRead(null, null);
});
document.getElementById("ou-tabs-range").addEventListener("input", (e) => { document.getElementById("ou-tabs-val").textContent = e.target.value; });
document.getElementById("ou-save-settings").addEventListener("click", async () => {
  const r = await API().save_outlet_settings({ auto_outlet_updates: document.getElementById("ou-auto").checked ? "1" : "0",
    ou_prepare_time: document.getElementById("ou-prepare-time").value, ou_tabs: document.getElementById("ou-tabs-range").value,
    ou_itinerary_url: document.getElementById("ou-itin-url").value.trim(), ou_outlets_url: document.getElementById("ou-outlets-url").value.trim(),
    ou_territory_url: document.getElementById("ou-terr-url").value.trim() });
  let r2 = { ok: true };
  if (r.ok && API().save_rep_sheet_settings) r2 = await API().save_rep_sheet_settings({
    seq_need_approval: document.getElementById("rs-need-approval").checked ? "1" : "0",
    seq_dry_run: document.getElementById("rs-dry-run").checked ? "1" : "0",
    ou_routes_url: document.getElementById("rs-routes-url").value.trim() });
  if (!r2.ok) r.ok = false, r.message = r2.message;
  if (window.loadRepSheets) window.loadRepSheets();
  document.getElementById("ou-settings-msg").innerHTML = `<span class="${r.ok ? "hint" : "error"}">${esc(r.ok ? "Saved." : r.message)}</span>`;
  if (r.ok) loadOutletUpdates();
});

async function ouDetect() {
  openModal({ title: "Detect territories from SFA", body: `<p><span class="spinner"></span> Logging in to SFA and reading every page of View Territory (nothing is changed)…</p>`,
    footer: `<button class="btn-outline" onclick="closeModal()">Close</button>` });
  const r = await API().detect_outlet_territories();
  document.getElementById("modal-body").innerHTML = r.ok
    ? `<div class="ok-box">${esc(r.message)}</div>${r.added && r.added.length ? `<p class="hint">New: ${esc(r.added.join(", "))}</p>` : ""}
       <p>Switch on the territories that are working now, in <b>Working Territories</b>.</p>`
    : `<div class="problem-box">${esc(r.message)}</div>`;
  document.getElementById("modal-footer").innerHTML = `<button class="btn-primary" id="ou-det-ok">${r.ok ? "Choose working territories" : "OK"}</button>`;
  document.getElementById("ou-det-ok").onclick = () => { closeModal(true); OU.tFilter = r.ok ? "all" : OU.tFilter;
    document.querySelectorAll("#ou-t-filter button").forEach(b => b.classList.toggle("on", b.dataset.f === OU.tFilter));
    selectOuTab("territories"); loadOutletUpdates(); };
}

function ouAddTerritory() {
  openModal({ title: "Add a territory", body: `<label class="field">Territory, exactly as SFA shows it <input id="ou-new-terr" placeholder="e.g. Galnewa"></label>
      <div id="ou-new-err"></div><p class="hint">Easier: <b>Detect territories from SFA</b> lists every territory with the exact spelling.</p>`,
    footer: `<button class="btn-outline" onclick="closeModal()">Cancel</button><button class="btn-primary" id="ou-new-ok">Add and switch on</button>` });
  const go = async () => { const r = await API().add_outlet_territory(document.getElementById("ou-new-terr").value);
    if (!r.ok) { document.getElementById("ou-new-err").innerHTML = `<div class="problem-box">${esc(r.message)}</div>`; return; }
    closeModal(true); loadOutletUpdates(); };
  document.getElementById("ou-new-ok").onclick = go;
  document.getElementById("ou-new-terr").addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
  document.getElementById("ou-new-terr").focus();
}

function openOuTerritoryEditor(name) {
  const t = OU.ov.territories.find(x => x.territory === name); if (!t) return;
  openModal({ title: `Territory — ${name}`, body: `<div id="ou-ed-err"></div>
    <div class="grid-2">
      <label class="field">Rep (read from the itinerary) <input value="${esc(t.rep || "—")}" disabled></label>
      <label class="field">Distributor (from the itinerary) <input value="${esc(t.distributor || "—")}" disabled></label>
      <label class="field">Manager <input id="ou-ed-manager" value="${esc(t.manager)}" placeholder="optional"></label>
      <label class="field">WhatsApp group for the daily sheet link
        <div class="wa-pick"><input id="ou-ed-chat" value="${esc(t.wa_chat)}" autocomplete="off" placeholder="Search or pick a chat…">
          <button type="button" class="wa-pick-arrow" tabindex="-1">&#9662;</button><div class="wa-list" id="ou-ed-chat-list" style="display:none;"></div></div></label>
    </div>
    <label class="field">Google Sheet <input value="${esc(t.sheet_url || "")}" disabled placeholder="Created by Chimera in the next update (step 2)"></label>
    <label class="field">Notes <input id="ou-ed-notes" value="${esc(t.notes)}"></label>`,
    footer: `<button class="btn-outline" onclick="closeModal()">Cancel</button><button class="btn-primary" id="ou-ed-save">Save</button>` });
  if (typeof waChatPicker === "function") waChatPicker(document.getElementById("ou-ed-chat"), document.getElementById("ou-ed-chat-list"));
  document.getElementById("ou-ed-save").onclick = async () => {
    const r = await API().update_outlet_territory(name, { manager: document.getElementById("ou-ed-manager").value,
      wa_chat: document.getElementById("ou-ed-chat").value, notes: document.getElementById("ou-ed-notes").value });
    if (!r.ok) { document.getElementById("ou-ed-err").innerHTML = `<div class="problem-box">${esc(r.message)}</div>`; return; }
    closeModal(true); loadOutletUpdates();
  };
}

// ---------------------------------------------------------------- itinerary calendar
async function openItineraryCalendar(territory, month) {
  const r = await API().get_itinerary_month(territory, month);
  const info = r.info, today = OU.ov.today, target = OU.ov.target_day;
  const [y, m] = month.split("-").map(Number);
  const prev = OU_ISO(new Date(y, m - 2, 1)).slice(0, 7), next = OU_ISO(new Date(y, m, 1)).slice(0, 7);
  const first = new Date(y, m - 1, 1).getDay();
  const byDay = Object.fromEntries(r.days.map(d => [d.day, d]));
  const n = new Date(y, m, 0).getDate();
  let cells = "";
  for (let k = 0; k < first; k++) cells += `<div class="ou-cal-cell blank"></div>`;
  for (let d = 1; d <= n; d++) {
    const iso = `${month}-${String(d).padStart(2, "0")}`, x = byDay[iso];
    cells += `<div class="ou-cal-cell ${x ? "s-" + x.state : "s-none"} ${iso === today ? "today" : ""} ${iso === target && target !== today ? "target" : ""}">
      <div class="ou-cal-top">${x ? `<span class="ou-badge ${x.badge === "H" ? "h" : "wd"}">${esc(x.badge)}</span>` : "<span></span>"}<b>${d}</b></div>
      <div class="ou-cal-route">${x ? (x.state === "route" ? esc(x.route) : x.state === "holiday" ? "<i>Holiday</i>" : "<i>No route</i>") : ""}</div>
      ${iso === today ? '<em class="ou-tag">TODAY</em>' : iso === target && target !== today ? '<em class="ou-tag next">PREPARING</em>' : ""}</div>`;
  }
  const wd = r.days.filter(d => d.state === "route").length, hol = r.days.filter(d => d.state === "holiday").length,
        nr = r.days.filter(d => d.state === "no_route").length;
  openModal({ title: `${territory} — ${OU_MONTH(month)} itinerary`, size: "xl", body: `
    <div class="ou-cal-head">
      <div>${info ? `<b>${esc(info.rep || "")}</b>${info.distributor ? " · " + esc(info.distributor) : ""}
        <div class="hint">${info.status === "ok" ? `Read from SFA ${esc(info.read_at.replace("T", " ").slice(0, 16))}` :
          info.status === "missing" ? `SFA had no itinerary for this month (checked ${esc(info.read_at.replace("T", " ").slice(0, 16))})` :
          `Couldn't read it: ${esc(info.message)}`}${info.message && info.status === "ok" ? " · " + esc(info.message) : ""}</div>`
        : `<span class="hint">This month hasn't been read from SFA yet.</span>`}</div>
      <div class="ou-cal-nav"><button class="btn-outline btn-small" data-cal-m="${prev}">&#8249; ${esc(OU_MONTH(prev).split(" ")[0])}</button>
        <button class="btn-outline btn-small" data-cal-m="${next}">${esc(OU_MONTH(next).split(" ")[0])} &#8250;</button></div>
    </div>
    ${r.days.length ? `<div class="ou-cal-sum"><span class="chip chip-ok">${wd} route days</span> <span class="chip chip-warn">${hol} holidays</span>${nr ? ` <span class="chip chip-grey">${nr} with no route</span>` : ""}</div>` : ""}
    <div class="ou-cal-bar">${esc(OU_MONTH(month).toUpperCase())}</div>
    <div class="ou-cal">${["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].map(x => `<div class="ou-cal-dow">${x}</div>`).join("")}${cells}</div>`,
    footer: `${info && info.sfa_url ? `<span class="hint left">${esc(info.sfa_url)}</span>` : ""}<button class="btn-outline" onclick="closeModal()">Close</button>
      <button class="btn-primary" id="ou-cal-read">&#8635; Read ${esc(OU_MONTH(month).split(" ")[0])} again from SFA</button>` });
  document.querySelectorAll("[data-cal-m]").forEach(b => b.onclick = () => openItineraryCalendar(territory, b.dataset.calM));
  document.getElementById("ou-cal-read").onclick = () => { closeModal(true); startOuRead([territory], month); };
}
window.openItineraryCalendar = openItineraryCalendar;

// ---------------------------------------------------------------- runs + live window
async function startOuRead(territories, month) {
  const r = await API().start_itinerary_read(territories, month, false);
  if (!r.ok) { openModal({ title: "Couldn't start", body: `<div class="problem-box">${esc(r.message)}</div>`, footer: `<button class="btn-primary" onclick="closeModal()">OK</button>` }); return; }
  openOuMonitor();
}

function openOuMonitor() {
  OU.monOpen = true;
  openModal({ title: "Reading itineraries from SFA", size: "xl", body: `<div id="oum-body"><span class="spinner"></span> Starting…</div>`,
    footer: `<button class="btn-stop left" id="oum-stop">&#9632; Stop now</button><button class="btn-outline" id="oum-min">Minimise</button>`,
    onClose: () => { OU.monOpen = false; } });
  document.getElementById("oum-min").onclick = () => closeModal();
  document.getElementById("oum-stop").onclick = () => openStopNow();
  ouStartPoll();
}
window.openOuMonitor = openOuMonitor;

function renderOuMonitor(s) {
  const el = document.getElementById("oum-body"); if (!el) return;
  const lanes = s.lanes || [], steps = s.steps || [];
  const icon = (st) => st === "done" ? "✓" : st === "failed" ? "!" : st === "skipped" ? "–" : "";
  const fin = (l) => ["done", "missing", "failed", "cancelled"].includes(l.status);
  const pct = lanes.length ? Math.round(100 * lanes.reduce((a, l) => a + (fin(l) ? steps.length : steps.filter(x => ["done", "skipped"].includes(l.steps[x])).length), 0) / (lanes.length * steps.length || 1)) : 0;
  const stChip = { done: ["chip-ok", "Read"], missing: ["chip-missing", "No itinerary in SFA"], failed: ["chip-missing", "Failed"],
                   running: ["chip-warn", "Reading"], queued: ["chip-grey", "Queued"], cancelled: ["chip-grey", "Stopped"] };
  el.innerHTML = `<div style="display:flex;justify-content:space-between;align-items:center;">
      <div><b>${{ logging_in: "Logging in to SFA…", reading: "Reading the monthly itineraries…", done: "Finished" }[s.phase] || "Starting…"}</b>
        <span class="hint">· ${esc(s.trigger || "")}${s.target_day ? " · preparing " + esc(OU_LABEL(s.target_day)) : ""}${s.run_id ? ` · run #${s.run_id}` : ""} · read only</span></div>
      <div>${s.result ? chip({ done: "success", failed: "failed", cancelled: "cancelled", busy: "busy" }[s.result.status] || s.result.status) : ""}</div></div>
    <div class="progress"><div style="width:${s.active ? Math.min(99, pct) : 100}%"></div></div>
    ${s.result && s.result.message ? `<div class="${s.result.ok ? "ok-box" : "problem-box"}">${esc(s.result.message)}</div>` : ""}
    <div class="lanes">${lanes.map(l => { const c = stChip[l.status] || ["chip-grey", l.status];
      return `<div class="lane ${["failed", "missing"].includes(l.status) ? "failed" : l.status === "done" ? "done" : ""}">
        <div><div class="lane-name">${esc(l.name)}</div><div class="lane-msg">${esc(l.message || (l.status === "queued" ? "Waiting for a free tab…" : ""))}</div></div>
        <div class="stepper">${steps.map(x => `<div class="st ${l.steps[x]}"><span class="dot">${icon(l.steps[x])}</span>${OU_STEP_LABEL[x] || x}</div>`).join("")}</div>
        <div class="lane-meta"><span class="chip ${c[0]}">${esc(c[1])}</span><div>${l.elapsed ? l.elapsed + "s" : ""}</div></div></div>`; }).join("")}</div>
    <div class="section-title">Log <button class="btn-outline btn-small" id="oum-copy">Copy log</button></div>
    <div class="log-box">${(s.log || []).slice(-150).map(x => `<div class="lv-${x.level}">${esc(x.t)} ${x.lane ? `<span class="who">[${esc(x.lane)}]</span> ` : ""}${esc(x.msg)}</div>`).join("")}</div>`;
  const stop = document.getElementById("oum-stop"); if (stop) stop.style.display = s.active ? "" : "none";
  const min = document.getElementById("oum-min"); if (min) min.textContent = s.active ? "Minimise" : "Close";
  const cp = document.getElementById("oum-copy"); if (cp) cp.onclick = async (e) => copyText(await API().get_itinerary_log_text(null), e.target);
}

function ouStartPoll() {
  if (OU.poll) return;
  const tick = async () => {
    let s; try { s = await API().get_itinerary_live(); } catch (e) { return; }
    onItineraryLive(s);
    if (!s.active) { clearInterval(OU.poll); OU.poll = null; }
  };
  OU.poll = setInterval(tick, 1000); tick();
}

// called every second while a read is live (and by the top bar's 3-second check)
function onItineraryLive(s) {
  if (OU.monOpen) renderOuMonitor(s);
  if (s.active) { OU.wasActive = true; if (!OU.poll) ouStartPoll(); return; }
  if (!OU.wasActive) return;
  OU.wasActive = false;
  if (document.getElementById("view-outlet-updates").style.display !== "none") loadOutletUpdates();
  else API().get_outlet_updates_overview(null).then(ov => { OU.ov = ov; renderOuBadge(); }).catch(() => {});
  if (typeof refreshBell === "function") refreshBell();
}
window.onItineraryLive = onItineraryLive;

window.addEventListener("pywebviewready", () => {
  if (typeof API().get_outlet_updates_overview !== "function") return;
  API().get_outlet_updates_overview(null).then(ov => { OU.ov = ov; renderOuBadge(); if (ov.live_active) { OU.wasActive = true; ouStartPoll(); } }).catch(() => {});
});
