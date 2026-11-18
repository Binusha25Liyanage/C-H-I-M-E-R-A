// about.js — the "About Chimera" pop-up (click the logo at the top of the sidebar).
//
// KEEP THIS UP TO DATE: every time a service is added to Chimera (or moves from Planned -> Beta -> Built),
// add / update it in ABOUT_SERVICES, and if it has its own process, add a flow to ABOUT_FLOWS.
// Status: "built" = in use, "beta" = built and tested, awaiting first live runs, "planned" = not active yet.
// Relies on openModal / closeModal (automation.js) and switchView (main.js).

const ABOUT_ICON = {
  report: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M8 13h8M8 17h5"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  bot: '<rect x="3" y="8" width="18" height="12" rx="2"/><path d="M12 8V4M8 14h.01M16 14h.01"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  eye: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  chat: '<path d="M21 11.5a8.4 8.4 0 0 1-12.4 7.4L3 21l2.1-5.6A8.4 8.4 0 1 1 21 11.5z"/>',
  alarm: '<circle cx="12" cy="13" r="8"/><path d="M12 9v4l2 2M5 3L2 6M22 6l-3-3"/>',
  bell: '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.7 21a2 2 0 0 1-3.4 0"/>',
  table: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18M9 4v16"/>',
  route: '<circle cx="6" cy="19" r="2"/><circle cx="18" cy="5" r="2"/><path d="M8 19h7a3 3 0 0 0 0-6H9a3 3 0 0 1 0-6h7"/>',
  upload: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12"/>',
  truck: '<path d="M1 4h13v11H1z"/><path d="M14 8h4l3 3v4h-7"/><circle cx="5.5" cy="17.5" r="2"/><circle cx="17.5" cy="17.5" r="2"/>',
  users: '<circle cx="9" cy="8" r="3.5"/><path d="M2 21a7 7 0 0 1 14 0M16 4.5a3.5 3.5 0 0 1 0 7M18 14a6 6 0 0 1 4 7"/>',
  calendar: '<rect x="3" y="4" width="18" height="17" rx="2"/><path d="M8 2v4M16 2v4M3 10h18M9 15l2 2 4-4"/>',
  map: '<path d="M9 4L3 6v14l6-2 6 2 6-2V4l-6 2z"/><path d="M9 4v14M15 6v14"/>',
  list: '<path d="M10 6h11M10 12h11M10 18h11M4 6l1.5 1.5L7 5M4 12l1.5 1.5L7 11M4 18l1.5 1.5L7 17"/>',
  pause: '<circle cx="12" cy="12" r="9"/><path d="M10 9v6M14 9v6"/>',
  pulse: '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
  palette: '<path d="M12 3a9 9 0 1 0 0 18c1.1 0 2-.9 2-2 0-.5-.2-1-.5-1.3-.3-.4-.5-.8-.5-1.3 0-1.1.9-2 2-2h2.4A4.6 4.6 0 0 0 22 9.8C22 6 17.5 3 12 3z"/><circle cx="7.5" cy="11" r="1"/><circle cx="9.5" cy="7" r="1"/><circle cx="14.5" cy="7" r="1"/>',
  power: '<path d="M18.4 6.6a9 9 0 1 1-12.8 0M12 2v10"/>',
  sync: '<path d="M21 12a9 9 0 0 1-15.5 6.2L3 16"/><path d="M3 12a9 9 0 0 1 15.5-6.2L21 8"/><path d="M21 3v5h-5M3 21v-5h5"/>',
  store: '<path d="M3 9l1.5-5h15L21 9M4 9v11h16V9M9 20v-6h6v6"/>',
  layers: '<path d="M12 2l10 5-10 5L2 7z"/><path d="M2 12l10 5 10-5M2 17l10 5 10-5"/>',
  grid: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><path d="M14 17.5h7M17.5 14v7"/>',
  db: '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/>',
  check: '<path d="M20 6L9 17l-5-5"/>',
  key: '<circle cx="7.5" cy="15.5" r="4.5"/><path d="M10.7 12.3L21 2M17 6l3 3M14 9l2 2"/>',
  download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/>',
  send: '<path d="M22 2L11 13M22 2l-7 20-4-9-9-4z"/>',
  file: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/>',
  phone: '<rect x="6" y="2" width="12" height="20" rx="2"/><path d="M11 18h2"/>',
  shield: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M9 12l2 2 4-4"/>',
};
const aboutSvg = (k, s = 18) => `<svg viewBox="0 0 24 24" width="${s}" height="${s}" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${ABOUT_ICON[k] || ABOUT_ICON.grid}</svg>`;

// [icon, name, status, description] grouped by area.
const ABOUT_SERVICES = [
  ["Reports", [
    ["sun", "Early Bills Report", "built", "Bills each territory created before the cut-off (9:00 AM by default), Synchronized / Not synced yet, with a sync reminder for Team Managers."],
    ["report", "SKU, Pivot & Hourly Reports", "built", "Built straight from raw SFA exports, per manager group, in Classic or Professional style — PDF and WhatsApp images."],
    ["eye", "Preview & approve", "built", "See every page before it's saved or sent. The first run of each report each day waits for you; scheduled times can be set to Ask me first."],
    ["table", "Combined SKU + Invoice", "built", "Route-wise outlet report anchored to the previous visit date."],
    ["route", "Last Visit Sales Summary", "built", "Checks the rep really worked the previous visit day before building the report."],
    ["calendar", "Rep Attendance Report", "beta", "Reads SFA's Rep Attendance report for the day, builds the images and sends them to the chats you choose."],
  ]],
  ["Automation & delivery", [
    ["bot", "Report Downloads", "built", "One SFA login, many parallel tabs: fills each report's filters, exports, validates and imports — retries when SFA is slow."],
    ["clock", "Schedules", "built", "Every report is its own job, plus loadings, attendance and list updates — days, times, holidays, skip-today; runs through Windows Task Scheduler even when Chimera is closed."],
    ["chat", "WhatsApp Delivery", "built", "Each manager group's pages to the right chats through your own WhatsApp, with captions, portrait/landscape and a delivery log — never sent twice."],
    ["alarm", "WhatsApp Reminders", "beta", "Sync reminders sent a set time before each report job, to any chats or groups, with their own text."],
  ]],
  ["Distribution", [
    ["truck", "Distributor Loadings", "beta", "Suggested quantities, Add loading (answers SFA's confirmation), then Prepare → View Loadings by territory and date → Confirm, which always waits for you."],
    ["users", "Distributor list update", "beta", "Keeps the Loadings distributor list in step with SFA's Users page, on schedule or by button."],
  ]],
  ["Outlets & routes", [
    ["map", "Daily itinerary", "beta", "From 23:59 the next day's route of every working territory is read from SFA — and caught up if the PC was off."],
    ["list", "Visit sequence & Visit State", "beta", "Reps' route sheets are previewed against SFA, approved, then each outlet's sequence and Visit State saved — dry run first, with an audit log."],
  ]],
  ["Control & app", [
    ["layers", "Side by side", "beta", "Report downloads, loadings, rep attendance and outlet updates run at the same time, sharing one SFA login; the same kind waits in a queue, and a time that never ran shows as Missed."],
    ["pause", "Running now · Pause · Stop", "built", "Every running process in one place, with Pause / Resume, Stop and Emergency stop; previews minimise to a small Chimera icon."],
    ["pulse", "Activity Monitor & Run History", "built", "Every run, download, approval and delivery with its result and log."],
    ["bell", "Notifications & help", "built", "A bell and desktop notifications for runs, approvals and deliveries, plus per-screen help."],
    ["upload", "Manual Import", "built", "Itinerary, territories, invoices, sales orders, outlet master and SKU sales from files — same checks as downloads."],
    ["power", "Startup check", "built", "Each time Chimera opens it logs in to SFA once and prepares the day, telling you plainly if something couldn't be done."],
    ["palette", "Customize Appearance", "built", "Splash animations, soundtracks, logo glitch, the heartbeat line and a desktop icon that glitches through three looks — Settings › Customize Appearance."],
  ]],
  ["Coming next", [
    ["sync", "Data Sync", "planned", "Any SFA Excel profile downloaded on its own schedule into Chimera's database."],
    ["store", "Outlets & Territories", "planned", "Outlet master and territory records, synced from SFA, with housekeeping tools."],
    ["grid", "Excel output templates", "planned", "Upload a sample output layout with formulas and Chimera fills it from SFA data — no code changes."],
  ]],
];

// How it works: one flow per process. [icon, step, short, detail]
const ABOUT_FLOWS = [
  { id: "reports", label: "Reports", icon: "report", steps: [
    ["clock", "Trigger", "Schedule · Run now", "A scheduled time, a report job (Early Bills, SKU, Pivot, Hourly) or Run now. Holidays and skip-today are respected."],
    ["key", "Log in once", "One SFA session", "Chimera logs in to SFA once in its own browser; every download tab shares that session."],
    ["download", "Download", "Parallel tabs", "Each report profile opens its page, fills its filters and dates, searches and exports the Excel — only the files the chosen reports need."],
    ["shield", "Check", "Every file validated", "Each file's banner, date and columns are checked before anything is imported, so a wrong or empty export never becomes a report."],
    ["db", "Import", "Day snapshot", "The rows go into Chimera's own database as that day's snapshot, with counts and timestamps."],
    ["layers", "Build", "Early · SKU · Pivot · Hourly", "Reports are built per manager group in Classic or Professional style, as PDFs and WhatsApp images."],
    ["eye", "Review", "Preview & approve", "The first run of each report each day (or an Ask me first time) waits for your approval; later runs go on their own."],
    ["send", "Deliver", "Folder + WhatsApp", "Saved to the reports folder and sent to each chat by its delivery rule, report by report."],
  ]},
  { id: "loadings", label: "Loadings", icon: "truck", steps: [
    ["users", "Distributors", "Synced from SFA", "The distributor list comes from SFA's Users page, with each one's territory."],
    ["table", "Suggest", "Quantities", "Chimera suggests quantities for each distributor's loading for you to adjust."],
    ["truck", "Add loading", "Saved in SFA", "Fills SFA's Add Loading, answers its Confirmation Alert with Yes and checks SFA said Saved."],
    ["phone", "Rep confirms", "SFA mobile app", "SFA refuses a new loading until the rep confirms the previous one in the mobile app — an SFA rule, shown clearly."],
    ["list", "Prepare", "Pending loadings", "View Loadings with the distributor's Territory and Date From / To, and the pending loadings are read."],
    ["check", "Confirm", "Always waits for you", "After your OK: the tick under Confirm, then Confirm, then Yes, Confirm it — and Chimera checks it was confirmed."],
  ]},
  { id: "attendance", label: "Rep Attendance", icon: "calendar", steps: [
    ["clock", "Trigger", "Schedule · Run now", "At the job's times, or when you press Run."],
    ["download", "Read SFA", "Attendance report", "SFA's Rep Attendance report for the day: Date From = Date To, Search, Excel."],
    ["db", "Save the day", "Corrections kept", "The day is saved; your corrections and uploads can rebuild it without SFA."],
    ["layers", "Build", "Images", "Classic, Professional or both."],
    ["eye", "Review", "Ask me first", "Times marked Ask me first wait for your approval."],
    ["send", "Send", "WhatsApp", "To the chats set on the Rep Attendance screen."],
  ]},
  { id: "outlets", label: "Outlet Updates", icon: "map", steps: [
    ["clock", "23:59", "Next day", "From 23:59 Chimera prepares the next day; if the PC was off, it catches up when Chimera opens."],
    ["map", "Itinerary", "Today's routes", "Each working territory's monthly itinerary is read, giving the route each rep works that day."],
    ["file", "Rep sheets", "New sequence · Top 15", "Reps' filled route sheets arrive and are read."],
    ["eye", "Preview", "Against SFA", "Each outlet on the route is compared with SFA's current sequence and Visit State."],
    ["check", "Approve", "Your call", "You approve, or it goes on by itself if approval isn't required."],
    ["list", "Save", "Outlet by outlet", "Each changed outlet's sequence and Visit State is saved in SFA, with an audit log. Dry run available."],
  ]},
  { id: "whatsapp", label: "WhatsApp", icon: "chat", steps: [
    ["grid", "Rules", "Per chat", "Each chat has its reports, manager groups, caption and page orientation."],
    ["alarm", "Reminders", "Before report jobs", "Reminders go a set time before each report job — whatever else is running."],
    ["phone", "Your WhatsApp", "Linked once", "One WhatsApp Web window on your linked phone; sends queue one after another."],
    ["send", "Send", "Pages + caption", "Opens the chat, attaches the pages, adds the caption and waits until WhatsApp has sent them."],
    ["pulse", "Log", "Never twice", "Every delivery is logged, so nothing is sent twice even after a retry."],
  ]},
  { id: "control", label: "Behind the scenes", icon: "shield", steps: [
    ["power", "Startup", "Check SFA", "Logs in to SFA once and prepares the day as Chimera opens."],
    ["clock", "Scheduler", "Even when closed", "Runs jobs at their times inside Chimera, and through Windows Task Scheduler when it's closed."],
    ["layers", "Side by side", "Queue, never missed", "Different kinds of job run together on one SFA login; the same kind queues. A time that never ran is marked Missed, with a notification."],
    ["pause", "Control", "Pause · Stop", "Every process can be paused, resumed or stopped; a stop is safe between steps."],
    ["bell", "Notify", "Bell + desktop", "Started, waiting for approval, done or failed — you're told."],
    ["db", "Your PC", "No cloud", "Data stays in Chimera's own database on this PC; your SFA password never leaves it."],
  ]},
];

const ABOUT = { timer: null, flow: "reports", step: 0, pinned: false };

function aboutFlowHtml(f) {
  return `<div class="aw-track" style="--n:${f.steps.length}">
      <div class="aw-line"><i class="aw-fill"></i><i class="aw-packet"></i></div>
      ${f.steps.map(([ic, t, s], i) => `<button class="aw-node" data-i="${i}"><span class="aw-dot">${aboutSvg(ic, 18)}<em>${i + 1}</em></span>
        <b>${t}</b><small>${s}</small></button>`).join("")}
    </div>
    <div class="aw-detail"><span class="aw-detail-ic"></span><div><b class="aw-detail-t"></b><p class="aw-detail-p"></p></div></div>`;
}

function aboutShowStep(i) {
  const f = ABOUT_FLOWS.find(x => x.id === ABOUT.flow), box = document.getElementById("aw-stage");
  if (!f || !box) return false;
  ABOUT.step = i;
  box.querySelectorAll(".aw-node").forEach((n, k) => { n.classList.toggle("on", k === i); n.classList.toggle("past", k < i); });
  const pct = f.steps.length > 1 ? (i / (f.steps.length - 1)) * 100 : 100;
  box.querySelector(".aw-fill").style.width = pct + "%";
  box.querySelector(".aw-packet").style.left = pct + "%";
  const [ic, t, , d] = f.steps[i];
  const det = box.querySelector(".aw-detail");
  det.classList.remove("swap"); void det.offsetWidth; det.classList.add("swap");
  det.querySelector(".aw-detail-ic").innerHTML = aboutSvg(ic, 22);
  det.querySelector(".aw-detail-t").textContent = `${i + 1}. ${t}`;
  det.querySelector(".aw-detail-p").textContent = d;
  return true;
}

function aboutSelectFlow(id) {
  ABOUT.flow = id; ABOUT.pinned = false;
  document.querySelectorAll(".aw-tab").forEach(t => t.classList.toggle("on", t.dataset.f === id));
  const box = document.getElementById("aw-stage");
  box.innerHTML = aboutFlowHtml(ABOUT_FLOWS.find(x => x.id === id));
  box.querySelectorAll(".aw-node").forEach(n => n.onclick = () => { ABOUT.pinned = true; aboutShowStep(Number(n.dataset.i)); });
  aboutShowStep(0);
}

function aboutTick() {
  if (!document.getElementById("aw-stage")) { clearInterval(ABOUT.timer); ABOUT.timer = null; return; }
  if (ABOUT.pinned || document.getElementById("modal-backdrop").style.display !== "flex") return;
  const f = ABOUT_FLOWS.find(x => x.id === ABOUT.flow);
  aboutShowStep((ABOUT.step + 1) % f.steps.length);
}

function openAppAbout() {
  const badge = { built: ["BUILT", "b-built"], beta: ["BETA", "b-beta"], planned: ["PLANNED", "b-planned"] };
  const all = ABOUT_SERVICES.flatMap(([, list]) => list);
  const count = (st) => all.filter(s => s[2] === st).length;
  openModal({ title: "About Chimera", size: "lg app-about", onClose: () => { clearInterval(ABOUT.timer); ABOUT.timer = null; }, body: `
    <div class="aa-hero">
      <div class="aa-logo"><img src="assets/chimera-logo.png" alt="Chimera"></div>
      <h1>C H I M E R A</h1>
      <p>A sales-operations desk for SFA &mdash; downloads and builds the day's reports, prepares distributor loadings,
         reads attendance and routes, updates outlets, and delivers everything to WhatsApp, on schedule, from one desktop app.</p>
      <div class="aa-stats"><span><b>${count("built")}</b> in use</span><span><b>${count("beta")}</b> in beta</span><span><b>${count("planned")}</b> coming next</span></div>
    </div>
    <div class="aa-body">
      <p class="aa-intro">Data lives on this PC in Chimera's own SQLite database &mdash; no server, no cloud account. Chimera works with
        SFA and WhatsApp through a browser, exactly as you would, and whether data arrives from an automatic download or a
        file you pick, it goes through the same checks, the same snapshot rules and the same run history &mdash; so the
        numbers stay consistent however they got there.</p>
      <div class="aa-title">How it works</div>
      <div class="aw">
        <div class="aw-tabs">${ABOUT_FLOWS.map(f => `<button class="aw-tab" data-f="${f.id}">${aboutSvg(f.icon, 15)} ${f.label}</button>`).join("")}</div>
        <div id="aw-stage" class="aw-stage"></div>
        <p class="aw-hint">Steps play on their own &mdash; click a step to stop on it.</p>
      </div>
      <div class="aa-title">What's inside</div>
      ${ABOUT_SERVICES.map(([group, list]) => `
        <div class="aa-group">${group}</div>
        <div class="aa-grid">${list.map(([ic, t, st, d]) => `
          <div class="aa-card ${st}"><div class="aa-card-head"><span class="aa-ic">${aboutSvg(ic)}</span><b>${t}</b><span class="aa-badge ${badge[st][1]}">${badge[st][0]}</span></div>
            <p>${d}</p></div>`).join("")}</div>`).join("")}
      <p class="aa-legend"><span class="aa-badge b-built">BUILT</span> in use &nbsp; <span class="aa-badge b-beta">BETA</span> built and tested, awaiting first live runs &nbsp; <span class="aa-badge b-planned">PLANNED</span> not active yet</p>
      <div class="aa-foot">Crafted by Binusha &middot; Full-Stack Software Engineer &middot; <button class="link-btn" onclick="closeModal();switchView('about')">About &amp; Support</button></div>
    </div>`, footer: "" });
  document.querySelectorAll(".aw-tab").forEach(t => t.onclick = () => aboutSelectFlow(t.dataset.f));
  aboutSelectFlow("reports");
  clearInterval(ABOUT.timer);
  ABOUT.timer = setInterval(aboutTick, 2600);
}
window.openAppAbout = openAppAbout;
