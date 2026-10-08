"use strict";

/* ---------- helpers ---------- */

const $ = (sel, root = document) => root.querySelector(sel);
const view = $("#view");
const ACTIVE = new Set(["queued", "running"]);

const SKU_INFO = {
  google_places_text_search: { name: "Google Places · Text Search", use: "Finding & matching businesses" },
  google_places_text_search_enterprise: { name: "Google Places · Quick-fill lookups", use: "Paste-one-line project setup" },
  google_places_details: { name: "Google Places · Place Details", use: "Profiles, hours, reviews" },
  google_geocoding: { name: "Google Geocoding", use: "Addresses → coordinates" },
  serpapi_search: { name: "SerpApi searches", use: "Local Pack & Local Finder ranks" },
};
const STEP_LABEL = {
  check_database: "Database connection",
  check_google_places: "Google Places API key",
  check_serpapi: "SerpApi key",
  resolve_place: "Find the business on Google",
  fetch_profile: "Google profile",
  fetch_reviews: "Top reviews",
  crawl_website: "Website: name, address, phone, social links & offerings",
  discover_website: "Read the website",
  read_website: "Read the website",
  match_business: "Find & verify the Google listing",
  verify_match: "Verify the listing match",
  analyze_reviews: "Review sentiment & topics",
  build_services: "Service list",
  generate_keywords: "Keywords",
  check_budget: "Check SerpApi credits",
  collect_rankings: "Local Pack & Local Finder searches",
  compute_visibility: "Visibility score",
  find_competitors: "Competitors & gaps",
  check_budget: "SerpApi credit check",
  build_report: "Report ready",
};
const stepLabel = (name) => STEP_LABEL[name] || humanize(name);
const STEP_ICON = { succeeded: "✓", failed: "!", skipped: "–", running: "…", pending: "" };

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}
const humanize = (s) => String(s || "").replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
const fmtNum = (n) => Number(n || 0).toLocaleString();
const shortId = (id) => String(id).slice(0, 8);

function timeAgo(iso) {
  if (!iso) return "—";
  const s = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}
const fullTime = (iso) => (iso ? new Date(iso).toLocaleString() : "");

function duration(start, end) {
  if (!start) return "—";
  const ms = (end ? new Date(end) : new Date()) - new Date(start);
  if (ms < 1000) return `${Math.max(ms, 0)} ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)} s`;
  return `${Math.floor(ms / 60000)} min ${Math.round((ms % 60000) / 1000)} s`;
}
const badge = (status) => `<span class="badge ${esc(status)}">${esc(humanize(status))}</span>`;

class ApiError extends Error {
  constructor(status, detail) {
    super(typeof detail === "string" ? detail : `Request failed (${status})`);
    this.status = status;
    this.detail = detail;
  }
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const body = res.headers.get("content-type")?.includes("json") ? await res.json() : null;
  if (!res.ok) throw new ApiError(res.status, body?.detail ?? body);
  return body;
}

function toast(message, kind = "") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = message;
  $("#toasts").append(el);
  setTimeout(() => el.remove(), 4200);
}

/* ---------- polling ---------- */

let pollTimer = null;
let navCount = 0; // bumps on every navigation; renders drop stale results
function schedule(fn, ms) {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(fn, ms);
}

async function refreshHealth() {
  const el = $("#health");
  try {
    const h = await api("/health");
    el.className = "health ok";
    el.lastElementChild.textContent = `API online · DB ${h.database}`;
  } catch {
    el.className = "health bad";
    el.lastElementChild.textContent = "API or database offline";
  }
}

/* ---------- shared actions ---------- */

async function runDiagnostic(button) {
  if (button) {
    button.disabled = true;
    button.innerHTML = `<span class="spinner"></span>Starting…`;
  }
  try {
    const job = await api("/v1/jobs", { method: "POST", body: JSON.stringify({ job_type: "diagnostic" }) });
    toast("Diagnostic job queued");
    location.hash = `#jobs/${job.id}`;
  } catch (err) {
    toast(err.message, "bad");
    if (button) {
      button.disabled = false;
      button.textContent = "Run diagnostic";
    }
  }
}

function stepDots(steps) {
  if (!steps?.length) return `<span class="muted">—</span>`;
  return `<span class="step-dots">${steps.map((s) => `<i class="${esc(s.status)}" title="${esc(stepLabel(s.name))}: ${esc(s.status)}"></i>`).join("")}</span>`;
}

function jobsTable(jobs, projects = {}) {
  if (!jobs.length) {
    return `<div class="empty">
      <svg viewBox="0 0 24 24"><path d="M3 5h2v2H3V5zm4 0h14v2H7V5zM3 11h2v2H3v-2zm4 0h14v2H7v-2zm-4 6h2v2H3v-2zm4 0h14v2H7v-2z"/></svg>
      <h3>No jobs yet</h3><p>Run a diagnostic to check the database and your API keys.</p>
      <button class="btn primary" data-action="diagnostic">Run diagnostic</button></div>`;
  }
  return `<div class="table-wrap"><table>
    <thead><tr><th>Job</th><th>Status</th><th>Steps</th><th>Project</th><th>Created</th><th>Duration</th></tr></thead>
    <tbody>${jobs.map((j) => `
      <tr class="clickable" data-href="#jobs/${esc(j.id)}">
        <td><b>${esc(humanize(j.job_type))}</b><div class="mono muted">${esc(shortId(j.id))}</div></td>
        <td>${badge(j.status)}</td>
        <td>${stepDots(j.steps)}</td>
        <td>${j.project_id ? esc(projects[j.project_id]?.name || shortId(j.project_id)) : `<span class="muted">—</span>`}</td>
        <td title="${esc(fullTime(j.created_at))}">${esc(timeAgo(j.created_at))}</td>
        <td class="mono">${esc(duration(j.started_at, j.finished_at))}</td>
      </tr>`).join("")}
    </tbody></table></div>`;
}

/* ---------- Overview ---------- */

function setupChecks(lastDiag) {
  const step = (name) => lastDiag?.steps?.find((s) => s.name === name);
  const row = (state, title, detail) => `
    <div class="check ${state}"><div class="ico">${{ ok: "✓", bad: "!", warn: "?", idle: "·" }[state]}</div>
    <div><div class="t">${esc(title)}</div><div class="d">${detail}</div></div></div>`;
  const fromStep = (s, okText) => {
    if (!s) return ["idle", "Not checked yet — run a diagnostic."];
    if (s.status === "succeeded") return ["ok", okText(s.result || {})];
    if (s.status === "skipped") return ["warn", esc(s.error || "Skipped")];
    if (s.status === "failed") return ["bad", esc(s.error || "Failed")];
    return ["idle", esc(humanize(s.status))];
  };
  const db = fromStep(step("check_database"), () => "Connected.");
  const places = fromStep(step("check_google_places"), (r) => `Key works — found <b>${esc(r.name || "a place")}</b>.`);
  const serp = fromStep(step("check_serpapi"), (r) =>
    `Key works — ${esc(r.plan_name || "plan")}, ${fmtNum(r.plan_searches_left)} searches left.`);
  return `<div class="checks">
    ${row(db[0], "Database", db[1])}
    ${row(places[0], "Google Places API key", places[1])}
    ${row(serp[0], "SerpApi key", serp[1])}
  </div>
  ${lastDiag ? `<p class="muted" style="margin:12px 0 0;font-size:12.5px">Last checked ${esc(timeAgo(lastDiag.created_at))} · <a href="#jobs/${esc(lastDiag.id)}">view job</a></p>` : ""}`;
}

function usageMeters(usage) {
  return `<div class="meter-list">${usage.map((u) => {
    const info = SKU_INFO[u.sku] || { name: u.sku, use: "" };
    const pct = u.monthly_limit ? Math.min(100, (u.month_count / u.monthly_limit) * 100) : 0;
    const level = pct >= 90 ? "bad" : pct >= 70 ? "warn" : "";
    const daily = u.daily_limit ? ` · today ${fmtNum(u.day_count)} / ${fmtNum(u.daily_limit)}` : "";
    return `<div>
      <div class="meter-top"><span class="name">${esc(info.name)}</span>
        <span class="nums">${fmtNum(u.month_count)} / ${fmtNum(u.monthly_limit)}</span></div>
      <div class="bar ${level}"><span style="width:${pct.toFixed(1)}%"></span></div>
      <div class="meter-sub">${esc(info.use)}${daily} · <b>${fmtNum(u.remaining)}</b> left</div>
      ${u.blocked ? `<div class="meter-sub bad-text">${esc(u.blocked)}</div>` : ""}
    </div>`;
  }).join("")}</div>`;
}

/* ---------- Settings (read-only) ---------- */

async function renderSettings() {
  const nav = navCount;
  view.innerHTML = `<div class="page-head"><div><h1>Settings</h1></div></div><div class="skeleton" style="height:320px"></div>`;
  const [s, ed] = await Promise.all([api("/v1/settings"), api("/v1/settings/editable")]);
  if (nav !== navCount) return;
  const yes = (v) => (v ? "On" : "Off");
  const key = (name, v) => `<tr><td class="mono">${esc(name)}</td><td>${v ? `<span class="ok-text">✓ set</span> <span class="mono muted">${esc(v)}</span>` : `<span class="bad-text">not set</span>`}</td></tr>`;
  const limits = s.limits.map((l) => `<tr><td>${esc(l.label)}<div class="muted small mono">${esc(l.daily_setting)} · ${esc(l.monthly_setting)}</div>
      ${l.blocked ? `<div class="bad-text small">${esc(l.blocked)}</div>` : ""}</td>
    <td class="num">${l.daily_limit ? `${fmtNum(l.day_count)} / ${fmtNum(l.daily_limit)}` : `${fmtNum(l.day_count)} <span class="muted">(no cap)</span>`}</td>
    <td class="num">${fmtNum(l.month_count)} / ${fmtNum(l.monthly_limit)}</td><td class="num"><b>${fmtNum(l.remaining)}</b></td></tr>`).join("");
  const lc = s.retention.last_cleanup;
  const worker = s.worker_online === true ? `<span class="ok-text">● online</span>` : s.worker_online === false ? `<span class="bad-text">● not responding</span>` : `<span class="bad-text">● job queue unreachable</span>`;
  view.innerHTML = `
    <div class="page-head"><div><h1>Settings</h1>
      <p class="muted">Change settings below: they apply within a few seconds, no restart needed. Your <span class="mono">.env</span> values stay as the defaults (<b>Reset</b> goes back to them).</p></div></div>
    <div class="settings-grid">
      <div class="card"><div class="card-head"><h2>App</h2></div><div class="card-body"><table class="kv-table">
        <tr><td>Access</td><td>${esc(s.app.access)}</td></tr>
        <tr><td>Worker</td><td>${worker}</td></tr>
        <tr><td class="mono">DEBUG</td><td>${yes(s.app.debug)}</td></tr>
        ${key("GOOGLE_API_KEY", s.keys.GOOGLE_API_KEY)}${key("SERPAPI_KEY", s.keys.SERPAPI_KEY)}
      </table></div></div>
      <div class="card"><div class="card-head"><div><h2>30-day clean-up</h2><p class="muted">Google's terms: its content is kept for at most ${esc(s.retention.GOOGLE_DATA_TTL_DAYS)} days.</p></div></div><div class="card-body"><table class="kv-table">
        <tr><td>Runs</td><td>Every night at ${String(s.retention.CLEANUP_HOUR_UTC).padStart(2, "0")}:00 UTC</td></tr>
        <tr><td>Last run</td><td>${lc ? `${esc(timeAgo(lc.at))} · ${badge(lc.status)}` : "Not yet"}</td></tr>
        ${lc?.result ? `<tr><td>Removed</td><td>${esc(lc.result.raw_responses_deleted)} raw responses · ${esc(lc.result.review_texts_removed)} review texts · ${esc(lc.result.old_profile_snapshots_trimmed)} old profile snapshots</td></tr>` : ""}
      </table></div></div>
    </div>
    <div class="card" style="margin-top:18px"><div class="card-head"><div><h2>Free-tier limits</h2>
        <p class="muted">Daily limits reset at 00:00 UTC (${esc(timeUntil(s.resets.daily))}); monthly on ${esc(new Date(s.resets.monthly).toLocaleDateString())}.</p></div></div>
      <div class="card-body"><div class="table-wrap"><table><thead><tr><th>API</th><th class="num">Today</th><th class="num">This month</th><th class="num">Left</th></tr></thead><tbody>${limits}</tbody></table></div></div></div>
    <h2 class="section-title" style="margin-top:22px">Change settings</h2>
    <p class="muted small" style="margin-top:-6px">Anyone who can open this app can change these, which is why it only opens on the server itself. Database, ports and passwords stay in <span class="mono">.env</span>.</p>
    <div class="settings-groups">${ed.groups.map(settingsGroup).join("")}</div>
    <div class="card" style="margin-top:18px"><div class="card-head"><h2>Recent changes</h2></div>
      <div class="card-body">${ed.history.length ? `<div class="table-wrap"><table><thead><tr><th>When</th><th>Setting</th><th>From</th><th>To</th></tr></thead><tbody>
        ${ed.history.map((h) => `<tr><td>${esc(timeAgo(h.at))}</td><td class="mono">${esc(h.key)}</td><td>${esc(h.old ?? "—")}</td><td>${h.new == null ? `<span class="muted">reset to .env</span>` : esc(h.new)}</td></tr>`).join("")}
      </tbody></table></div>` : `<p class="muted">No changes made in the app yet.</p>`}</div></div>`;
}

function settingInput(x) {
  const name = esc(x.key);
  if (x.type === "secret") {
    return `<input type="password" name="${name}" autocomplete="off" placeholder="${x.value ? `Set (${esc(x.value)}): paste a new key to replace` : "Paste the key"}">`;
  }
  if (x.type === "bool") {
    return `<select name="${name}" data-initial="${x.value ? "true" : "false"}"><option value="true" ${x.value ? "selected" : ""}>On</option><option value="false" ${x.value ? "" : "selected"}>Off</option></select>`;
  }
  if (x.type === "choice") {
    return `<select name="${name}" data-initial="${esc(x.value)}">${x.choices.map((c) => `<option ${c === x.value ? "selected" : ""}>${esc(c)}</option>`).join("")}</select>`;
  }
  const attrs = x.type === "int" ? `type="number" step="1" ${x.min != null ? `min="${x.min}"` : ""} ${x.max != null ? `max="${x.max}"` : ""}` : `type="text"`;
  return `<input ${attrs} name="${name}" value="${esc(x.value ?? "")}" data-initial="${esc(x.value ?? "")}">`;
}

function settingsGroup(g) {
  const hasCost = g.settings.some((x) => x.free_max != null);
  const rows = g.settings.map((x) => `<div class="setting-row">
      <div class="setting-label"><label for="set-${esc(x.key)}">${esc(x.label)}</label>
        <span class="mono muted small">${esc(x.key)}</span>
        ${x.source === "app" ? `<span class="pill ok small" title="Changed in the app; .env has ${esc(x.env_value ?? "nothing")}">changed in app</span>` : ""}
        ${x.restart ? `<span class="pill warn small" title="Read when the app starts">needs restart</span>` : ""}
        ${x.help ? `<div class="muted small">${esc(x.help)}</div>` : ""}</div>
      <div class="setting-input">${settingInput(x).replace("<input", `<input id="set-${esc(x.key)}"`).replace("<select", `<select id="set-${esc(x.key)}"`)}
        ${x.source === "app" ? `<button type="button" class="btn ghost sm" data-action="setting-reset" data-key="${esc(x.key)}" title="Back to the .env value (${esc(x.env_value ?? "not set")})">Reset</button>` : ""}</div>
    </div>`).join("");
  return `<form class="card" data-form="settings-group"><div class="card-head"><h2>${esc(g.name)}</h2></div>
    <div class="card-body"><div class="setting-list">${rows}</div>
      ${hasCost ? `<label class="run-check" style="margin-top:10px"><input type="checkbox" name="__accept_charges"> I accept possible charges for monthly limits above the free tier</label>` : ""}
      <div class="toolbar" style="margin-top:12px"><button class="btn primary sm" type="submit">Save</button></div></div></form>`;
}

async function saveSettingsGroup(form) {
  const values = {};
  form.querySelectorAll("input[name], select[name]").forEach((el) => {
    if (el.name === "__accept_charges") return;
    if (el.type === "password") {
      if (el.value.trim()) values[el.name] = el.value.trim();
    } else if (el.value !== el.dataset.initial) {
      values[el.name] = el.value;
    }
  });
  if (!Object.keys(values).length) return toast("Nothing changed");
  const btn = form.querySelector('button[type="submit"]');
  btn.disabled = true;
  try {
    const r = await api("/v1/settings", { method: "PATCH", body: JSON.stringify({ values, accept_charges: Boolean(form.querySelector('[name="__accept_charges"]')?.checked) }) });
    toast(r.changed.length ? `Saved: ${r.changed.join(", ")}` : "No change");
    if (r.restart_needed.length) toast(`${r.restart_needed.join(", ")}: applies after "docker compose restart api worker"`);
    renderSettings();
  } catch (err) {
    toast(err.message, "bad");
    btn.disabled = false;
  }
}

function timeUntil(iso) {
  const m = Math.max(0, Math.round((new Date(iso).getTime() - Date.now()) / 60000));
  return m >= 60 ? `in ${Math.floor(m / 60)} h ${m % 60} min` : `in ${m} min`;
}

// A reached free-tier limit, in plain words with its reset time (shown on Overview and project pages).
function limitBanner(usage) {
  const blocked = (usage || []).filter((u) => u.blocked);
  if (!blocked.length) return "";
  return `<div class="card card-body limit-banner"><b>Free-tier limit reached</b>
    <ul>${blocked.map((u) => `<li>${esc(u.blocked)}</li>`).join("")}</ul></div>`;
}

async function renderOverview(silent = false) {
  const nav = navCount;
  if (!silent) view.innerHTML = `
    <div class="page-head"><div><h1>Overview</h1><p class="muted">System status, free-tier usage and recent activity.</p></div>
      <button class="btn primary" data-action="diagnostic">Run diagnostic</button></div>
    <div class="stack"><div class="skeleton" style="height:96px"></div><div class="skeleton" style="height:260px"></div></div>`;

  const [projects, jobs, usage, health] = await Promise.all([
    api("/v1/projects?limit=200"),
    api("/v1/jobs?limit=200"),
    api("/v1/usage"),
    api("/health").catch(() => null),
  ]);
  if (nav !== navCount) return;
  const projectMap = Object.fromEntries(projects.map((p) => [p.id, p]));
  const lastDiag = jobs.find((j) => j.job_type === "diagnostic" && !ACTIVE.has(j.status));
  const counts = jobs.reduce((acc, j) => ((acc[j.status] = (acc[j.status] || 0) + 1), acc), {});
  const active = (counts.queued || 0) + (counts.running || 0);
  const problems = (counts.failed || 0) + (counts.partial_success || 0);

  view.innerHTML = `
    <div class="page-head"><div><h1>Overview</h1><p class="muted">System status, free-tier usage and recent activity.</p></div>
      <button class="btn primary" data-action="diagnostic">Run diagnostic</button></div>
    <div class="stack">${limitBanner(usage)}
      <div class="stats">
        <div class="card stat"><div class="label">System</div>
          <div class="value" style="color:var(--${health ? "ok" : "bad"})">${health ? "Online" : "Offline"}</div>
          <div class="sub">API · database ${esc(health?.database || "unavailable")}</div></div>
        <div class="card stat"><div class="label">Projects</div><div class="value">${fmtNum(projects.length)}</div>
          <div class="sub"><a href="#projects">Manage projects →</a></div></div>
        <div class="card stat"><div class="label">Jobs</div><div class="value">${fmtNum(jobs.length)}</div>
          <div class="sub">${fmtNum(counts.completed || 0)} completed · ${fmtNum(active)} active</div></div>
        <div class="card stat"><div class="label">Need attention</div>
          <div class="value" style="color:var(--${problems ? "warn" : "text"})">${fmtNum(problems)}</div>
          <div class="sub">failed or partial jobs</div></div>
      </div>
      <div class="cols">
        <div class="card"><div class="card-head"><div><h2>Free-tier usage</h2>
          <p class="muted">This month. The app refuses calls before these limits, so nothing is billed.</p></div></div>
          <div class="card-body">${usageMeters(usage)}</div></div>
        <div class="card"><div class="card-head"><div><h2>Setup check</h2>
          <p class="muted">From the latest diagnostic job.</p></div></div>
          <div class="card-body">${setupChecks(lastDiag)}</div></div>
      </div>
      <div class="card"><div class="card-head"><h2>Recent jobs</h2><a href="#jobs" class="btn ghost sm">All jobs</a></div>
        <div class="card-body" style="padding:8px 0 0">${jobsTable(jobs.slice(0, 6), projectMap)}</div></div>
    </div>`;

  if (active) schedule(() => renderOverview(true), 3000);
}

/* ---------- Projects ---------- */

function projectCard(p) {
  const areas = p.service_areas.length
    ? `<div class="chips">${p.service_areas.map((a) => `<span class="chip" title="${a.latitude != null ? esc(`${a.latitude}, ${a.longitude}`) : "no coordinates"}">📍 ${esc(a.name)}</span>`).join("")}</div>`
    : `<span class="muted">—</span>`;
  const keywords = p.keywords.length
    ? `<div class="chips">${p.keywords.map((k) => `<span class="chip">${esc(k)}</span>`).join("")}</div>`
    : `<span class="muted">—</span>`;
  const site = p.website_url
    ? `<a href="${esc(p.website_url)}" target="_blank" rel="noopener noreferrer">${esc(p.website_url.replace(/^https?:\/\//, "").replace(/\/$/, ""))}</a>`
    : `<span class="muted">—</span>`;
  return `<article class="card project clickable" data-href="#projects/${esc(p.id)}">
    <div class="top"><div><h2>${esc(p.name)}</h2><div class="biz">${esc(p.business_name)}</div></div>
      <span class="chips"><span class="tag">${esc(p.country)}</span><span class="tag">${esc(p.language)}</span></span></div>
    <dl class="kv">
      <dt>Address</dt><dd>${p.address ? esc(p.address) : `<span class="muted">—</span>`}</dd>
      <dt>Phone</dt><dd>${p.phone ? esc(p.phone) : `<span class="muted">—</span>`}</dd>
      <dt>Website</dt><dd>${site}</dd>
      <dt>Google</dt><dd>${p.place_id
        ? `<a href="https://www.google.com/maps/place/?q=place_id:${encodeURIComponent(p.place_id)}" target="_blank" rel="noopener noreferrer">✓ Business profile linked ↗</a>`
        : `<span class="muted">Not linked</span>`}</dd>
      <dt>Areas</dt><dd>${areas}</dd>
      <dt>Keywords</dt><dd>${keywords}</dd>
    </dl>
    <div class="foot"><span title="${esc(fullTime(p.created_at))}">Created ${esc(timeAgo(p.created_at))}</span>
      <span>${p.match_status === "manual_review_required" ? `<span class="pill warn">Needs your choice</span>` : confidencePill(p.match_confidence)}
        <span class="mono" title="${esc(p.id)}">${esc(shortId(p.id))}</span></span></div>
  </article>`;
}

async function renderProjects() {
  const nav = navCount;
  view.innerHTML = `<div class="page-head"><div><h1>Projects</h1><p class="muted">Businesses being audited.</p></div>
    <button class="btn primary" data-action="new-project">+ New project</button></div>
    <div class="skeleton" style="height:220px"></div>`;
  const projects = await api("/v1/projects?limit=200");
  if (nav !== navCount) return;
  const body = projects.length
    ? `<div class="project-grid">${projects.map(projectCard).join("")}</div>`
    : `<div class="card empty">
        <svg viewBox="0 0 24 24"><path d="M10 4H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-8l-2-2z"/></svg>
        <h3>No projects yet</h3><p>Create a project for the business you want to audit.</p>
        <button class="btn primary" data-action="new-project">+ New project</button></div>`;
  view.innerHTML = `<div class="page-head"><div><h1>Projects</h1>
      <p class="muted">${fmtNum(projects.length)} business${projects.length === 1 ? "" : "es"} being audited.</p></div>
    <button class="btn primary" data-action="new-project">+ New project</button></div>${body}`;
}


/* ---------- Project page (audit results) ---------- */

const SOCIAL_LABEL = {
  facebook: "Facebook", instagram: "Instagram", linkedin: "LinkedIn", x: "X (Twitter)", youtube: "YouTube",
  tiktok: "TikTok", pinterest: "Pinterest", houzz: "Houzz", yelp: "Yelp",
};
const OFFERING_SOURCE = {
  gbp_category: "Google categories",
  website_schema: "Website · structured data",
  website_service_page: "Website · service pages",
  website_heading: "Website · services page headings",
  website_sitemap: "Website · sitemap",
  review_topic: "Reviews · services mentioned",
};

function stars(rating) {
  if (rating == null) return "";
  const full = Math.round(rating);
  return `<span class="stars" aria-label="${esc(rating)} out of 5">${"★".repeat(full)}<span>${"★".repeat(5 - full)}</span></span>`;
}

const dash = `<span class="muted">—</span>`;
const orDash = (v) => (v == null || v === "" ? dash : esc(v));
const chipList = (items) => (items?.length ? `<div class="chips">${items.map((x) => `<span class="chip">${esc(x)}</span>`).join("")}</div>` : dash);
const extLink = (url, label) => `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(label)} ↗</a>`;

function auditProgress(job) {
  if (!job) return "";
  const active = ACTIVE.has(job.status);
  const steps = (job.steps || []).map((s) => `
    <li class="${esc(s.status)}"><span class="node">${s.status === "running" ? `<span class="spinner"></span>` : STEP_ICON[s.status] ?? ""}</span>
      <span>${esc(stepLabel(s.name))}${s.result?.note ? ` <span class="muted">· ${esc(s.result.note)}</span>` : ""}${s.error ? ` <span class="${s.status === "skipped" ? "muted" : "err"}">· ${esc(s.error)}</span>` : ""}</span></li>`).join("");
  return `<div class="card card-body audit-bar ${active ? "active" : ""}">
    <div class="audit-head"><b>${active ? "Audit running…" : "Last audit"}</b> ${badge(job.status)}
      <span class="muted">${esc(timeAgo(job.created_at))}</span><a href="#jobs/${esc(job.id)}" class="muted">details</a></div>
    ${steps ? `<ul class="mini-steps">${steps}</ul>` : `<p class="muted" style="margin:6px 0 0">Waiting for the worker…</p>`}</div>`;
}


const SENTIMENT_CLASS = { positive: "ok", neutral: "idle", negative: "bad" };
const THEME_LABEL = {
  service_quality: "Service quality", staff: "Staff / customer service", price_value: "Price / value",
  speed: "Speed", cleanliness: "Cleanliness", communication: "Communication", specific_service: "Specific service",
};

function reviewInsights(sum) {
  if (!sum) return "";
  const d = sum.sentiment_distribution;
  const total = (d.positive + d.neutral + d.negative) || 1;
  const seg = (k) => d[k] ? `<span class="seg ${SENTIMENT_CLASS[k]}" style="width:${(d[k] / total) * 100}%" title="${esc(d[k])} ${k}"></span>` : "";
  const chips = (items, cls) => items?.length
    ? `<div class="chips">${items.map((t) => `<span class="tag-chip ${cls}">${esc(t)}</span>`).join("")}</div>` : `<span class="muted small">None</span>`;
  const themes = Object.entries(sum.themes || {}).map(([k, t]) => `
      <tr><td>${esc(t.label)}</td><td class="num ok-text">${t.positive || ""}</td><td class="num">${t.neutral || ""}</td><td class="num bad-text">${t.negative || ""}</td></tr>`).join("");
  return `<div class="insights">
    <div class="insight-row">
      <div><div class="label">Sentiment</div>
        <div class="sent-bar">${seg("positive")}${seg("neutral")}${seg("negative")}</div>
        <div class="small muted">${d.positive} positive · ${d.neutral} neutral · ${d.negative} negative</div></div>
      <div><div class="label">Customers praise</div>${chips(sum.top_positive_topics, "ok")}</div>
      <div><div class="label">Customers complain about</div>${chips(sum.top_negative_topics, "bad")}</div>
    </div>
    ${themes ? `<details class="themes"><summary>Themes</summary><div class="table-wrap"><table>
      <thead><tr><th>Theme</th><th class="num">Positive</th><th class="num">Neutral</th><th class="num">Negative</th></tr></thead>
      <tbody>${themes}</tbody></table></div></details>` : ""}
    ${sum.note ? `<p class="muted small" style="margin:0">${esc(sum.note)}</p>` : ""}
  </div>`;
}


/* ---------- Services & keywords (Phase 6) ---------- */

const FAMILY_LABEL = (src) => src === "gbp_category" ? "Google" : src.startsWith("website") ? "Website"
  : src === "review_topic" ? "Reviews" : src === "user" ? "You" : src;
const PATTERN_LABEL = { in_city: "in city", near_me: "near me", city: "city", user: "yours" };

function serviceRow(svc) {
  const families = [...new Set((svc.sources || []).map((x) => FAMILY_LABEL(x.source)))];
  return `<label class="svc-row">
    <input type="checkbox" data-svc="${esc(svc.id)}" ${svc.selected ? "checked" : ""} ${svc.kind !== "service" ? "disabled" : ""}>
    <span class="svc-name">${esc(svc.name)}</span>
    <span class="svc-meta">${families.map((f) => `<span class="src-chip">${esc(f)}</span>`).join("")}${svc.review_mentions ? `<span class="src-chip">💬 ${esc(svc.review_mentions)}</span>` : ""}</span>
  </label>`;
}

function servicesCard(projectId, data) {
  const core = data.services.filter((x) => x.selected);
  const rest = data.services.filter((x) => !x.selected);
  return `<div class="card"><div class="card-head"><div><h2>Services</h2>
      <p class="muted">Tick the core services: they become keywords. Merged from Google, the website and reviews.</p></div>
      <button class="btn ghost sm" data-action="svc-refresh" data-project="${esc(projectId)}">Refresh from audit</button></div>
    <div class="card-body">
      ${data.services.length ? `<div class="svc-list">${core.map(serviceRow).join("")}</div>
        ${rest.length ? `<details class="svc-more"><summary>${rest.length} more services</summary><div class="svc-list">${rest.map(serviceRow).join("")}</div></details>` : ""}`
        : `<p class="muted">No services yet. Run the audit, or add one below.</p>`}
      ${data.customer_types.length ? `<details class="svc-more"><summary>${data.customer_types.length} customer types (not used for keywords)</summary>
        <div class="chips">${data.customer_types.map((x) => `<span class="chip">${esc(x.name)}</span>`).join("")}</div></details>` : ""}
      ${data.generic.length ? `<details class="svc-more"><summary>${data.generic.length} generic Google types (hidden)</summary>
        <div class="chips">${data.generic.map((x) => `<span class="chip">${esc(x.name)}</span>`).join("")}</div></details>` : ""}
      <form class="inline-form" data-form="add-service" data-project="${esc(projectId)}">
        <input name="name" placeholder="Add a service, e.g. Hot water systems" minlength="2" required>
        <button class="btn sm">Add</button></form>
    </div></div>`;
}

function areasCard(projectId, areas) {
  return `<div class="card"><div class="card-head"><div><h2>Service areas</h2>
      <p class="muted">Cities or suburbs the business serves. Each becomes "{service} in {city}".</p></div></div>
    <div class="card-body">
      <div class="chips">${areas.map((a, i) => `<span class="chip area-chip" title="${a.latitude != null ? esc(`${a.latitude.toFixed(4)}, ${a.longitude.toFixed(4)}`) : "no coordinates yet"}">📍 ${esc(a.name)}${areas.length > 1 ? ` <button class="icon-btn xs" data-action="area-remove" data-project="${esc(projectId)}" data-index="${i}" aria-label="Remove">✕</button>` : ""}</span>`).join("") || `<span class="muted">None</span>`}</div>
      <form class="inline-form" data-form="add-area" data-project="${esc(projectId)}">
        <input name="name" placeholder="Add an area, e.g. Bondi, NSW" minlength="2" required>
        <button class="btn sm">Add</button></form>
    </div></div>`;
}

function keywordsCard(projectId, data) {
  const pv = data.preview;
  const budget = pv.active
    ? `<b>${pv.active}</b> of ${pv.cap} active · one ranking check uses <b>${pv.serpapi_credits_per_ranking_run}</b> SerpApi credits · ${fmtNum(pv.serpapi_remaining_this_month)} left this month (≈ ${pv.ranking_runs_possible ?? 0} checks)`
    : "No active keywords yet.";
  const rows = data.keywords.map((k) => `
    <tr class="${k.active ? "" : "inactive"}">
      <td><input type="checkbox" data-kw="${esc(k.id)}" data-project="${esc(projectId)}" ${k.active ? "checked" : ""} title="Rank-check this keyword"></td>
      <td><b>${esc(k.keyword)}</b></td>
      <td>${esc(k.service || "—")}</td>
      <td>${esc(k.location_name)}</td>
      <td><span class="tag">${esc(PATTERN_LABEL[k.pattern] || k.pattern || "")}</span></td>
      <td><button class="icon-btn xs" data-action="kw-delete" data-project="${esc(projectId)}" data-id="${esc(k.id)}" aria-label="Delete">✕</button></td>
    </tr>`).join("");
  return `<div class="card"><div class="card-head"><div><h2>Keywords</h2>
      <p class="muted">${budget}</p></div>
      <button class="btn primary sm" data-action="kw-generate" data-project="${esc(projectId)}">${data.keywords.length ? "Regenerate" : "Generate keywords"}</button></div>
    <div class="card-body">
      ${data.notes?.length ? `<ul class="notes">${data.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>` : ""}
      ${rows ? `<div class="table-wrap"><table class="kw-table"><thead><tr><th>On</th><th>Keyword</th><th>Service</th><th>Area</th><th>Type</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>`
        : `<p class="muted">Tick core services and areas, then click “Generate keywords”. Generating uses no credits.</p>`}
      <form class="inline-form" data-form="add-keyword" data-project="${esc(projectId)}">
        <input name="keyword" placeholder="Add your own keyword, e.g. 24 hour plumber sydney" minlength="3" required>
        <button class="btn sm">Add</button></form>
      ${pv.active ? `<div class="next-step"><span class="muted">Happy with the <b>${pv.active}</b> keywords that are On?</span>
        <button class="btn primary sm" data-action="goto-rank" data-project="${esc(projectId)}">Next: check rankings ↓</button></div>` : ""}
    </div></div>`;
}

async function renderKeywordSection(project) {
  const holder = document.getElementById("kw-section");
  if (!holder) return;
  try {
    const [services, keywords] = await Promise.all([
      api(`/v1/projects/${encodeURIComponent(project.id)}/services`),
      api(`/v1/projects/${encodeURIComponent(project.id)}/keywords`),
    ]);
    // keep "N more services" etc. open across refreshes
    const open = new Set([...holder.querySelectorAll("details[open] > summary")].map((s) => s.textContent));
    holder.innerHTML = `<h2 class="section-title"><span class="step-no">2</span>Choose services & keywords</h2>
      <div class="kw-grid"><div class="stack">${servicesCard(project.id, services)}${areasCard(project.id, project.service_areas || [])}</div>
      ${keywordsCard(project.id, keywords)}</div>`;
    holder.querySelectorAll("details > summary").forEach((s) => { if (open.has(s.textContent)) s.parentElement.open = true; });
  } catch (err) {
    holder.innerHTML = `<div class="card card-body muted">Could not load services & keywords: ${esc(err.message)}</div>`;
  }
}

// Redraws only the keyword + ranking sections; the old content stays until the new one replaces it,
// so the page height (and the scroll position) does not jump.
async function refreshProjectSections(projectId) {
  const project = await api(`/v1/projects/${encodeURIComponent(projectId)}`);
  await Promise.all([renderKeywordSection(project), renderRankingSection(project)]);
}

async function kwAction(fn, okMessage, { full = false } = {}) {
  try {
    const result = await fn();
    if (okMessage) toast(okMessage);
    return result;
  } catch (err) {
    toast(err.message, "bad");
  } finally {
    const [section, id] = location.hash.slice(1).split("/");
    if (section === "projects" && id) {
      if (full) renderProject(id, true);
      else refreshProjectSections(id).catch(() => renderProject(id, true));
    } else route();
  }
}

async function currentAreas(projectId) {
  return (await api(`/v1/projects/${encodeURIComponent(projectId)}`)).service_areas || [];
}


/* ---------- Rankings (Phase 7) ---------- */

function rankCell(rank, prev, extra = "") {
  if (rank == null) return `<span class="muted">not found</span>${extra}`;
  let move = "";
  if (prev !== undefined) {
    if (prev == null) move = ` <span class="pill ok small">new</span>`;
    else if (prev > rank) move = ` <span class="ok-text small">▲${prev - rank}</span>`;
    else if (prev < rank) move = ` <span class="bad-text small">▼${rank - prev}</span>`;
  }
  return `<b>#${esc(rank)}</b>${move}${extra}`;
}

// Keywords whose full results are open (kept open when the page refreshes).
const openResults = new Set();

// Links to check a result by hand: the exact Google address SerpApi opened (location built in)
// and SerpApi's saved copy of the page it saw.
function verifyLinks(block, isPack) {
  if (!block) return "";
  const google = block.google_url
    ? `<a href="${esc(block.google_url)}" target="_blank" rel="noopener noreferrer" title="${block.google_url_exact ? "The exact Google address SerpApi opened, with the same location" : "The same search and location, rebuilt from the saved settings"}">Open on Google ↗</a>` : "";
  const copy = block.snapshot_url
    ? `<a href="${esc(block.snapshot_url)}" target="_blank" rel="noopener noreferrer" title="The page exactly as SerpApi received it">SerpApi's copy ↗</a>` : "";
  const hint = isPack ? `<span class="muted" title="The app checks as a phone: press F12, then Ctrl+Shift+M for phone view">phone view: F12 → Ctrl+Shift+M</span>` : "";
  return google || copy ? `<div class="verify-links">${[google, copy, hint].filter(Boolean).join(" · ")}</div>` : "";
}

function resultsList(title, block, isPack) {
  if (!block) return `<div><div class="label">${title}</div><p class="muted small">Not checked in this run.</p></div>`;
  if (block.status !== "succeeded") return `<div><div class="label">${title}</div><p class="bad-text small">${esc(block.error || "Search failed")}</p></div>`;
  if (isPack && block.shown === false) return `<div><div class="label">${title}</div>${verifyLinks(block, isPack)}<p class="muted small">Google showed no Local Pack for this search.</p></div>`;
  const items = block.results.map((r) => `<li class="${r.is_client ? "is-client" : ""}">
      <span class="res-rank">#${esc(r.rank)}</span>
      <span class="res-main"><b>${esc(r.business_name)}</b>${r.is_client ? ` <span class="tag">you</span>` : ""}
        <span class="muted small">${[r.category, r.rating != null ? `${r.rating} ★ (${fmtNum(r.review_count)})` : null].filter(Boolean).map(esc).join(" · ")}</span></span>
      ${r.maps_url ? `<a class="small" href="${esc(r.maps_url)}" target="_blank" rel="noopener noreferrer">Maps ↗</a>` : ""}</li>`).join("");
  return `<div><div class="label">${title} · ${block.results.length} result${block.results.length === 1 ? "" : "s"}</div>
    ${verifyLinks(block, isPack)}
    ${items ? `<ol class="res-list">${items}</ol>` : `<p class="muted small">No businesses returned.</p>`}</div>`;
}

async function showKeywordResults(projectId, keywordId, jobId) {
  const row = document.querySelector(`tr[data-kwrow="${CSS.escape(keywordId)}"]`);
  if (!row) return;
  row.nextElementSibling?.classList.contains("kw-detail") && row.nextElementSibling.remove();
  const detail = document.createElement("tr");
  detail.className = "kw-detail";
  detail.innerHTML = `<td colspan="4"><span class="spinner"></span> Loading results…</td>`;
  row.after(detail);
  try {
    const d = await api(`/v1/projects/${encodeURIComponent(projectId)}/rankings/keywords/${encodeURIComponent(keywordId)}?job_id=${encodeURIComponent(jobId)}`);
    detail.innerHTML = `<td colspan="4"><div class="res-grid">
        ${resultsList("Local Pack (map box)", d.local_pack, true)}
        ${resultsList(d.search_scope === "country" ? "Local Finder (“More places”)" : "Local Finder (Google Maps list)", d.local_finder, false)}</div>
      <p class="muted small" style="margin:8px 0 0">📍 ${esc(d.search_from || "")} · checked ${esc(timeAgo(d.checked_at))} · sponsored results are not included</p></td>`;
  } catch (err) {
    detail.innerHTML = `<td colspan="4" class="bad-text small">${esc(err.message)}</td>`;
  }
}

const SEARCH_FROM = {
  city: { label: "City centre", short: "from the city centre", help: "Like a customer in the middle of the city (e.g. downtown Atlanta)" },
  country: { label: "Whole country", short: "from the whole country", help: "Like a customer anywhere in the country, e.g. United States" },
  business: { label: "Business location", short: "from the business location", help: "Like a customer standing at the business's own address" },
  current: { label: "My current location", short: "from your location", help: "Where you are now (your browser asks for permission once)" },
};

// The browser's location for "My current location" (asked once, reused for 10 minutes).
let myPoint = null;
function getMyLocation() {
  if (myPoint && Date.now() - myPoint.at < 600000) return Promise.resolve(myPoint);
  return new Promise((resolve, reject) => {
    if (!navigator.geolocation) return reject(new Error("This browser cannot share its location."));
    navigator.geolocation.getCurrentPosition(
      (p) => resolve((myPoint = { lat: p.coords.latitude, lng: p.coords.longitude, at: Date.now() })),
      (e) => reject(new Error(e.code === 1
        ? "Location access was blocked. Allow it for this site (padlock in the address bar → Location → Allow), then choose this option again."
        : "Your location could not be found right now. Try again, or choose another option.")),
      { enableHighAccuracy: false, timeout: 15000, maximumAge: 600000 },
    );
  });
}

function rankingsCard(projectId, data, est) {
  const latest = data.latest;
  const runBox = `<div class="run-box" id="run-box" hidden>
      <div class="run-mode"><label><input type="radio" name="rmode" value="full" ${est?.mode !== "maps_only" ? "checked" : ""}> Full · Local Pack + Maps (2 per keyword)</label>
        <label><input type="radio" name="rmode" value="maps_only" ${est?.mode === "maps_only" ? "checked" : ""}> Maps only (1 per keyword, Local Pack estimated)</label></div>
      <div class="run-mode"><b class="run-label">Search from</b>
        ${Object.entries(SEARCH_FROM).map(([v, t]) => `<label title="${esc(t.help)}"><input type="radio" name="rscope" value="${v}" ${(est?.scope || "city") === v ? "checked" : ""}> ${esc(t.label)}</label>`).join("")}</div>
      <p class="run-cost" id="run-cost"></p>
      <div class="toolbar"><button class="btn primary sm" data-action="rank-confirm" data-project="${esc(projectId)}">Run now</button>
        <button class="btn ghost sm" data-action="rank-cancel">Cancel</button></div></div>`;
  const live = data.live;
  const title = `<h2 class="section-title"><span class="step-no">3</span>Check rankings</h2>`;
  const runBtn = live
    ? `<button class="btn sm" disabled><span class="spinner"></span>Checking…</button>`
    : data.limit
      ? `<button class="btn sm" disabled title="${esc(data.limit)}">Limit reached</button>`
      : `<button class="btn ${latest ? "" : "primary"} sm" data-action="rank-run" data-project="${esc(projectId)}">Run ranking check</button>`;
  const head = `<div class="card-head"><div><h2>Rankings</h2>
      <p class="muted">${latest ? `Last checked ${esc(timeAgo(latest.checked_at))} · ${latest.mode === "maps_only" ? "Maps only (Local Pack estimated)" : "Local Pack + Local Finder"} · <b>${esc(SEARCH_FROM[latest.summary.search_from]?.short || "from the area's saved point")}</b>` : "Where the business appears on Google for its active keywords."}</p></div>
      ${runBtn}</div>`;
  // One plain message when a limit is reached: no spinner, no re-checking.
  const notices = (data.limit && !live ? `<div class="notice bad"><b>Limit reached.</b> ${esc(data.limit)}</div>` : "")
    + (data.failed && !live && !data.limit ? `<div class="notice bad"><b>Last check failed</b> (${esc(timeAgo(data.failed.at))}): ${esc(data.failed.error || "")}</div>` : "")
    + (latest?.note && !live ? `<div class="notice warn">${esc(latest.note)}</div>` : "");
  const body = (inner) => `${title}<div class="card">${head}<div class="card-body">${notices}${live ? liveCheck(live) : runBox}${inner}</div></div>`;
  if (!latest) {
    return body(live ? "" : `<p class="muted">No ranking check yet. Each check uses SerpApi searches only for keywords that are <b>On</b> in step 2 above.</p>`);
  }
  const sm = latest.summary;
  const changeNote = sm.compared_keywords != null && sm.compared_keywords < sm.keywords
    ? `<div class="muted small">change over the ${esc(sm.compared_keywords)} keywords checked both times</div>` : "";
  const change = sm.visibility_change == null ? "" : sm.visibility_change > 0 ? `<span class="pill ok">▲ ${sm.visibility_change}</span>`
    : sm.visibility_change < 0 ? `<span class="pill bad">▼ ${Math.abs(sm.visibility_change)}</span>` : `<span class="pill idle">no change</span>`;
  const rows = latest.keywords.map((k) => `<tr data-kwrow="${esc(k.keyword_id)}">
      <td><b>${esc(k.keyword)}</b> <button class="link-btn" data-action="kw-results" data-project="${esc(projectId)}" data-kw="${esc(k.keyword_id)}" data-job="${esc(latest.job_id)}" title="Every business found: Local Pack top 3 and Local Finder top 20">${openResults.has(k.keyword_id) ? "Top 20 ▾" : "Top 20 ▸"}</button>
        <div class="muted small">${esc(k.location_name || "")}</div>
        <div class="muted small" title="Where Google was told the searcher is">📍 searched from ${esc(k.search_scope ? k.search_from : "the area's saved point (older check)")}</div></td>
      <td>${k.local_pack_shown === false ? `<span class="muted">no Local Pack shown</span>` : rankCell(k.local_pack_rank, k.previous_local_pack_rank, k.local_pack_estimated ? ` <span class="tag">est.</span>` : "")}</td>
      <td>${k.local_finder_checked === false ? `<span class="muted">not checked (limit)</span>` : rankCell(k.local_finder_rank, k.previous_local_finder_rank)}</td>
      <td class="num">${esc(k.visibility)}</td></tr>`).join("");
  const history = data.history.length > 1
    ? `<div class="history">${data.history.slice().reverse().map((h) => `<span title="${esc(fullTime(h.checked_at))} · ${esc(SEARCH_FROM[h.search_from]?.short || "from the area's saved point")}"><i style="height:${Math.max(4, h.visibility_score)}%"></i><small>${esc(Math.round(h.visibility_score))}</small></span>`).join("")}</div>` : "";
  const top = data.top_businesses.map((b) => `<li class="${b.is_client ? "is-client" : ""}"><span>${esc(b.business_name)}${b.is_client ? ` <span class="tag">you</span>` : ""}</span>
      <span class="muted small">${esc(b.appearances)}× · best #${esc(b.best_rank)}${b.local_pack ? ` · ${esc(b.local_pack)} Local Pack` : ""}</span></li>`).join("");
  return body(`${live ? `<div class="label" style="margin-top:6px">Previous check</div>` : ""}
    <div class="vis-row">
      <div class="vis-score"><div class="label">Visibility score</div><div class="big">${esc(sm.visibility_score)}<small>/100</small></div>${change}${changeNote}</div>
      <div class="vis-stats">
        <div><b>${sm.in_local_pack}</b>/${sm.keywords}<span>in Local Pack</span></div>
        <div><b>${sm.in_local_finder}</b>/${sm.keywords}<span>in Local Finder</span></div>
        <div><b>${sm.top_3}</b><span>top 3</span></div>
        <div><b>${sm.top_10}</b><span>top 10</span></div>
        <div><b>${sm.not_found}</b><span>not found</span></div>
        <div><b>${sm.average_local_pack_rank ?? "—"}</b><span>avg Pack rank</span></div>
        <div><b>${sm.average_local_finder_rank ?? "—"}</b><span>avg Finder rank</span></div>
      </div>${history}
    </div>
    <div class="rank-grid">
      <div class="table-wrap"><table class="rank-table"><thead><tr><th>Keyword</th><th>Local Pack</th><th>Local Finder</th><th class="num">Score</th></tr></thead><tbody>${rows}</tbody></table></div>
      <div><div class="label">Businesses appearing most</div><ul class="top-biz">${top}</ul>
        <p class="muted small">Full comparison and gaps in step 4 below.</p></div>
    </div>`);
}

// The check running now: one row per keyword, filled in as each search finishes (Local Pack first).
function liveCheck(live) {
  const total = live.keywords_total || 1;
  const pct = Math.round((live.keywords_done / total) * 100);
  const halfDone = live.keywords.some((k) => !k.pending && k.local_finder_checked === false);
  let current = live.status === "running" && live.step === "collect_rankings" && !halfDone;
  const rows = live.keywords.map((k) => {
    if (k.pending) {
      const now = current;
      current = false;  // only the first pending keyword is being searched
      return `<tr class="pending"><td><b>${esc(k.keyword)}</b><div class="muted small">${esc(k.location_name || "")}</div></td>
        <td colspan="3" class="muted">${now ? `<span class="spinner"></span> searching…` : "waiting"}</td></tr>`;
    }
    const finder = k.local_finder_checked === false ? `<span class="spinner"></span> searching…` : rankCell(k.local_finder_rank);
    return `<tr><td><b>${esc(k.keyword)}</b><div class="muted small">${esc(k.location_name || "")}</div></td>
      <td>${k.local_pack_shown === false ? `<span class="muted">no Local Pack shown</span>` : rankCell(k.local_pack_rank, undefined, k.local_pack_estimated ? ` <span class="tag">est.</span>` : "")}</td>
      <td>${finder}</td><td class="num">${esc(k.visibility)}</td></tr>`;
  }).join("");
  const stage = live.status === "queued" ? "Waiting for the worker…"
    : live.step === "check_budget" ? "Checking credits…"
    : live.step === "collect_rankings" ? `${live.keywords_done} of ${live.keywords_total} keywords done`
    : "Calculating the score…";
  return `<div class="live-check">
    <div class="live-head"><b>Live check</b><span class="muted small">${esc(stage)}</span></div>
    <div class="live-bar"><span style="width:${pct}%"></span></div>
    <div class="table-wrap"><table class="rank-table"><thead><tr><th>Keyword</th><th>Local Pack</th><th>Local Finder</th><th class="num">Score</th></tr></thead><tbody>${rows}</tbody></table></div>
    <p class="muted small" style="margin:6px 0 0">Searching ${esc(SEARCH_FROM[live.search_from]?.short || "")}. Results appear keyword by keyword. If a limit is reached, the keywords done so far are kept.</p></div>`;
}

async function renderRankingSection(project) {
  const holder = document.getElementById("rank-section");
  if (!holder || !project.place_id) return;
  try {
    const data = await api(`/v1/projects/${encodeURIComponent(project.id)}/rankings`);
    // Keep the cost box exactly as it was on a refresh (no new "Checking credits…" each time).
    const box = holder.querySelector("#run-box");
    const kept = box && !box.hidden ? {
      cost: holder.querySelector("#run-cost")?.innerHTML || "",
      blocked: holder.querySelector('[data-action="rank-confirm"]')?.disabled,
      mode: box.querySelector('input[name="rmode"]:checked')?.value,
      scope: box.querySelector('input[name="rscope"]:checked')?.value,
    } : null;
    holder.innerHTML = rankingsCard(project.id, data, { mode: kept?.mode || data.latest?.mode, scope: kept?.scope || data.search_from });
    if (data.latest) {  // re-open the keyword lists that were open before the refresh
      openResults.forEach((kid) => showKeywordResults(project.id, kid, data.latest.job_id));
    }
    const fresh = holder.querySelector("#run-box");
    if (kept && fresh && !data.live) {
      fresh.hidden = false;
      holder.querySelector("#run-cost").innerHTML = kept.cost;
      holder.querySelector('[data-action="rank-confirm"]').disabled = kept.blocked;
    }
  } catch (err) {
    holder.innerHTML = `<div class="card card-body muted">Could not load rankings: ${esc(err.message)}</div>`;
  }
}

/* ---------- Competitors & gaps (Phase 8) ---------- */

const GAP_LABEL = { ranking: "Ranking", category: "Category", review: "Reviews", service: "Service", review_topic: "Review topic" };
const PRIORITY_CLASS = { high: "bad", medium: "warn", low: "idle" };
const GENERIC_CATEGORY = new Set(["service establishment", "establishment", "point of interest"]);

function compRow(c, you = false) {
  const cats = (c.categories || []).filter((x) => !GENERIC_CATEGORY.has(x.toLowerCase())).slice(0, 4).join(" · ");
  const speed = c.review_velocity_30d != null ? `<div class="muted small">+${esc(c.review_velocity_30d)}/month</div>` : "";
  const link = c.maps_url ? ` <a href="${esc(c.maps_url)}" target="_blank" rel="noopener noreferrer" class="small">Maps ↗</a>` : "";
  return `<tr class="${you ? "is-client" : ""}">
    <td><b>${esc(c.business_name || "—")}</b>${you ? ` <span class="tag">you</span>` : link}
      <div class="muted small">${esc(cats || c.primary_category || "")}</div>
      ${c.website_domain ? `<div class="muted small">${esc(c.website_domain)}</div>` : ""}</td>
    <td class="num">${c.rating != null ? `${esc(c.rating)} ★` : "—"}</td>
    <td class="num">${c.review_count != null ? fmtNum(c.review_count) : "—"}${speed}</td>
    <td class="num">${you ? `${esc(c.local_finder_count)}/${esc(c.keywords)}` : `${Math.round(c.keyword_share * 100)}%`}</td>
    <td class="num">${esc(c.local_pack_count)}</td>
    <td class="num">${you || c.keyword_overlap == null ? "—" : `${Math.round(c.keyword_overlap * 100)}%`}</td></tr>`;
}

function gapItem(g, projectId) {
  const ev = g.evidence || {};
  const who = Array.isArray(ev.competitors) ? ev.competitors : ev.competitors ? Object.keys(ev.competitors) : [];
  const pack = ev.local_pack ? ev.local_pack.map((p) => `#${p.rank} ${p.business_name}`) : [];
  const support = ev.related_client_services?.length ? `<div class="small ok-text">Supported by your services: ${esc(ev.related_client_services.join(", "))}</div>` : "";
  const kwBtn = ev.suggested_keyword
    ? `<button class="btn ghost sm" data-action="gap-keyword" data-project="${esc(projectId)}" data-keyword="${esc(ev.suggested_keyword)}" title="Adds it to step 2 (switched on if under the keyword cap)">Track “${esc(ev.suggested_keyword)}”</button>` : "";
  return `<li class="gap">
    <div class="gap-head"><span class="pill ${PRIORITY_CLASS[g.priority] || "idle"} small">${esc(g.priority)}</span>
      <span class="tag">${esc(GAP_LABEL[g.gap_type] || g.gap_type)}</span><b>${esc(g.title || "")}</b></div>
    <p>${esc(g.recommendation)}</p>${support}
    ${pack.length ? `<div class="muted small">Local Pack: ${esc(pack.join(" · "))}</div>`
      : who.length ? `<div class="muted small">Competitors: ${esc(who.slice(0, 6).join(", "))}${who.length > 6 ? ` +${who.length - 6}` : ""}</div>` : ""}
    ${ev.basis ? `<div class="muted small">${esc(ev.basis)}</div>` : ""}
    ${kwBtn ? `<div class="gap-actions">${kwBtn}</div>` : ""}</li>`;
}

function competitorsSection(projectId, comp, gaps, hasCheck) {
  const title = `<h2 class="section-title"><span class="step-no">4</span>Competitors & gaps</h2>`;
  const btn = (label, primary = false) => `<button class="btn ${primary ? "primary" : "ghost"} sm" data-action="comp-analyze" data-project="${esc(projectId)}" title="Uses the stored ranking results: 0 SerpApi credits">${label}</button>`;
  if (!comp.analysis) {
    return `${title}<div class="card card-body">${hasCheck
      ? `<p class="muted">Competitors come from the businesses in your latest ranking check (no SerpApi credits).</p>${btn("Find competitors · free", true)}`
      : `<p class="muted">Run a ranking check first (step 3): competitors are the businesses that keep appearing for your keywords.</p>`}</div>`;
  }
  const a = comp.analysis;
  const rows = comp.competitors.map((c) => compRow(c)).join("");
  const counts = Object.entries(gaps.counts || {}).filter(([, n]) => n).map(([t, n]) => `<span class="chip">${esc(GAP_LABEL[t] || t)} · ${n}</span>`).join("");
  const sampleNote = comp.competitors.some((c) => c.review_sample_size)
    ? "Review topics are based on Google's public sample of up to 5 reviews per business." : "";
  return `${title}
    <div class="card"><div class="card-head"><div><h2>Competitors</h2>
        <p class="muted">${esc(a.competitors)} found from ${esc(a.keywords ?? "?")} keywords · analysed ${esc(timeAgo(a.analysed_at))} · <span title="${esc(comp.rule)}">rule: ≥20% of keywords or ≥3 Local Packs</span></p></div>
        ${btn("Re-analyse · free")}</div>
      <div class="card-body">${comp.competitors.length
        ? `<div class="table-wrap"><table class="comp-table"><thead><tr><th>Business</th><th class="num">Rating</th><th class="num">Reviews</th><th class="num" title="Share of tracked keywords it appears for">Keywords</th><th class="num" title="Keywords where it is in the Local Pack">Local Pack</th><th class="num" title="Keywords where you both appear">Overlap</th></tr></thead>
          <tbody>${comp.client ? compRow(comp.client, true) : ""}${rows}</tbody></table></div>`
        : `<p class="muted">No business appears often enough to count as a competitor yet. Track more keywords (step 2) and re-check.</p>`}
        ${sampleNote ? `<p class="muted small" style="margin:10px 0 0">${esc(sampleNote)}</p>` : ""}</div></div>
    <div class="card"><div class="card-head"><div><h2>Gaps to review</h2>
        <p class="muted">${esc(gaps.note || "")}</p></div></div>
      <div class="card-body">${counts ? `<div class="chips" style="margin-bottom:12px">${counts}</div>` : ""}
        ${gaps.gaps.length ? `<ul class="gaps">${gaps.gaps.map((g) => gapItem(g, projectId)).join("")}</ul>` : `<p class="muted">No gaps found against these competitors.</p>`}</div></div>`;
}

async function renderCompetitorSection(project) {
  const holder = document.getElementById("comp-section");
  if (!holder || !project.place_id) return;
  const pid = encodeURIComponent(project.id);
  try {
    const [comp, gaps, ranks] = await Promise.all([
      api(`/v1/projects/${pid}/competitors`), api(`/v1/projects/${pid}/gaps`), api(`/v1/projects/${pid}/rankings`),
    ]);
    holder.innerHTML = competitorsSection(project.id, comp, gaps, Boolean(ranks.latest));
  } catch (err) {
    holder.innerHTML = `<div class="card card-body muted">Could not load competitors: ${esc(err.message)}</div>`;
  }
}

async function showFullCost(projectId) {
  const box = document.getElementById("full-box");
  if (!box) return;
  box.hidden = false;
  const withRank = document.getElementById("full-rank").checked;
  box.querySelectorAll('input[name="fmode"]').forEach((r) => { r.disabled = !withRank; });
  const cost = document.getElementById("full-cost");
  const go = box.querySelector('[data-action="full-confirm"]');
  if (!withRank) {
    cost.innerHTML = "No SerpApi searches: competitors &amp; gaps use the last ranking check, if there is one.";
    go.disabled = false;
    return;
  }
  const mode = box.querySelector('input[name="fmode"]:checked')?.value || "full";
  cost.innerHTML = `<span class="spinner"></span> Checking credits…`;
  try {
    const e = await api(`/v1/projects/${encodeURIComponent(projectId)}/rankings/estimate?mode=${mode}`);
    if (!e.active_keywords) {
      cost.innerHTML = `Keywords are created during the audit; the ranking check then uses up to <b>${10 * (mode === "maps_only" ? 1 : 2)}</b> searches · <b>${e.credits_left}</b> left. It is skipped if there are not enough.`;
      go.disabled = false;
      return;
    }
    cost.innerHTML = `The ranking check uses <b>${e.searches_needed}</b> SerpApi search${e.searches_needed === 1 ? "" : "es"} for <b>${e.active_keywords}</b> active keywords`
      + (e.searches_from_cache ? ` (${e.searches_from_cache} reused free)` : "")
      + ` · <b>${e.credits_left}</b> left${e.renews_on ? ` · renews ${esc(e.renews_on)}` : ""}.`
      + (e.enough ? "" : ` <span class="bad-text">Not enough searches left: untick the ranking check or switch keywords off.</span>`);
    go.disabled = !e.enough;
  } catch (err) {
    cost.textContent = err.message;
  }
}

async function showRunCost(projectId) {
  const box = document.getElementById("run-box");
  if (!box) return;
  box.hidden = false;
  const mode = box.querySelector('input[name="rmode"]:checked')?.value || "full";
  const scope = box.querySelector('input[name="rscope"]:checked')?.value || "city";
  const cost = document.getElementById("run-cost");
  const go = box.querySelector('[data-action="rank-confirm"]');
  let where = "";
  if (scope === "current") {
    cost.innerHTML = `<span class="spinner"></span> Getting your location…`;
    try {
      const me = await getMyLocation();
      where = `&lat=${me.lat}&lng=${me.lng}`;
    } catch (err) {
      cost.innerHTML = `<span class="bad-text">${esc(err.message)}</span>`;
      go.disabled = true;
      return;
    }
  }
  cost.innerHTML = `<span class="spinner"></span> Checking credits…`;
  try {
    const e = await api(`/v1/projects/${encodeURIComponent(projectId)}/rankings/estimate?mode=${mode}&search_from=${scope}${where}`);
    if (!e.can_start) {  // nothing can run: say so once, plainly
      cost.innerHTML = `<span class="bad-text"><b>Limit reached.</b> ${esc(e.limit_message || "No SerpApi searches left.")}</span>`;
      go.disabled = true;
      return;
    }
    cost.innerHTML = `This check uses <b>${e.searches_needed}</b> SerpApi search${e.searches_needed === 1 ? "" : "es"} for <b>${e.active_keywords}</b> active keyword${e.active_keywords === 1 ? "" : "s"}`
      + (e.searches_from_cache ? ` (${e.searches_from_cache} reused free from the last ${24} h)` : "")
      + ` · <b>${e.credits_left}</b> left${e.renews_on ? ` · renews ${esc(e.renews_on)}` : ""}.`
      + (e.enough ? "" : ` <span class="warn-text">Only ${e.credits_left} left: it runs keyword by keyword (Local Pack first) and stops at the limit, keeping the keywords done so far.</span>`)
      + (e.search_points?.length ? `<br><span class="muted">📍 Searching from: ${e.search_points.map(esc).join(" · ")}</span>` : "");
    go.disabled = e.active_keywords === 0;
  } catch (err) {
    cost.textContent = err.message;
  }
}

async function startReviewAnalysis(projectId, button) {
  if (button) {
    button.disabled = true;
    button.innerHTML = `<span class="spinner"></span>Analysing…`;
  }
  try {
    await api(`/v1/projects/${encodeURIComponent(projectId)}/reviews/analyze`, { method: "POST" });
    toast("Re-analysing reviews (no API credits used)");
  } catch (err) {
    toast(err.message, "bad");
  }
  route();
}

function reviewCard(r) {
  const initial = esc((r.author_name || "?").trim().charAt(0).toUpperCase());
  const author = r.author_url ? `<a href="${esc(r.author_url)}" target="_blank" rel="noopener noreferrer">${esc(r.author_name || "Google user")}</a>` : esc(r.author_name || "Google user");
  const when = r.relative_publish_time || (r.published_at ? timeAgo(r.published_at) : "");
  return `<article class="review">
    <div class="review-head"><span class="avatar">${initial}</span>
      <div><div class="author">${author}</div><div class="muted small">${stars(r.rating)} ${esc(when)}</div></div>
      <span class="pos">${r.sentiment ? `<span class="pill ${SENTIMENT_CLASS[r.sentiment]}">${esc(r.sentiment)}</span> ` : ""}#${esc(r.position ?? "")}</span></div>
    ${r.review_text ? `<p class="review-text">${esc(r.review_text)}</p>` : `<p class="muted small">Rating only, no text.</p>`}
    ${r.tags?.length ? `<div class="chips tag-chips">${r.tags.map((t) => `<span class="tag-chip ${SENTIMENT_CLASS[t.sentiment] || ""}" title="${esc(THEME_LABEL[t.theme] || t.theme)}${t.sentence ? ` — “${esc(t.sentence)}”` : ""}">${t.theme === "specific_service" ? "🛠 " : ""}${esc(t.tag)}</span>`).join("")}</div>` : ""}
    ${r.owner_reply ? `<div class="owner-reply"><b>Reply from the owner</b><p>${esc(r.owner_reply)}</p></div>` : ""}
    ${r.review_url ? `<div class="small">${extLink(r.review_url, "View on Google")}</div>` : ""}
  </article>`;
}


const NAP_SOURCE = {
  schema: "structured data", microdata: "microdata", tel_link: "phone link", address_tag: "address block",
  page_text: "page text", og_site_name: "site name tag", page_title: "page title",
};
const STATUS_ICON = { match: ["ok", "✓", "Match"], mismatch: ["bad", "✗", "Different"], missing: ["idle", "–", "Missing on one side"] };

function pinDistance(meters, addressMatches = false) {
  if (meters == null) return `<span class="muted">Not available — needs coordinates for both the map pin and the website address</span>`;
  if (meters > 1000 && addressMatches) {
    const far = meters < 1000 ? `${Math.round(meters)} m` : `${(meters / 1000).toFixed(1)} km`;
    return `<span class="pill warn">${esc(far)}</span> <span class="muted small">The addresses match, so the website's coordinates are likely wrong — fix its structured data</span>`;
  }
  const level = meters <= 150 ? "ok" : meters <= 1000 ? "warn" : "bad";
  const text = meters < 10 ? `${meters.toFixed(1)} m` : meters < 1000 ? `${Math.round(meters)} m` : `${(meters / 1000).toFixed(1)} km`;
  const hint = { ok: "between the Google pin and the website location — same place", warn: "between the Google pin and the website location — check the map", bad: "between the Google pin and the website location — pin may be in the wrong place" }[level];
  return `<span class="pill ${level}">${esc(text)}</span> <span class="muted small">${esc(hint)}</span>`;
}

function websiteCard(site, check, location) {
  if (!site) return "";
  const src = (key) => {
    const s = site.nap_sources?.[key];
    if (!s) return "";
    const page = s.url ? s.url.replace(/^https?:\/\/[^/]+/, "") || "/" : "";
    return `<span class="muted small"> · ${esc(NAP_SOURCE[s.source] || s.source)}${page ? ` on ${esc(page)}` : ""}</span>`;
  };
  const GEO_SOURCE = { schema: "from the website's own data", nominatim: "address looked up on OpenStreetMap", google_geocoding: "address looked up with Google Geocoding" };
  const coords = site.latitude != null
    ? `${site.latitude.toFixed(6)}, ${site.longitude.toFixed(6)} <span class="muted small">· ${esc(GEO_SOURCE[site.geocode_source] || site.geocode_source || "")}</span>`
    : `<span class="muted">Not available</span>`;
  const pinCoords = location?.latitude != null
    ? `${location.latitude.toFixed(6)}, ${location.longitude.toFixed(6)} <span class="muted small">· Google Maps pin</span>`
    : `<span class="muted">Not available</span>`;
  const table = check
    ? `<div class="table-wrap nap-table"><table><thead><tr><th></th><th>Google profile</th><th>Website</th><th></th></tr></thead><tbody>
        ${["name", "phone", "address"].map((k) => {
          const r = check[k];
          const [cls, icon, label] = STATUS_ICON[r.status] || STATUS_ICON.missing;
          return `<tr><td><b>${esc(humanize(k))}</b></td><td>${orDash(r.google)}</td><td>${orDash(r.website)}</td>
            <td><span class="pill ${cls}" title="${esc(r.detail || "")}">${icon} ${esc(label)}</span></td></tr>`;
        }).join("")}</tbody></table></div>`
    : "";
  const notes = site.notes?.length ? `<ul class="notes">${site.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>` : "";
  return `<div class="card"><div class="card-head"><div><h2>${check ? "Website vs Google" : "Website"}</h2>
      <p class="muted">What the business's own site says${check ? ", compared with its Google profile" : ""}.</p></div>
      ${extLink(site.url, "Open site")}</div>
    <div class="card-body stack" style="gap:14px">
      ${table || `<dl class="kv wide">
        <dt>Name</dt><dd>${orDash(site.business_name)}${src("name")}</dd>
        <dt>Phone</dt><dd>${orDash(site.phone)}${src("phone")}</dd>
        <dt>Address</dt><dd>${orDash(site.address)}${src("address")}</dd></dl>`}
      <dl class="kv wide">
        ${check ? `<dt>Found on</dt><dd>${["name", "phone", "address"].map((k) => site.nap_sources?.[k] ? `${esc(humanize(k))}${src(k)}` : "").filter(Boolean).join("<br>") || dash}</dd>` : ""}
        ${location ? `<dt>Google pin</dt><dd>${pinCoords}</dd>` : ""}
        <dt>Website location</dt><dd>${coords}</dd>
        ${location ? `<dt>Distance</dt><dd>${pinDistance(location.pin_vs_website_address_distance_meters, check?.address?.status === "match")}</dd>` : ""}
        <dt>Pages read</dt><dd>${esc(site.pages_fetched?.length ?? 0)}${site.rendered_with_browser ? ` <span class="pill info">rendered with browser</span>` : ""}</dd>
      </dl>${notes}
      <p class="muted small" style="margin:0">Read ${esc(timeAgo(site.collected_at))}</p></div></div>`;
}


const MATCH_STATUS = {
  auto_selected: "Found & verified automatically",
  manually_selected: "Chosen by you",
  manual_review_required: "Needs your choice",
  not_found: "Not found on Google",
};

function confidencePill(value) {
  if (value == null) return "";
  const level = value >= 0.85 ? "ok" : value >= 0.6 ? "warn" : "bad";
  return `<span class="pill ${level}" title="Match confidence">${Math.round(value * 100)}% match</span>`;
}

function reasonList(good, bad) {
  const items = [
    ...(good || []).map((r) => `<li class="ok"><span>✓</span>${esc(r)}</li>`),
    ...(bad || []).map((r) => `<li class="bad"><span>✗</span>${esc(r)}</li>`),
  ];
  return items.length ? `<ul class="reasons">${items.join("")}</ul>` : "";
}

function matchCard(project) {
  if (!project.match_status || project.match_status === "manual_review_required") return "";
  const note = project.match_confidence != null && project.match_confidence < 0.85
    ? `<p class="small warn-text">Low confidence — check this is the right Google listing. Differences found are listed below.</p>` : "";
  return `<div class="card"><div class="card-head"><div><h2>Google listing match</h2>
      <p class="muted">${esc(MATCH_STATUS[project.match_status] || humanize(project.match_status))}${project.match_confidence == null ? " · verified after the audit" : ""}</p></div>
      ${confidencePill(project.match_confidence)}</div>
    <div class="card-body">${note}${reasonList(project.match_reasons, project.mismatch_reasons) || `<p class="muted">Run the audit to verify this listing against the website.</p>`}</div></div>`;
}

function chooseCard(project, discovery) {
  const cands = discovery?.candidates || [];
  const why = ((discovery?.mismatch_reasons || []).slice(-1)[0] || "The best match is not certain enough to pick automatically").replace(/\.?$/, ".");
  return `<div class="card choose"><div class="card-head"><div><h2>Choose the right business</h2>
      <p class="muted">${esc(why)} Pick the listing that is this business.</p></div></div>
    <div class="card-body candidates-list">${cands.map((c) => `
      <div class="cand">
        <div class="cand-main">
          <div class="cand-title"><b>${esc(c.business_name)}</b>${c.primary_category ? ` <span class="muted small">· ${esc(c.primary_category)}</span>` : ""} ${confidencePill(c.match_confidence)}</div>
          <div class="muted small">${esc(c.address || "")}${c.phone ? ` · ${esc(c.phone)}` : ""}${c.website_url ? ` · ${esc(c.website_url.replace(/^https?:\/\/(www\.)?/, "").replace(/\/.*$/, ""))}` : ""}</div>
          ${reasonList(c.match_reasons, c.mismatch_reasons)}
        </div>
        <div class="cand-actions">
          ${c.maps_url ? extLink(c.maps_url, "View on Maps") : ""}
          <button class="btn primary sm" data-action="select" data-project="${esc(project.id)}" data-place="${esc(c.place_id)}">This is the business</button>
        </div>
      </div>`).join("") || `<p class="muted">No candidates.</p>`}
      <p class="muted small" style="margin:6px 0 0">None of these? Check the business name and address of the project, then click “Find on Google” again.</p>
    </div></div>`;
}

async function startDiscovery(projectId, button) {
  if (button) {
    button.disabled = true;
    button.innerHTML = `<span class="spinner"></span>Starting…`;
  }
  try {
    await api(`/v1/projects/${encodeURIComponent(projectId)}/discover-business`, {
      method: "POST",
      body: JSON.stringify({ then_audit: true }),
    });
    toast("Finding the business on Google");
  } catch (err) {
    toast(err.message, "bad");
  }
  if (location.hash === `#projects/${projectId}`) route();
  else location.hash = `#projects/${projectId}`;
}

async function selectCandidate(projectId, placeId, button) {
  if (button) {
    button.disabled = true;
    button.innerHTML = `<span class="spinner"></span>Saving…`;
  }
  try {
    await api(`/v1/projects/${encodeURIComponent(projectId)}/discover-business/select`, {
      method: "POST",
      body: JSON.stringify({ place_id: placeId, then_audit: true }),
    });
    toast("Business selected — audit started");
  } catch (err) {
    toast(err.message, "bad");
  }
  route();
}

async function startWebsiteOnly(projectId, button) {
  if (button) {
    button.disabled = true;
    button.innerHTML = `<span class="spinner"></span>Starting…`;
  }
  try {
    await api(`/v1/projects/${encodeURIComponent(projectId)}/website-discovery`, { method: "POST" });
    toast("Reading the website");
  } catch (err) {
    toast(err.message, "bad");
  }
  route();
}

async function startAudit(projectId, button, options = null) {
  if (button) {
    button.disabled = true;
    button.innerHTML = `<span class="spinner"></span>Starting…`;
  }
  try {
    await api(`/v1/projects/${encodeURIComponent(projectId)}/gbp-audit`, {
      method: "POST",
      ...(options ? { body: JSON.stringify(options) } : {}),
    });
    toast(options?.top10_reviews ? "Audit started — fetching the top 10 reviews (2 SerpApi credits)" : "Audit started");
  } catch (err) {
    toast(err.message, "bad");
  }
  if (location.hash === `#projects/${projectId}`) route();
  else location.hash = `#projects/${projectId}`;
}

async function renderProject(id, silent = false) {
  const nav = navCount;
  if (!silent) view.innerHTML = `<a class="crumb" href="#projects">← Projects</a><div class="skeleton" style="height:320px"></div>`;
  let data, usage;
  try {
    [data, usage] = await Promise.all([
      api(`/v1/projects/${encodeURIComponent(id)}/profile`),
      api("/v1/usage").catch(() => []),
    ]);
  } catch (err) {
    if (nav !== navCount) return;
    view.innerHTML = `<a class="crumb" href="#projects">← Projects</a><div class="card empty"><h3>Project not found</h3><p>${esc(err.message)}</p></div>`;
    return;
  }
  if (nav !== navCount) return;

  const { project, profile: g, last_audit: job, reviews, offerings, social_profiles: social, website: site, nap_check: check, location, review_summary: reviewSum } = data;
  const running = job && ACTIVE.has(job.status);
  const title = g?.business_name || project.business_name;
  const linked = Boolean(project.place_id);
  const auditBtn = linked
    ? `<button class="btn ${g ? "" : "primary"}" data-action="audit" data-project="${esc(project.id)}" ${running ? "disabled" : ""}>
        ${running ? `<span class="spinner"></span>Running…` : g ? "Re-run audit" : "Run audit"}</button>`
    : `<button class="btn primary" data-action="discover" data-project="${esc(project.id)}" ${running ? "disabled" : ""}>
        ${running ? `<span class="spinner"></span>Running…` : "Find on Google & audit"}</button>`;
  let discovery = null;
  if (project.match_status === "manual_review_required" && !running) {
    try {
      discovery = await api(`/v1/projects/${encodeURIComponent(project.id)}/discover-business`);
    } catch {}
    if (nav !== navCount) return;
  }
  const pidEnc = encodeURIComponent(project.id);
  const fullBtn = linked
    ? `<button class="btn primary" data-action="full-open" data-project="${esc(project.id)}" ${running ? "disabled" : ""} title="Audit, keywords, rankings, competitors and report in one go">Full audit</button>` : "";
  const reportMenu = g
    ? `<details class="menu"><summary class="btn">Report ▾</summary><div class="menu-list">
        <a href="/v1/projects/${pidEnc}/report?format=html" target="_blank" rel="noopener">Open report</a>
        <a href="/v1/projects/${pidEnc}/report?format=pdf">Download PDF</a>
        <a href="/v1/projects/${pidEnc}/report?format=csv">Download CSV (zip)</a></div></details>` : "";
  const fullBox = linked ? `<div class="card card-body run-box" id="full-box" hidden>
      <b>Full audit</b>
      <p class="muted" style="margin:0">Runs everything in order: listing audit → services &amp; keywords → ranking check → competitors &amp; gaps → report. If one part fails, the rest is kept.</p>
      <label class="run-check"><input type="checkbox" id="full-rank" checked> Include a ranking check (uses SerpApi searches)</label>
      <div class="run-mode"><label><input type="radio" name="fmode" value="full" checked> Full · Local Pack + Maps (2 per keyword)</label>
        <label><input type="radio" name="fmode" value="maps_only"> Maps only (1 per keyword)</label></div>
      <p class="run-cost" id="full-cost"></p>
      <div class="toolbar"><button class="btn primary sm" data-action="full-confirm" data-project="${esc(project.id)}">Start full audit</button>
        <button class="btn ghost sm" data-action="full-cancel">Cancel</button></div></div>` : "";
  const websiteBtn = project.website_url && !running
    ? `<button class="btn ghost" data-action="website" data-project="${esc(project.id)}" title="Only read the website: no Google calls">Read website only</button>` : "";

  const socialList = Object.keys(social || {}).length
    ? `<ul class="links">${Object.entries(social).map(([k, url]) => `<li><span class="platform">${esc(SOCIAL_LABEL[k] || k)}</span>${extLink(url, url.replace(/^https?:\/\/(www\.)?/, ""))}</li>`).join("")}</ul>`
    : `<p class="muted">None found on the website.</p>`;
  const groups = {};
  for (const o of offerings) (groups[o.source] ||= []).push(o);
  const offeringHtml = offerings.length
    ? Object.entries(groups).map(([src, items]) => `<div class="offer-group"><div class="label">${esc(OFFERING_SOURCE[src] || humanize(src))}</div>
        <div class="chips">${items.map((o) => o.source_url && o.source_url.startsWith("http")
          ? `<a class="chip" href="${esc(o.source_url)}" target="_blank" rel="noopener noreferrer" title="Found on ${esc(o.source_url)}">${esc(o.name)}</a>`
          : `<span class="chip">${esc(o.name)}</span>`).join("")}</div></div>`).join("")
    : `<p class="muted">No offerings found yet.</p>`;
  const sideCards = `<div class="card"><div class="card-head"><h2>Social profiles</h2></div><div class="card-body">${socialList}</div></div>
        <div class="card"><div class="card-head"><h2>Offerings</h2></div><div class="card-body">${offeringHtml}</div></div>`;

  let body;
  if (!g && site) {
    body = `<div class="detail-grid"><div class="stack">${websiteCard(site, null, null)}
      <div class="card card-body muted">No Google profile yet. Click “${linked ? "Run audit" : "Find on Google & audit"}” to find it and compare it with the website.</div></div>
      <div class="stack">${sideCards}</div></div>`;
  } else if (!g) {
    body = running || discovery ? "" : `<div class="card empty">
      <svg viewBox="0 0 24 24"><path d="M12 2a7 7 0 0 0-7 7c0 5.25 7 13 7 13s7-7.75 7-13a7 7 0 0 0-7-7zm0 9.5A2.5 2.5 0 1 1 12 6.5a2.5 2.5 0 0 1 0 5z"/></svg>
      <h3>No audit yet</h3><p>Collects the Google profile, the top reviews, and social links + offerings from the website.</p>
      <button class="btn primary" data-action="${linked ? "audit" : "discover"}" data-project="${esc(project.id)}">${linked ? "Run audit" : "Find on Google & audit"}</button></div>`;
  } else {
    const hours = g.opening_hours?.weekday_descriptions?.length
      ? `<ul class="hours">${g.opening_hours.weekday_descriptions.map((d) => {
          const [day, ...rest] = d.split(": ");
          return `<li><span>${esc(day)}</span><span>${esc(rest.join(": "))}</span></li>`;
        }).join("")}</ul>`
      : `<p class="muted">Not listed on Google.</p>`;
    const fromSerp = reviews[0]?.source === "serpapi";
    const reviewNote = !reviews.length ? "No reviews returned."
      : fromSerp ? `Top ${reviews.length} by relevance, with owner replies, from Google Maps via SerpApi.`
      : `${reviews.length} most relevant reviews from Google (free; Google returns at most 5).`;
    const top10Btn = !fromSerp && !running
      ? `<button class="btn ghost sm" data-action="top10" data-project="${esc(project.id)}" title="Re-runs the audit and fetches the top 10 reviews with owner replies via SerpApi">Load top 10 · 2 SerpApi credits</button>` : "";
    const openNow = g.opening_hours?.open_now;

    body = `<div class="detail-grid">
      <div class="stack">
        <div class="card"><div class="card-head"><h2>Business profile</h2>
          ${g.maps_url ? extLink(g.maps_url, "Open in Google Maps") : ""}</div>
          <div class="card-body"><dl class="kv wide">
            <dt>Address</dt><dd>${orDash(g.formatted_address)}</dd>
            <dt>Phone</dt><dd>${g.phone_number ? `<a href="tel:${esc(g.phone_number.replace(/\s/g, ""))}">${esc(g.phone_number)}</a>` : dash}</dd>
            <dt>Website</dt><dd>${g.website_url ? extLink(g.website_url, g.website_url.replace(/^https?:\/\/(www\.)?/, "").replace(/\/$/, "")) : dash}</dd>
            <dt>Category</dt><dd>${orDash(g.primary_category)}</dd>
            <dt>Other types</dt><dd>${chipList(g.secondary_categories)}</dd>
            <dt>Status</dt><dd>${g.business_status ? esc(humanize(g.business_status.toLowerCase())) : dash}</dd>
            <dt>Map pin</dt><dd>${g.map_pin_status === "present" ? "✓ Present" : orDash(g.map_pin_status)}</dd>
            <dt>Plus code</dt><dd>${orDash(g.plus_code)}</dd>
            <dt>Photos</dt><dd>${g.photos_count_available != null ? `${esc(g.photos_count_available)} returned by the API (max 10)` : dash}</dd>
            <dt>Description</dt><dd>${orDash(g.editorial_summary)}</dd>
            <dt>Service options</dt><dd>${chipList(g.service_options)}</dd>
            <dt>Accessibility</dt><dd>${chipList(g.accessibility_attributes)}</dd>
            <dt>Place ID</dt><dd class="mono">${esc(g.place_id)}</dd>
          </dl><p class="muted small" style="margin:12px 0 0">Checked ${esc(timeAgo(g.last_checked_at))} · source: Google Places API</p></div></div>

        ${websiteCard(site, check, location)}

        <div class="card"><div class="card-head"><div><h2>Top reviews</h2><p class="muted">${esc(reviewNote)}</p></div>
          <div class="review-actions">${top10Btn}<span class="muted small">Reviews from Google</span></div></div>
          <div class="card-body">${reviewInsights(reviewSum)}
            ${reviews.length && !running ? `<div class="reanalyse"><button class="btn ghost sm" data-action="reanalyse" data-project="${esc(project.id)}" title="Runs locally on the stored reviews">Re-analyse · free</button></div>` : ""}
            <div class="reviews">${reviews.map(reviewCard).join("") || `<p class="muted">—</p>`}</div></div></div>
      </div>
      <div class="stack">
        <div class="card"><div class="card-head"><h2>Opening hours</h2>
          ${openNow == null ? "" : `<span class="badge ${openNow ? "completed" : "failed"}">${openNow ? "Open now" : "Closed now"}</span>`}</div>
          <div class="card-body">${hours}</div></div>
        ${sideCards}
      </div>
    </div>`;
  }

  // On a silent refresh keep the sections' current content as placeholders and restore the scroll,
  // so the page never shrinks for a moment and jumps to the top.
  const keepY = silent ? window.scrollY : null;
  const oldKw = silent ? document.getElementById("kw-section")?.innerHTML || "" : "";
  const oldRank = silent ? document.getElementById("rank-section")?.innerHTML || "" : "";
  const oldComp = silent ? document.getElementById("comp-section")?.innerHTML || "" : "";
  const steps = linked
    ? `<nav class="stepper" aria-label="Steps">
        <button data-action="jump" data-target="sec-audit"><span class="step-no">1</span>Listing audit</button>
        <button data-action="jump" data-target="kw-section"><span class="step-no">2</span>Services & keywords</button>
        <button data-action="jump" data-target="rank-section"><span class="step-no">3</span>Rankings</button>
        <button data-action="jump" data-target="comp-section"><span class="step-no">4</span>Competitors & gaps</button></nav>` : "";
  view.innerHTML = `
    <a class="crumb" href="#projects">← Projects</a>
    <div class="page-head"><div>
      <h1>${esc(title)}</h1>
      <p class="muted head-meta">${g?.primary_category ? `<span>${esc(g.primary_category)}</span>` : ""}
        ${g?.rating != null ? `<span>${stars(g.rating)} <b>${esc(g.rating)}</b> · ${fmtNum(g.review_count)} reviews</span>` : ""}
        <span>${esc(project.name)}</span></p></div>
      <div class="toolbar">${reportMenu}${websiteBtn}${auditBtn}${fullBtn}
        <button class="icon-btn" data-action="delete-open" title="Delete this project" aria-label="Delete project" ${running ? "disabled" : ""}>🗑</button></div></div>
    <div class="card card-body danger-box" id="delete-box" hidden>
      <b>Delete “${esc(project.name)}”?</b>
      <p class="muted" style="margin:0">This removes the project and everything collected for it: audits, reviews, keywords, rankings, competitors, gaps and its jobs. It cannot be undone. API usage counters are kept.</p>
      <div class="toolbar"><button class="btn danger sm" data-action="delete-confirm" data-project="${esc(project.id)}">Delete project</button>
        <button class="btn ghost sm" data-action="delete-cancel">Cancel</button></div></div>
    ${limitBanner(usage)}
    ${fullBox}
    ${steps}
    <div class="stack">${job?.job_type === "ranking_check" ? "" : auditProgress(job)}${discovery ? chooseCard(project, discovery) : ""}
      <section id="sec-audit" class="stack">${g ? `<h2 class="section-title"><span class="step-no">1</span>Listing audit</h2>` : ""}
        ${matchCard(project)}${body}</section>
      <section id="kw-section">${oldKw}</section>
      <section id="rank-section">${oldRank}</section>
      <section id="comp-section" class="stack">${oldComp}</section></div>`;
  if (keepY != null) window.scrollTo(0, keepY);
  await Promise.all([renderKeywordSection(project), renderRankingSection(project), renderCompetitorSection(project)]);

  if (running) schedule(() => renderProject(id, true), 2000);
}

/* ---------- Jobs ---------- */

async function renderJobs(params) {
  const nav = navCount;
  const status = params.get("status") || "";
  const qs = new URLSearchParams({ limit: "200" });
  if (status) qs.set("status", status);
  const [jobs, projects] = await Promise.all([api(`/v1/jobs?${qs}`), api("/v1/projects?limit=200")]);
  if (nav !== navCount) return;
  const projectMap = Object.fromEntries(projects.map((p) => [p.id, p]));
  const options = ["", "queued", "running", "completed", "partial_success", "failed"]
    .map((s) => `<option value="${s}" ${s === status ? "selected" : ""}>${s ? humanize(s) : "All statuses"}</option>`).join("");

  view.innerHTML = `
    <div class="page-head"><div><h1>Jobs</h1><p class="muted">Background work. Each job runs its steps in order and records the result of each.</p></div>
      <div class="toolbar"><select class="inline" id="statusFilter" aria-label="Filter by status">${options}</select>
        <button class="btn primary" data-action="diagnostic">Run diagnostic</button></div></div>
    <div class="card" style="padding-top:6px">${jobsTable(jobs, projectMap)}</div>`;

  $("#statusFilter").addEventListener("change", (e) => {
    location.hash = e.target.value ? `#jobs?status=${e.target.value}` : "#jobs";
  });
  if (jobs.some((j) => ACTIVE.has(j.status))) schedule(() => renderJobs(params), 2500);
}

function resultBlock(result) {
  const entries = Object.entries(result || {});
  if (!entries.length) return "";
  const simple = entries.every(([, v]) => v === null || typeof v !== "object");
  if (!simple) return `<div class="result"><pre class="json">${esc(JSON.stringify(result, null, 2))}</pre></div>`;
  return `<div class="result"><dl class="kv">${entries.map(([k, v]) =>
    `<dt>${esc(humanize(k))}</dt><dd>${v === null ? `<span class="muted">—</span>` : esc(typeof v === "number" ? fmtNum(v) : v)}</dd>`).join("")}</dl></div>`;
}

async function renderJob(id) {
  const nav = navCount;
  let job;
  try {
    job = await api(`/v1/jobs/${encodeURIComponent(id)}`);
  } catch (err) {
    view.innerHTML = `<a class="crumb" href="#jobs">← Jobs</a><div class="card empty"><h3>Job not found</h3><p>${esc(err.message)}</p></div>`;
    return;
  }
  if (nav !== navCount) return;
  const steps = job.steps || [];
  const timeline = steps.length
    ? `<ol class="timeline">${steps.map((s) => `
        <li class="${esc(s.status)}"><div class="node">${s.status === "running" ? `<span class="spinner"></span>` : STEP_ICON[s.status] ?? ""}</div>
          <div><div class="step-head"><h3>${esc(stepLabel(s.name))}</h3>${badge(s.status)}
            <span class="tag">${s.required ? "required" : "optional"}</span>
            <span class="dur">${s.duration_ms != null ? `${fmtNum(s.duration_ms)} ms` : ""}</span></div>
            ${s.error ? `<div class="result error">${esc(s.error)}</div>` : ""}
            ${resultBlock(s.result)}</div></li>`).join("")}</ol>`
    : `<p class="muted">Waiting for a worker to pick up this job…</p>`;

  const summary = {
    completed: "All steps succeeded.",
    partial_success: "Required steps succeeded, but at least one optional step failed.",
    failed: "A required step failed, so later steps were not run.",
    running: "Running — this page updates automatically.",
    queued: "Waiting in the queue — this page updates automatically.",
  }[job.status] || "";

  view.innerHTML = `
    <a class="crumb" href="#jobs">← Jobs</a>
    <div class="page-head"><div><h1>${esc(humanize(job.job_type))} job ${badge(job.status)}</h1>
      <p class="muted">${esc(summary)}</p></div>
      ${job.job_type === "diagnostic" && !ACTIVE.has(job.status) ? `<button class="btn" data-action="diagnostic">Run again</button>` : ""}</div>
    <div class="stack">
      <div class="card card-body"><div class="meta-row">
        <span>ID <b class="mono">${esc(job.id)}</b></span>
        <span>Created <b title="${esc(fullTime(job.created_at))}">${esc(timeAgo(job.created_at))}</b></span>
        <span>Duration <b>${esc(duration(job.started_at, job.finished_at))}</b></span>
        ${job.project_id ? `<span>Project <b class="mono">${esc(shortId(job.project_id))}</b></span>` : ""}
      </div>${job.error ? `<div class="result error" style="margin-top:12px">${esc(job.error)}</div>` : ""}</div>
      <div class="card"><div class="card-head"><h2>Steps</h2><span class="muted">${steps.filter((s) => s.status === "succeeded").length} / ${steps.length} succeeded</span></div>
        <div class="card-body">${timeline}</div></div>
      <div class="card card-body"><details><summary>Raw job data</summary><pre class="json" style="margin-top:10px">${esc(JSON.stringify(job, null, 2))}</pre></details></div>
    </div>`;

  if (ACTIVE.has(job.status)) schedule(() => renderJob(id), 1200);
}

/* ---------- New project dialog ---------- */

const dialog = $("#projectDialog");
const form = $("#projectForm");

function addAreaRow(area = {}) {
  const row = document.createElement("div");
  row.className = "area-row";
  row.innerHTML = `
    <input data-f="name" placeholder="City, e.g. Manchester, UK" value="${esc(area.name || "")}">
    <input data-f="latitude" type="number" step="any" min="-90" max="90" placeholder="Latitude" value="${esc(area.latitude ?? "")}">
    <input data-f="longitude" type="number" step="any" min="-180" max="180" placeholder="Longitude" value="${esc(area.longitude ?? "")}">
    <button type="button" class="icon-btn" aria-label="Remove area">✕</button>`;
  row.querySelector("button").addEventListener("click", () => row.remove());
  $("#areas").append(row);
}

function openProjectDialog() {
  form.reset();
  form.elements.place_id.value = ""; // reset() does not clear hidden inputs
  delete form.elements.name.dataset.auto;
  $("#quickInput").value = "";
  $("#quickResult").innerHTML = "";
  $("#areas").innerHTML = "";
  addAreaRow();
  $("#formErrors").hidden = true;
  dialog.showModal();
  $("#quickInput").focus();
}

/* Quick fill: one pasted line -> all fields (Google Places, or a local parser as fallback). */

let lookupResult = null;

function setField(name, value) {
  const input = form.elements[name];
  if (value == null || value === "") return;
  input.value = value;
  input.classList.add("autofilled");
  setTimeout(() => input.classList.remove("autofilled"), 1600);
}

function applyCandidate(c, source) {
  const f = form.elements;
  // Only replace the project name if the user has not typed their own.
  if (!f.name.value.trim() || f.name.dataset.auto === "1") {
    setField("name", `${c.business_name} audit`);
    f.name.dataset.auto = "1";
  }
  setField("business_name", c.business_name);
  setField("address", c.address);
  setField("phone", c.phone);
  setField("website_url", c.website_url);
  setField("country", c.country);
  f.place_id.value = source === "google_places" && c.place_id ? c.place_id : "";
  const area = c.service_area || c.city;
  if (area) {
    $("#areas").innerHTML = "";
    addAreaRow({ name: area, latitude: c.latitude, longitude: c.longitude });
  }
}

function renderQuickResult(result, selected = 0) {
  const c = result.candidates[selected];
  const google = result.source === "google_places";
  const banner = google
    ? `<div class="match"><div class="ico">✓</div><div>
        <div>Found on Google: <b>${esc(c.business_name)}</b>${c.primary_category ? ` · ${esc(c.primary_category)}` : ""}</div>
        <div class="links">${c.maps_url ? `<a href="${esc(c.maps_url)}" target="_blank" rel="noopener noreferrer">Open in Google Maps ↗</a>` : ""}
          <span>Fields filled below. Check them, then create.</span>${result.cached ? "<span>(cached, no quota used)</span>" : ""}</div></div></div>`
    : `<div class="match warn"><div class="ico">!</div><div>
        <div>${esc(result.warning || "Filled without Google.")}</div>
        <div class="links"><span>Not linked to a Google profile. Fields were split from your text.</span></div></div></div>`;
  const others = google && result.candidates.length > 1
    ? `<div class="candidates"><p>Not the right business? Pick another match:</p>${result.candidates.map((x, i) => `
        <button type="button" class="candidate ${i === selected ? "selected" : ""}" data-candidate="${i}">
          <span><b>${esc(x.business_name)}</b>${x.primary_category ? ` · ${esc(x.primary_category)}` : ""}</span>
          <small>${esc(x.address || "")}</small></button>`).join("")}</div>`
    : "";
  $("#quickResult").innerHTML = banner + others;
}

async function quickLookup() {
  const query = $("#quickInput").value.trim();
  if (query.length < 3) {
    $("#quickInput").focus();
    return;
  }
  const btn = $("#quickBtn");
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner"></span>Looking up…`;
  try {
    lookupResult = await api("/v1/lookup/business", { method: "POST", body: JSON.stringify({ query }) });
    applyCandidate(lookupResult.candidates[0], lookupResult.source);
    renderQuickResult(lookupResult, 0);
    $("#formErrors").hidden = true;
  } catch (err) {
    $("#quickResult").innerHTML = `<div class="match warn"><div class="ico">!</div><div>${esc(err.message)}</div></div>`;
  } finally {
    btn.disabled = false;
    btn.textContent = "Look up";
  }
}

$("#quickBtn").addEventListener("click", quickLookup);
$("#quickInput").addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault(); // don't submit the project form
    quickLookup();
  }
});
$("#quickResult").addEventListener("click", (e) => {
  const pick = e.target.closest("[data-candidate]");
  if (!pick || !lookupResult) return;
  const i = Number(pick.dataset.candidate);
  applyCandidate(lookupResult.candidates[i], lookupResult.source);
  renderQuickResult(lookupResult, i);
});
form.elements.name.addEventListener("input", () => delete form.elements.name.dataset.auto);

function formPayload() {
  const f = form.elements;
  const val = (name) => f[name].value.trim() || null;
  const areas = [...document.querySelectorAll("#areas .area-row")]
    .map((row) => {
      const get = (k) => row.querySelector(`[data-f="${k}"]`).value.trim();
      const num = (k) => (get(k) === "" ? null : Number(get(k)));
      return { name: get("name"), latitude: num("latitude"), longitude: num("longitude") };
    })
    .filter((a) => a.name);
  return {
    name: val("name"),
    business_name: val("business_name"),
    address: val("address"),
    phone: val("phone"),
    website_url: val("website_url"),
    country: (val("country") || "").toUpperCase(),
    language: val("language") || "en",
    service_areas: areas,
    keywords: f.keywords.value.split(/[\n,]/).map((k) => k.trim()).filter(Boolean),
    place_id: val("place_id"),
  };
}

function showFormErrors(detail) {
  const box = $("#formErrors");
  const items = Array.isArray(detail)
    ? detail.map((d) => (typeof d === "string" ? d : `${humanize((d.loc || []).filter((x) => x !== "body").join(" › "))}: ${d.msg}`))
    : [typeof detail === "string" ? detail : "Something went wrong."];
  box.innerHTML = `<b>Please fix the following:</b><ul>${items.map((i) => `<li>${esc(i)}</li>`).join("")}</ul>`;
  box.hidden = false;
  box.scrollIntoView({ block: "nearest" });
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const payload = formPayload();
  const missing = [];
  if (!payload.name) missing.push("Project name is required");
  if (!payload.business_name) missing.push("Business name is required");
  if (payload.country.length !== 2) missing.push("Country must be a 2-letter code, e.g. GB or US");
  if (missing.length) return showFormErrors(missing);

  const btn = $("#saveProject");
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner"></span>Creating…`;
  try {
    const project = await api("/v1/projects", { method: "POST", body: JSON.stringify(payload) });
    dialog.close();
    toast(`Project “${project.name}” created — starting the audit`);
    if (project.place_id) await startAudit(project.id);
    else await startDiscovery(project.id);
  } catch (err) {
    showFormErrors(err.detail ?? err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Create project";
  }
});

$("#addArea").addEventListener("click", () => addAreaRow());
dialog.addEventListener("click", (e) => {
  if (e.target === dialog || e.target.closest("[data-close]")) dialog.close();
});

/* ---------- routing ---------- */

document.addEventListener("click", (e) => {
  const action = e.target.closest("[data-action]");
  if (action?.dataset.action === "diagnostic") return runDiagnostic(action);
  if (action?.dataset.action === "new-project") return openProjectDialog();
  if (action?.dataset.action === "audit") return startAudit(action.dataset.project, action);
  if (action?.dataset.action === "top10") return startAudit(action.dataset.project, action, { top10_reviews: true });
  if (action?.dataset.action === "website") return startWebsiteOnly(action.dataset.project, action);
  if (action?.dataset.action === "discover") return startDiscovery(action.dataset.project, action);
  if (action?.dataset.action === "reanalyse") return startReviewAnalysis(action.dataset.project, action);
  const pid = action?.dataset.project ? encodeURIComponent(action.dataset.project) : null;
  if (action?.dataset.action === "jump") {
    return document.getElementById(action.dataset.target)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  if (action?.dataset.action === "goto-rank") {
    document.getElementById("rank-section")?.scrollIntoView({ behavior: "smooth", block: "start" });
    return showRunCost(action.dataset.project);
  }
  if (action?.dataset.action === "rank-run") return showRunCost(action.dataset.project);
  if (action?.dataset.action === "rank-cancel") {
    document.getElementById("run-box").hidden = true;
    return;
  }
  if (action?.dataset.action === "rank-confirm") {
    const mode = document.querySelector('#run-box input[name="rmode"]:checked')?.value || "full";
    const scope = document.querySelector('#run-box input[name="rscope"]:checked')?.value || "city";
    const here = scope === "current" && myPoint ? { lat: myPoint.lat, lng: myPoint.lng } : undefined;
    action.disabled = true;
    action.innerHTML = `<span class="spinner"></span>Starting…`;
    return kwAction(() => api(`/v1/projects/${pid}/rankings/run`, { method: "POST", body: JSON.stringify({ mode, search_from: scope, here }) }),
      "Ranking check started", { full: true });  // full redraw shows the job progress
  }
  if (action?.dataset.action === "kw-results") {
    const kid = action.dataset.kw;
    if (openResults.has(kid)) {
      openResults.delete(kid);
      action.textContent = "Top 20 ▸";
      const next = action.closest("tr").nextElementSibling;
      if (next?.classList.contains("kw-detail")) next.remove();
      return;
    }
    openResults.add(kid);
    action.textContent = "Top 20 ▾";
    return showKeywordResults(action.dataset.project, kid, action.dataset.job);
  }
  if (action?.dataset.action === "setting-reset") {
    action.disabled = true;
    return api(`/v1/settings/${encodeURIComponent(action.dataset.key)}`, { method: "DELETE" })
      .then(() => { toast(`${action.dataset.key} is back to the .env value`); renderSettings(); })
      .catch((err) => { toast(err.message, "bad"); action.disabled = false; });
  }
  if (action?.dataset.action === "delete-open") {
    document.getElementById("delete-box").hidden = false;
    return;
  }
  if (action?.dataset.action === "delete-cancel") {
    document.getElementById("delete-box").hidden = true;
    return;
  }
  if (action?.dataset.action === "delete-confirm") {
    action.disabled = true;
    action.innerHTML = `<span class="spinner"></span>Deleting…`;
    return api(`/v1/projects/${pid}`, { method: "DELETE" })
      .then((r) => { toast(`Project “${r.deleted}” deleted`); location.hash = "#projects"; })
      .catch((err) => { toast(err.message, "bad"); action.disabled = false; action.textContent = "Delete project"; });
  }
  if (action?.dataset.action === "full-open") return showFullCost(action.dataset.project);
  if (action?.dataset.action === "full-cancel") {
    document.getElementById("full-box").hidden = true;
    return;
  }
  if (action?.dataset.action === "full-confirm") {
    const box = document.getElementById("full-box");
    const rankingsOn = document.getElementById("full-rank").checked;
    const mode = box.querySelector('input[name="fmode"]:checked')?.value || "full";
    action.disabled = true;
    action.innerHTML = `<span class="spinner"></span>Starting…`;
    return kwAction(() => api(`/v1/projects/${pid}/full-audit`, { method: "POST", body: JSON.stringify({ rankings: rankingsOn, mode }) }),
      "Full audit started", { full: true });
  }
  if (action?.dataset.action === "comp-analyze") {
    action.disabled = true;
    action.innerHTML = `<span class="spinner"></span>Analysing…`;
    return kwAction(() => api(`/v1/projects/${pid}/competitors/analyze`, { method: "POST" }),
      "Finding competitors (no SerpApi credits used)", { full: true });
  }
  if (action?.dataset.action === "gap-keyword") {
    return kwAction(() => api(`/v1/projects/${pid}/keywords`, { method: "POST", body: JSON.stringify({ keyword: action.dataset.keyword }) }),
      "Keyword added to step 2");
  }
  if (action?.dataset.action === "svc-refresh") {
    return kwAction(() => api(`/v1/projects/${pid}/services/refresh`, { method: "POST" }), "Services rebuilt from the latest audit");
  }
  if (action?.dataset.action === "kw-generate") {
    action.disabled = true;
    action.innerHTML = `<span class="spinner"></span>Generating…`;
    return kwAction(() => api(`/v1/projects/${pid}/keywords/generate`, { method: "POST" }), "Keywords generated (no credits used)");
  }
  if (action?.dataset.action === "kw-delete") {
    return kwAction(() => api(`/v1/projects/${pid}/keywords/${encodeURIComponent(action.dataset.id)}`, { method: "DELETE" }));
  }
  if (action?.dataset.action === "area-remove") {
    e.preventDefault();
    return kwAction(async () => {
      const areas = await currentAreas(action.dataset.project);
      areas.splice(Number(action.dataset.index), 1);
      return api(`/v1/projects/${pid}/service-areas`, { method: "PUT", body: JSON.stringify(areas) });
    }, "Area removed — regenerate keywords to update them");
  }
  if (action?.dataset.action === "select") return selectCandidate(action.dataset.project, action.dataset.place, action);
  const row = e.target.closest("[data-href]");
  if (row && !e.target.closest("a, button")) location.hash = row.dataset.href;
});

async function route() {
  clearTimeout(pollTimer);
  navCount += 1;
  const [path, query] = (location.hash.slice(1) || "overview").split("?");
  const [section, id] = path.split("/");
  const params = new URLSearchParams(query || "");

  document.querySelectorAll("[data-nav]").forEach((a) => a.classList.toggle("active", a.dataset.nav === section));
  try {
    if (section === "projects" && id) await renderProject(id);
    else if (section === "projects") await renderProjects();
    else if (section === "jobs" && id) await renderJob(id);
    else if (section === "jobs") await renderJobs(params);
    else if (section === "settings") await renderSettings();
    else await renderOverview();
  } catch (err) {
    view.innerHTML = `<div class="card empty"><h3>Could not load this page</h3><p>${esc(err.message)}</p>
      <button class="btn" onclick="location.reload()">Retry</button></div>`;
  }
  document.title = `${humanize(section || "overview")} · Local SEO Audit`;
}

document.addEventListener("change", (e) => {
  if (e.target.name === "fmode" || e.target.id === "full-rank") {
    const pid1 = location.hash.split("/")[1];
    if (pid1) showFullCost(pid1);
    return;
  }
  if (e.target.name === "rmode" || e.target.name === "rscope") {
    const pid0 = location.hash.split("/")[1];
    if (pid0) showRunCost(pid0);
    return;
  }
  const svc = e.target.closest("[data-svc]");
  const kwBox = e.target.closest("[data-kw]");
  const pid = location.hash.split("/")[1];
  if (svc && pid) {
    kwAction(() => api(`/v1/projects/${encodeURIComponent(pid)}/services/${encodeURIComponent(svc.dataset.svc)}`, {
      method: "PATCH", body: JSON.stringify({ selected: svc.checked }),
    }), svc.checked ? "Core service added — regenerate keywords to use it" : "Core service removed");
  }
  if (kwBox) {
    kwAction(() => api(`/v1/projects/${encodeURIComponent(kwBox.dataset.project)}/keywords/${encodeURIComponent(kwBox.dataset.kw)}`, {
      method: "PATCH", body: JSON.stringify({ active: kwBox.checked }),
    }));
  }
});

document.addEventListener("submit", (e) => {
  const f = e.target.closest("[data-form]");
  if (!f || f.id === "projectForm") return;
  e.preventDefault();
  if (f.dataset.form === "settings-group") return saveSettingsGroup(f);
  const pid = encodeURIComponent(f.dataset.project);
  const value = f.querySelector("input").value.trim();
  if (!value) return;
  if (f.dataset.form === "add-service") {
    kwAction(() => api(`/v1/projects/${pid}/services`, { method: "POST", body: JSON.stringify({ name: value }) }), "Service added");
  } else if (f.dataset.form === "add-keyword") {
    kwAction(() => api(`/v1/projects/${pid}/keywords`, { method: "POST", body: JSON.stringify({ keyword: value }) }), "Keyword added");
  } else if (f.dataset.form === "add-area") {
    kwAction(async () => {
      const areas = await currentAreas(f.dataset.project);
      areas.push({ name: value });
      return api(`/v1/projects/${pid}/service-areas`, { method: "PUT", body: JSON.stringify(areas) });
    }, "Area added — regenerate keywords to use it");
  }
});

window.addEventListener("hashchange", route);
refreshHealth();
setInterval(refreshHealth, 15000);
route();
