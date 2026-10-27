// main.js — Chimera frontend logic

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}

const VIEW_TITLES = {
  "overview": "Dashboard Overview",
  "daily": "Daily SFA Reports",
  "automation": "SFA Automation",
  "reports": "Combined SKU + Invoice Report",
  "visit-state": "Visit State Update",
  "sequence": "Sequence Update",
  "import-data": "Import Data",
  "outlet-master": "Outlet Master",
  "maintenance": "Maintenance",
  "settings": "Settings",
  "support": "Support",
};

function switchView(view) {
  document.querySelectorAll(".view").forEach(el => el.style.display = "none");
  const target = document.getElementById("view-" + view);
  if (!target) return;
  target.style.display = "block";
  document.querySelectorAll(".nav-btn").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.view === view);
  });
  document.getElementById("topbar-title").textContent = VIEW_TITLES[view] || "Chimera";
  if (view === "import-data") loadImportLog();
  if (view === "settings") { loadSfaConfig(); loadReportSettings(); loadManagerGroups(); }
  if (view === "daily") { loadDailyStatus(); loadRuns(); }
  if (view === "automation" && window.loadAutomation) window.loadAutomation();
}

document.querySelectorAll(".nav-btn[data-view]").forEach(btn => {
  btn.addEventListener("click", () => switchView(btn.dataset.view));
});
document.querySelectorAll("[data-goto]").forEach(btn => {
  btn.addEventListener("click", () => switchView(btn.dataset.goto));
});

// ---------------- Overview ----------------
async function loadDashboard() {
  try {
    const s = await window.pywebview.api.get_dashboard_summary();
    document.getElementById("stat-total-outlets").textContent = s.total_outlets.toLocaleString();
    document.getElementById("stat-territories").textContent = s.territories_with_outlets.toLocaleString();
    document.getElementById("stat-routes").textContent = s.routes_with_outlets.toLocaleString();
  } catch (err) {
    console.error("Dashboard load error:", err);
  }

  try {
    const log = await window.pywebview.api.get_import_log();
    const entries = Object.values(log);
    const lastImportEl = document.getElementById("stat-last-import");
    if (entries.length === 0) {
      lastImportEl.textContent = "Never";
    } else {
      const mostRecent = entries.map(e => e.last_imported).sort().reverse()[0];
      lastImportEl.textContent = mostRecent.slice(0, 10);
    }
  } catch (err) {
    console.error("Import log load error:", err);
  }
}

// ---------------- Reports (Combined SKU + Invoice) ----------------
async function loadTerritories() {
  const select = document.getElementById("cr-territory");
  try {
    const territories = await window.pywebview.api.get_all_territories();
    if (territories.length === 0) {
      select.innerHTML = `<option value="">No territories imported yet</option>`;
      return;
    }
    select.innerHTML = territories.map(t => `<option value="${escapeHtml(t)}">${escapeHtml(t)}</option>`).join("");
  } catch (err) {
    select.innerHTML = `<option value="">Error loading territories</option>`;
  }
}

function renderReportRows(rows) {
  if (rows.length === 0) return "<p class='hint'>No outlets found for this route.</p>";
  let html = `
    <table class="report-table">
      <thead><tr><th>Seq.</th><th>Outlet Code</th><th>Outlet</th><th>Invoices</th><th>Products</th><th>Units</th><th>Sales Value</th></tr></thead>
      <tbody>
  `;
  for (const r of rows) {
    if (r.lines.length === 0) {
      html += `<tr>
        <td>${r.sequence ?? ""}</td><td>${escapeHtml(r.outlet_code)}</td><td>${escapeHtml(r.outlet)}</td>
        <td class="center">${r.invoices || ""}</td><td colspan="3"></td>
      </tr>`;
      continue;
    }
    r.lines.forEach((line, i) => {
      html += `<tr>
        <td>${i === 0 ? (r.sequence ?? "") : ""}</td>
        <td>${i === 0 ? escapeHtml(r.outlet_code) : ""}</td>
        <td>${i === 0 ? escapeHtml(r.outlet) : ""}</td>
        <td class="center">${i === 0 ? (r.invoices || "") : ""}</td>
        <td>${escapeHtml(line[0])}</td>
        <td class="right">${line[1]}</td>
        <td class="right sales-value">${Number(line[2]).toLocaleString(undefined, {minimumFractionDigits: 2})}</td>
      </tr>`;
    });
    if (r.lines.length > 1) {
      html += `<tr class="outlet-total-row"><td colspan="5"></td><td class="right">${r.lines.reduce((a,l)=>a+Number(l[1]),0)}</td><td class="right sales-value">${r.subtotal.toLocaleString(undefined,{minimumFractionDigits:2})}</td></tr>`;
    }
  }
  html += "</tbody></table>";
  return html;
}

document.getElementById("cr-preview-btn").addEventListener("click", async () => {
  const territory = document.getElementById("cr-territory").value;
  const date = document.getElementById("cr-date").value;
  const resultEl = document.getElementById("cr-result");
  if (!territory || !date) {
    resultEl.innerHTML = "<p class='hint'>Pick a territory and a reference date first.</p>";
    return;
  }
  resultEl.innerHTML = "<p class='hint'>Loading preview...</p>";
  try {
    const r = await window.pywebview.api.preview_combined_report(territory, date);
    if (!r.ok) {
      resultEl.innerHTML = `<p class="error">Could not build report: ${escapeHtml(r.reason)}</p>`;
      return;
    }
    let html = `
      <p class="hint">
        Route: <b>${escapeHtml(r.route)}</b> | Sales Model: ${escapeHtml(r.sale_model)} |
        Previous Visit Date: <b>${r.previous_visit_date}</b> | Bills: ${r.bills} | Total: ${r.total.toLocaleString(undefined, {minimumFractionDigits: 2})}
      </p>
    `;
    if (r.unmatched_pre_sales_customers && r.unmatched_pre_sales_customers.length > 0) {
      html += `<p class="error">Unmatched Pre Selling customers (not counted in Invoices): ${r.unmatched_pre_sales_customers.map(escapeHtml).join(", ")}</p>`;
    }
    html += renderReportRows(r.rows);
    resultEl.innerHTML = html;
  } catch (err) {
    resultEl.innerHTML = "<p class='error'>Error: " + err + "</p>";
  }
});

document.getElementById("cr-generate-all-btn").addEventListener("click", async () => {
  const date = document.getElementById("cr-date").value;
  const resultEl = document.getElementById("cr-result");
  if (!date) {
    resultEl.innerHTML = "<p class='hint'>Pick a reference date first.</p>";
    return;
  }
  const select = document.getElementById("cr-territory");
  const allTerritories = Array.from(select.options).map(o => o.value).filter(Boolean);
  if (allTerritories.length === 0) {
    resultEl.innerHTML = "<p class='hint'>No territories to generate for.</p>";
    return;
  }
  resultEl.innerHTML = `<p class="hint">Generating for ${allTerritories.length} territories...</p>`;
  try {
    const folder = await window.pywebview.api.get_default_export_folder();
    const result = await window.pywebview.api.generate_combined_reports(allTerritories, date, folder);
    let html = `<p class="hint"><b>${result.written.length}</b> PDF(s) written to <code>${escapeHtml(folder)}</code>.</p>`;
    if (result.skipped.length > 0) {
      html += `<p class="hint">Skipped (${result.skipped.length}):</p><ul>`;
      html += result.skipped.map(s => `<li>${escapeHtml(s.territory)} — ${escapeHtml(s.reason)}</li>`).join("");
      html += `</ul>`;
    }
    resultEl.innerHTML = html;
  } catch (err) {
    resultEl.innerHTML = "<p class='error'>Error: " + err + "</p>";
  }
});

// ---------------- Import Data ----------------
const MODULES = [
  { key: "itinerary", label: "Itinerary", unit: "rows" },
  { key: "territories", label: "Territories", unit: "regions" },
  { key: "invoices", label: "Invoices", unit: "records" },
  { key: "sales_orders", label: "Sales Orders", unit: "orders" },
  { key: "outlets", label: "Outlet Master", unit: "outlets" },
  { key: "product_sales", label: "SKU Sales", unit: "items" },
];

async function loadImportLog() {
  const grid = document.getElementById("import-modules");
  let log = {};
  try {
    log = await window.pywebview.api.get_import_log();
  } catch (err) {
    grid.innerHTML = "<p class='error'>Error loading import status.</p>";
    return;
  }
  grid.innerHTML = MODULES.map(m => {
    const entry = log[m.key];
    const hasData = !!entry;
    const isSkuSales = m.key === "product_sales";
    return `
      <div class="module-card">
        <div class="module-card-header">
          <span>${m.label}</span>
          <span class="badge ${hasData ? 'badge-success' : 'badge-danger'}">${hasData ? '&#10003;' : '!'}</span>
        </div>
        <div class="module-card-count">${hasData ? entry.row_count.toLocaleString() : '—'}<span style="font-size:12px;font-weight:400;color:var(--color-ash);"> ${m.unit}</span></div>
        <div class="module-card-foot">
          <span>Last Imported:</span>
          <b>${hasData ? entry.last_imported.replace('T',' ') : 'Never'}</b>
        </div>
        ${isSkuSales
          ? `<p class="hint" style="margin-top:10px;">Use the SKU Sales Import form above (needs Sale Model + date).</p>`
          : `<button class="btn-outline module-import-btn" data-type="${m.key}" style="margin-top:10px;width:100%;">Select File & Import</button>
             <div class="module-import-result" data-type="${m.key}" style="margin-top:8px;font-size:12px;"></div>`
        }
      </div>
    `;
  }).join("");

  document.querySelectorAll(".module-import-btn").forEach(btn => {
    btn.addEventListener("click", async () => {
      const type = btn.dataset.type;
      const resultEl = document.querySelector(`.module-import-result[data-type="${type}"]`);
      resultEl.textContent = "Waiting for file selection...";
      btn.disabled = true;
      try {
        const r = await window.pywebview.api.import_module(type);
        resultEl.innerHTML = `<span class="${r.ok ? 'hint' : 'error'}">${escapeHtml(r.message)}</span>`;
        if (r.ok) loadImportLog(); // refresh counts/timestamps
      } catch (err) {
        resultEl.innerHTML = `<span class="error">Error: ${escapeHtml(String(err))}</span>`;
      } finally {
        btn.disabled = false;
      }
    });
  });
}

document.getElementById("sku-import-btn").addEventListener("click", async () => {
  const saleModel = document.getElementById("sku-sale-model").value;
  const reportDate = document.getElementById("sku-report-date").value;
  const resultEl = document.getElementById("sku-import-result");
  if (!reportDate) {
    resultEl.innerHTML = "<p class='hint'>Pick the report date first.</p>";
    return;
  }
  resultEl.innerHTML = "<p class='hint'>Waiting for file selection...</p>";
  try {
    const r = await window.pywebview.api.import_sku_sales(saleModel, reportDate);
    resultEl.innerHTML = `<p class="${r.ok ? 'hint' : 'error'}">${escapeHtml(r.message)}</p>`;
    if (r.ok) loadImportLog();
  } catch (err) {
    resultEl.innerHTML = `<p class="error">Error: ${escapeHtml(String(err))}</p>`;
  }
});

// ---------------- Last Visit Sales Summary Report ----------------
document.getElementById("lvs-generate-btn").addEventListener("click", async () => {
  const date = document.getElementById("lvs-date").value;
  const resultEl = document.getElementById("lvs-result");
  if (!date) {
    resultEl.innerHTML = "<p class='hint'>Pick a date first.</p>";
    return;
  }
  resultEl.innerHTML = "<p class='hint'>Checking every Van Selling territory...</p>";
  try {
    const folder = await window.pywebview.api.get_default_export_folder();
    const result = await window.pywebview.api.generate_last_visit_summaries([], date, folder);
    const notWorked = result.reports.filter(r => r.worked === false);
    const otherSkips = result.skipped.filter(s => !notWorked.some(nw => nw.territory === s.territory));

    let html = `<p class="hint"><b>${result.written.length}</b> report(s) generated to <code>${escapeHtml(folder)}</code>.</p>`;

    if (notWorked.length > 0) {
      html += `<p class="hint"><b>${notWorked.length}</b> territory(ies) — rep did not work the previous visit date:</p><ul>`;
      html += notWorked.map(r => `<li>${escapeHtml(r.territory)} (${escapeHtml(r.route || '')}) — ${escapeHtml(r.reason)}</li>`).join("");
      html += `</ul>`;
    }
    if (otherSkips.length > 0) {
      html += `<p class="hint">Skipped for other reasons:</p><ul>`;
      html += otherSkips.map(s => `<li>${escapeHtml(s.territory)} — ${escapeHtml(s.reason)}</li>`).join("");
      html += `</ul>`;
    }
    resultEl.innerHTML = html;
  } catch (err) {
    resultEl.innerHTML = "<p class='error'>Error: " + err + "</p>";
  }
});

window.addEventListener("pywebviewready", () => {
  loadDashboard();
  loadTerritories();
  const dateInput = document.getElementById("cr-date");
  dateInput.value = new Date().toISOString().slice(0, 10);
  const lvsDateInput = document.getElementById("lvs-date");
  const tomorrow = new Date();
  tomorrow.setDate(tomorrow.getDate() + 1);
  lvsDateInput.value = tomorrow.toISOString().slice(0, 10);
});

// ---------------- Settings (SFA connection) ----------------
async function loadSfaConfig() {
  try {
    const c = await window.pywebview.api.get_sfa_config();
    document.getElementById("sfa-login-url").value = c.login_url || "";
    document.getElementById("sfa-username").value = c.username || "";
    document.getElementById("sfa-browser").value = c.browser || "chrome";
    document.getElementById("sfa-password").placeholder = c.has_password ? "•••• saved (leave blank to keep)" : "";
  } catch (err) {
    console.error("Could not load SFA config:", err);
  }
}

document.getElementById("sfa-save-btn").addEventListener("click", async () => {
  const resultEl = document.getElementById("sfa-result");
  const login_url = document.getElementById("sfa-login-url").value.trim();
  const username = document.getElementById("sfa-username").value.trim();
  const password = document.getElementById("sfa-password").value;
  const browser = document.getElementById("sfa-browser").value;
  if (!login_url || !username) {
    resultEl.innerHTML = "<p class='hint'>Login URL and username are required.</p>";
    return;
  }
  try {
    await window.pywebview.api.save_sfa_config(login_url, username, password, browser);
    resultEl.innerHTML = "<p class='hint'>Saved.</p>";
    document.getElementById("sfa-password").value = "";
    loadSfaConfig();
  } catch (err) {
    resultEl.innerHTML = "<p class='error'>Error saving: " + err + "</p>";
  }
});

async function runSfaProbe(apiMethod, loadingText) {
  const resultEl = document.getElementById("sfa-result");
  resultEl.innerHTML = `<p class="hint">${loadingText}</p>`;
  try {
    const r = await window.pywebview.api[apiMethod]();
    let html = `<p class="${r.ok ? 'hint' : 'error'}">${escapeHtml(r.message)}</p>`;
    if (r.note) html += `<p class="hint"><b>${escapeHtml(r.note)}</b></p>`;
    if (r.screenshot_base64) {
      html += `<img src="data:image/png;base64,${r.screenshot_base64}" style="max-width:100%;border:1px solid #ddd;border-radius:8px;margin-top:10px;">`;
    }
    resultEl.innerHTML = html;
  } catch (err) {
    resultEl.innerHTML = "<p class='error'>Error: " + err + "</p>";
  }
}

document.getElementById("sfa-test-btn").addEventListener("click", () =>
  runSfaProbe("test_sfa_connection", "Launching browser and attempting login — a real browser window will open..."));

document.getElementById("sfa-test-nav-btn").addEventListener("click", () =>
  runSfaProbe("test_sfa_navigate_sales_org", "Logging in and attempting to navigate to Sales Organization..."));

document.getElementById("sfa-test-nav-itinerary-btn").addEventListener("click", () =>
  runSfaProbe("test_sfa_navigate_itinerary", "Logging in and attempting to navigate to Itinerary..."));


// ---------------- Daily SFA Reports ----------------
function localToday() {
  const d = new Date();
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
}
function drDate() { return document.getElementById("dr-date").value; }
function fileName(p) { return String(p || "").split(/[\\/]/).pop(); }

async function loadDailyStatus() {
  const body = document.getElementById("dr-status-body");
  try {
    const st = await window.pywebview.api.get_daily_status(drDate());
    body.innerHTML = st.datasets.map(d => `
      <tr>
        <td><b>${escapeHtml(d.label)}</b>${d.source_file ? `<div class="hint">${escapeHtml(fileName(d.source_file))}</div>` : ""}</td>
        <td>${d.loaded ? '<span class="chip chip-ok">Loaded</span>' : '<span class="chip chip-missing">Missing</span>'}</td>
        <td class="right">${d.loaded ? d.rows.toLocaleString() : "—"}</td>
        <td>${escapeHtml(d.generated_at || (d.loaded ? "imported " + d.imported_at.replace("T", " ") : "—"))}</td>
        <td>${d.warnings.length ? d.warnings.map(w => `<div class="chip chip-warn" style="margin:2px 0;white-space:normal;">${escapeHtml(w)}</div>`).join("") : ""}</td>
      </tr>`).join("");
    if (st.history_bills > 0) {
      body.innerHTML += `<tr><td colspan="5" class="hint">${st.history_bills} bill(s) for this date come from imported SalesPulse history.</td></tr>`;
    }
    if (Object.keys(st.groups).length === 0) {
      body.innerHTML += `<tr><td colspan="5" class="error">No manager groups set up yet — go to Settings → Manager groups and import them from SalesPulse.</td></tr>`;
    }
  } catch (err) {
    body.innerHTML = `<tr><td colspan="5" class="error">Error: ${escapeHtml(String(err))}</td></tr>`;
  }
}

document.getElementById("dr-date").addEventListener("change", loadDailyStatus);

document.getElementById("dr-import-btn").addEventListener("click", async () => {
  const el = document.getElementById("dr-import-result");
  const btn = document.getElementById("dr-import-btn");
  el.innerHTML = "<p class='hint'>Waiting for file selection…</p>";
  btn.disabled = true;
  try {
    const r = await window.pywebview.api.import_sfa_exports();
    if (!r.results || r.results.length === 0) { el.innerHTML = `<p class="hint">${escapeHtml(r.message || "Nothing imported.")}</p>`; return; }
    el.innerHTML = "<ul class='result-list'>" + r.results.map(x => {
      if (!x.ok) return `<li><span class="chip chip-missing">Failed</span> ${escapeHtml(fileName(x.file))} — ${escapeHtml(x.message)}</li>`;
      return x.stored.map(s => `<li><span class="chip chip-ok">OK</span> ${escapeHtml(fileName(x.file))} → <b>${escapeHtml(s.label)}</b> for ${s.date}: ${s.rows} rows
        ${s.warnings.map(w => `<div class="sub">${escapeHtml(w)}</div>`).join("")}</li>`).join("");
    }).join("") + "</ul>";
    // Jump to the imported date if it differs from the one shown.
    const dates = r.results.filter(x => x.ok).flatMap(x => x.stored.map(s => s.date));
    if (dates.length && !dates.includes(drDate())) document.getElementById("dr-date").value = dates[0];
    loadDailyStatus();
  } catch (err) {
    el.innerHTML = `<p class="error">Error: ${escapeHtml(String(err))}</p>`;
  } finally {
    btn.disabled = false;
  }
});

function openPathButton(path, label) {
  return `<button class="link-btn" data-open="${escapeHtml(path)}">${escapeHtml(label)}</button>`;
}
document.addEventListener("click", async (e) => {
  const t = e.target.closest("[data-open]");
  if (!t) return;
  const r = await window.pywebview.api.open_path(t.dataset.open);
  if (!r.ok) alertInline(r.message);
});
function alertInline(msg) {
  const el = document.getElementById("dr-generate-result");
  if (el) el.insertAdjacentHTML("afterbegin", `<p class="error">${escapeHtml(msg)}</p>`);
}

document.getElementById("dr-generate-btn").addEventListener("click", async () => {
  const el = document.getElementById("dr-generate-result");
  const btn = document.getElementById("dr-generate-btn");
  const kinds = Array.from(document.querySelectorAll(".dr-kind:checked")).map(c => c.value);
  if (kinds.length === 0) { el.innerHTML = "<p class='hint'>Tick at least one report.</p>"; return; }
  el.innerHTML = "<p class='hint'>Generating…</p>";
  btn.disabled = true;
  try {
    const style = document.getElementById("dr-style").value || null;
    const r = await window.pywebview.api.generate_daily_reports(drDate(), kinds, document.getElementById("dr-images").checked, style);
    let html = r.warning ? `<p class="error">${escapeHtml(r.warning)}</p>` : "";
    html += `<p class="${r.ok ? 'hint' : 'error'}">Run #${r.run_id}: <b>${r.status.toUpperCase()}</b>${r.folder ? " — " + openPathButton(r.folder, "Open output folder") : ""}</p><ul class="result-list">`;
    for (const rep of r.reports) {
      if (rep.ok) {
        const pdfLinks = (rep.outputs || [{style: "", pdf: rep.pdf}]).map(o => openPathButton(o.pdf, o.style ? `PDF (${o.style})` : "PDF")).join(" · ");
        html += `<li><span class="chip chip-ok">OK</span> <b>${escapeHtml(rep.label)}</b> — ${pdfLinks}${rep.images.length ? ` + ${rep.images.length} WhatsApp image(s)` : ""}
          ${rep.notes.map(n => `<div class="sub">${escapeHtml(n)}</div>`).join("")}</li>`;
      } else {
        html += `<li><span class="chip chip-missing">Skipped</span> <b>${escapeHtml(rep.label)}</b> — ${escapeHtml(rep.reason)}</li>`;
      }
    }
    el.innerHTML = html + "</ul>";
    loadRuns();
  } catch (err) {
    el.innerHTML = `<p class="error">Error: ${escapeHtml(String(err))}</p>`;
  } finally {
    btn.disabled = false;
  }
});

async function loadRuns() {
  const el = document.getElementById("dr-runs");
  try {
    const runs = await window.pywebview.api.get_report_runs(15);
    if (runs.length === 0) { el.innerHTML = "<p class='hint'>No runs yet.</p>"; return; }
    el.innerHTML = `<table class="report-table"><thead><tr><th>#</th><th>Started</th><th>Report date</th><th>Trigger</th><th>Status</th><th>Reports</th><th>Output</th></tr></thead><tbody>` +
      runs.map(r => {
        const reps = (r.summary.reports || []).map(x => `${x.ok ? "✓" : "✗"} ${escapeHtml(x.label)}`).join("<br>");
        const cls = r.status === "success" ? "chip-ok" : (r.status === "partial" ? "chip-warn" : "chip-missing");
        return `<tr><td>${r.id}</td><td>${escapeHtml(r.started_at.replace("T", " "))}</td><td>${r.report_date}</td><td>${escapeHtml(r.trigger)}</td>
          <td><span class="chip ${cls}">${escapeHtml(r.status)}</span></td><td>${reps}</td><td>${r.folder ? openPathButton(r.folder, "Open") : "—"}</td></tr>`;
      }).join("") + "</tbody></table>";
  } catch (err) {
    el.innerHTML = `<p class="error">Error: ${escapeHtml(String(err))}</p>`;
  }
}
document.getElementById("dr-runs-refresh").addEventListener("click", loadRuns);

// ---------------- Settings: report rules + manager groups ----------------
const RS_KEYS = ["first_bill_time", "gap_red_minutes", "pivot_bill_count", "small_bill_lines", "focus_product",
  "hourly_start_hour", "hourly_end_hour", "separate_early_bucket",
  "van_bc_target", "van_sv_target", "pre_bc_target", "pre_sv_target", "output_style"];

async function loadReportSettings() {
  try {
    const s = await window.pywebview.api.get_report_settings();
    RS_KEYS.forEach(k => { const el = document.getElementById("rs-" + k); if (el && s[k] !== undefined) el.value = s[k]; });
  } catch (err) { console.error(err); }
}
document.getElementById("rs-save-btn").addEventListener("click", async () => {
  const values = {};
  RS_KEYS.forEach(k => { values[k] = document.getElementById("rs-" + k).value; });
  const r = await window.pywebview.api.save_report_settings(values);
  document.getElementById("rs-result").innerHTML = `<p class="${r.ok ? 'hint' : 'error'}">${escapeHtml(r.message)}</p>`;
  if (r.ok) loadReportSettings();
});

async function loadManagerGroups() {
  const el = document.getElementById("mg-groups");
  try {
    const st = await window.pywebview.api.get_daily_status(localToday());
    const names = Object.keys(st.groups);
    if (names.length === 0) { el.innerHTML = "<p class='hint'>No groups yet.</p>"; return; }
    el.innerHTML = names.map(g => `<div class="group-card"><b>${escapeHtml(g)}</b>${st.groups[g].map(t =>
      `${escapeHtml(t.territory)}<span class="model">${t.model}</span>`).join("<br>")}</div>`).join("");
  } catch (err) { el.innerHTML = `<p class="error">Error: ${escapeHtml(String(err))}</p>`; }
}
document.getElementById("mg-import-btn").addEventListener("click", async () => {
  const el = document.getElementById("mg-result");
  el.innerHTML = "<p class='hint'>Waiting for file selection…</p>";
  const r = await window.pywebview.api.import_salespulse_database(true);
  el.innerHTML = `<p class="${r.ok ? 'hint' : 'error'}">${escapeHtml(r.message)}</p>`;
  if (r.ok) { loadManagerGroups(); loadReportSettings(); }
});

window.addEventListener("pywebviewready", () => {
  document.getElementById("dr-date").value = localToday();
});
