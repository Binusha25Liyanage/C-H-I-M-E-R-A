// rep_attendance.js — Rep Attendance: a day's logins from SFA (two images, Classic / Professional), corrections,
// sending to WhatsApp, history and settings. Relies on esc/API/chip/openModal/closeModal/confirmModal (automation.js),
// copyText (main.js), waChatPicker/SH/openStopNow/switchView (shell.js / main.js).

const AT = { day: null, data: null, style: "classic", poll: null, wasActive: false, hPeriod: "7", chats: [] };
const atApi = () => window.pywebview.api;
const AT_STATE = { built: ["chip-grey", "Images ready"], pending_review: ["chip-warn", "Waiting for your approval"],
                   sent: ["chip-ok", "Sent to WhatsApp"], partly_sent: ["chip-warn", "Partly sent"], send_failed: ["chip-missing", "WhatsApp failed"],
                   not_sent: ["chip-grey", "Not sent"] };
const atIso = (d) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
const atLong = (iso) => new Date(iso + "T00:00:00").toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
const atShortRep = (rep) => { const m = (rep || "").match(/^(CSR|DSR|SR|PSR|REP)\b.* - (.+)$/i); return m ? m[2] : (rep || ""); };

async function loadRepAttendance(day) {
  if (typeof atApi().get_rep_attendance !== "function") {
    document.getElementById("att-kpis").innerHTML = `<div class="problem-box">This screen needs the latest backend files (api.py, rep_attendance.py,
      rep_attendance_images.py, rep_attendance_automation.py). Replace them and restart Chimera.</div>`;
    return;
  }
  if (day) AT.day = day;
  if (!AT.day) AT.day = atIso(new Date());
  document.getElementById("att-day").value = AT.day;
  try { AT.data = await atApi().get_rep_attendance(AT.day); } catch (e) { return; }
  const d = AT.data;
  AT.chats = [...d.settings.att_chats];
  if (d.settings.att_style !== "both") AT.style = d.settings.att_style;
  atRenderPending(); atRenderDay(); atRenderSettings();
  const b = document.getElementById("nav-att-badge");
  if (b) { b.textContent = d.pending.length; b.style.display = d.pending.length ? "" : "none"; }
  if (d.live_active && !AT.poll) atStartPoll();
}
window.loadRepAttendance = loadRepAttendance;

function atRenderPending() {
  const p = AT.data.pending;
  document.getElementById("att-pending").innerHTML = p.map(r => `<div class="warn-box att-pend"><b>Rep attendance ${esc(r.day)}</b> (${esc(r.slot || r.trigger)})
      is waiting for your approval before it goes to WhatsApp.
      <span><button class="btn-outline btn-small" data-att-view="${esc(r.day)}">View</button>
      <button class="btn-primary btn-small" data-att-approve="${r.id}">Approve &amp; send</button>
      <button class="btn-outline btn-small" data-att-skip="${r.id}">Don't send</button></span></div>`).join("");
}

function atRenderDay() {
  const d = AT.data, c = d.counts;
  const card = (l, v, f, cls = "") => `<div class="stat-card ${cls}"><div class="stat-label">${l}</div><div class="stat-value">${v}</div><div class="stat-foot">${f}</div></div>`;
  document.getElementById("att-kpis").innerHTML = d.rows.length || d.meta
    ? card("LOGGED IN", c.logged_in, esc(atLong(d.day)))
      + card("NOT LOGGED OUT YET", c.not_logged_out, "still in the field")
      + card("LOGGED OUT", c.logged_out, "finished the day")
      + card("NOT LOGGED IN", c.working ? c.not_logged_in : "&ndash;", c.working ? `of ${c.working} working territories` : "set working territories in Outlet Updates", c.not_logged_in ? "am-bad" : "")
    : `<div class="rn-quiet" style="grid-column:1/-1;">No rep attendance for ${esc(atLong(d.day))} yet — <b>Download from SFA</b> or <b>Upload Excel</b>.</div>`;
  const m = d.meta;
  document.getElementById("att-source").innerHTML = m ? `From ${m.source === "sfa" ? "SFA" : "an uploaded file"}${m.file_name ? ` (${esc(m.file_name)})` : ""}
      · read ${esc(m.read_at.replace("T", " ").slice(0, 16))}${m.generated_at ? ` · SFA generated it ${esc(m.generated_at)}` : ""}${c.edited ? ` · <b>${c.edited} corrected by you</b>` : ""}` : "";
  // images
  const latest = d.latest;
  const styles = latest ? [...new Set(latest.images.map(i => i.style))] : [];
  if (styles.length && !styles.includes(AT.style)) AT.style = styles[0];
  document.querySelectorAll("#att-style-seg button").forEach(b => { b.classList.toggle("on", b.dataset.s === AT.style); b.style.display = styles.includes(b.dataset.s) ? "" : "none"; });
  document.getElementById("att-style-seg").style.display = styles.length > 1 ? "" : "none";
  const imgs = latest ? latest.images.filter(i => i.style === AT.style) : [];
  const box = document.getElementById("att-images");
  box.innerHTML = imgs.length ? imgs.map(i => `<figure><figcaption>${i.group === "logged_out" ? "Logged out" : "Not logged out yet"}</figcaption>
      <img data-att-img="${esc(i.path)}" alt=""></figure>`).join("")
    : `<p class="hint">${d.rows.length ? "No images yet — click <b>Rebuild images</b>." : "Images appear here once the day is read."}</p>`;
  imgs.forEach(async i => { const b64 = await atApi().get_image_b64(i.path); const el = box.querySelector(`img[data-att-img="${CSS.escape(i.path)}"]`);
    if (el && b64) { el.src = "data:image/png;base64," + b64; el.onclick = () => atZoom(el.src); } });
  const st = latest ? AT_STATE[latest.state] : null;
  document.getElementById("att-send").textContent = latest && latest.state === "sent" ? "Send again" : "Send to WhatsApp";
  document.getElementById("att-send").disabled = !latest;
  document.getElementById("att-rebuild").disabled = !d.rows.length;
  document.getElementById("att-open-folder").disabled = !latest;
  // tables
  const nlo = d.rows.filter(r => r.group === "not_logged_out"), lo = d.rows.filter(r => r.group === "logged_out");
  const table = (rows, out) => rows.length ? `<div class="am-scroll"><table class="report-table am-table att-table"><thead><tr><th>Territory</th><th>Sales rep</th>
      <th>Logged in</th>${out ? "<th>Logged out</th>" : ""}<th></th></tr></thead><tbody>${rows.map(r => `<tr class="${r.edited ? "att-edited" : ""}">
      <td><b>${esc(r.territory)}</b><div class="am-note">${esc(r.rep_type || "")}${r.asm ? " · " + esc(r.asm) : ""}</div></td>
      <td>${esc(atShortRep(r.rep))}</td><td class="mono">${esc(r.in_time)}</td>${out ? `<td class="mono">${esc(r.out_time)}</td>` : ""}
      <td style="text-align:right;white-space:nowrap;">${r.edited ? `<span class="chip chip-warn" title="SFA said: ${esc(r.orig_in)} / ${esc(r.orig_out || "-")}">corrected</span> ` : ""}
        <button class="link-btn" data-att-edit="${esc(r.territory)}">Edit</button></td></tr>`).join("")}</tbody></table></div>`
    : `<p class="hint" style="padding:12px 16px;">Nobody.</p>`;
  document.getElementById("att-t-nlo").innerHTML = table(nlo, false);
  document.getElementById("att-t-lo").innerHTML = table(lo, true);
  document.getElementById("att-n-nlo").textContent = nlo.length ? `${nlo.length} rep(s)` : "";
  document.getElementById("att-n-lo").textContent = lo.length ? `${lo.length} rep(s)` : "";
  document.getElementById("att-nli").innerHTML = !c.working ? `<p class="hint">Switch on your working territories in <b>Outlet Updates › Working Territories</b> to see who hasn't logged in.</p>`
    : d.not_logged_in.length ? `<div class="rm-chips">${d.not_logged_in.map(t => `<span class="rm-chip att-nli">${esc(t)}</span>`).join("")}</div>`
    : d.rows.length ? `<p class="hint" style="color:#1f7a43;">Every working territory has logged in.</p>` : `<p class="hint">—</p>`;
  document.getElementById("att-runs").innerHTML = d.runs.length ? `<table class="report-table am-table"><tbody>${d.runs.map(r => {
      const s = AT_STATE[r.state];
      return `<tr><td style="white-space:nowrap;">${esc(r.started_at.slice(11, 16))}</td><td>${esc(r.trigger)}${r.slot ? " " + esc(r.slot) : ""}</td>
        <td>${s ? `<span class="chip ${s[0]}">${esc(s[1])}</span>` : chip({ done: "success", failed: "failed", running: "running", cancelled: "cancelled", interrupted: "interrupted" }[r.status] || r.status)}
          <div class="am-note">${esc(r.message || "")}</div></td>
        <td style="text-align:right;white-space:nowrap;">${r.state === "pending_review" ? `<button class="btn-primary btn-small" data-att-approve="${r.id}">Approve &amp; send</button> ` : ""}
          <button class="icon-action" data-att-log="${r.id}">Copy log</button></td></tr>`; }).join("")}</tbody></table>`
    : `<p class="hint" style="padding:12px 16px;">Nothing has run for this day.</p>`;
}

function atZoom(src) {
  openModal({ title: "Rep attendance", size: "xl", body: `<div style="text-align:center;"><img src="${src}" style="max-width:100%;border:1px solid #eee;"></div>`,
    footer: `<button class="btn-primary" onclick="closeModal()">Close</button>` });
}

function atEdit(territory) {
  const r = AT.data.rows.find(x => x.territory === territory); if (!r) return;
  openModal({ title: `Correct ${territory}`, body: `
      <p class="hint">Only Chimera's copy changes — the SFA export stays as it is. SFA said: logged in <b>${esc(r.orig_in || "-")}</b>, logged out <b>${esc(r.orig_out || "-")}</b>.
        If SFA later shows a different value (e.g. the rep logs out), SFA's new value is used.</p>
      <label class="field">Sales rep <input id="ate-rep" value="${esc(r.rep)}"></label>
      <div class="grid-2"><label class="field">Logged in <input id="ate-in" value="${esc(r.in_time)}" placeholder="HH:MM:SS"></label>
        <label class="field">Logged out <input id="ate-out" value="${esc(r.out_time)}" placeholder="empty = not logged out"></label></div>
      <div id="ate-msg"></div>`,
    footer: `${r.edited ? `<button class="btn-outline left" id="ate-undo">Back to SFA's values</button>` : ""}<button class="btn-outline" onclick="closeModal()">Cancel</button>
      <button class="btn-primary" id="ate-save">Save</button>` });
  const done = async (res) => { if (!res.ok) { document.getElementById("ate-msg").innerHTML = `<div class="problem-box">${esc(res.message)}</div>`; return; }
    closeModal(); await loadRepAttendance(); atHint("Corrected — click <b>Rebuild images</b> to put it on the images."); };
  document.getElementById("ate-save").onclick = async () => done(await atApi().edit_rep_attendance(AT.day, territory,
    { rep: document.getElementById("ate-rep").value, in_time: document.getElementById("ate-in").value, out_time: document.getElementById("ate-out").value }));
  const u = document.getElementById("ate-undo"); if (u) u.onclick = async () => done(await atApi().edit_rep_attendance(AT.day, territory, { undo: true }));
}

function atHint(html) {
  const el = document.getElementById("att-source");
  el.innerHTML = `<span class="att-flash">${html}</span> ` + el.innerHTML;
}

function atAlert(msg, ok) { openModal({ title: "Rep attendance", body: `<div class="${ok ? "ok-box" : "problem-box"}">${esc(msg)}</div>`, footer: `<button class="btn-primary" onclick="closeModal()">OK</button>` }); }

async function atSend(runId) {
  const chats = AT.data.settings.att_chats;
  if (!chats.length) { atAlert("No WhatsApp chats yet — add them in Rep Attendance › Settings."); return; }
  const run = AT.data.runs.find(r => r.id === runId) || AT.data.pending.find(r => r.id === runId) || AT.data.latest;
  confirmModal("Send rep attendance", `Send the <b>${esc(run.style)}</b> images for <b>${esc(atLong(run.day))}</b> to ${chats.map(esc).join(", ")}?`, "Send", async () => {
    const r = await atApi().send_rep_attendance(runId);
    if (!r.ok) { atAlert(r.message); return; }
    atAlert("Sending… (WhatsApp shows the progress; the result appears under Runs for this day).", true);
    setTimeout(() => loadRepAttendance(), 4000); setTimeout(() => loadRepAttendance(), 15000);
  });
}

// ---------------------------------------------------------------- live run
function atStartPoll() {
  if (AT.poll) return;
  AT.poll = setInterval(async () => {
    let s; try { s = await atApi().get_rep_attendance_live(); } catch (e) { return; }
    const btn = document.getElementById("att-download");
    if (s.active) { AT.wasActive = true; btn.disabled = true; btn.innerHTML = `<span class="spinner"></span> ${esc((s.log || []).slice(-1)[0]?.msg || "Working…").slice(0, 60)}`; return; }
    clearInterval(AT.poll); AT.poll = null; btn.disabled = false; btn.innerHTML = "&#8635; Download from SFA";
    if (AT.wasActive) { AT.wasActive = false; if (s.result) atHint(`<b>${s.result.ok ? "Done" : "Problem"}:</b> ${esc(s.result.message || "")}`); loadRepAttendance(); }
  }, 1000);
}

// ---------------------------------------------------------------- history
function atRange() {
  const t = new Date(), y = t.getFullYear(), m = t.getMonth();
  if (AT.hPeriod === "month") return [atIso(new Date(y, m, 1)), atIso(t)];
  if (AT.hPeriod === "last_month") return [atIso(new Date(y, m - 1, 1)), atIso(new Date(y, m, 0))];
  if (AT.hPeriod === "custom") return [document.getElementById("att-h-from").value || atIso(t), document.getElementById("att-h-to").value || atIso(t)];
  const a = new Date(); a.setDate(a.getDate() - 6); return [atIso(a), atIso(t)];
}
async function atHistory() {
  const [a, b] = atRange(), q = document.getElementById("att-h-q").value;
  const h = await atApi().get_rep_attendance_history(a, b, q);
  document.getElementById("att-h-days").textContent = `${h.days.length} day(s) with data · ${a} → ${b}`;
  document.getElementById("att-h-sum").innerHTML = `<thead><tr><th>Territory</th><th>Sales rep</th><th>ASM</th><th class="num">Days logged in</th>
      <th class="num">Days without login</th><th class="num">Average login</th><th class="num">Earliest</th><th class="num">Not logged out</th></tr></thead><tbody>`
    + (h.summary.length ? h.summary.map(s => `<tr><td><b>${esc(s.territory)}</b></td><td>${esc(atShortRep(s.rep))}</td><td>${esc(s.asm)}</td>
        <td class="num">${s.days}</td><td class="num ${s.missing_days ? "error" : ""}">${s.missing_days}</td><td class="num mono">${esc(s.avg_in)}</td>
        <td class="num mono">${esc(s.first_in)}</td><td class="num">${s.not_out}</td></tr>`).join("")
      : `<tr><td colspan="8" class="hint am-empty">No rep attendance in this period.</td></tr>`) + `</tbody>`;
  document.getElementById("att-h-rows").innerHTML = `<thead><tr><th>Date</th><th>Territory</th><th>Sales rep</th><th>Type</th><th>Logged in</th><th>Logged out</th><th>ASM</th></tr></thead><tbody>`
    + h.rows.slice(0, 1500).map(r => `<tr><td>${esc(r.day)}</td><td><b>${esc(r.territory)}</b></td><td>${esc(atShortRep(r.rep))}</td><td>${esc(r.rep_type)}</td>
        <td class="mono">${esc(r.in_time)}${r.edited ? " ✎" : ""}</td><td class="mono">${esc(r.out_time || "–")}</td><td>${esc(r.asm)}</td></tr>`).join("") + `</tbody>`;
}

// ---------------------------------------------------------------- settings
function atRenderSettings() {
  const s = AT.data.settings;
  document.getElementById("att-url").value = s.att_url;
  document.querySelectorAll('input[name="att-style"]').forEach(r => { r.checked = r.value === s.att_style; });
  document.getElementById("att-send-style").value = s.att_send_style;
  document.getElementById("att-send-style-f").style.display = s.att_style === "both" ? "" : "none";
  document.getElementById("att-pdf").checked = s.att_send_pdf === "1";
  atRenderChips();
}
function atRenderChips() {
  document.getElementById("att-chips").innerHTML = AT.chats.length ? AT.chats.map(c => `<span class="rm-chip">${esc(c)} <button type="button" data-att-rc="${esc(c)}">&#10005;</button></span>`).join("")
    : `<span class="hint">No chats yet.</span>`;
}

(function wireRepAttendance() {
  const v = document.getElementById("view-rep-attendance"); if (!v) return;
  document.getElementById("att-day").addEventListener("change", (e) => { if (e.target.value) loadRepAttendance(e.target.value); });
  document.getElementById("att-tabs").addEventListener("click", (e) => {
    const t = e.target.closest(".tab"); if (!t) return;
    document.querySelectorAll("#att-tabs .tab").forEach(b => b.classList.toggle("active", b === t));
    ["day", "history", "settings"].forEach(x => { document.getElementById("att-panel-" + x).style.display = x === t.dataset.tab ? "" : "none"; });
    if (t.dataset.tab === "history") atHistory();
  });
  document.getElementById("att-download").onclick = () => {
    const today = atIso(new Date()), day = AT.day || today;
    confirmModal("Download from SFA", `Read SFA's Rep Attendance Report for <b>${esc(atLong(day))}</b> and build the images? Nothing is sent to WhatsApp — use
      <b>Send to WhatsApp</b> afterwards.`, "Download", async () => {
        const r = await atApi().start_rep_attendance(day, false, false);
        if (!r.ok) { atAlert(r.message); return; }
        AT.wasActive = true; atStartPoll(); });
  };
  document.getElementById("att-upload").onclick = async () => {
    const r = await atApi().import_rep_attendance();
    if (r.message === "No file selected.") return;
    atAlert(r.message, r.ok);
    if (r.ok) loadRepAttendance(r.day);
  };
  document.getElementById("att-style-seg").addEventListener("click", (e) => { const b = e.target.closest("button[data-s]"); if (!b) return; AT.style = b.dataset.s; atRenderDay(); });
  document.getElementById("att-rebuild").onclick = async (e) => { e.target.disabled = true; const r = await atApi().rebuild_rep_attendance(AT.day); e.target.disabled = false;
    if (!r.ok) { atAlert(r.message); return; } loadRepAttendance(); };
  document.getElementById("att-open-folder").onclick = () => { const l = AT.data.latest; if (l && l.images.length) atApi().open_path(l.images[0].path.replace(/[\\/][^\\/]+[\\/][^\\/]+$/, "")); };
  document.getElementById("att-send").onclick = () => { if (AT.data.latest) atSend(AT.data.latest.id); };
  v.addEventListener("click", async (e) => {
    const b = e.target.closest("button"); if (!b) return;
    if (b.dataset.attEdit) atEdit(b.dataset.attEdit);
    if (b.dataset.attApprove) atSend(Number(b.dataset.attApprove));
    if (b.dataset.attSkip) confirmModal("Don't send", "Mark this run as not sent? The images stay on the Day tab.", "Don't send", async () => {
      await atApi().discard_rep_attendance(Number(b.dataset.attSkip)); loadRepAttendance(); });
    if (b.dataset.attView) loadRepAttendance(b.dataset.attView);
    if (b.dataset.attLog) copyText(await atApi().get_rep_attendance_log(Number(b.dataset.attLog)), b);
    if (b.dataset.attRc) { AT.chats = AT.chats.filter(c => c !== b.dataset.attRc); atRenderChips(); }
  });
  // history
  document.getElementById("att-h-period").addEventListener("click", (e) => { const b = e.target.closest("button[data-p]"); if (!b) return;
    AT.hPeriod = b.dataset.p; document.querySelectorAll("#att-h-period button").forEach(x => x.classList.toggle("on", x === b));
    document.getElementById("att-h-custom").style.display = AT.hPeriod === "custom" ? "" : "none"; if (AT.hPeriod !== "custom") atHistory(); });
  document.getElementById("att-h-apply").onclick = atHistory;
  let qt; document.getElementById("att-h-q").addEventListener("input", () => { clearTimeout(qt); qt = setTimeout(atHistory, 300); });
  document.getElementById("att-h-export").onclick = async (e) => { const [a, b] = atRange();
    const r = await atApi().export_rep_attendance(a, b, document.getElementById("att-h-q").value);
    if (!r.ok) { atAlert(r.message); return; } atApi().open_path(r.path); };
  // settings
  document.querySelectorAll('input[name="att-style"]').forEach(r => r.addEventListener("change", () => {
    document.getElementById("att-send-style-f").style.display = r.value === "both" && r.checked ? "" : document.querySelector('input[name="att-style"]:checked').value === "both" ? "" : "none"; }));
  const addChat = () => { const i = document.getElementById("att-chat"), val = i.value.trim(); if (val && !AT.chats.includes(val)) AT.chats.push(val); i.value = ""; atRenderChips(); };
  document.getElementById("att-chat-add").onclick = addChat;
  document.getElementById("att-chat").addEventListener("keydown", (e) => { if (e.key === "Enter" && document.getElementById("att-chat-list").style.display === "none") { e.preventDefault(); addChat(); } });
  // shell.js (which has the chat picker) loads after this file
  window.addEventListener("DOMContentLoaded", () => { if (typeof waChatPicker === "function") waChatPicker(document.getElementById("att-chat"), document.getElementById("att-chat-list")); });
  document.getElementById("att-to-sched").onclick = () => switchView("schedules");
  document.getElementById("att-save").onclick = async () => {
    const pending = document.getElementById("att-chat").value.trim(); if (pending && !AT.chats.includes(pending)) AT.chats.push(pending);
    const r = await atApi().save_rep_attendance_settings({ att_url: document.getElementById("att-url").value.trim(),
      att_style: (document.querySelector('input[name="att-style"]:checked') || {}).value || "both", att_send_style: document.getElementById("att-send-style").value,
      att_chats: AT.chats, att_send_pdf: document.getElementById("att-pdf").checked ? "1" : "0" });
    document.getElementById("att-save-msg").innerHTML = `<span class="${r.ok ? "hint" : "error"}">${esc(r.ok ? "Saved." : r.message)}</span>`;
    if (r.ok) loadRepAttendance();
  };
  window.addEventListener("pywebviewready", () => setTimeout(async () => {   // the sidebar shows runs waiting for approval
    try { const d = await atApi().get_rep_attendance(); const b = document.getElementById("nav-att-badge");
      if (b) { b.textContent = d.pending.length; b.style.display = d.pending.length ? "" : "none"; } } catch (e) { /* older backend */ }
  }, 2500));
})();
