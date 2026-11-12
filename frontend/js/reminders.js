// reminders.js — WhatsApp › Reminders: messages sent N minutes before each report time (or at times you set) to their
// own chats. Relies on esc/openModal/closeModal/confirmModal (automation.js), waChatPicker/SH (shell.js).

const RM = { data: null, edit: null };
const rmApi = () => window.pywebview.api;
const RM_STATUS = { sent: ["chip-ok", "Sent"], partial: ["chip-warn", "Partly sent"], failed: ["chip-missing", "Failed"], skipped: ["chip-grey", "Skipped"] };

function rmFmt(hhmm) {            // 14:15 -> 2.15 PM (as the reps' messages write it)
  if (!hhmm) return "";
  const [h, m] = hhmm.split(":").map(Number);
  return `${(h % 12) || 12}.${String(m).padStart(2, "0")} ${h < 12 ? "AM" : "PM"}`;
}
function rmRender(text, reportTime) {
  const now = new Date();
  return (text || "").replaceAll("{report_time}", rmFmt(reportTime) || now.toTimeString().slice(0, 5))
    .replaceAll("{date}", now.toISOString().slice(0, 10)).replaceAll("{day}", now.toLocaleDateString("en-GB", { weekday: "long" }));
}
function rmWa(text) {             // WhatsApp's *bold* / _italic_ / ~strike~ for the preview bubble
  return esc(text).replace(/\*([^*\n]+)\*/g, "<b>$1</b>").replace(/(^|\s)_([^_\n]+)_/g, "$1<i>$2</i>").replace(/~([^~\n]+)~/g, "<s>$1</s>");
}
function rmSends(r) {             // the times this reminder goes, from its job and its own times
  const out = [];
  const job = RM.data.jobs.find(j => j.id === r.job_id);
  if (job) job.times.forEach(t => { const [h, m] = t.split(":").map(Number); const mins = h * 60 + m - Number(r.offset_min || 0);
    if (mins >= 0) out.push([`${String(Math.floor(mins / 60)).padStart(2, "0")}:${String(mins % 60).padStart(2, "0")}`, t]); });
  (r.extra_list || []).forEach(t => { const nxt = job ? job.times.filter(x => x >= t)[0] || "" : ""; out.push([t, nxt]); });
  return out.sort();
}

async function loadReminders() {
  if (typeof rmApi().get_reminders !== "function") {
    document.getElementById("rm-list").innerHTML = `<div class="problem-box">Reminders need the latest backend files (api.py, reminders.py). Replace them and restart Chimera.</div>`;
    return;
  }
  try { RM.data = await rmApi().get_reminders(); } catch (e) { return; }
  const d = RM.data;
  document.getElementById("rm-list").innerHTML = d.reminders.length ? d.reminders.map(r => {
    const sends = rmSends(r);
    return `<div class="rule-card rm-rule ${r.enabled ? "" : "off"}">
      <div style="flex:1;min-width:0;">
        <div class="name">${esc(r.name)} ${r.enabled ? "" : `<span class="chip chip-grey">Off</span>`}</div>
        <div class="what">${r.job_id ? (r.job_missing ? `<span class="error">Its scheduled job was deleted</span>` : `${r.offset_min} min before <b>${esc(r.job_name)}</b>`) : "At its own times"}
          · ${sends.map(([t]) => esc(t)).join(", ") || "no times"}</div>
        <div class="what">To: ${r.chats.map(esc).join(", ")}</div>
        <div class="wa-bubble rm-bubble">${rmWa(rmRender(r.text, sends.length ? sends[0][1] : ""))}</div>
      </div>
      <div class="rule-actions">
        <label class="switch2" title="On / off"><input type="checkbox" data-rm-on="${r.id}" ${r.enabled ? "checked" : ""}><span></span></label>
        <button class="btn-outline btn-small" data-rm-edit="${r.id}">Edit</button>
        <button class="btn-outline btn-small" data-rm-test="${r.id}" title="Send it now to its chats">Send now</button>
        <button class="btn-outline btn-small" data-rm-del="${r.id}">&#10005;</button>
      </div></div>`;
  }).join("") : `<p class="hint">No reminders yet. <b>+ Add reminder</b> — e.g. 15 minutes before each Daily Sales Report, “Please sync your bills…”.</p>`;
  const now = new Date().toTimeString().slice(0, 5);
  document.getElementById("rm-today").innerHTML = d.today.length ? `<table class="report-table am-table rm-today"><tbody>${d.today.map(x => {
      const st = x.done ? RM_STATUS[x.done] : x.skip ? ["chip-grey", "Won't go"] : x.at <= now ? ["chip-warn", "Due"] : ["chip-grey", "Planned"];
      return `<tr class="${x.done || x.at <= now ? "" : "rm-future"}"><td><b>${esc(x.at)}</b></td><td>${esc(x.name)}${x.report_label ? `<div class="am-note">before the ${esc(x.report_label)} report</div>` : ""}</td>
        <td><span class="chip ${st[0]}">${esc(st[1])}</span>${x.skip && !x.done ? `<div class="am-note">${esc(x.skip)}</div>` : ""}</td></tr>`; }).join("")}</tbody></table>`
    : `<p class="hint">Nothing planned for today.</p>`;
}
window.loadReminders = loadReminders;

function openReminder(r) {
  const d = RM.data;
  const salesJob = d.jobs.find(j => /sales/i.test(j.name)) || d.jobs[0];
  r = r || { name: "Sync your bills", text: d.default_text, job_id: salesJob ? salesJob.id : null, offset_min: 15, extra_list: [], chats: [], enabled: true };
  RM.edit = { ...r, chats: [...r.chats], extra_list: [...(r.extra_list || [])] };
  const jobOpts = `<option value="">— none (only my own times) —</option>` + d.jobs.map(j =>
    `<option value="${j.id}" ${j.id === r.job_id ? "selected" : ""}>${esc(j.name)} — ${esc(j.times.join(", "))}${j.enabled ? "" : " (off)"}</option>`).join("");
  openModal({ title: r.id ? "Edit reminder" : "Add reminder", size: "lg", body: `
    <label class="field">Name <input id="rm-name" value="${esc(r.name)}" maxlength="60"></label>
    <label class="field">Message
      <textarea id="rm-text" rows="4" maxlength="2000">${esc(r.text)}</textarea>
      <span><b>{report_time}</b> becomes the report's time, e.g. 12.15 PM · {date} · {day}. *bold* and _italic_ work as in WhatsApp. New lines are kept.</span></label>
    <div class="rm-preview"><div class="wa-bubble" id="rm-prev"></div><span class="hint" id="rm-prev-note"></span></div>
    <div class="section-title">When</div>
    <div class="rm-when">
      <label class="field">Before these reports <select id="rm-job">${jobOpts}</select></label>
      <label class="field rm-offset">Minutes before <input type="number" id="rm-offset" min="0" max="240" value="${Number(r.offset_min ?? 15)}"></label>
    </div>
    <label class="field">Extra times (optional)
      <div class="rm-times" id="rm-times"></div>
      <span>Sent at exactly these times as well. With reports picked above, {report_time} is the next report after it.</span></label>
    <div class="rm-plan" id="rm-plan"></div>
    <div class="section-title">To these chats / groups</div>
    <div class="rm-chips" id="rm-chips"></div>
    <div class="rm-addchat"><div class="wa-pick"><input id="rm-chat" autocomplete="off" placeholder="${(SH.wa && SH.wa.chats && SH.wa.chats.length) ? "Search or pick a chat…" : "Press “Load my chats” first, or type the exact name"}">
        <button type="button" class="wa-pick-arrow" tabindex="-1" title="Show chats">&#9662;</button>
        <div class="wa-list" id="rm-chat-list" style="display:none;"></div></div>
      <button class="btn-outline" id="rm-chat-add" type="button">+ Add</button></div>
    <span class="hint">These can be groups that never get reports. Each gets the message as its own WhatsApp message.</span>
    <label class="check-row" style="margin-top:12px;"><input type="checkbox" id="rm-enabled" ${r.enabled ? "checked" : ""}> On</label>
    <div id="rm-msg"></div>`,
    footer: `<button class="btn-outline" onclick="closeModal()">Cancel</button><button class="btn-primary" id="rm-save">Save reminder</button>` });
  const draw = () => {
    const e = RM.edit;
    e.job_id = Number(document.getElementById("rm-job").value) || null;
    e.offset_min = Number(document.getElementById("rm-offset").value || 0);
    e.text = document.getElementById("rm-text").value;
    const sends = rmSends(e);
    document.getElementById("rm-offset").disabled = !e.job_id;
    document.getElementById("rm-prev").innerHTML = rmWa(rmRender(e.text, sends.length ? sends[0][1] : "")) || `<span class="hint">(empty)</span>`;
    document.getElementById("rm-prev-note").textContent = sends.length && sends[0][1] ? `How it looks at ${sends[0][0]}, before the ${rmFmt(sends[0][1])} report` : "How it looks";
    document.getElementById("rm-times").innerHTML = e.extra_list.map(t => `<span class="rm-chip">${esc(t)} <button type="button" data-rm-rt="${t}">&#10005;</button></span>`).join("")
      + `<input type="time" id="rm-time-in"><button class="btn-outline btn-small" type="button" id="rm-time-add">Add time</button>`;
    document.getElementById("rm-plan").innerHTML = sends.length ? `<b>Sent at:</b> ` + sends.map(([t, rep]) => `<span class="rm-slot">${esc(t)}${rep ? `<em>→ ${esc(rmFmt(rep))} report</em>` : ""}</span>`).join("")
      : `<span class="error">No times yet — pick the reports it goes before, or add a time.</span>`;
    document.getElementById("rm-chips").innerHTML = e.chats.length ? e.chats.map(c => `<span class="rm-chip">${esc(c)} <button type="button" data-rm-rc="${esc(c)}">&#10005;</button></span>`).join("")
      : `<span class="hint">No chats yet.</span>`;
    document.getElementById("rm-time-add").onclick = () => { const v = document.getElementById("rm-time-in").value; if (v && !e.extra_list.includes(v)) { e.extra_list.push(v); e.extra_list.sort(); } draw(); };
  };
  ["rm-job", "rm-offset", "rm-text"].forEach(id => document.getElementById(id).addEventListener("input", draw));
  document.getElementById("rm-job").addEventListener("change", draw);
  const body = document.getElementById("modal-body");
  body.onclick = (ev) => { const b = ev.target.closest("button"); if (!b) return;
    if (b.dataset.rmRt) { RM.edit.extra_list = RM.edit.extra_list.filter(t => t !== b.dataset.rmRt); draw(); }
    if (b.dataset.rmRc) { RM.edit.chats = RM.edit.chats.filter(c => c !== b.dataset.rmRc); draw(); } };
  const addChat = () => { const i = document.getElementById("rm-chat"), v = i.value.trim(); if (v && !RM.edit.chats.includes(v)) RM.edit.chats.push(v); i.value = ""; draw(); };
  document.getElementById("rm-chat-add").onclick = addChat;
  document.getElementById("rm-chat").addEventListener("keydown", (ev) => { if (ev.key === "Enter" && document.getElementById("rm-chat-list").style.display === "none") { ev.preventDefault(); addChat(); } });
  waChatPicker(document.getElementById("rm-chat"), document.getElementById("rm-chat-list"));
  document.getElementById("rm-save").onclick = async () => {
    const e = RM.edit;
    const pending = document.getElementById("rm-chat").value.trim(); if (pending && !e.chats.includes(pending)) e.chats.push(pending);
    const res = await rmApi().save_reminder({ id: r.id, name: document.getElementById("rm-name").value, text: document.getElementById("rm-text").value,
      job_id: e.job_id, offset_min: e.offset_min, extra_times: e.extra_list, chats: e.chats, enabled: document.getElementById("rm-enabled").checked });
    if (!res.ok) { document.getElementById("rm-msg").innerHTML = `<div class="problem-box">${esc(res.message)}</div>`; return; }
    closeModal(); loadReminders();
  };
  draw();
}

(function wireReminders() {
  const card = document.getElementById("rm-card"); if (!card) return;
  document.getElementById("rm-add").onclick = () => { if (RM.data) openReminder(null); };
  card.addEventListener("click", async (e) => {
    const b = e.target.closest("button"); if (!b) return;
    if (b.dataset.rmEdit) openReminder(RM.data.reminders.find(r => r.id === Number(b.dataset.rmEdit)));
    if (b.dataset.rmDel) confirmModal("Delete reminder", "Delete this reminder? Messages already sent stay in the Activity Monitor.", "Delete",
      async () => { await rmApi().delete_reminder(Number(b.dataset.rmDel)); loadReminders(); }, true);
    if (b.dataset.rmTest) { const r = RM.data.reminders.find(x => x.id === Number(b.dataset.rmTest));
      confirmModal("Send now", `Send <b>${esc(r.name)}</b> now to ${r.chats.map(esc).join(", ")}? (Its scheduled sends still go as planned.)`, "Send now", async () => {
        const res = await rmApi().send_reminder_test(r.id);
        openModal({ title: "Reminder", body: `<div class="${res.ok ? "ok-box" : "problem-box"}">${esc(res.message)}</div>`, footer: `<button class="btn-primary" onclick="closeModal()">OK</button>` });
        if (res.ok && typeof startWaPoll === "function") startWaPoll(); }); }
  });
  card.addEventListener("change", async (e) => {
    if (e.target.dataset.rmOn) { await rmApi().set_reminder_enabled(Number(e.target.dataset.rmOn), e.target.checked); loadReminders(); }
  });
})();
