// activity.js — Activity Monitor: one timeline of everything Chimera did in a period (read only).
// Relies on openModal/closeModal/esc/API (automation.js), copyText/localToday/fileName (main.js).

const AM = { from: null, to: null, period: "today", process: "all", q: "", data: null, ldFilter: "all", olFilter: "all", timer: null };
const AM_COLORS = { downloads: "#3b6fd4", reports: "#B5121B", whatsapp: "#1f9d55", loadings: "#d98b00",
                    distributors: "#7b4fc9", itinerary: "#138a9e", outlets: "#c2185b", attendance: "#5d4037", schedules: "#6E6F71" };
const AM_STATUS = { ok: ["chip-ok", "Success"], fail: ["chip-missing", "Failed"], warn: ["chip-warn", "Attention"],
                    running: ["chip-warn", "Running"], info: ["chip-grey", "Info"] };
const amChip = (s) => { const c = AM_STATUS[s] || AM_STATUS.info; return `<span class="chip ${c[0]}">${c[1]}</span>`; };
const amIso = (d) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
const amTime = (t, withDay) => {
  if (!t) return "&mdash;";
  const d = new Date(t);
  if (isNaN(d)) return esc(t);
  const hm = d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  return withDay ? `${d.toLocaleDateString("en-GB", { day: "2-digit", month: "short" })} ${hm}` : hm;
};
const amMoney = (n) => Number(n || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function amRange(p) {
  const t = new Date();
  if (p === "yesterday") { t.setDate(t.getDate() - 1); return [amIso(t), amIso(t)]; }
  if (p === "7") { const a = new Date(); a.setDate(a.getDate() - 6); return [amIso(a), amIso(t)]; }
  if (p === "month") return [amIso(new Date(t.getFullYear(), t.getMonth(), 1)), amIso(t)];
  if (p === "custom") return [document.getElementById("am-from").value || amIso(t), document.getElementById("am-to").value || amIso(t)];
  return [amIso(t), amIso(t)];
}

async function loadActivity() {
  [AM.from, AM.to] = amRange(AM.period);
  let r;
  try {
    if (typeof API().get_activity !== "function") throw new Error("OUTDATED");
    r = await API().get_activity(AM.from, AM.to, AM.process, AM.q);
  } catch (err) {
    const old = String(err).includes("OUTDATED") || String(err).includes("not a function");
    document.getElementById("am-ev-table").innerHTML = `<tr><td><div class="problem-box">${old
      ? "This screen needs the latest backend files (backend\\api.py and backend\\activity.py). Replace them, then restart Chimera."
      : "Couldn't load the activity: " + esc(String(err))}</div></td></tr>`;
    return;
  }
  if (!r.ok) { document.getElementById("am-ev-table").innerHTML = `<tr><td><div class="problem-box">${esc(r.message)}</div></td></tr>`; return; }
  AM.data = r;
  renderAmProcs(); renderAmKpis(); renderAmChart(); renderAmLoadings(); renderAmOutlets(); renderAmEvents();
}
window.loadActivity = loadActivity;

function renderAmProcs() {
  const k = AM.data.kpis.by_process, all = Object.values(k).reduce((a, b) => a + b, 0);
  const btn = (p, label, n) => `<button data-proc="${p}" class="${AM.process === p ? "on" : ""}">${p !== "all"
    ? `<i style="background:${AM_COLORS[p]}"></i>` : ""}${esc(label)} <em>${n}</em></button>`;
  document.getElementById("am-procs").innerHTML = btn("all", "Everything", all)
    + Object.entries(AM.data.processes).map(([p, l]) => btn(p, l, k[p])).join("");
}

function renderAmKpis() {
  const k = AM.data.kpis, ld = AM.process === "all" || AM.process === "loadings";
  const days = AM.from === AM.to ? new Date(AM.from + "T00:00:00").toLocaleDateString("en-GB", { weekday: "short", day: "2-digit", month: "short" })
    : `${AM.from} → ${AM.to}`;
  const card = (label, val, foot, cls = "") => `<div class="stat-card ${cls}"><div class="stat-label">${label}</div>
    <div class="stat-value">${val}</div><div class="stat-foot">${foot}</div></div>`;
  document.getElementById("am-kpis").innerHTML =
    card("ACTIVITIES", k.events, esc(days))
    + card("SUCCESSFUL", k.ok, k.events ? `${Math.round(100 * k.ok / k.events)}% of all` : "&nbsp;", "am-ok")
    + card("FAILED / ATTENTION", `${k.failed} <small>/ ${k.warn}</small>`, k.failed ? "open the row for the log" : "nothing failed", k.failed ? "am-bad" : "")
    + (AM.process === "outlets" ? card("OUTLETS SAVED IN SFA", k.outlets_saved || 0,
          `${k.outlets_mandatory || 0} made Mandatory${k.outlets_failed ? ` &middot; ${k.outlets_failed} failed` : ""} &middot; ${k.sheets_received || 0} sheet(s) received`)
       : ld ? card("LOADINGS CONFIRMED", k.loadings_confirmed, `Rs ${amMoney(k.loadings_amount)} &middot; ${k.loadings_added} added`)
          : card("WHATSAPP SENT", k.whatsapp_sent, "deliveries to chats"));
}

function renderAmChart() {
  const rows = AM.data.per_day, procs = Object.keys(AM.data.processes).filter(p => AM.process === "all" || p === AM.process);
  const card = document.getElementById("am-chart-card");
  card.style.display = rows.length > 1 ? "" : "none";
  if (rows.length <= 1) return;
  const max = Math.max(1, ...rows.map(r => procs.reduce((a, p) => a + r[p], 0)));
  const step = rows.length > 31 ? 7 : rows.length > 14 ? 3 : 1;
  document.getElementById("am-chart").innerHTML = rows.map((r, i) => {
    const tot = procs.reduce((a, p) => a + r[p], 0);
    const d = new Date(r.day + "T00:00:00");
    const tip = `${d.toLocaleDateString("en-GB", { weekday: "short", day: "2-digit", month: "short" })}: ${tot} — `
      + procs.filter(p => r[p]).map(p => `${AM.data.processes[p]} ${r[p]}`).join(", ");
    return `<div class="am-col" title="${esc(tip)}" data-day="${r.day}"><div class="am-stack" style="height:${Math.round(100 * tot / max)}%">`
      + procs.filter(p => r[p]).map(p => `<span style="background:${AM_COLORS[p]};flex:${r[p]}"></span>`).join("")
      + `</div><b>${i % step === 0 ? d.getDate() : ""}</b></div>`;
  }).join("");
  document.getElementById("am-legend").innerHTML = procs.map(p => `<span><i style="background:${AM_COLORS[p]}"></i>${esc(AM.data.processes[p])}</span>`).join("");
}

function amLdRows() {
  const f = AM.ldFilter, rows = AM.data.loadings;
  if (f === "all") return rows;
  if (f === "problem") return rows.filter(r => ["failed", "confirm_failed", "changed"].includes(r.state));
  if (f === "added") return rows.filter(r => ["added", "dry_run"].includes(r.state));
  return rows.filter(r => r.state === f);
}

function renderAmLoadings() {
  const show = AM.process === "loadings";
  document.getElementById("am-loadings-card").style.display = show ? "" : "none";
  if (!show) return;
  const rows = amLdRows(), multi = AM.from !== AM.to;
  const st = { confirmed: "ok", added: "ok", pending_approval: "warn", confirming: "running", failed: "fail", confirm_failed: "fail",
               changed: "fail", dry_run: "info", skipped: "info", superseded: "info", nothing_pending: "info" };
  document.getElementById("am-ld-table").innerHTML = `<thead><tr><th>${multi ? "When" : "Time"}</th><th>Distributor</th><th>Loading number</th>
      <th>Rep</th><th>Loading date</th><th>What happened</th><th class="num">Qty</th><th class="num">Amount (Rs)</th><th></th></tr></thead><tbody>`
    + (rows.length ? rows.map(r => `<tr><td>${amTime(r.time, multi)}</td><td><b>${esc(r.distributor)}</b></td>
        <td class="mono">${esc(r.loading_number || "—")}</td><td>${esc(r.rep || "—")}</td><td>${esc(r.loading_date || "—")}</td>
        <td><span class="chip ${(AM_STATUS[st[r.state] || "info"])[0]}">${esc(r.action)}</span>${r.message
          ? `<div class="am-note">${esc(r.message)}</div>` : ""}</td>
        <td class="num">${r.qty ? r.qty.toLocaleString() : "—"}</td><td class="num">${r.amount ? amMoney(r.amount) : "—"}</td>
        <td><button class="link-btn" data-am-detail="loading" data-am-id="${r.run_id}">Run log</button></td></tr>`).join("")
      : `<tr><td colspan="9" class="hint am-empty">No loadings ${AM.ldFilter === "all" ? "in this period" : "match this filter"}.</td></tr>`)
    + `</tbody>` + (rows.length ? (() => { const c = rows.filter(r => r.state === "confirmed");
        return `<tfoot><tr><td colspan="6"><b>${rows.length} loading(s)</b> &middot; total of the ${c.length} confirmed</td>
        <td class="num"><b>${c.reduce((a, r) => a + r.qty, 0).toLocaleString()}</b></td>
        <td class="num"><b>${amMoney(c.reduce((a, r) => a + r.amount, 0))}</b></td><td></td></tr></tfoot>`; })() : "");
}

function renderAmOutlets() {
  const show = AM.process === "outlets";
  document.getElementById("am-ol-card").style.display = show ? "" : "none";
  if (!show) return;
  const f = AM.olFilter, all = AM.data.outlets || [], multi = AM.from !== AM.to;
  const rows = f === "all" ? all : f === "mandatory" ? all.filter(r => r.outcome === "updated" && r.new_state === "Mandatory" && r.old_state !== "Mandatory")
    : f === "notes" ? all.filter(r => r.remark || r.demand) : all.filter(r => r.outcome === f);
  const arrow = (a, b) => String(a ?? "") === String(b ?? "") ? `<span class="hint">${esc(String(a ?? "—"))}</span>`
    : `<span class="hint">${esc(String(a || "—"))}</span> → <b>${esc(String(b ?? "—"))}</b>`;
  document.getElementById("am-ol-table").innerHTML = `<thead><tr><th>${multi ? "When" : "Time"}</th><th>Route</th><th>Outlet</th><th>Sequence</th>
      <th>Visit State</th><th>Top 15</th><th>What happened</th><th>Remark / demand</th><th></th></tr></thead><tbody>`
    + (rows.length ? rows.slice(0, 1500).map(r => `<tr><td>${amTime(r.time, multi)}</td>
        <td><b>${esc(r.route)}</b><div class="am-note">${esc(r.territory)} · ${esc(r.route_code)}</div></td>
        <td class="mono">${esc(r.outlet_code)}<div class="am-note">${esc(r.outlet_name || "")}</div></td>
        <td>${arrow(r.old_seq, r.new_seq)}</td><td>${arrow(r.old_state, r.new_state)}</td><td>${r.top ? "✓" : ""}</td>
        <td><span class="chip ${(AM_STATUS[r.status] || AM_STATUS.info)[0]}">${esc(r.action)}</span>${r.message ? `<div class="am-note">${esc(r.message)}</div>` : ""}</td>
        <td>${esc(r.remark || "")}${r.demand ? `<div class="am-note">Demand: ${esc(r.demand)}</div>` : ""}</td>
        <td><button class="link-btn" data-am-detail="sheet" data-am-id="${r.sheet}">Sheet</button></td></tr>`).join("")
      : `<tr><td colspan="9" class="hint am-empty">No outlet ${f === "all" ? "was changed in this period" : "matches this filter"}.</td></tr>`)
    + `</tbody>`;
}

function renderAmEvents() {
  const ev = AM.data.events, multi = AM.from !== AM.to;
  document.getElementById("am-ev-count").textContent = `${ev.length} activit${ev.length === 1 ? "y" : "ies"}${AM.data.truncated ? " (first 1,500 shown — export for all)" : ""}`;
  let lastDay = "";
  document.getElementById("am-ev-table").innerHTML = `<thead><tr><th>Time</th><th>Process</th><th>What happened</th><th>Started by</th>
      <th>Result</th><th></th></tr></thead><tbody>`
    + (ev.length ? ev.map((e, i) => {
        const day = (e.time || "").slice(0, 10);
        const sep = multi && day !== lastDay ? `<tr class="am-day"><td colspan="6">${new Date(day + "T00:00:00").toLocaleDateString("en-GB",
          { weekday: "long", day: "2-digit", month: "long" })}</td></tr>` : "";
        lastDay = day;
        return sep + `<tr><td class="mono">${amTime(e.time)}</td>
          <td><span class="am-proc"><i style="background:${AM_COLORS[e.process]}"></i>${esc(AM.data.processes[e.process] || e.process)}</span></td>
          <td><b>${esc(e.title)}</b>${e.detail ? `<div class="am-note">${esc(e.detail)}</div>` : ""}</td>
          <td>${esc(e.trigger || "—")}</td><td>${amChip(e.status)}</td>
          <td><button class="link-btn" data-am-detail="${e.ref.source}" data-am-id="${e.ref.id}" data-am-i="${i}">Details</button></td></tr>`;
      }).join("")
      : `<tr><td colspan="6" class="hint am-empty">Nothing ${AM.q ? "matches your search" : "happened"} in this period.</td></tr>`)
    + `</tbody>`;
}

async function openAmDetail(source, id, i) {
  const e = i != null ? AM.data.events[Number(i)] : null;
  openModal({ title: e ? e.title : "Run details", size: "lg",
    body: `${e ? `<p class="hint">${esc((e.time || "").replace("T", " "))} &middot; ${esc(AM.data.processes[e.process])} &middot; ${amChip(e.status)}</p>` : ""}
           <pre class="log-box" id="am-detail-text">Loading…</pre>`,
    footer: `<button class="btn-outline" id="am-detail-copy">Copy</button><button class="btn-primary" onclick="closeModal()">Close</button>` });
  const r = await API().get_activity_detail(source, Number(id));
  const text = r.ok ? r.text : r.message;
  document.getElementById("am-detail-text").textContent = text;
  document.getElementById("am-detail-copy").onclick = (ev) => copyText(text, ev.target);
}

async function amExport(btn) {
  btn.disabled = true; const t = btn.textContent; btn.textContent = "Exporting…";
  try {
    const r = await API().export_activity(AM.from, AM.to, AM.process, AM.q);
    if (!r.ok) { openModal({ title: "Export failed", body: `<p>${esc(r.message)}</p>`, footer: `<button class="btn-primary" onclick="closeModal()">OK</button>` }); return; }
    openModal({ title: "Exported", body: `<p>Saved <b>${esc(fileName(r.path))}</b> with three sheets: Activity, Loadings and Summary.</p>
      <p class="hint">${esc(r.folder)}</p>`,
      footer: `<button class="btn-outline" id="am-open-folder">Open folder</button><button class="btn-primary" id="am-open-file">Open file</button>` });
    document.getElementById("am-open-folder").onclick = () => { API().open_path(r.folder); closeModal(); };
    document.getElementById("am-open-file").onclick = () => { API().open_path(r.path); closeModal(); };
  } finally { btn.disabled = false; btn.textContent = t; }
}

(function wireActivity() {
  const v = document.getElementById("view-activity"); if (!v) return;
  const t = amIso(new Date());
  document.getElementById("am-from").value = t; document.getElementById("am-to").value = t;
  document.getElementById("am-period").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-p]"); if (!b) return;
    AM.period = b.dataset.p;
    document.querySelectorAll("#am-period button").forEach(x => x.classList.toggle("on", x === b));
    document.getElementById("am-custom").style.display = AM.period === "custom" ? "" : "none";
    if (AM.period !== "custom") loadActivity();
  });
  document.getElementById("am-apply").onclick = loadActivity;
  document.getElementById("am-procs").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-proc]"); if (!b) return;
    AM.process = b.dataset.proc; loadActivity();
  });
  document.getElementById("am-ld-filter").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-f]"); if (!b) return;
    AM.ldFilter = b.dataset.f;
    document.querySelectorAll("#am-ld-filter button").forEach(x => x.classList.toggle("on", x === b));
    renderAmLoadings();
  });
  document.getElementById("am-ol-filter").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-f]"); if (!b) return;
    AM.olFilter = b.dataset.f;
    document.querySelectorAll("#am-ol-filter button").forEach(x => x.classList.toggle("on", x === b));
    renderAmOutlets();
  });
  document.getElementById("am-search").addEventListener("input", (e) => {
    clearTimeout(AM.timer); AM.timer = setTimeout(() => { AM.q = e.target.value; loadActivity(); }, 300);
  });
  document.getElementById("am-refresh").onclick = loadActivity;
  document.getElementById("am-export").onclick = (e) => amExport(e.currentTarget);
  document.getElementById("am-chart").addEventListener("click", (e) => {
    const c = e.target.closest(".am-col"); if (!c) return;            // click a day's bar to see that day
    AM.period = "custom";
    document.getElementById("am-from").value = document.getElementById("am-to").value = c.dataset.day;
    document.querySelectorAll("#am-period button").forEach(x => x.classList.toggle("on", x.dataset.p === "custom"));
    document.getElementById("am-custom").style.display = "";
    loadActivity();
  });
  v.addEventListener("click", (e) => {
    const b = e.target.closest("[data-am-detail]"); if (b) openAmDetail(b.dataset.amDetail, b.dataset.amId, b.dataset.amI);
  });
})();
