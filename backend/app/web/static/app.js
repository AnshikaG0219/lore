"use strict";
// Lore dashboard. All data is rendered with textContent via h() — never innerHTML —
// because captions and titles are untrusted text.

const API = "/api/v1";
const PAGE = 25;
const state = { offset: 0, total: 0, facets: null };
const $ = (sel) => document.querySelector(sel);

const CATEGORY_LABELS = {
  travel: "Travel", food_and_recipes: "Food & Recipes", books_and_reading: "Books & Reading",
  technology_and_ai: "Technology & AI", career_and_learning: "Career & Learning",
  business_and_entrepreneurship: "Business", finance_and_investing: "Finance",
  shopping_and_products: "Shopping & Products", fashion_and_beauty: "Fashion & Beauty",
  health_and_fitness: "Health & Fitness", productivity: "Productivity", lifestyle: "Lifestyle",
  home_and_interior: "Home & Interior", entertainment: "Entertainment", education: "Education",
  miscellaneous: "Miscellaneous",
};
const STATUS_LABELS = {
  RECEIVED: "Not started", QUEUED: "In queue", COMPLETED: "Understood",
  NEEDS_MANUAL_INPUT: "Needs your help", FAILED: "Couldn't process", STRUCTURING: "Reading…",
  VALIDATING: "Reading…", FETCHING: "Downloading…", TRANSCRIBING: "Listening…",
  EXTRACTING_MEDIA: "Reading images…",
};
const SOURCE_LABELS = {
  group_message: "Group chat shares", direct_message: "DM shares",
  saved_post: "Your saved posts", saved_collection: "Your collections",
  manual_capture: "Added manually",
};
const CATEGORY_EMOJI = {
  travel: "✈️", food_and_recipes: "🍜", books_and_reading: "📚", technology_and_ai: "💻",
  career_and_learning: "🎓", business_and_entrepreneurship: "💼", finance_and_investing: "💰",
  shopping_and_products: "🛍️", fashion_and_beauty: "👗", health_and_fitness: "💪",
  productivity: "✅", lifestyle: "🌿", home_and_interior: "🏠", entertainment: "🎬",
  education: "🧠", miscellaneous: "🗂️",
};
const HELP_REASONS = {
  not_attempted: "Only the caption was available (media not fetched yet)",
  deferred: "Waiting: Instagram asked us to slow down, will retry",
  blocked: "Waiting: Instagram asked us to slow down, will retry",
  unavailable: "Private, deleted or age-restricted on Instagram",
  error: "Instagram wouldn't show it to logged-out visitors",
  no_media: "Post has no downloadable media",
  fetched: "Photo without readable text, or an unclear video",
};
const FOUND_LABELS = { places: "📍 places", books: "📚 books", products: "🛍️ products",
  films_and_shows: "🎬 films & shows", tools: "🧰 tools", recipe: "🍳 recipe", tips: "💡 tips" };
const cat = (c) => CATEGORY_LABELS[c] || c || "—";

// ---------- tiny safe DOM helper ----------
function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "href") el.setAttribute("href", safeUrl(v));
    else if (k === "style") el.style.cssText = v; // CSSOM: allowed under our CSP
    else el.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}
function safeUrl(u) {
  try { const p = new URL(u, location.origin);
    return (p.protocol === "https:" && p.hostname.endsWith("instagram.com")) ||
      p.origin === location.origin ? p.href : "#"; } catch { return "#"; }
}
const fmtDate = (s) => (s ? new Date(s).toLocaleDateString(undefined,
  { year: "numeric", month: "short", day: "numeric" }) : "—");
// Items extracted before the processing queue existed have results but no job status yet.
const statusBadge = (s, processedAt) => (s === "RECEIVED" && processedAt
  ? h("span", { class: "badge s-COMPLETED", title: "Extracted outside the queue (early trial)" }, "Extracted")
  : h("span", { class: `badge s-${s}` }, STATUS_LABELS[s] || s));
function toast(msg) { const t = $("#toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), 3000); }

// ---------- API ----------
async function api(path, opts = {}) {
  const key = sessionStorage.getItem("lore-key");
  const res = await fetch(API + path, { ...opts,
    headers: { "X-API-Key": key || "", "Content-Type": "application/json", ...(opts.headers || {}) } });
  if (res.status === 401) { showLogin("That API key was rejected."); throw new Error("unauthorized"); }
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

// ---------- login ----------
function showLogin(msg = "") {
  $("#login").hidden = false; $("#login-error").textContent = msg;
  document.querySelectorAll(".view").forEach((v) => (v.hidden = true));
}
$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  sessionStorage.setItem("lore-key", $("#api-key").value.trim());
  $("#login").hidden = true; await boot();
});
$("#logout").addEventListener("click", () => { sessionStorage.removeItem("lore-key"); showLogin(); });

// ---------- navigation ----------
function show(view) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.view === view));
  document.querySelectorAll(".view").forEach((v) => (v.hidden = v.id !== `view-${view}`));
  if (view === "overview") loadOverview().catch(() => {});
  if (view === "content") loadContent();
  if (view === "places") loadPlaces();
}
document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => show(t.dataset.view)));
function filterBy(field, value) {
  const f = $("#filters"); f.reset(); f.elements[field].value = value;
  state.offset = 0; show("content");
}

// ---------- overview ----------
function bars(el, rows, labelFn, onClick) {
  el.replaceChildren();
  const max = Math.max(1, ...rows.map((r) => r.count));
  for (const r of rows) {
    el.append(h("div", { class: "row", title: `${labelFn(r.value)}: ${r.count}`,
      onclick: onClick ? () => onClick(r.value) : null },
      h("span", { class: "label" }, labelFn(r.value)),
      h("span", { class: "track" }, h("span", { class: "fill", style: `width:${(100 * r.count) / max}%` })),
      h("span", { class: "num" }, r.count)));
  }
  if (!rows.length) el.append(h("p", { class: "muted" }, "Nothing yet."));
}
const progress = { samples: [] };
async function loadOverview() {
  const [f, recent] = await Promise.all([api("/facets"),
    api("/content?processed=true&status=COMPLETED&sort=processed&limit=6")]);
  state.facets = f;
  const s = Object.fromEntries(f.statuses.map((x) => [x.value, x.count]));
  const total = f.statuses.reduce((a, x) => a + x.count, 0);
  const inProgress = ["QUEUED", "VALIDATING", "FETCHING", "EXTRACTING_MEDIA", "TRANSCRIBING", "STRUCTURING"]
    .reduce((a, k) => a + (s[k] || 0), 0);
  const understood = s.COMPLETED || 0, help = s.NEEDS_MANUAL_INPUT || 0;
  const notStarted = s.RECEIVED || 0, failed = s.FAILED || 0;
  const n = (x) => x.toLocaleString();

  // 1. one-sentence summary
  $("#hero-summary").replaceChildren(
    `Lore has found ${n(total)} posts you saved or were sent on Instagram. `,
    h("b", {}, `${n(understood)} are understood`), " and searchable",
    inProgress ? `, ${n(inProgress)} are being read right now` : "",
    help ? `, and ${n(help)} need your help.` : ".");

  // 2. live progress while a run is active
  const jobsActive = (f.jobs.queued || 0) + (f.jobs.running || 0) + (f.jobs.retry_wait || 0);
  $("#progress").hidden = jobsActive === 0;
  if (jobsActive) {
    const done = Object.entries(f.jobs).filter(([k]) => !["queued", "running", "retry_wait"].includes(k))
      .reduce((a, [, v]) => a + v, 0);
    const all = done + jobsActive;
    $("#progress-bar").style.width = `${(100 * done) / Math.max(1, all)}%`;
    progress.samples.push({ t: Date.now(), done });
    progress.samples = progress.samples.filter((x) => Date.now() - x.t < 10 * 60 * 1000);
    const first = progress.samples[0];
    const rate = progress.samples.length > 1 ? (done - first.done) / ((Date.now() - first.t) / 60000) : 0;
    $("#progress-eta").textContent = rate > 0
      ? `about ${fmtDuration(jobsActive / rate)} left · ${rate.toFixed(1)} posts/min` : "measuring speed…";
    $("#progress-text").textContent = `${n(done)} of ${n(all)} posts processed · ${n(jobsActive)} left. ` +
      "This page updates by itself. You can close it; processing continues in the background.";
  }

  // 3. four status cards, each explained
  const kpi = (cls, num, label, why, onClick) => h("div", { class: `kpi ${cls}`, onclick: onClick, tabindex: 0,
    role: "button", title: "Show these posts" },
    h("div", { class: "n" }, n(num)), h("div", { class: "l" }, label), h("div", { class: "why" }, why));
  $("#kpis").replaceChildren(
    kpi("k-ok", understood, "✅ Understood", "Summarised, tagged and searchable", () => filterBy("status", "COMPLETED")),
    kpi("k-info", inProgress, "⏳ In progress", "Being downloaded and read now", () => filterBy("status", "QUEUED")),
    kpi("k-warn", help, "🙋 Needs your help", "Not enough information to understand yet", () => filterBy("status", "NEEDS_MANUAL_INPUT")),
    kpi("k-muted", notStarted + failed, "⏸ Not started", failed ? `${n(failed)} failed, can be retried` : "Not processed yet", () => filterBy("status", notStarted ? "RECEIVED" : "FAILED")));

  // 4. topics
  $("#topics").replaceChildren(...(f.categories.length ? f.categories.map((c) =>
    h("button", { class: "topic", type: "button", onclick: () => filterBy("category", c.value) },
      h("span", { class: "emoji" }, CATEGORY_EMOJI[c.value] || "🗂️"),
      h("span", {}, h("b", {}, cat(c.value)), h("small", {}, `${n(c.count)} post${c.count === 1 ? "" : "s"}`))))
    : [h("p", { class: "muted" }, "Topics appear here once posts are understood.")]));

  // 5. why posts need help
  $("#help-reasons").replaceChildren(...(f.needs_help_reasons.length ? f.needs_help_reasons.map((r) =>
    h("div", { class: "reason" }, h("span", {}, HELP_REASONS[r.value] || r.value), h("span", {}, n(r.count))))
    : [h("p", { class: "muted" }, "Nothing needs your help. 🎉")]));
  $("#help-open").hidden = !help;

  // 6. collections & sources
  $("#collections").replaceChildren(...f.collections.map((c) =>
    h("button", { class: "chip", type: "button", onclick: () => filterBy("collection", c.value) },
      c.value, h("b", {}, c.count))));
  bars($("#chart-sources"), f.source_types, (v) => SOURCE_LABELS[v] || v);
  $("#last-import").textContent = f.last_import
    ? `From your export ${f.last_import.filename} · the same post saved or shared several times is counted once ` +
      `(${n(f.last_import.duplicates_found)} duplicates merged).` : "No imports yet.";

  // 7. recent
  $("#recent").replaceChildren(...(recent.items.length ? recent.items.map(itemCard)
    : [h("p", { class: "muted" }, "Nothing understood yet.")]));
  fillSelects(f);

  clearTimeout(loadOverview._t);
  if (jobsActive && !$("#view-overview").hidden) loadOverview._t = setTimeout(() => loadOverview().catch(() => {}), 20000);
}
function fmtDuration(min) {
  if (min < 1) return "less than a minute";
  if (min < 90) return `${Math.round(min)} min`;
  return `${Math.floor(min / 60)} h ${Math.round(min % 60)} min`;
}
$("#hero-search").addEventListener("submit", (e) => {
  e.preventDefault(); const q = new FormData(e.target).get("q") || "";
  const f = $("#filters"); f.reset(); f.elements.q.value = q; state.offset = 0; show("content");
});
$("#help-open").addEventListener("click", () => filterBy("status", "NEEDS_MANUAL_INPUT"));
function fillSelects(f) {
  const fill = (name, rows, label) => {
    const sel = $("#filters").elements[name]; const keep = sel.value;
    sel.replaceChildren(sel.options[0]);
    rows.forEach((r) => sel.append(h("option", { value: r.value }, `${label(r.value)} (${r.count})`)));
    sel.value = keep;
  };
  if (!fillSelects.done) {
    fill("category", f.categories, cat); fill("status", f.statuses, (v) => STATUS_LABELS[v] || v);
    fill("collection", f.collections, (v) => v); fill("content_type", f.content_types, (v) => v);
    fillSelects.done = true;
  }
}

// ---------- content list ----------
function itemCard(it) {
  return h("div", { class: "item", tabindex: 0, onclick: () => openDetail(it.id),
    onkeydown: (e) => e.key === "Enter" && openDetail(it.id) },
    h("div", { class: "top" },
      h("span", { class: "title" }, it.title || it.caption_preview?.slice(0, 90) || it.url),
      it.primary_category && h("span", { class: "chip cat" }, cat(it.primary_category)),
      statusBadge(it.status, it.last_processed_at)),
    it.summary && h("div", { class: "summary" }, it.summary),
    h("div", { class: "meta" },
      h("span", {}, it.content_type),
      it.creator && h("span", {}, `@${it.creator}`),
      h("span", {}, `saved ${fmtDate(it.first_captured_at)}`),
      it.collections.length ? h("span", {}, `📁 ${it.collections.join(", ")}`) : null,
      it.source_count > 1 ? h("span", {}, `seen ${it.source_count}×`) : null,
      it.confidence != null ? h("span", {}, `confidence ${it.confidence}`) : null),
    foundChips(it.found),
    it.status === "NEEDS_MANUAL_INPUT" ? h("div", { class: "meta" }, "🙋 ",
      HELP_REASONS[it.fetch_status || "not_attempted"] || "Needs more information") : null,
    it.tags?.length ? h("div", { class: "chips" }, it.tags.slice(0, 8).map((t) => h("span", { class: "chip" }, `#${t}`))) : null);
}
function foundChips(found) {
  const entries = Object.entries(found || {});
  return entries.length ? h("div", { class: "found" }, entries.map(([k, v]) =>
    h("span", { class: "chip" }, k === "recipe" ? FOUND_LABELS.recipe : `${v} ${FOUND_LABELS[k] || k}`))) : null;
}
async function loadContent() {
  const params = new URLSearchParams();
  for (const [k, v] of new FormData($("#filters"))) if (v) params.set(k, v);
  params.set("limit", PAGE); params.set("offset", state.offset);
  const data = await api(`/content?${params}`);
  state.total = data.total;
  $("#result-count").textContent = `${data.total.toLocaleString()} item(s)`;
  const pages = Math.max(1, Math.ceil(data.total / PAGE));
  $("#page").textContent = `${Math.floor(state.offset / PAGE) + 1} / ${pages}`;
  $("#prev").disabled = state.offset === 0;
  $("#next").disabled = state.offset + PAGE >= data.total;
  $("#content-list").replaceChildren(...(data.items.length ? data.items.map(itemCard)
    : [h("p", { class: "muted" }, "No matching content.")]));
}
let debounce;
$("#filters").addEventListener("input", () => { clearTimeout(debounce);
  debounce = setTimeout(() => { state.offset = 0; loadContent(); }, 250); });
$("#filters").addEventListener("submit", (e) => e.preventDefault());
$("#prev").addEventListener("click", () => { state.offset = Math.max(0, state.offset - PAGE); loadContent(); });
$("#next").addEventListener("click", () => { state.offset += PAGE; loadContent(); });

// ---------- detail ----------
const ev = (e) => e && h("span", { class: `badge ev-${e}`, title: e === "explicit"
  ? "Written or said in the post" : "Not stated directly; inferred by the AI" },
  e === "explicit" ? "✓ in the post" : "~ AI guess");
function table(headers, rows) {
  return h("table", { class: "table" }, h("thead", {}, h("tr", {}, headers.map((x) => h("th", {}, x)))),
    h("tbody", {}, rows.map((r) => h("tr", {}, r.map((c) => h("td", {}, c ?? "—"))))));
}
function section(title, ...body) {
  const content = body.flat().filter(Boolean);
  return content.length ? h("div", { class: "section" }, h("h4", {}, title), ...content) : null;
}
async function openDetail(id) {
  const d = await api(`/content/${id}`);
  const x = d.extraction || {}; const info = x.extracted_information || {};
  const list = (arr) => arr?.length ? h("ul", {}, arr.map((t) => h("li", {}, t))) : null;
  const body = h("div", {},
    h("h2", {}, d.title || "Untitled (not processed yet)"),
    h("div", { class: "chips" }, statusBadge(d.status, d.last_processed_at),
      d.primary_category && h("span", { class: "chip cat" }, cat(d.primary_category)),
      d.secondary_category && h("span", { class: "chip" }, cat(d.secondary_category)),
      x.subcategory && h("span", { class: "chip" }, x.subcategory),
      d.confidence != null && h("span", { class: "chip" }, `confidence ${d.confidence}`)),
    h("div", { class: "actions" },
      h("a", { href: d.url, target: "_blank", rel: "noopener noreferrer" }, h("button", { type: "button" }, "Open on Instagram ↗")),
      h("button", { class: "ghost", type: "button", onclick: () => reprocess(d.id) }, "Reprocess")),
    d.status === "NEEDS_MANUAL_INPUT" && h("p", { class: "notice" },
      `🙋 ${HELP_REASONS[d.fetch_status || "not_attempted"] || "Not enough information"}. ` +
      "Lore didn't guess. Open it on Instagram; if it matters to you, attach a screenshot or " +
      "screen recording (scripts/add_media.py) and press Reprocess."),
    foundChips(d.found),
    d.summary && section("Summary", h("p", {}, d.summary)),
    section("Key takeaways", list(x.key_takeaways)),
    info.places?.length > 0 && section("Places", table(["Name", "Type", "Location", "Notes", "Evidence"],
      info.places.map((p) => [p.name, p.place_type, p.location, p.notes, ev(p.evidence)]))),
    info.books?.length > 0 && section("Books", table(["Title", "Author", "Why", "Evidence"],
      info.books.map((b) => [b.title, b.author, b.reason, ev(b.evidence)]))),
    info.products?.length > 0 && section("Products", table(["Name", "Brand", "Price", "Evidence"],
      info.products.map((p) => [p.name, p.brand, p.price, ev(p.evidence)]))),
    info.films_and_shows?.length > 0 && section("Films & shows", table(["Title", "Notes", "Evidence"],
      info.films_and_shows.map((f) => [f.name, f.description, ev(f.evidence)]))),
    info.tools?.length > 0 && section("Tools", table(["Name", "Notes", "Evidence"],
      info.tools.map((t) => [t.name, t.description, ev(t.evidence)]))),
    info.recipe && section("Recipe", h("b", {}, info.recipe.name || ""),
      list(info.recipe.ingredients), list(info.recipe.steps)),
    section("Tips from the post", list(info.tips)),
    d.tags?.length > 0 && section("Tags", h("div", { class: "chips" }, d.tags.map((t) => h("span", { class: "chip" }, t)))),
    d.caption && section("Original caption", h("div", { class: "caption" }, d.caption)),
    d.media.on_screen_text && section("On-screen text (OCR)", h("div", { class: "caption" }, d.media.on_screen_text)),
    d.media.transcript && section("Spoken transcript", h("div", { class: "caption" }, d.media.transcript)),
    section("Where you saved / received it", table(["Source", "Name", "Sender", "When"],
      d.sources.map((s) => [SOURCE_LABELS[s.type] || s.type, s.name, s.sender, fmtDate(s.captured_at)]))),
    d.job && section("Processing", table(["Status", "Stage", "Attempts", "Finished", "Error"],
      [[d.job.status, d.job.stage, `${d.job.attempts}/${d.job.max_attempts}`, fmtDate(d.job.completed_at), d.job.error]])),
    d.extraction_meta && h("p", { class: "muted small" },
      `Extracted from ${d.extraction_meta.source} by ${d.extraction_meta.model} ` +
      `(${d.extraction_meta.prompt_version}) on ${fmtDate(d.extraction_meta.extracted_at)}`));
  $("#drawer-body").replaceChildren(body);
  $("#drawer").hidden = false; $("#scrim").hidden = false; $("#drawer-close").focus();
}
function closeDrawer() { $("#drawer").hidden = true; $("#scrim").hidden = true; }
$("#drawer-close").addEventListener("click", closeDrawer);
$("#scrim").addEventListener("click", closeDrawer);
document.addEventListener("keydown", (e) => e.key === "Escape" && closeDrawer());
async function reprocess(id) {
  const r = await api(`/content/${id}/reprocess`, { method: "POST" });
  toast(r.already_active ? "Already queued or running." : "Queued for processing.");
  setTimeout(() => openDetail(id), 800);
}

// ---------- places ----------
async function loadPlaces() {
  const q = $("#places-q").value.trim();
  const rows = await api(`/places${q ? `?q=${encodeURIComponent(q)}` : ""}`);
  $("#places-empty").hidden = rows.length > 0;
  $("#places-body").replaceChildren(...rows.map((p) => h("tr", {},
    h("td", {}, h("b", {}, p.name)), h("td", {}, p.place_type || "—"), h("td", {}, p.location || "—"),
    h("td", {}, ev(p.evidence)),
    h("td", {}, h("a", { href: "#", onclick: (e) => { e.preventDefault(); openDetail(p.content_id); } },
      p.content_title || "post")))));
}
$("#places-q").addEventListener("input", () => { clearTimeout(debounce); debounce = setTimeout(loadPlaces, 250); });

// ---------- start ----------
async function boot() {
  if (!sessionStorage.getItem("lore-key")) return showLogin();
  try { await loadOverview(); show("overview"); } catch (e) { if (e.message !== "unauthorized") toast(e.message); }
}
boot();
