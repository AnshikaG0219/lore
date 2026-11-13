"use strict";
// Lore dashboard. All data is rendered with textContent via h() — never innerHTML —
// because captions and titles are untrusted text.

const UI_VERSION = "5"; // must match <meta name="lore-ui-version"> in index.html
(function checkVersion() {
  // A cached page combined with a newer script breaks rendering; reload once to resync.
  const pageVersion = document.querySelector('meta[name="lore-ui-version"]')?.content;
  if (pageVersion !== UI_VERSION) {
    if (!sessionStorage.getItem("lore-ui-reloaded")) {
      sessionStorage.setItem("lore-ui-reloaded", "1");
      location.reload();
    }
    throw new Error(`Dashboard files out of sync (page ${pageVersion}, script ${UI_VERSION}). Hard-refresh the page.`);
  }
  sessionStorage.removeItem("lore-ui-reloaded");
})();

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
  // v5 post types
  eat_drink: "Eat & drink", place: "Places to visit", trip: "Trip ideas", property: "Property",
  job: "Jobs & opportunities", recommendations: "Recommendations", how_to: "How-tos & recipes",
  shopping: "Shopping", event: "Events", thought: "Thoughts & quotes", fun: "Just for fun",
  other: "Other",
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
  eat_drink: "🍽️", place: "🏙️", trip: "🗺️", property: "🏠", job: "💼", recommendations: "📚",
  how_to: "🛠️", shopping: "🛍️", event: "📅", thought: "💭", fun: "😂", other: "🗂️",
};
const HIDE_LABELS = { fun: "Just for fun", thought: "Thought / quote", unclear: "No useful information",
  manual: "Hidden by you" };
const FACET_LABELS = {
  "venue.city": "City", "venue.zone": "Delhi zone", "venue.area": "Area", venue_type: "Type of place",
  cuisine: "Cuisine", good_for: "Good for", vibe: "Vibe", price_level: "Price",
  "place.city": "City", "place.zone": "Delhi zone", "place.area": "Area", place_kind: "Kind of place",
  trip_scope: "India or abroad", "trip.state": "State", "trip.country": "Country",
  trip_type: "Trip type", terrain: "Terrain", trek_difficulty: "Trek difficulty", stay: "Stay",
  origin: "Saved or shared",
};
const VALUE_LABELS = { saved: "Saved by you", shared: "Shared with you", india: "India",
  international: "Abroad", work_study: "Work / study", bakery_dessert: "Bakery & desserts",
  street_food: "Street food", north_indian: "North Indian", south_indian: "South Indian",
  multi_cuisine: "Multi-cuisine", live_music: "Live music", hidden_gem: "Hidden gem",
  pet_friendly: "Pet-friendly", late_night: "Late night", road_trip: "Road trip",
  wildlife_nature: "Wildlife & nature", heritage_culture: "Heritage & culture",
  backpacking_solo: "Backpacking / solo", adventure_sports: "Adventure sports",
  city_break: "City break", art_supplies: "Art supplies", temple_religious: "Temple / religious",
  workshop_studio: "Workshop / studio", salon_spa: "Salon / spa", gym_studio: "Gym / studio" };
const pretty = (v) => VALUE_LABELS[v] || CATEGORY_LABELS[v] ||
  String(v).replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
const HELP_REASONS = {
  not_attempted: "Only the caption was available (media not fetched yet)",
  deferred: "Waiting: Instagram asked us to slow down, will retry",
  blocked: "Waiting: Instagram asked us to slow down, will retry",
  unavailable: "Private, deleted or age-restricted on Instagram",
  error: "Instagram wouldn't show it to logged-out visitors",
  no_media: "Post has no downloadable media",
  fetched: "Photo without readable text, or an unclear video",
};
const FOUND_LABELS = { venues: "🍽️ spots", places: "📍 places", books: "📚 books",
  films: "🎬 films", series: "📺 series", music: "🎵 songs", podcasts: "🎙️ podcasts",
  products: "🛍️ products", apps: "📱 apps", courses: "🎓 courses", items: "⭐ picks",
  listing: "🏠 listing", job: "💼 job", trek: "🥾 trek", recipe: "🍳 recipe", steps: "🪜 steps",
  tips: "💡 tips", films_and_shows: "🎬 films & shows", tools: "🧰 tools" };
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
  const target = EXPLORE[view] ? "explore" : view;
  document.querySelectorAll(".view").forEach((v) => (v.hidden = v.id !== `view-${target}`));
  if (EXPLORE[view]) { explore.view = view; explore.filters = []; explore.offset = 0; loadExplore(); }
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
      it.primary_category && h("span", { class: "chip cat" },
        `${CATEGORY_EMOJI[it.primary_category] || ""} ${cat(it.primary_category)}`.trim()),
      it.hidden ? h("span", { class: "badge hidden-badge" }, `Hidden · ${HIDE_LABELS[it.hidden_reason] || it.hidden_reason}`)
        : statusBadge(it.status, it.last_processed_at)),
    (it.gist || it.summary) && h("div", { class: it.gist ? "gist" : "summary" }, it.gist || it.summary),
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
const loc = (l) => l ? [l.place, l.area, l.city, l.state, l.country].filter(Boolean)
  .filter((v, i, a) => a.indexOf(v) === i).join(", ") : "";
const facts = (pairs) => {
  const rows = pairs.filter(([, v]) => v !== null && v !== undefined && v !== "" && !(Array.isArray(v) && !v.length));
  return rows.length ? h("dl", { class: "facts-grid" }, rows.flatMap(([k, v]) =>
    [h("dt", {}, k), h("dd", {}, Array.isArray(v) ? v.map(pretty).join(", ") : String(v))])) : null;
};
function factsFor(x) {
  const out = [];
  const ed = x.eat_drink, pl = x.place, tr = x.trip, pr = x.property, jb = x.job;
  const rec = x.recommendations, how = x.how_to, sh = x.shopping, evt = x.event;
  if (ed?.venues?.length > 0) out.push(section("Spots", table(["Name", "Type", "Where", "Cuisine / dishes", "Notes", "Evidence"],
    ed.venues.map((v) => [v.name, pretty(v.venue_type || "—"), loc(v.location),
      [...(v.cuisines || []).map(pretty), ...(v.dishes || [])].join(", "),
      [v.price_note, v.notes, ...(v.good_for || []).map(pretty), ...(v.vibe || []).map(pretty)].filter(Boolean).join(" · "),
      ev(v.evidence)]))));
  if (pl?.places?.length > 0) out.push(section("Places", table(["Name", "Kind", "Where", "Notes", "Evidence"],
    pl.places.map((p) => [p.name, pretty(p.kind || "—"), loc(p.location), [p.price_note, p.notes].filter(Boolean).join(" · "), ev(p.evidence)]))));
  if (tr) {
    out.push(section("Trip", facts([["Where", (tr.destinations || []).map(loc).filter(Boolean).join("; ")],
      ["Type", tr.trip_types], ["Terrain", tr.terrain], ["Duration", tr.duration], ["Budget", tr.budget],
      ["Best time", tr.best_season], ["How to reach", tr.how_to_reach], ["Stay", tr.stays], ["Permits", tr.permits],
      ["Trek", tr.trek ? [tr.trek.name, tr.trek.difficulty && pretty(tr.trek.difficulty), tr.trek.days && `${tr.trek.days} days`,
        tr.trek.max_altitude_m && `${tr.trek.max_altitude_m} m`, tr.trek.base && `from ${tr.trek.base}`].filter(Boolean).join(" · ") : null]])));
    if (tr.places_to_visit?.length > 0) out.push(section("Stops", table(["Name", "Kind", "Where", "Notes", "Evidence"],
      tr.places_to_visit.map((p) => [p.name, p.kind || "—", loc(p.location), p.notes, ev(p.evidence)]))));
  }
  if (pr) out.push(section("Property", facts([["Deal", pr.deal && pretty(pr.deal)], ["BHK", pr.bhk],
    ["Size", pr.size_sqft && `${pr.size_sqft} sq ft`], ["Price", pr.price], ["Project", pr.project], ["Where", loc(pr.location)]])));
  if (jb) out.push(section("Job", facts([["Role", jb.role], ["Company", jb.company], ["Mode", jb.mode && pretty(jb.mode)],
    ["Where", loc(jb.location)], ["Experience", jb.experience], ["Deadline", jb.deadline], ["Apply", jb.how_to_apply]])));
  if (rec?.items?.length > 0) out.push(section(pretty(rec.kind || "Recommendations"), table(["Title", "By", "Genre", "Why", "Evidence"],
    rec.items.map((i) => [i.title, i.creator, i.genre && pretty(i.genre), i.why, ev(i.evidence)]))));
  if (how) {
    const r = how.recipe;
    if (r) out.push(section(`Recipe${r.name ? `: ${r.name}` : ""}`, facts([["Cuisine", r.cuisine && pretty(r.cuisine)],
      ["Diet", r.diet && pretty(r.diet)], ["Time", r.time], ["Ingredients", r.ingredients]])));
    if (how.workout) out.push(section("Workout", facts([["Body parts", how.workout.body_parts], ["Equipment", how.workout.equipment]])));
    if (how.steps?.length > 0) out.push(section("Steps", h("ol", {}, how.steps.map((t) => h("li", {}, t)))));
    if (how.tools?.length > 0) out.push(section("Tools", h("div", { class: "chips" }, how.tools.map((t) => h("span", { class: "chip" }, t)))));
  }
  if (sh?.products?.length > 0) out.push(section("Products", table(["Name", "Brand", "Price", "Where to buy", "Evidence"],
    sh.products.map((p) => [p.name, p.brand, p.price, p.where_to_buy, ev(p.evidence)]))));
  if (evt && evt.what) out.push(section("Event", facts([["What", evt.what], ["When", evt.date], ["Where", loc(evt.location)], ["Price", evt.price]])));
  return out;
}
async function openDetail(id) {
  const d = await api(`/content/${id}`);
  const x = d.extraction || {};
  const info = x.extracted_information || {}; // legacy shape
  const list = (arr) => arr?.length > 0 ? h("ul", {}, arr.map((t) => h("li", {}, t))) : null;
  const legacy = [
    info.places?.length > 0 && section("Places", table(["Name", "Type", "Location", "Notes", "Evidence"],
      info.places.map((p) => [p.name, p.place_type, p.location, p.notes, ev(p.evidence)]))),
    info.books?.length > 0 && section("Books", table(["Title", "Author", "Why", "Evidence"],
      info.books.map((b) => [b.title, b.author, b.reason, ev(b.evidence)]))),
  ];
  const visibilityBtn = d.hidden
    ? h("button", { class: "ghost", type: "button", onclick: () => setVisibility(d.id, "keep") }, "↩︎ Restore")
    : h("button", { class: "ghost", type: "button", onclick: () => setVisibility(d.id, "hide") }, "Hide");
  const body = h("div", {},
    h("h2", {}, d.title || "Untitled (not processed yet)"),
    h("div", { class: "chips" },
      d.hidden ? h("span", { class: "badge hidden-badge" }, `Hidden · ${HIDE_LABELS[d.hidden_reason] || d.hidden_reason}`)
        : statusBadge(d.status, d.last_processed_at),
      d.primary_category && h("span", { class: "chip cat" }, `${CATEGORY_EMOJI[d.primary_category] || ""} ${cat(d.primary_category)}`.trim()),
      x.subtopic && h("span", { class: "chip" }, x.subtopic),
      x.information_sufficient === false && h("span", { class: "badge ev-inferred" }, "low confidence")),
    h("div", { class: "actions" },
      h("a", { href: d.url, target: "_blank", rel: "noopener noreferrer" }, h("button", { type: "button" }, "Open on Instagram ↗")),
      visibilityBtn,
      h("button", { class: "ghost", type: "button", onclick: () => reprocess(d.id) }, "Reprocess")),
    x.gist ? h("p", { class: "gist" }, x.gist) : null,
    ...factsFor(x), ...legacy,
    section("Key points", list(x.key_takeaways)),
    section("Tips from the post", list(x.tips || info.tips)),
    h("details", { class: "source" }, h("summary", {}, "Show source (summary, caption, transcript, where it came from)"),
      d.summary && section("AI summary", h("p", {}, d.summary)),
      d.caption && section("Original caption", h("div", { class: "caption" }, d.caption)),
      d.media.on_screen_text && section("On-screen text (OCR)", h("div", { class: "caption" }, d.media.on_screen_text)),
      d.media.transcript && section("Spoken transcript", h("div", { class: "caption" }, d.media.transcript)),
      d.tags?.length > 0 && section("Tags", h("div", { class: "chips" }, d.tags.map((t) => h("span", { class: "chip" }, `#${t}`)))),
      section("Where you saved / received it", table(["Source", "Name", "Sender", "When"],
        d.sources.map((s) => [SOURCE_LABELS[s.type] || s.type, s.name, s.sender, fmtDate(s.captured_at)]))),
      d.job && section("Processing", table(["Status", "Stage", "Attempts", "Finished", "Error"],
        [[d.job.status, d.job.stage, `${d.job.attempts}/${d.job.max_attempts}`, fmtDate(d.job.completed_at), d.job.error]])),
      d.extraction_meta && h("p", { class: "muted small" },
        `Extracted from ${d.extraction_meta.source} by ${d.extraction_meta.model} (${d.extraction_meta.prompt_version}) on ${fmtDate(d.extraction_meta.extracted_at)}`)));
  $("#drawer-body").replaceChildren(body);
  $("#drawer").hidden = false; $("#scrim").hidden = false; $("#drawer-close").focus();
}
async function setVisibility(id, choice) {
  const r = await api(`/content/${id}/visibility`, { method: "POST", body: JSON.stringify({ choice }) });
  toast(r.hidden ? "Hidden. Find it under All posts → Hidden posts." : "Restored to your useful posts.");
  openDetail(id);
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

// ---------- explore (drill-down) ----------
const EXPLORE = {
  eat: { title: "🍽️ Eat & drink", base: ["type=eat_drink"],
    help: "Cafés, restaurants, bars and food spots from your posts. Pick a city, zone or cuisine to narrow down.",
    groups: ["venue.city", "venue.zone", "venue.area", "venue_type", "cuisine", "good_for", "vibe", "price_level", "origin"] },
  spots: { title: "🏙️ Places to visit", base: ["type=place"],
    help: "Shops, markets, bookstores, museums, parks, studios and other non-food places.",
    groups: ["place.city", "place.zone", "place.area", "place_kind", "good_for", "vibe", "origin"] },
  trips: { title: "🗺️ Trip ideas", base: ["type=trip"],
    help: "Destinations, treks and stays. Start with India or abroad, then a state or trip type.",
    groups: ["trip_scope", "trip.state", "trip.country", "trip_type", "terrain", "trek_difficulty", "stay", "origin"] },
};
const explore = { view: null, filters: [], offset: 0 };
async function loadExplore() {
  const cfg = EXPLORE[explore.view];
  const all = [...cfg.base, ...explore.filters];
  const qs = (extra) => { const p = new URLSearchParams(); all.forEach((f) => p.append("facet", f)); extra(p); return p; };
  const [counts, list] = await Promise.all([
    api(`/facet-values?${qs((p) => cfg.groups.forEach((g) => p.append("name", g)))}`),
    api(`/content?${qs((p) => { p.set("limit", PAGE); p.set("offset", explore.offset); p.set("sort", "processed"); })}`),
  ]);
  $("#explore-title").textContent = cfg.title;
  $("#explore-help").textContent = cfg.help;
  $("#explore-crumbs").replaceChildren(...explore.filters.map((f) => {
    const [name, value] = f.split("=");
    return h("button", { class: "chip", type: "button", title: "Remove filter",
      onclick: () => { explore.filters = explore.filters.filter((x) => x !== f); explore.offset = 0; loadExplore(); } },
      `${FACET_LABELS[name] || name}: ${pretty(value)} ✕`);
  }), ...(explore.filters.length ? [] : [h("span", { class: "muted small" }, "No filters yet: choose from the left.")]));
  $("#explore-facets").replaceChildren(...cfg.groups.flatMap((g) => {
    const options = (counts[g] || []).filter((o) => !explore.filters.includes(`${g}=${o.value}`));
    if (!options.length) return [];
    return [h("div", { class: "facet-group" }, h("h4", {}, FACET_LABELS[g] || g),
      options.slice(0, 12).map((o) => h("button", { class: "facet-opt", type: "button",
        onclick: () => { explore.filters.push(`${g}=${o.value}`); explore.offset = 0; loadExplore(); } },
        h("span", {}, pretty(o.value)), h("span", { class: "c" }, o.count))))];
  }));
  $("#explore-count").textContent = `${list.total.toLocaleString()} post${list.total === 1 ? "" : "s"}`;
  const pages = Math.max(1, Math.ceil(list.total / PAGE));
  $("#explore-page").textContent = `${Math.floor(explore.offset / PAGE) + 1} / ${pages}`;
  $("#explore-prev").disabled = explore.offset === 0;
  $("#explore-next").disabled = explore.offset + PAGE >= list.total;
  $("#explore-list").replaceChildren(...(list.items.length ? list.items.map(itemCard)
    : [h("p", { class: "muted" }, "Nothing here yet. More appears as posts are reclassified.")]));
}
$("#explore-prev").addEventListener("click", () => { explore.offset = Math.max(0, explore.offset - PAGE); loadExplore(); });
$("#explore-next").addEventListener("click", () => { explore.offset += PAGE; loadExplore(); });

// ---------- start ----------
async function boot() {
  if (!sessionStorage.getItem("lore-key")) return showLogin();
  try { await loadOverview(); show("overview"); } catch (e) { if (e.message !== "unauthorized") toast(e.message); }
}
boot();
