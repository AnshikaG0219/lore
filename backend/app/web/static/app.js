"use strict";
// InstaBrain dashboard. All data is rendered with textContent via h() — never innerHTML —
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
  RECEIVED: "Not processed", QUEUED: "Queued", COMPLETED: "Completed",
  NEEDS_MANUAL_INPUT: "Needs input", FAILED: "Failed", STRUCTURING: "Processing",
  VALIDATING: "Processing", TRANSCRIBING: "Processing", EXTRACTING_MEDIA: "Processing",
};
const SOURCE_LABELS = {
  group_message: "Group chats", direct_message: "Direct messages", saved_post: "Saved posts",
  saved_collection: "Saved collections", manual_capture: "Manual capture",
};
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
  const key = sessionStorage.getItem("instabrain-key");
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
  sessionStorage.setItem("instabrain-key", $("#api-key").value.trim());
  $("#login").hidden = true; await boot();
});
$("#logout").addEventListener("click", () => { sessionStorage.removeItem("instabrain-key"); showLogin(); });

// ---------- navigation ----------
function show(view) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.view === view));
  document.querySelectorAll(".view").forEach((v) => (v.hidden = v.id !== `view-${view}`));
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
async function loadOverview() {
  const [f, recent] = await Promise.all([api("/facets"),
    api("/content?processed=true&sort=processed&limit=8")]);
  state.facets = f;
  const s = Object.fromEntries(f.statuses.map((x) => [x.value, x.count]));
  const total = f.statuses.reduce((a, x) => a + x.count, 0);
  const processing = ["QUEUED", "VALIDATING", "EXTRACTING_MEDIA", "TRANSCRIBING", "STRUCTURING"]
    .reduce((a, k) => a + (s[k] || 0), 0);
  const kpi = (n, l, onClick) => h("div", { class: "kpi", onclick: onClick }, h("div", { class: "n" }, n.toLocaleString()), h("div", { class: "l" }, l));
  $("#kpis").replaceChildren(
    kpi(total, "Saved & shared posts", () => filterBy("q", "")),
    kpi(s.COMPLETED || 0, "Processed", () => filterBy("status", "COMPLETED")),
    kpi(s.NEEDS_MANUAL_INPUT || 0, "Need media / input", () => filterBy("status", "NEEDS_MANUAL_INPUT")),
    kpi(processing, "In queue", () => filterBy("status", "QUEUED")),
    kpi(s.FAILED || 0, "Failed", () => filterBy("status", "FAILED")),
    kpi(s.RECEIVED || 0, "Not processed yet", () => filterBy("status", "RECEIVED")));
  bars($("#chart-categories"), f.categories, cat, (v) => filterBy("category", v));
  bars($("#chart-status"), f.statuses, (v) => STATUS_LABELS[v] || v, (v) => filterBy("status", v));
  bars($("#chart-sources"), f.source_types, (v) => SOURCE_LABELS[v] || v);
  $("#collections").replaceChildren(...f.collections.map((c) =>
    h("button", { class: "chip", type: "button", onclick: () => filterBy("collection", c.value) },
      c.value, h("b", {}, c.count))));
  $("#last-import").textContent = f.last_import
    ? `Last import: ${f.last_import.filename} · ${f.last_import.records_found.toLocaleString()} references · ${f.last_import.duplicates_found} duplicates merged · ${fmtDate(f.last_import.completed_at)}`
    : "No imports yet.";
  $("#recent").replaceChildren(...(recent.items.length ? recent.items.map(itemCard)
    : [h("p", { class: "muted" }, "Nothing processed yet. Queue items with POST /api/v1/processing/enqueue.")]));
  fillSelects(f);
}
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
    it.tags?.length ? h("div", { class: "chips" }, it.tags.slice(0, 8).map((t) => h("span", { class: "chip" }, t))) : null);
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
  ? "Stated in the post" : "Inferred by the AI" }, e);
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
      "The caption doesn't say enough about this post. Attach a screenshot or screen recording " +
      "(scripts/add_media.py or POST /content/{id}/media) to extract the rest."),
    d.summary && section("Summary", h("p", {}, d.summary)),
    section("Key takeaways", list(x.key_takeaways)),
    info.places?.length && section("Places", table(["Name", "Type", "Location", "Notes", "Evidence"],
      info.places.map((p) => [p.name, p.place_type, p.location, p.notes, ev(p.evidence)]))),
    info.books?.length && section("Books", table(["Title", "Author", "Why", "Evidence"],
      info.books.map((b) => [b.title, b.author, b.reason, ev(b.evidence)]))),
    info.products?.length && section("Products", table(["Name", "Brand", "Price", "Evidence"],
      info.products.map((p) => [p.name, p.brand, p.price, ev(p.evidence)]))),
    info.films_and_shows?.length && section("Films & shows", table(["Title", "Notes", "Evidence"],
      info.films_and_shows.map((f) => [f.name, f.description, ev(f.evidence)]))),
    info.tools?.length && section("Tools", table(["Name", "Notes", "Evidence"],
      info.tools.map((t) => [t.name, t.description, ev(t.evidence)]))),
    info.recipe && section("Recipe", h("b", {}, info.recipe.name || ""),
      list(info.recipe.ingredients), list(info.recipe.steps)),
    section("Tips from the post", list(info.tips)),
    d.tags?.length && section("Tags", h("div", { class: "chips" }, d.tags.map((t) => h("span", { class: "chip" }, t)))),
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
  if (!sessionStorage.getItem("instabrain-key")) return showLogin();
  try { await loadOverview(); show("overview"); } catch (e) { if (e.message !== "unauthorized") toast(e.message); }
}
boot();
