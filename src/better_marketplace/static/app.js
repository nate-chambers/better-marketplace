"use strict";
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const icon = (name) => `<svg aria-hidden="true"><use href="#i-${name}"/></svg>`;
const money = (value) =>
  value == null
    ? "Price not provided"
    : new Intl.NumberFormat("en-US", {
        style: "currency",
        currency: "USD",
        maximumFractionDigits: 0,
      }).format(value);
const number = (value) =>
  value == null ? "Unknown" : Math.round(value).toLocaleString("en-US");
const usageMoney = (value) =>
  value == null
    ? "Unavailable"
    : new Intl.NumberFormat("en-US", {
        style: "currency",
        currency: "USD",
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      }).format(value);
const date = (value) =>
  new Date(value).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
const clone = (value) => JSON.parse(JSON.stringify(value));
const statusLabel = {
  match: "Matches your filters",
  needs_review: "Needs review",
  rejected: "Excluded",
};
let boot,
  state,
  result,
  activeDetail,
  queryController,
  debounce,
  toastTimer,
  polling = false,
  wizard;
let selectedSearch = "",
  didAutoSelect = false;
let accountUsage = null,
  usagePending = null;
const modal = $("#modal"),
  detailDialog = $("#detail-dialog");

async function api(path, { method = "GET", body, signal } = {}) {
  const response = await fetch(`/api${path}`, {
    method,
    signal,
    headers: {
      "Content-Type": "application/json",
      "X-Marketplace-Client": "1",
    },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    const detail = Array.isArray(data.detail)
      ? data.detail
          .map((e) => `${e.loc.slice(1).join(" ")}: ${e.msg}`)
          .join("; ")
      : data.detail;
    throw new Error(detail || `Request failed (${response.status})`);
  }
  return response.json();
}
function toast(message) {
  clearTimeout(toastTimer);
  $("#toast").textContent = message;
  $("#toast").hidden = false;
  toastTimer = setTimeout(() => ($("#toast").hidden = true), 5000);
}
function openModal(title, body) {
  $("#modal-title").textContent = title;
  $("#modal-body").innerHTML = body;
  if (!modal.open) modal.showModal();
}
function modalError(error) {
  let el = $("#modal-error");
  if (!el) {
    el = document.createElement("p");
    el.id = "modal-error";
    el.className = "error-text";
    el.setAttribute("role", "alert");
    $("#modal-body").append(el);
  }
  el.textContent = error.message || error;
}
function persist() {
  try {
    localStorage.setItem(
      "better-marketplace.search.v1",
      JSON.stringify(state.search),
    );
  } catch {}
}
function normalizeSearch(search) {
  const next = clone(search);
  if (!("max_price" in next)) next.max_price = next.max_price_exclusive ?? null;
  delete next.max_price_exclusive;
  delete next.exclude_four_cylinder;
  return next;
}
function filterForm() {
  const s = state.search;
  $("#makes").value = s.makes.join(", ");
  $("#models").value = s.allowed_models.join(", ");
  $("#min-year").value = s.min_year ?? "";
  $("#max-year").value = s.max_year ?? "";
  $("#min-price").value = s.min_price ?? "";
  $("#max-price").value = s.max_price ?? "";
  $("#max-miles").value = s.max_miles_exclusive ?? "";
  $("#transmission").value = s.transmission;
  $("#freshness").value = String(s.stale_after_days);
  $("#location-label").textContent = !s.search_centers.length
    ? "Choose search area"
    : s.region_mode === "any"
      ? "Any listed location"
      : `${s.search_centers[0].name}${s.search_centers.length > 1 ? ` + ${s.search_centers.length - 1}` : ""}`;
  $("#results-title").textContent =
    state.status === "saved" ? "Your saved listings" : "Vehicles for you";
  $("#eyebrow").textContent = `VEHICLES · ${s.makes.join(" / ").toUpperCase() || "ANY MAKE"}`;
  const models = s.allowed_models.length
    ? s.allowed_models.map((m) => m.toUpperCase()).join(" / ")
    : "Any model";
  const chips = [
    `${icon("car")}${esc(s.makes.join(", ") || "Any make")}`,
    esc(models),
  ];
  if (s.min_year != null && s.max_year != null) chips.push(`${s.min_year}–${s.max_year}`);
  else if (s.min_year != null) chips.push(`${s.min_year} or newer`);
  else if (s.max_year != null) chips.push(`${s.max_year} or older`);
  if (s.min_price != null && s.max_price != null) chips.push(`${money(s.min_price)}–${money(s.max_price)}`);
  else if (s.min_price != null) chips.push(`From ${money(s.min_price)}`);
  else if (s.max_price != null) chips.push(`Up to ${money(s.max_price)}`);
  if (s.max_miles_exclusive != null) chips.push(`Under ${number(s.max_miles_exclusive)} miles`);
  $("#active-filters").innerHTML = chips.map((c) => `<span class="filter-chip">${c}</span>`).join("");
}
function searchSummary(s) {
  return [s.makes.join(", ") || "Any make", s.allowed_models.join(", ") || "Any model",
    s.min_year != null ? `From ${s.min_year}` : "",
    s.max_year != null ? `Through ${s.max_year}` : "",
    s.max_price != null ? `Up to ${money(s.max_price)}` : "",
  ].filter(Boolean).join(" · ");
}

function readFilters() {
  if (!$("#filters").checkValidity()) return false;
  const split = (value) =>
    value
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
  const s = {
    ...state.search,
    makes: split($("#makes").value),
    allowed_models: split($("#models").value),
    min_year: $("#min-year").value ? Number($("#min-year").value) : null,
    max_year: $("#max-year").value ? Number($("#max-year").value) : null,
    min_price: $("#min-price").value ? Number($("#min-price").value) : null,
    max_price: $("#max-price").value ? Number($("#max-price").value) : null,
    max_miles_exclusive: $("#max-miles").value ? Number($("#max-miles").value) : null,
    transmission: $("#transmission").value,
    stale_after_days: Number($("#freshness").value),
  };
  state.search = s;
  state.model = $("#model-filter").value;
  state.reason = $("#reason-filter").value;
  state.page = 1;
  persist();
  filterForm();
  return true;
}
function options(select, values, current, first) {
  select.replaceChildren(new Option(first, ""));
  Object.entries(values)
    .sort((a, b) => a[0].localeCompare(b[0]))
    .forEach(([label, count]) =>
      select.add(new Option(`${label} (${count})`, label)),
    );
  if (current && !values[current])
    select.add(new Option(`${current} (0)`, current));
  select.value = current;
}
async function loadListings() {
  queryController?.abort();
  queryController = new AbortController();
  const controller = queryController;
  $("#loading").hidden = false;
  $("#filter-error").hidden = true;
  try {
    const data = await api("/listings/query", {
      method: "POST",
      body: state,
      signal: controller.signal,
    });
    if (controller !== queryController) return;
    result = data;
    if (
      !didAutoSelect &&
      state.status === "match" &&
      !data.counts.match &&
      data.counts.needs_review
    ) {
      didAutoSelect = true;
      state.status = "needs_review";
      return await loadListings();
    }
    didAutoSelect = true;
    render();
  } catch (error) {
    if (error.name !== "AbortError") {
      $("#filter-error").textContent = error.message;
      $("#filter-error").hidden = false;
      toast(error.message);
    }
  } finally {
    if (controller === queryController) $("#loading").hidden = true;
  }
}
function card(item) {
  const price = item.price ?? item.displayed_price;
  return `<article class="listing-card">
    <button class="card-open" data-listing="${esc(item.id)}" aria-label="View ${esc(item.title)}">
      <div class="card-photo">${photo(item)}<span class="status-badge ${item.status}">${icon(item.status === "match" ? "check" : item.status === "needs_review" ? "info" : "close")}${item.status === "match" ? "Match" : item.status === "needs_review" ? "Needs review" : "Excluded"}</span></div>
      <div class="card-copy"><div class="card-price">${money(price)}</div><h3 class="card-title">${esc(item.title || "Untitled vehicle")}</h3><p class="card-location">${esc(item.location)}</p><div class="card-mileage">${item.miles == null ? "Mileage needs review" : `${number(item.miles)} miles`}${item.price == null && price != null ? " · Listed price" : ""}</div>${item.reasons.length ? `<div class="card-note ${item.status}">${esc(item.reason_groups[0] || item.reasons[0])}${item.reason_groups.length > 1 ? ` +${item.reason_groups.length - 1}` : ""}</div>` : ""}</div>
    </button><button class="card-save" data-bookmark="${esc(item.id)}" aria-pressed="${item.saved}" aria-label="${item.saved ? "Unsave" : "Save"} ${esc(item.title)}">${icon("bookmark")}</button>
  </article>`;
}
function fallback() {
  return `<div class="photo-fallback">${icon("car")}<span>Photo unavailable</span></div>`;
}
function photo(item) {
  return item.image_url
    ? `<img src="${esc(item.image_url)}" alt="${esc(item.demo ? "Illustrative vehicle photo" : item.title)}" loading="lazy" referrerpolicy="no-referrer">`
    : fallback();
}
document.addEventListener(
  "error",
  (event) => {
    if (event.target instanceof HTMLImageElement) {
      const img = event.target;
      img.insertAdjacentHTML("afterend", fallback());
      img.remove();
    }
  },
  true,
);
function render() {
  const { items, counts, total, page, page_size } = result;
  $("#grid").innerHTML = items.map(card).join("");
  $("#saved-count").textContent = counts.saved || 0;
  $$("[data-status]").forEach((button) => {
    const key = button.dataset.status;
    button.setAttribute("aria-selected", String(key === state.status));
    $("span", button).textContent = counts[key] || 0;
  });
  $$("[data-nav]").forEach((button) => {
    const active =
      button.dataset.nav === (state.status === "saved" ? "saved" : "browse");
    button.classList.toggle("selected", active);
    button.classList.toggle("active", active);
  });
  $("#results-title").textContent =
    state.status === "saved" ? "Your saved listings" : "Vehicles for you";
  $("#results-description").textContent =
    `${number(total)} ${state.status === "saved" ? "saved " : ""}listing${total === 1 ? "" : "s"} · ${number(counts.all || 0)} collected · Filters checked against listing evidence`;
  options(
    $("#model-filter"),
    result.models,
    state.model,
    "All extracted models",
  );
  options($("#reason-filter"), result.reasons, state.reason, "All reasons");
  $("#status-note").hidden = !(
    state.status === "needs_review" ||
    state.status === "rejected" ||
    items.some((i) => i.demo)
  );
  $("#status-note").classList.toggle(
    "sample-banner",
    items.some((i) => i.demo),
  );
  $("#status-note").innerHTML = items.some((i) => i.demo)
    ? `${icon("info")}Sample listings with illustrative photos. These vehicles are not live offers.`
    : state.status === "needs_review"
      ? `${icon("info")}These listings need a closer look. Open a card for missing details, conflicting values, or an older observation.`
      : `${icon("sliders")}Use More filters → Reason to inspect exclusions. A model mention can appear in a conflicting or parts listing.`;
  $("#empty").hidden = total > 0;
  $("#empty-title").textContent = counts.all
    ? state.status === "match"
      ? "No verified matches yet"
      : "No listings in this view"
    : "Your next car starts here";
  $("#empty-text").textContent = counts.all
    ? "Try Needs review, change your filters, or collect fresh listings. Older observations stay available for comparison."
    : "Collect vehicle listings with Apify, or import a JSON export. Your search criteria decide which vehicles qualify.";
  $("#page-label").textContent = total
    ? `${(page - 1) * page_size + 1}–${Math.min(page * page_size, total)} of ${number(total)} listings`
    : "0 listings";
  $("#prev-page").disabled = page <= 1;
  $("#next-page").disabled = page * page_size >= total;
}
function showDetail(item) {
  activeDetail = item;
  const reasons = item.reasons.length
    ? `<ul class="decision-list">${item.reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>`
    : '<p class="success-text">The listing passes your selected make, model, year, mileage, price, and location rules.</p>';
  $("#detail-content").innerHTML =
    `<div class="detail-layout"><div class="detail-photo"><button class="icon-button detail-back" data-action="close-detail" aria-label="Close listing">${icon("close")}</button>${photo(item)}</div><div class="detail-info">
  <span class="status-badge ${item.status}">${icon(item.status === "match" ? "check" : "info")}${statusLabel[item.status]}</span><h2>${esc(item.title)}</h2><p class="detail-price">${money(item.price ?? item.displayed_price)}</p>${item.price == null && item.displayed_price != null ? '<p class="card-note">Listed price; full asking price needs review.</p>' : ""}<p class="detail-subtitle">${esc(item.location)} · Observed ${date(item.observed_at)}${item.demo ? " · Sample listing" : ""}</p>
  <div class="detail-actions">${item.url ? `<a class="primary-button" href="${esc(item.url)}" target="_blank" rel="noopener noreferrer">Open on Facebook ${icon("link")}</a>` : '<span class="notice-box">Sample listing · no seller link</span>'}<button class="secondary-button" data-bookmark="${esc(item.id)}" aria-pressed="${item.saved}" aria-label="${item.saved ? "Unsave" : "Save"} listing">${icon("bookmark")}${item.saved ? "Saved" : "Save"}</button></div>
  <div class="detail-section"><h3>About this vehicle</h3><dl class="fact-grid">${[
    ["Make", item.make],
    ["Model", item.model?.toUpperCase()],
    ["Year", item.year],
    ["Mileage", item.miles != null ? `${number(item.miles)} miles` : null],
    ["Transmission", item.transmission],
    ["Location precision", item.location_precision],
  ]
    .map(
      ([name, value]) =>
        `<div><dt>${name}</dt><dd>${esc(value || "Needs review")}</dd></div>`,
    )
    .join("")}</dl></div>
  <div class="detail-section"><h3>Filter check</h3>${reasons}${item.url ? '<button class="text-button" data-action="refresh-detail">Refresh this listing</button>' : ""}</div>
  <div class="detail-section"><h3>Description</h3><div class="description">${esc(item.description || "The collector did not return a full description.")}</div></div>
  <details class="detail-section"><summary>Evidence behind the decision</summary>${item.evidence.map((e) => `<div class="evidence-item"><strong>${esc(e.field.replaceAll("_", " "))}: ${esc(typeof e.value === "object" ? JSON.stringify(e.value) : e.value)}</strong><blockquote>${esc(e.quote)}</blockquote><small>Source: ${esc(e.source)}</small></div>`).join("")}<p class="field-help">Observation ${esc(item.observation_id)}</p></details>
  <details class="detail-section"><summary>Listed price history</summary><p class="field-help">Displayed amounts can include payments. Check the description for the full price.</p>${item.price_history.map((h) => `<div class="history-row"><span>${date(h.observed_at)}</span><strong>${h.displayed_price != null && Number.isFinite(Number(h.displayed_price)) ? money(Number(h.displayed_price)) : esc(h.displayed_price || "Unknown")}</strong></div>`).join("")}</details>
  </div></div>`;
  if (!detailDialog.open) detailDialog.showModal();
}
async function bookmark(identifier) {
  const item = result.items.find((i) => i.id === identifier) || activeDetail;
  if (!item) return;
  try {
    await api(`/listings/${encodeURIComponent(identifier)}/bookmark`, {
      method: "PUT",
      body: { saved: !item.saved },
    });
    item.saved = !item.saved;
    toast(
      item.saved ? "Saved to your listings" : "Removed from saved listings",
    );
    if (detailDialog.open && activeDetail?.id === identifier) showDetail(item);
    await loadListings();
  } catch (error) {
    toast(error.message);
  }
}
async function refreshBootstrap() {
  boot = await api("/bootstrap");
  renderSaved();
  renderConnection();
}
function renderSaved() {
  const select = $("#saved-search");
  select.replaceChildren(new Option("Current search", ""));
  boot.searches.forEach((s) => select.add(new Option(s.search.name, s.id)));
  select.value = selectedSearch;
}
function renderConnection() {
  const el = $("#connection");
  el.textContent = boot.token_configured
    ? "Apify connected"
    : "Local mode · Apify not connected";
  el.classList.toggle("connected", boot.token_configured);
  $("#version").textContent = boot.version;
}
function renderUsage() {
  const card = $("#apify-usage"),
    data = accountUsage;
  card.hidden = !boot.token_configured;
  $("#usage-shortcut").hidden = !boot.token_configured;
  if (!boot.token_configured) return;
  $("#usage-shortcut-caption").textContent = data?.stale
    ? "Apify · last known usage / limit"
    : "Apify · used / monthly limit";
  const used = data?.used_usd,
    limit = data?.monthly_limit_usd;
  $("#usage-shortcut-values").textContent = !data
    ? "Checking usage…"
    : used != null && limit != null
      ? `${usageMoney(used)} / ${usageMoney(limit)}`
      : used != null
        ? `${usageMoney(used)} used`
        : "Usage unavailable";
  $("#usage-values").textContent = !data
    ? "Checking usage…"
    : used != null && limit != null
      ? `${usageMoney(used)} / ${usageMoney(limit)}`
      : used != null
        ? `${usageMoney(used)} used`
        : "Usage unavailable";
  const progress = $("#usage-progress");
  progress.hidden = used == null || limit == null || limit <= 0;
  if (!progress.hidden) {
    progress.max = limit;
    progress.value = Math.min(used, limit);
  }
  $("#usage-remaining").textContent =
    data?.remaining_to_limit_usd != null
      ? `${usageMoney(data.remaining_to_limit_usd)} available before limit`
      : "";
  $("#usage-cycle").textContent = data?.cycle_end_at
    ? `Cycle ends ${new Date(data.cycle_end_at).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" })} (UTC)`
    : "";
  $("#usage-message").hidden = !data?.message;
  $("#usage-message").textContent = data?.message || "";
  card.dataset.stale = String(!!data?.stale);
  $("#usage-plan").textContent =
    data?.plan_included_credits_usd != null
      ? `${data.plan_name ? `${data.plan_name} · ` : ""}${usageMoney(data.plan_included_credits_usd)} included per cycle.`
      : "Plan allowance unavailable for this token.";
  const checked = $("#usage-checked");
  checked.dateTime = data?.checked_at || "";
  checked.textContent = data?.checked_at
    ? `${data.stale ? "Last update" : "Updated"} ${new Date(data.checked_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`
    : "";
  const modalUsage = $("#collection-account-usage");
  if (modalUsage) {
    modalUsage.hidden = !data;
    modalUsage.innerHTML =
      data && used != null
        ? `<strong>Apify account usage</strong><p>${usageMoney(used)} used${limit != null ? ` / ${usageMoney(limit)} monthly spending limit` : ""}${data.stale ? " · last known values" : ""}</p>${data.remaining_to_limit_usd != null ? `<p>${usageMoney(data.remaining_to_limit_usd)} available before the limit. This is not a prepaid credit balance.</p>` : ""}<a href="https://console.apify.com/billing" target="_blank" rel="noopener noreferrer">Check credits in Apify billing</a>`
        : `<p>Account usage unavailable. <a href="https://console.apify.com/billing" target="_blank" rel="noopener noreferrer">Check Apify billing</a>.</p>`;
  }
}
async function refreshUsage(force = false) {
  if (!boot?.token_configured) {
    accountUsage = null;
    renderUsage();
    return;
  }
  if (usagePending) return usagePending;
  const button = $("[data-action=refresh-usage]");
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  usagePending = (async () => {
    try {
      accountUsage = await api(`/apify/usage${force ? "?refresh=true" : ""}`);
    } catch {
      accountUsage = {
        ...(accountUsage || {}),
        status: accountUsage?.checked_at ? "stale" : "unavailable",
        stale: !!accountUsage?.checked_at,
        message: "Cannot refresh Apify usage. Try again or open billing.",
      };
    } finally {
      renderUsage();
      button.disabled = false;
      button.setAttribute("aria-busy", "false");
      usagePending = null;
    }
  })();
  return usagePending;
}
function saveSearchModal() {
  openModal(
    "Save your search",
    `<p>Keep your vehicle criteria and search locations together.</p><form id="save-search-form"><label>Search name<input id="search-name" maxlength="80" required value="${esc(state.search.name)}"></label><p class="field-help">${esc(searchSummary(state.search))}</p><div class="modal-actions"><button type="button" class="secondary-button" data-action="close-modal">Cancel</button>${selectedSearch ? '<button class="secondary-button" type="submit" name="mode" value="new">Save as new</button>' : ""}<button class="primary-button" type="submit" name="mode" value="update">${selectedSearch ? "Update search" : "Save search"}</button></div></form>`,
  );
  $("#save-search-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const search = { ...state.search, name: $("#search-name").value.trim() };
      const update = selectedSearch && event.submitter.value === "update";
      const saved = await api(
        update ? `/searches/${selectedSearch}` : "/searches",
        { method: update ? "PUT" : "POST", body: search },
      );
      state.search = search;
      selectedSearch = saved.id;
      await refreshBootstrap();
      persist();
      modal.close();
      toast("Search saved");
    } catch (error) {
      modalError(error);
    }
  });
}
function manageSearches() {
  openModal(
    "Saved searches",
    boot.searches.length
      ? boot.searches
          .map(
            (s) =>
              `<div class="saved-search-row"><div><strong>${esc(s.search.name)}</strong><p>${esc(searchSummary(s.search))}</p></div><button class="secondary-button" data-load-search="${s.id}">Load</button><button class="icon-button small" data-delete-search="${s.id}" aria-label="Delete ${esc(s.search.name)}">${icon("trash")}</button></div>`,
          )
          .join("")
      : "<p>You haven’t saved a search yet. Choose your filters, then use Save search.</p>",
  );
}
function loadSaved(identifier) {
  const saved = boot.searches.find((s) => s.id === identifier);
  if (!saved) return;
  state.search = normalizeSearch(saved.search);
  state.status = "match";
  state.text = "";
  $("#search").value = "";
  didAutoSelect = false;
  state.model = "";
  state.reason = "";
  state.page = 1;
  selectedSearch = identifier;
  filterForm();
  renderSaved();
  persist();
  loadListings();
  modal.close();
}
function locationModal() {
  const centers = new Map(
    [...boot.default_search.search_centers, ...state.search.search_centers].map(
      (c) => [c.marketplace_location, { ...c }],
    ),
  );
  const selected = new Set(
    state.search.search_centers.map((c) => c.marketplace_location),
  );
  openModal(
    "Search location",
    `<p>Search around one or more cities. Radius filters use listing coordinates; city-only locations may need review.</p><label>Where vehicles may qualify<select id="region-mode"><option value="radius">Within selected city radii</option><option value="any">Any listed location</option></select></label><div class="center-list" id="center-list"></div><details class="advanced" open><summary>Add a city ${icon("plus")}</summary><div class="field-row"><label>City name<input id="new-city" placeholder="Los Angeles"></label><label>Marketplace city slug / ID<input id="new-slug" placeholder="la"></label></div><div class="field-row"><label>Latitude<input id="new-lat" type="number" step="any" min="-90" max="90" placeholder="34.0522"></label><label>Longitude<input id="new-lon" type="number" step="any" min="-180" max="180" placeholder="-118.2437"></label></div><p class="field-help">Use the city segment from a Facebook Marketplace URL. Coordinates let us check the distance independently.</p><button class="secondary-button" id="add-center">Add city</button></details><div class="modal-actions"><button class="secondary-button" data-action="close-modal">Cancel</button><button class="primary-button" id="apply-location">Apply locations</button></div>`,
  );
  $("#region-mode").value = state.search.search_centers.length ? state.search.region_mode : "radius";
  const draw = () => {
    $("#center-list").innerHTML = [...centers.values()]
      .map(
        (c) =>
          `<div class="center-row"><label><input type="checkbox" data-center="${esc(c.marketplace_location)}" ${selected.has(c.marketplace_location) ? "checked" : ""}>${esc(c.name)}</label><input type="number" min="1" max="500" value="${c.radius_km}" data-radius="${esc(c.marketplace_location)}" aria-label="Radius for ${esc(c.name)}"><small>km</small></div>`,
      )
      .join("");
  };
  draw();
  $("#center-list").addEventListener("change", (event) => {
    if (event.target.dataset.center) {
      const id = event.target.dataset.center;
      event.target.checked ? selected.add(id) : selected.delete(id);
    }
    if (event.target.dataset.radius)
      centers.get(event.target.dataset.radius).radius_km = Number(
        event.target.value,
      );
  });
  $("#add-center").addEventListener("click", () => {
    const name = $("#new-city").value.trim(),
      slug = $("#new-slug").value.trim(),
      latitude = Number($("#new-lat").value),
      longitude = Number($("#new-lon").value);
    if (
      !name ||
      !/^[A-Za-z0-9_-]+$/.test(slug) ||
      !$("#new-lat").value ||
      !$("#new-lon").value ||
      Math.abs(latitude) > 90 ||
      Math.abs(longitude) > 180
    ) {
      modalError("Enter a city, valid slug, latitude, and longitude.");
      return;
    }
    centers.set(slug, {
      name,
      marketplace_location: slug,
      latitude,
      longitude,
      radius_km: 100,
    });
    selected.add(slug);
    draw();
  });
  $("#apply-location").addEventListener("click", () => {
    const next = [...selected].map((id) => centers.get(id));
    if (
      (!next.length && $("#region-mode").value === "radius") ||
      next.length > 20 ||
      next.some((c) => c.radius_km < 1 || c.radius_km > 500)
    ) {
      modalError("Radius filtering needs 1–20 cities, each with a radius of 1–500 km.");
      return;
    }
    state.search.search_centers = next;
    state.search.region_mode = $("#region-mode").value;
    state.page = 1;
    filterForm();
    persist();
    modal.close();
    loadListings();
  });
}
function collectModal(listingIds = []) {
  wizard = {
    search: clone(state.search),
    budget_usd: Math.min(2, boot.limits.max_run_usd),
    max_jobs: Math.min(8, boot.limits.max_jobs),
    max_pages: 2,
    request_id: crypto.randomUUID(),
    listing_ids: listingIds,
  };
  openModal(
    listingIds.length ? "Refresh listing" : "Collect fresh listings",
    `<p>Fetch full vehicle descriptions from Facebook Marketplace, then check them against your search.</p>${!boot.token_configured ? '<div class="notice-box warning"><strong>Connect Apify to collect listings.</strong><p>Add <code>APIFY_TOKEN</code> to your <code>.env</code> file and restart the server. Your token stays on the server.</p><button class="text-button" data-action="help">View setup instructions</button></div>' : ""}<div class="field-row"><label>Maximum spend ($)<input id="run-budget" type="number" min="0.01" max="${boot.limits.max_run_usd}" step="0.01" value="${wizard.budget_usd}"></label><label>Maximum searches<input id="run-jobs" type="number" min="1" max="${boot.limits.max_jobs}" value="${wizard.max_jobs}"></label></div><label>Pages per search<select id="run-pages"><option value="1">1 page</option><option value="2" selected>2 pages</option><option value="3">3 pages</option><option value="5">5 pages</option></select></label><details class="advanced"><summary>Custom search terms ${icon("down")}</summary><label>One search term per line<textarea id="run-terms" rows="3" placeholder="Leave blank to search each make and model">${esc(state.search.search_terms.join("\n"))}</textarea></label><p class="field-help">These control collection. Your vehicle filters still decide eligibility.</p></details><div id="run-plan" class="plan-summary"><p>Preparing your search plan…</p></div><p class="field-help">Provider limits can reduce coverage. A completed run does not guarantee all available listings. Running a collection spends Apify credits.</p><div class="modal-actions"><button class="secondary-button" id="preview-run">Update plan</button><button class="primary-button" id="start-run" disabled>Start collection</button></div>`,
  );
  const accountBox = document.createElement("div");
  accountBox.id = "collection-account-usage";
  accountBox.className = "notice-box account-run-usage";
  accountBox.setAttribute("aria-live", "polite");
  $("#modal-body .field-row").before(accountBox);
  renderUsage();
  refreshUsage(true);
  const read = () => {
    wizard.budget_usd = Number($("#run-budget").value);
    wizard.max_jobs = Number($("#run-jobs").value);
    wizard.max_pages = Number($("#run-pages").value);
    wizard.search.search_terms = $("#run-terms")
      .value.split("\n")
      .map((s) => s.trim())
      .filter(Boolean);
  };
  const preview = async () => {
    read();
    $("#start-run").disabled = true;
    try {
      const plan = await api("/collections/plan", {
        method: "POST",
        body: wizard,
      });
      $("#run-plan").innerHTML =
        `<strong>Up to ${usageMoney(plan.maximum_charge_usd)}</strong><p>${plan.search_count} ${listingIds.length ? "refresh job" : "searches"} · ${plan.per_job_usd.toLocaleString("en-US", { style: "currency", currency: "USD" })} cap per job</p>${plan.truncated ? `<p>${plan.available_searches - plan.search_count} searches fall outside this run’s limits. Increase the budget or search count to include them.</p>` : ""}<ul class="plan-list">${plan.tasks.map((t) => `<li>${esc(t.label)}</li>`).join("")}</ul>`;
      $("#start-run").disabled = !boot.token_configured;
      $("#modal-error")?.remove();
    } catch (error) {
      $("#run-plan").textContent = "Choose your vehicle criteria and add a search city from the location row before collecting.";
      modalError(error);
    }
  };
  ["run-budget", "run-jobs", "run-pages", "run-terms"].forEach((id) =>
    $("#" + id).addEventListener("input", () => {
      $("#start-run").disabled = true;
    }),
  );
  $("#preview-run").addEventListener("click", preview);
  $("#start-run").addEventListener("click", async () => {
    const button = $("#start-run");
    button.disabled = true;
    button.textContent = "Starting…";
    try {
      const job = await api("/collections", { method: "POST", body: wizard });
      boot.jobs = [job, ...boot.jobs.filter((j) => j.id !== job.id)];
      modal.close();
      toast("Collection started. You can keep browsing.");
      updateJobBanner();
      pollJobs();
    } catch (error) {
      modalError(error);
      button.textContent = "Start collection";
      button.disabled = false;
    }
  });
  preview();
}
function updateJobBanner() {
  const job = boot.jobs[0],
    banner = $("#collection-banner");
  const active =
    job && ["queued", "running", "interrupted"].includes(job.status);
  $("#run-dot").hidden = !active;
  if (!job) {
    banner.hidden = true;
    return;
  }
  const complete = job.tasks.filter((t) => t.status === "complete").length;
  const limits = job.tasks.filter((t) => t.notices?.length).length;
  banner.hidden = false;
  banner.innerHTML = `${icon(active ? "refresh" : "check")}<span>${active ? `Collection ${job.status}` : `Last collection: ${job.status.replaceAll("_", " ")}`} · ${complete}/${job.tasks.length} searches · ${Number(job.reported_cost_usd || 0).toLocaleString("en-US", { style: "currency", currency: "USD" })}${limits ? ` · ${limits} provider limit notices` : ""}</span><button class="text-button" data-action="activity">View activity</button>`;
}
async function pollJobs() {
  if (polling) return;
  polling = true;
  try {
    const prior = boot.jobs[0];
    boot.jobs = await api("/collections");
    updateJobBanner();
    const latest = boot.jobs[0];
    if (
      latest &&
      (latest.completed !== prior?.completed || latest.status !== prior?.status)
    ) {
      await loadListings();
      refreshUsage(!["queued", "running"].includes(latest.status));
    }
  } catch (error) {
    toast(error.message);
  } finally {
    polling = false;
    if (boot.jobs.some((j) => ["queued", "running"].includes(j.status)))
      setTimeout(pollJobs, 5000);
  }
}
function activity() {
  openModal(
    "Collection activity",
    boot.jobs.length
      ? boot.jobs
          .map((job) => {
            const completed = job.tasks.filter(
              (t) => t.status === "complete",
            ).length;
            return `<div class="job-row"><h3>${esc(job.name)}</h3><div class="job-meta"><span class="status-badge">${esc(job.status.replaceAll("_", " "))}</span><span>${date(job.started_at)}</span></div><progress max="${job.tasks.length}" value="${completed}" aria-label="Collection progress"></progress><p>${completed}/${job.tasks.length} completed · ${Number(job.reported_cost_usd || 0).toLocaleString("en-US", { style: "currency", currency: "USD" })} reported · ${usageMoney(job.maximum_charge_usd)} cap</p>${["queued", "running", "interrupted"].includes(job.status) ? `<button class="secondary-button" data-stop-job="${job.id}">Stop queued searches</button>` : ""}${job.status === "interrupted" ? ` <button class="primary-button" data-resume-job="${job.id}">Resume collection</button>` : ""}<details><summary>Searches and provider notices</summary><ul class="plan-list">${job.tasks.map((task) => `<li><strong>${esc(task.label)}</strong><br>${esc(task.status)}${task.run_id ? ` · <a href="https://console.apify.com/actors/runs/${esc(task.run_id)}" target="_blank" rel="noopener noreferrer">Apify run ${icon("link")}</a>` : ""}${task.error ? `<p>${esc(task.error)}</p>` : ""}${task.notices?.length ? `<p>${esc(task.notices.map((n) => n.error || JSON.stringify(n)).join("; "))}</p>` : ""}</li>`).join("")}</ul></details></div>`;
          })
          .join("")
      : "<p>No collections yet. Use Collect listings to preview a bounded search plan.</p>",
  );
}
function help() {
  openModal(
    "About Better Marketplace",
    `<p>A local vehicle search app for Facebook Marketplace listings. You choose the criteria; we check the title, description, and vehicle fields and show the supporting evidence.</p><h3>Connect your collector</h3><p>In the repo folder, copy <code>.env.example</code> to <code>.env</code>. Set <code>APIFY_TOKEN=your_token</code>, then restart <code>uv run better-marketplace serve</code>.</p><p>The server uses Curious Coder’s Facebook Marketplace actor. Browsing and importing work without a token. <a href="https://console.apify.com/account/integrations" target="_blank" rel="noopener noreferrer">Manage Apify tokens ${icon("link")}</a></p><h3>Understand the results</h3><p><strong>Matches</strong> meet your criteria with known values. <strong>Needs review</strong> covers missing, conflicting, or old evidence. <strong>Excluded</strong> contains known failures with specific reasons.</p><p>Min and max prices are inclusive. Mileage must be below its limit. Saved searches store criteria; saved listings bookmark individual cars.</p><h3>Photos and freshness</h3><p>Facebook image links can expire. Refresh a listing to retrieve its latest fields. We keep old observations and displayed-price history. We do not treat a missing search result as sold.</p><h3>Your local setup</h3><div class="notice-box">Apify: <strong>${boot.token_configured ? "connected" : "not configured"}</strong><br>Maximum collection budget: ${usageMoney(boot.limits.max_run_usd)}<br>Version: ${esc(boot.version)}</div><p>Read <code>README.md</code> for installation, Docker, Tailscale, the API, tests, and recovery. This single-user app is intended for localhost or a trusted private network.</p><p class="field-help">Independent project. Not affiliated with Facebook, Meta, or Apify.</p>`,
  );
}
function importModal() {
  openModal(
    "Import listings",
    `<p>Import an Apify dataset exported as a JSON array. We preserve the raw fields and re-check the listings against your current search.</p><label>Original observation time (optional)<input id="import-time" type="datetime-local"></label><p class="field-help">Use the original collection time for older exports. Without a timestamp, the import counts as observed now.</p><div class="notice-box">JSON array · up to 10,000 records · 20 MB maximum</div><div class="modal-actions"><button class="secondary-button" data-action="close-modal">Cancel</button><button class="primary-button" id="choose-import">Choose JSON file</button></div>`,
  );
  $("#choose-import").addEventListener("click", () =>
    $("#import-file").click(),
  );
}
$("#import-file").addEventListener("change", async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  try {
    if (file.size > 20 * 1024 * 1024)
      throw new Error("File exceeds 20 MB. Split the export first.");
    const records = JSON.parse(await file.text());
    const time = $("#import-time")?.value;
    const data = await api("/listings/import", {
      method: "POST",
      body: {
        records,
        observed_at: time ? new Date(time).toISOString() : null,
      },
    });
    modal.close();
    toast(`Imported ${data.imported} observations`);
    await loadListings();
  } catch (error) {
    modalError(error);
  } finally {
    event.target.value = "";
  }
});
function exportModal() {
  openModal(
    "Export this view",
    `<p>Download all ${number(result?.total || 0)} listings in the current view, including the filters you applied. The JSON includes source evidence and displayed-price history.</p><div class="modal-actions"><button class="secondary-button" data-export="json">Evidence JSON</button><button class="primary-button" data-export="csv">Download CSV</button></div>`,
  );
}
async function download(format) {
  try {
    const response = await fetch(`/api/listings/export/${format}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Marketplace-Client": "1",
      },
      body: JSON.stringify(state),
    });
    if (!response.ok) throw new Error("Export failed");
    const url = URL.createObjectURL(await response.blob());
    const a = document.createElement("a");
    a.href = url;
    a.download = `marketplace-listings.${format}`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    modal.close();
  } catch (error) {
    modalError(error);
  }
}
function showFilters(open) {
  $("#sidebar").classList.toggle("open", open);
  $("#sidebar-shade").hidden = !open;
  if (open) $("#makes").focus();
}
const actions = {
  "usage-details": () => {
    const card = $("#apify-usage");
    $("details", card).open = true;
    card.scrollIntoView({ block: "nearest" });
    card.focus({ preventScroll: true });
  },
  "refresh-usage": () => refreshUsage(true),
  collect: () => collectModal(),
  activity,
  help,
  import: importModal,
  export: exportModal,
  "save-search": saveSearchModal,
  "manage-searches": manageSearches,
  location: locationModal,
  "close-modal": () => modal.close(),
  "close-detail": () => detailDialog.close(),
  "refresh-detail": () => {
    const id = activeDetail.id;
    detailDialog.close();
    collectModal([id]);
  },
  filters: () => showFilters(true),
  "close-filters": () => showFilters(false),
  reset: () => {
    state.search = clone(boot.default_search);
    state.model = "";
    state.reason = "";
    state.text = "";
    state.status = "match";
    state.page = 1;
    selectedSearch = "";
    $("#search").value = "";
    didAutoSelect = false;
    filterForm();
    renderSaved();
    persist();
    loadListings();
  },
};
document.addEventListener("click", async (event) => {
  const button = event.target.closest("button,a[data-action]");
  if (!button) return;
  if (button.dataset.action && actions[button.dataset.action]) {
    actions[button.dataset.action]();
    return;
  }
  if (button.dataset.listing) {
    showDetail(result.items.find((i) => i.id === button.dataset.listing));
    return;
  }
  if (button.dataset.bookmark) {
    bookmark(button.dataset.bookmark);
    return;
  }
  if (button.dataset.nav) {
    state.status = button.dataset.nav === "saved" ? "saved" : "all";
    state.page = 1;
    loadListings();
    return;
  }
  if (button.dataset.status) {
    state.status = button.dataset.status;
    state.page = 1;
    loadListings();
    return;
  }
  if (button.dataset.loadSearch) {
    loadSaved(button.dataset.loadSearch);
    return;
  }
  if (button.dataset.deleteSearch) {
    try {
      await api(`/searches/${button.dataset.deleteSearch}`, {
        method: "DELETE",
      });
      if (selectedSearch === button.dataset.deleteSearch) selectedSearch = "";
      await refreshBootstrap();
      manageSearches();
      toast("Search deleted");
    } catch (error) {
      modalError(error);
    }
    return;
  }
  if (button.dataset.export) {
    download(button.dataset.export);
    return;
  }
  if (button.dataset.stopJob || button.dataset.resumeJob) {
    try {
      button.disabled = true;
      const id = button.dataset.stopJob || button.dataset.resumeJob;
      await api(
        `/collections/${id}/${button.dataset.stopJob ? "stop" : "resume"}`,
        { method: "POST" },
      );
      await pollJobs();
      activity();
    } catch (error) {
      modalError(error);
      button.disabled = false;
    }
  }
});
$("#filters").addEventListener("submit", (event) => event.preventDefault());
$("#filters").addEventListener("input", (event) => {
  if (event.target.id === "makes") return;
  clearTimeout(debounce);
  debounce = setTimeout(() => {
    if (readFilters()) loadListings();
  }, 450);
});
$("#makes").addEventListener("change", () => {
  const makes = $("#makes")
    .value.split(",")
    .map((s) => s.trim());
  if (
    makes.join(",").toLowerCase() !== state.search.makes.join(",").toLowerCase()
  ) {
    $("#models").value = "";
    state.model = "";
    state.reason = "";
    $("#model-filter").value = "";
    $("#reason-filter").value = "";
  }
  if (readFilters()) loadListings();
});
$("#search").addEventListener("input", () => {
  clearTimeout(debounce);
  debounce = setTimeout(() => {
    state.text = $("#search").value;
    state.page = 1;
    loadListings();
  }, 300);
});
$("#sort").addEventListener("change", () => {
  state.sort = $("#sort").value;
  state.page = 1;
  loadListings();
});
$("#saved-search").addEventListener("change", (event) => {
  if (event.target.value) loadSaved(event.target.value);
  else selectedSearch = "";
});
$("#prev-page").addEventListener("click", () => {
  state.page--;
  loadListings();
  window.scrollTo({ top: 0, behavior: "smooth" });
});
$("#next-page").addEventListener("click", () => {
  state.page++;
  loadListings();
  window.scrollTo({ top: 0, behavior: "smooth" });
});
$("#sidebar-shade").addEventListener("click", () => showFilters(false));
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") showFilters(false);
});
[modal, detailDialog].forEach((dialog) =>
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) {
      const rect = dialog.getBoundingClientRect();
      if (
        event.clientX < rect.left ||
        event.clientX > rect.right ||
        event.clientY < rect.top ||
        event.clientY > rect.bottom
      )
        dialog.close();
    }
  }),
);
async function init() {
  $("#grid").innerHTML = Array.from(
    { length: 8 },
    () => '<div class="skeleton"></div>',
  ).join("");
  try {
    boot = await api("/bootstrap");
    let search = clone(boot.default_search);
    try {
      const stored = JSON.parse(
        localStorage.getItem("better-marketplace.search.v1"),
      );
      if (Array.isArray(stored?.search_centers) && Array.isArray(stored?.makes))
        search = { ...search, ...normalizeSearch(stored) };
    } catch {}
    state = {
      search,
      status: "match",
      text: "",
      model: "",
      reason: "",
      sort: "newest",
      page: 1,
      page_size: 24,
    };
    filterForm();
    renderSaved();
    renderConnection();
    renderUsage();
    refreshUsage();
    setInterval(() => {
      if (!document.hidden) refreshUsage();
    }, 60000);
    updateJobBanner();
    await loadListings();
    if (boot.jobs.some((j) => ["running", "queued"].includes(j.status)))
      pollJobs();
  } catch (error) {
    $("#grid").innerHTML = "";
    $("#empty").hidden = false;
    $("#empty-title").textContent = "Could not connect to the server";
    $("#empty-text").textContent = error.message;
    $("#connection").textContent = "Server unavailable";
  }
}
init();
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && boot?.token_configured) refreshUsage();
});
