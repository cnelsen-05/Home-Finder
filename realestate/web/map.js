const tagOptions = [
  "quiet_street",
  "parks",
  "playgrounds",
  "trails",
  "mature_trees",
  "good_commute",
  "near_lifetime",
  "daycare_nearby",
  "school_zone_interest",
  "feels_too_busy",
  "road_noise",
  "expensive",
  "needs_more_research",
  "tour_again",
  "favorite_pocket",
];

const state = {
  map: null,
  data: null,
  selected: null,
  lastClickLatLng: null,
  groups: {},
  homeLayers: new Map(),
  areaLayers: new Map(),
  highlightLayers: new Map(),
  schoolZoneLayers: new Map(),
  userLocationMarker: null,
  activeNeighborhoodId: null,
  activeSchoolZoneId: null,
  activeDrawHandler: null,
  pendingHighlightMode: null,
  lazyData: {
    schoolZones: null,
    schoolLocations: null,
    parksTrails: null,
  },
};

document.addEventListener("DOMContentLoaded", () => {
  initMap();
  bindStaticControls();
  loadMapData();
  registerServiceWorker();
  window.addEventListener("online", flushQueuedNotes);
});

function initMap() {
  state.map = L.map("map", { zoomControl: true }).setView([44.98, -93.37], 11);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "&copy; OpenStreetMap contributors",
  }).addTo(state.map);

  state.groups.homes = L.layerGroup().addTo(state.map);
  state.groups.neighborhoods = new L.FeatureGroup().addTo(state.map);
  state.groups.mapHighlights = L.layerGroup().addTo(state.map);
  state.groups.schoolZones = L.layerGroup();
  state.groups.schoolLocations = L.layerGroup();
  state.groups.parksTrails = L.layerGroup();
  state.groups.lifeAnchors = L.layerGroup().addTo(state.map);
  state.groups.mapNotes = L.layerGroup().addTo(state.map);

  const drawControl = new L.Control.Draw({
    position: "topleft",
    draw: {
      marker: false,
      polyline: false,
      circlemarker: false,
      polygon: { allowIntersection: false, showArea: true },
      rectangle: true,
      circle: true,
    },
    edit: { featureGroup: state.groups.neighborhoods },
  });
  state.map.addControl(drawControl);

  state.map.on(L.Draw.Event.CREATED, (event) => {
    const geometry = layerToGeometry(event.layer);
    if (state.pendingHighlightMode) {
      const highlightType = state.pendingHighlightMode;
      clearHighlightMode();
      state.selected = { type: "new_highlight", geometry, highlightType };
      renderHighlightForm({ geometry, highlightType });
      return;
    }
    state.selected = { type: "new_neighborhood", geometry };
    renderNeighborhoodForm({ geometry });
  });

  state.map.on(L.Draw.Event.EDITED, async (event) => {
    const updates = [];
    event.layers.eachLayer((layer) => {
      if (layer.neighborhoodId) {
        updates.push(
          api(`/api/neighborhoods/${layer.neighborhoodId}`, {
            method: "PUT",
            body: { geometry: layerToGeometry(layer) },
          }),
        );
      }
    });
    await Promise.all(updates);
    await loadMapData();
  });

  state.map.on("click", async (event) => {
    state.lastClickLatLng = event.latlng;
    const result = await api("/api/school-zones/identify", {
      method: "POST",
      body: { lat: event.latlng.lat, lon: event.latlng.lng },
    });
    state.selected = { type: "map_point", latlng: event.latlng, zone: result };
    renderSchoolLookupDetails(event.latlng, result);
  });
}
function bindStaticControls() {
  document.querySelectorAll("[data-layer]").forEach((input) => {
    input.addEventListener("change", async () => {
      const group = state.groups[input.dataset.layer];
      if (!group) return;
      if (input.checked) {
        await ensureLazyLayerLoaded(input.dataset.layer);
        group.addTo(state.map);
      } else {
        state.map.removeLayer(group);
      }
    });
  });
  document.getElementById("refreshButton").addEventListener("click", loadMapData);
  document.getElementById("saveNoteButton").addEventListener("click", saveQuickNote);
  document.getElementById("searchBox").addEventListener("input", renderLists);
  document.getElementById("addHomeForm").addEventListener("submit", addHomeFromForm);
  document.getElementById("mobileOpenMenu")?.addEventListener("click", () => toggleMobilePanel("menu"));
  document.getElementById("mobileOpenDetails")?.addEventListener("click", () => toggleMobilePanel("details"));
  document.getElementById("mobileUseLocation")?.addEventListener("click", useCurrentLocation);
  document.getElementById("mobileQuickLike")?.addEventListener("click", () => {
    closeMobilePanels();
    beginHighlightMode("liked_area");
  });
  document.querySelectorAll("[data-highlight-mode]").forEach((button) => {
    button.addEventListener("click", () => beginHighlightMode(button.dataset.highlightMode));
  });
}

function toggleMobilePanel(panel) {
  const sidebar = document.querySelector(".sidebar");
  const details = document.querySelector(".details");
  const target = panel === "menu" ? sidebar : details;
  const alreadyOpen = target.classList.contains("mobile-open");
  sidebar.classList.remove("mobile-open");
  details.classList.remove("mobile-open");
  if (!alreadyOpen) target.classList.add("mobile-open");
}

function openMobilePanel(panel) {
  if (!window.matchMedia("(max-width: 760px)").matches) return;
  document.querySelector(".sidebar").classList.toggle("mobile-open", panel === "menu");
  document.querySelector(".details").classList.toggle("mobile-open", panel === "details");
}

function closeMobilePanels() {
  document.querySelector(".sidebar").classList.remove("mobile-open");
  document.querySelector(".details").classList.remove("mobile-open");
}

function useCurrentLocation() {
  if (!navigator.geolocation) {
    window.alert("Current location is not available in this browser.");
    return;
  }
  navigator.geolocation.getCurrentPosition(
    (position) => {
      const latlng = L.latLng(position.coords.latitude, position.coords.longitude);
      state.lastClickLatLng = latlng;
      if (state.userLocationMarker) {
        state.userLocationMarker.setLatLng(latlng);
      } else {
        state.userLocationMarker = L.circleMarker(latlng, {
          radius: 7,
          color: "#ffffff",
          weight: 2,
          fillColor: "#2f5f92",
          fillOpacity: 0.95,
        }).addTo(state.map);
      }
      state.map.setView(latlng, Math.max(state.map.getZoom(), 16));
      document.getElementById("addHomeStatus").textContent = "Current location ready for new home pin.";
      openMobilePanel("menu");
    },
    () => {
      window.alert("Could not get current location.");
    },
    { enableHighAccuracy: true, timeout: 10000, maximumAge: 30000 },
  );
}

function registerServiceWorker() {
  if (!("serviceWorker" in navigator)) return;
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/service-worker.js").catch(() => {});
  });
}

async function loadMapData() {
  state.data = await api("/api/map-data");
  renderLayers();
  renderLists();
  renderWelcome();
  flushQueuedNotes();
}

function renderLayers() {
  state.homeLayers.clear();
  state.areaLayers.clear();
  state.highlightLayers.clear();
  state.schoolZoneLayers.clear();
  Object.values(state.groups).forEach((group) => group.clearLayers());

  renderHomeLayer(state.data.homes);
  renderNeighborhoodLayer(state.data.saved_neighborhoods);
  renderMapHighlightLayer(state.data.map_highlights);
  if (state.lazyData.schoolZones) renderSchoolZoneLayer(state.lazyData.schoolZones);
  if (state.lazyData.schoolLocations) renderSchoolLocationLayer(state.lazyData.schoolLocations);
  if (state.lazyData.parksTrails) renderParksTrailsLayer(state.lazyData.parksTrails);
  renderLifeAnchorLayer(state.data.life_anchors);
  renderMapNoteLayer(state.data.map_notes);

  document.querySelectorAll("[data-layer]").forEach((input) => {
    const group = state.groups[input.dataset.layer];
    if (!group) return;
    if (input.checked && !state.map.hasLayer(group)) group.addTo(state.map);
    if (!input.checked && state.map.hasLayer(group)) state.map.removeLayer(group);
  });

  const allHomePoints = state.data.homes.features
    .map((feature) => feature.geometry?.coordinates)
    .filter(Boolean)
    .map((coords) => [coords[1], coords[0]]);
  if (allHomePoints.length) {
    state.map.fitBounds(allHomePoints, { padding: [36, 36], maxZoom: 13 });
  }
}

function renderHomeLayer(collection) {
  collection.features.forEach((feature) => {
    const coords = feature.geometry?.coordinates;
    if (!coords) return;
    const marker = L.circleMarker([coords[1], coords[0]], {
      radius: 8,
      color: "#ffffff",
      weight: 2,
      fillColor: homeColor(feature.properties.user_rating),
      fillOpacity: 0.95,
    });
    marker.on("click", (event) => {
      L.DomEvent.stopPropagation(event.originalEvent);
      state.selected = { type: "home", feature };
      renderHomeDetails(feature);
    });
    marker.bindTooltip(feature.properties.address || "Favorite home");
    marker.addTo(state.groups.homes);
    state.homeLayers.set(feature.properties.listing_id, marker);
  });
}

function renderNeighborhoodLayer(collection) {
  collection.features.forEach((feature) => {
    const layerGroup = L.geoJSON(feature, {
      style: neighborhoodStyle(feature.properties.rating),
      onEachFeature: (_feature, layer) => {
        layer.neighborhoodId = feature.properties.id;
        layer.on("click", (event) => {
          L.DomEvent.stopPropagation(event.originalEvent);
          state.selected = { type: "neighborhood", feature };
          setActiveNeighborhood(feature.properties.id);
          renderNeighborhoodDetails(feature);
        });
        layer.bindTooltip(feature.properties.name || "Saved area");
      },
    });
    layerGroup.eachLayer((layer) => {
      layer.neighborhoodId = feature.properties.id;
      layer.featureProperties = feature.properties;
      state.groups.neighborhoods.addLayer(layer);
      state.areaLayers.set(feature.properties.id, layer);
    });
  });
  applyNeighborhoodHighlight();
}

function renderMapHighlightLayer(collection) {
  state.groups.mapHighlights.clearLayers();
  (collection?.features || []).forEach((feature) => {
    const layerGroup = L.geoJSON(feature, {
      style: highlightStyle(feature.properties),
      pointToLayer: (pointFeature, latlng) =>
        L.circleMarker(latlng, {
          radius: 7,
          ...highlightStyle(pointFeature.properties),
        }),
      onEachFeature: (_feature, layer) => {
        layer.highlightId = feature.properties.id;
        layer.on("click", (event) => {
          L.DomEvent.stopPropagation(event.originalEvent);
          state.selected = { type: "highlight", feature };
          renderHighlightDetails(feature);
        });
        layer.bindTooltip(feature.properties.name || "Map highlight");
      },
    });
    layerGroup.eachLayer((layer) => {
      layer.highlightId = feature.properties.id;
      state.groups.mapHighlights.addLayer(layer);
      state.highlightLayers.set(feature.properties.id, layer);
    });
  });
}

function renderSchoolZoneLayer(collection) {
  state.groups.schoolZones.clearLayers();
  state.schoolZoneLayers.clear();
  L.geoJSON(collection, {
    style: (feature) => schoolZoneStyle(feature),
    onEachFeature: (feature, layer) => {
      layer.on("click", (event) => {
        L.DomEvent.stopPropagation(event.originalEvent);
        state.selected = { type: "school_zone", feature };
        setActiveSchoolZone(feature.properties.id);
        renderSchoolZoneDetails(feature);
      });
      layer.bindTooltip(feature.properties.school_name || "Elementary zone");
      state.schoolZoneLayers.set(feature.properties.id, layer);
      layer.addTo(state.groups.schoolZones);
    },
  });
  applySchoolZoneHighlight();
}

function renderSchoolLocationLayer(collection) {
  state.groups.schoolLocations.clearLayers();
  L.geoJSON(collection, {
    pointToLayer: (feature, latlng) =>
      L.marker(latlng, {
        icon: L.divIcon({
          className: "school-square-marker",
          iconSize: [14, 14],
          iconAnchor: [7, 7],
        }),
      }),
    onEachFeature: (feature, layer) => {
      layer.on("click", (event) => {
        L.DomEvent.stopPropagation(event.originalEvent);
        state.selected = { type: "school_location", feature };
        renderSchoolLocationDetails(feature);
      });
      const rank = schoolTooltipRank(feature.properties);
      layer.bindTooltip(`${feature.properties.name || "Elementary school"}${rank}`);
      layer.addTo(state.groups.schoolLocations);
    },
  });
}

function renderParksTrailsLayer(collection) {
  state.groups.parksTrails.clearLayers();
  L.geoJSON(collection, {
    pointToLayer: (feature, latlng) =>
      L.circleMarker(latlng, {
        radius: 5,
        color: "#ffffff",
        weight: 1.5,
        fillColor: parksColor(feature.properties.category),
        fillOpacity: 0.92,
      }),
    onEachFeature: (feature, layer) => {
      layer.on("click", (event) => {
        L.DomEvent.stopPropagation(event.originalEvent);
        state.selected = { type: "parks_trails", feature };
        renderParksTrailsDetails(feature);
      });
      layer.bindTooltip(feature.properties.name || feature.properties.category || "Map feature");
      layer.addTo(state.groups.parksTrails);
    },
  });
}

function renderLifeAnchorLayer(collection) {
  collection.features.forEach((feature) => {
    const coords = feature.geometry.coordinates;
    const marker = L.circleMarker([coords[1], coords[0]], {
      radius: 6,
      color: "#ffffff",
      weight: 2,
      fillColor: "#9a6a16",
      fillOpacity: 0.95,
    });
    marker.bindTooltip(feature.properties.name || "Life anchor");
    marker.addTo(state.groups.lifeAnchors);
  });
}

function renderMapNoteLayer(collection) {
  L.geoJSON(collection, {
    pointToLayer: (_feature, latlng) =>
      L.circleMarker(latlng, {
        radius: 5,
        color: "#ffffff",
        weight: 2,
        fillColor: "#6d5a8d",
        fillOpacity: 0.95,
      }),
    onEachFeature: (feature, layer) => {
      layer.bindTooltip(feature.properties.title || feature.properties.note_type || "Map note");
      layer.on("click", (event) => {
        L.DomEvent.stopPropagation(event.originalEvent);
        state.selected = { type: "note", feature };
        renderNoteDetails(feature);
      });
      layer.addTo(state.groups.mapNotes);
    },
  });
}

function renderLists() {
  const query = document.getElementById("searchBox").value.trim().toLowerCase();
  const homes = (state.data?.homes.features || []).filter((feature) =>
    `${feature.properties.address || ""} ${feature.properties.user_rating || ""}`
      .toLowerCase()
      .includes(query),
  );
  const areas = (state.data?.saved_neighborhoods.features || []).filter((feature) =>
    `${feature.properties.name || ""} ${(feature.properties.tags || []).join(" ")}`
      .toLowerCase()
      .includes(query),
  );
  const highlights = (state.data?.map_highlights.features || []).filter((feature) =>
    `${feature.properties.name || ""} ${feature.properties.highlight_type || ""} ${feature.properties.sentiment || ""} ${(feature.properties.tags || []).join(" ")}`
      .toLowerCase()
      .includes(query),
  );
  document.getElementById("homeCount").textContent = homes.length;
  document.getElementById("areaCount").textContent = areas.length;
  document.getElementById("highlightCount").textContent = highlights.length;
  document.getElementById("homeList").innerHTML = homes.map(homeListItem).join("");
  document.getElementById("areaList").innerHTML = areas.map(areaListItem).join("");
  document.getElementById("highlightList").innerHTML = highlights.map(highlightListItem).join("");

  document.querySelectorAll("[data-home-id]").forEach((button) => {
    button.addEventListener("click", () => {
      const id = Number(button.dataset.homeId);
      const feature = homes.find((item) => item.properties.listing_id === id);
      const layer = state.homeLayers.get(id);
      if (feature) {
        if (layer) {
          state.map.setView(layer.getLatLng(), 15);
        }
        state.selected = { type: "home", feature };
        renderHomeDetails(feature);
      }
    });
  });
  document.querySelectorAll("[data-area-id]").forEach((button) => {
    button.addEventListener("click", () => {
      const id = Number(button.dataset.areaId);
      const feature = areas.find((item) => item.properties.id === id);
      const layer = state.areaLayers.get(id);
      if (feature && layer) {
        state.map.fitBounds(layer.getBounds(), { padding: [30, 30] });
        state.selected = { type: "neighborhood", feature };
        setActiveNeighborhood(id);
        renderNeighborhoodDetails(feature);
      }
    });
  });
  document.querySelectorAll("[data-highlight-id]").forEach((button) => {
    button.addEventListener("click", () => {
      const id = Number(button.dataset.highlightId);
      const feature = highlights.find((item) => item.properties.id === id);
      const layer = state.highlightLayers.get(id);
      if (feature && layer) {
        if (layer.getBounds) {
          state.map.fitBounds(layer.getBounds(), { padding: [30, 30], maxZoom: 16 });
        } else if (layer.getLatLng) {
          state.map.setView(layer.getLatLng(), 16);
        }
        state.selected = { type: "highlight", feature };
        renderHighlightDetails(feature);
      }
    });
  });
}

function homeListItem(feature) {
  const props = feature.properties;
  const score = props.score?.overall_score ? `${props.score.overall_score.toFixed(1)}/100` : "Unscored";
  const mapStatus = props.has_location ? "mapped" : "needs map location";
  return `<button class="list-item" data-home-id="${props.listing_id}" type="button">
    <span class="item-title">${escapeHtml(props.address || "Unknown address")}</span>
    <span class="item-subtitle">${escapeHtml(props.user_rating || "unrated")} - ${score} - ${mapStatus}</span>
  </button>`;
}

function areaListItem(feature) {
  const props = feature.properties;
  const fit = props.fit_score?.overall_score == null ? "unscored" : `${props.fit_score.overall_score.toFixed(1)}/100`;
  return `<button class="list-item" data-area-id="${props.id}" type="button">
    <span class="item-title">${escapeHtml(props.name || "Saved area")}</span>
    <span class="item-subtitle">Fit ${fit}</span>
    <span class="item-subtitle">${escapeHtml(props.rating || "maybe")} - ${(props.tags || []).slice(0, 3).map(escapeHtml).join(", ")}</span>
  </button>`;
}

function highlightListItem(feature) {
  const props = feature.properties;
  return `<button class="list-item" data-highlight-id="${props.id}" type="button">
    <span class="item-title">${escapeHtml(props.name || "Map highlight")}</span>
    <span class="item-subtitle">${escapeHtml((props.sentiment || "maybe").replace("_", " "))} - ${escapeHtml((props.highlight_type || "tour_note").replaceAll("_", " "))}</span>
  </button>`;
}

function renderWelcome() {
  if (state.selected) return;
  const schoolCount = state.data.lazy_layers?.school_zones?.feature_count || 0;
  const schoolLocationCount = state.data.lazy_layers?.school_locations?.feature_count || 0;
  const parksCount = state.data.lazy_layers?.parks_trails_playgrounds?.feature_count || 0;
  document.getElementById("detailsTitle").textContent = "Map Hub";
  document.getElementById("detailsBody").innerHTML = `
    <div class="summary-box">
      <h3>Status</h3>
      <p class="meta">${state.data.homes.features.length} homes, ${state.data.saved_neighborhoods.features.length} saved areas, ${state.data.map_highlights.features.length} highlights, ${schoolCount} elementary zones, ${schoolLocationCount} school locations, ${parksCount} parks/trails/playgrounds.</p>
    </div>
    <div class="warning">School assignments are likely matches from imported public data. Verify directly with the district before relying.</div>
  `;
}

function renderHomeDetails(feature) {
  const props = feature.properties;
  const score = props.score || {};
  const zone = props.elementary_zone || {};
  const matches = props.neighborhood_matches || [];
  const highlights = props.highlight_matches || [];
  const selectedRating = props.user_rating === "rejected" ? "reject" : props.user_rating;
  if (zone.zone_id) {
    setActiveSchoolZone(zone.zone_id);
  } else {
    setActiveSchoolZone(null);
  }
  document.getElementById("detailsTitle").textContent = props.address || "Favorite home";
  document.getElementById("detailsBody").innerHTML = `
    <div class="summary-box">
      <h3>Home</h3>
      <div class="metric-grid">
        ${metric("Price", formatMoney(props.price))}
        ${metric("Overall", score.overall_score ? `${score.overall_score.toFixed(1)}/100` : "Unscored")}
        ${metric("Beds/Baths", `${formatNumber(props.beds)} / ${formatNumber(props.baths)}`)}
        ${metric("Sqft", formatNumber(props.finished_sqft))}
        ${metric("Map", props.has_location ? "Mapped" : "Needs location")}
      </div>
    </div>
    ${props.location_warning ? `<div class="warning">${escapeHtml(props.location_warning)}</div>` : ""}
    <div class="summary-box">
      <h3>Map Fit</h3>
      ${matches.length ? `<div class="tag-list">${matches.map(matchTag).join("")}</div>` : `<p class="meta">Outside saved pockets or not matched yet.</p>`}
      ${highlights.length ? `<div class="tag-list">${highlights.map(highlightTag).join("")}</div>` : `<p class="meta">No liked/avoided highlight match yet.</p>`}
    </div>
    <div class="summary-box">
      ${schoolZoneCardHtml(zone, "Elementary Zone")}
    </div>
    <div class="summary-box">
      <h3>Actions</h3>
      <div class="form-grid">
        <select id="homeRating">
          ${["strong_like", "like", "maybe", "dislike", "reject"].map((rating) => `<option value="${rating}" ${rating === selectedRating ? "selected" : ""}>${rating.replace("_", " ")}</option>`).join("")}
        </select>
        <textarea id="homeNotes" class="text-area" placeholder="Home note">${escapeHtml(props.user_notes || "")}</textarea>
        <div class="button-row">
          <button id="saveHomeFeedback" type="button">Save Home</button>
          <button id="createAreaAroundHome" class="ghost-button" type="button">Create Pocket</button>
          <button id="deleteHomeButton" class="danger-button" type="button">Delete Home</button>
          ${props.report_path ? `<a class="report-link" href="/report?path=${encodeURIComponent(props.report_path)}" target="_blank" rel="noopener">Open report</a>` : ""}
        </div>
      </div>
    </div>
  `;
  document.getElementById("saveHomeFeedback").addEventListener("click", () => saveHomeFeedback(props));
  document.getElementById("createAreaAroundHome").addEventListener("click", () => createAreaAroundHome(feature));
  document.getElementById("deleteHomeButton").addEventListener("click", () => deleteHome(props));
  openMobilePanel("details");
}

function renderNeighborhoodDetails(feature) {
  const props = feature.properties;
  setActiveNeighborhood(props.id);
  const fitScore = props.fit_score || {};
  const relatedHomes = (state.data.homes.features || []).filter((home) =>
    (home.properties.neighborhood_matches || []).some((match) => match.id === props.id),
  );
  document.getElementById("detailsTitle").textContent = props.name || "Saved area";
  document.getElementById("detailsBody").innerHTML = `
    <div class="summary-box">
      <h3>Saved Area</h3>
      <div class="tag-list">${(props.tags || []).map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join("") || `<span class="meta">No tags yet</span>`}</div>
      <p class="meta">${escapeHtml(props.rating || "maybe")} - ${escapeHtml(props.city || "city unknown")}</p>
    </div>
    <div class="summary-box">
      <h3>Neighborhood Fit</h3>
      <div class="metric-grid">
        ${metric("Overall", fitScore.overall_score == null ? "Unscored" : `${fitScore.overall_score.toFixed(1)}/100`)}
        ${metric("Confidence", fitScore.confidence || "Unknown")}
        ${metric("Amenities", fitScore.amenity_score == null ? "Unknown" : `${fitScore.amenity_score.toFixed(1)}/100`)}
        ${metric("Quiet/Risk", fitScore.risk_score == null ? "Unknown" : `${fitScore.risk_score.toFixed(1)}/100`)}
      </div>
      ${fitScore.positive_drivers?.length ? `<p class="meta">${escapeHtml(fitScore.positive_drivers[0])}</p>` : ""}
    </div>
    <div class="summary-box">
      <h3>Related Homes</h3>
      ${relatedHomes.length ? relatedHomes.map((home) => `<p class="meta">${escapeHtml(home.properties.address)}</p>`).join("") : `<p class="meta">No matched homes yet. Run match-homes-to-neighborhoods after adding locations.</p>`}
    </div>
  `;
  renderNeighborhoodForm({ feature, geometry: feature.geometry });
}

function renderNeighborhoodForm({ feature = null, geometry }) {
  const props = feature?.properties || {};
  state.selected = feature ? { type: "neighborhood", feature } : { type: "new_neighborhood", geometry };
  document.getElementById("detailsTitle").textContent = feature ? props.name || "Saved area" : "New Saved Area";
  if (!feature) {
    document.getElementById("detailsBody").innerHTML = "";
  }
  const checkedTags = new Set(props.tags || []);
  const form = `
    <div class="summary-box">
      <h3>${feature ? "Edit Area" : "Save Drawn Area"}</h3>
      <div class="form-grid">
        <input id="areaName" class="text-input" value="${escapeHtml(props.name || "")}" placeholder="Name">
        <select id="areaRating">
          ${["favorite", "strong_like", "like", "maybe", "avoid"].map((rating) => `<option value="${rating}" ${rating === (props.rating || "maybe") ? "selected" : ""}>${rating.replace("_", " ")}</option>`).join("")}
        </select>
        <input id="areaCity" class="text-input" value="${escapeHtml(props.city || "")}" placeholder="City">
        <textarea id="areaNotes" class="text-area" placeholder="Notes">${escapeHtml(props.notes || "")}</textarea>
        <div class="checkbox-grid">
          ${tagOptions.map((tag) => `<label><input type="checkbox" value="${tag}" ${checkedTags.has(tag) ? "checked" : ""}> ${tag.replaceAll("_", " ")}</label>`).join("")}
        </div>
        <div class="button-row">
          <button id="saveAreaButton" type="button">${feature ? "Save Area" : "Create Area"}</button>
          ${feature ? `<button id="deleteAreaButton" class="danger-button" type="button">Delete</button>` : ""}
        </div>
      </div>
    </div>
  `;
  document.getElementById("detailsBody").insertAdjacentHTML("beforeend", form);
  document.getElementById("saveAreaButton").addEventListener("click", () => saveArea(feature, geometry));
  const deleteButton = document.getElementById("deleteAreaButton");
  if (deleteButton) deleteButton.addEventListener("click", () => deleteArea(props.id));
  openMobilePanel("details");
}

function renderHighlightDetails(feature) {
  const props = feature.properties;
  document.getElementById("detailsTitle").textContent = props.name || "Map highlight";
  document.getElementById("detailsBody").innerHTML = `
    <div class="summary-box">
      <h3>${escapeHtml((props.highlight_type || "tour_note").replaceAll("_", " "))}</h3>
      <p class="meta">${escapeHtml(props.sentiment || "maybe")} - ${escapeHtml(props.source || "user_drawn")}</p>
      <div class="tag-list">${(props.tags || []).map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join("") || `<span class="meta">No tags yet</span>`}</div>
      <p>${escapeHtml(props.notes || "")}</p>
    </div>
  `;
  renderHighlightForm({ feature, geometry: feature.geometry, highlightType: props.highlight_type });
}

function renderHighlightForm({ feature = null, geometry, highlightType = "tour_note" }) {
  const props = feature?.properties || {};
  const type = props.highlight_type || highlightType;
  const sentiment = props.sentiment || sentimentForHighlightType(type);
  const checkedTags = new Set(props.tags || defaultHighlightTags(type));
  state.selected = feature
    ? { type: "highlight", feature }
    : { type: "new_highlight", geometry, highlightType: type };
  document.getElementById("detailsTitle").textContent = feature ? props.name || "Map highlight" : "Save Highlight";
  if (!feature) {
    document.getElementById("detailsBody").innerHTML = "";
  }
  const form = `
    <div class="summary-box">
      <h3>${feature ? "Edit Highlight" : "Save Drawn Highlight"}</h3>
      <div class="form-grid">
        <input id="highlightName" class="text-input" value="${escapeHtml(props.name || defaultHighlightName(type))}" placeholder="Name">
        <select id="highlightType">
          ${["liked_area", "avoid_area", "liked_street", "avoid_street", "question_area", "tour_note"].map((value) => `<option value="${value}" ${value === type ? "selected" : ""}>${value.replaceAll("_", " ")}</option>`).join("")}
        </select>
        <select id="highlightSentiment">
          ${["favorite", "like", "maybe", "avoid"].map((value) => `<option value="${value}" ${value === sentiment ? "selected" : ""}>${value}</option>`).join("")}
        </select>
        <textarea id="highlightNotes" class="text-area" placeholder="What did we notice?">${escapeHtml(props.notes || "")}</textarea>
        <div class="checkbox-grid">
          ${tagOptions.map((tag) => `<label><input type="checkbox" value="${tag}" ${checkedTags.has(tag) ? "checked" : ""}> ${tag.replaceAll("_", " ")}</label>`).join("")}
        </div>
        <div class="button-row">
          <button id="saveHighlightButton" type="button">${feature ? "Save Highlight" : "Create Highlight"}</button>
          ${feature ? `<button id="deleteHighlightButton" class="danger-button" type="button">Delete</button>` : ""}
        </div>
      </div>
    </div>
  `;
  document.getElementById("detailsBody").insertAdjacentHTML("beforeend", form);
  document.getElementById("saveHighlightButton").addEventListener("click", () => saveHighlight(feature, geometry));
  const deleteButton = document.getElementById("deleteHighlightButton");
  if (deleteButton) deleteButton.addEventListener("click", () => deleteHighlight(props.id));
  openMobilePanel("details");
}

function renderSchoolLookupDetails(latlng, zone) {
  if (zone.zone_id) setActiveSchoolZone(zone.zone_id);
  setActiveNeighborhood(null);
  document.getElementById("detailsTitle").textContent = "School Zone Lookup";
  document.getElementById("detailsBody").innerHTML = `
    <div class="summary-box">
      <h3>Point</h3>
      <p class="meta">${latlng.lat.toFixed(6)}, ${latlng.lng.toFixed(6)}</p>
    </div>
    <div class="summary-box">
      ${schoolZoneCardHtml(zone, "Likely Elementary Zone")}
    </div>
  `;
  openMobilePanel("details");
}

function renderSchoolZoneDetails(feature) {
  const props = feature.properties;
  setActiveSchoolZone(props.id);
  document.getElementById("detailsTitle").textContent = props.school_name || "Elementary zone";
  document.getElementById("detailsBody").innerHTML = `
    <div class="summary-box">
      ${schoolZoneCardHtml(props, "Selected Elementary Zone")}
      <div class="button-row">
        <button class="ghost-button" type="button" id="zoomZoneButton">Zoom to Zone</button>
        <button class="ghost-button" type="button" id="clearZoneButton">Clear Highlight</button>
      </div>
    </div>
  `;
  document.getElementById("zoomZoneButton").addEventListener("click", () => {
    const layer = state.schoolZoneLayers.get(props.id);
    if (layer?.getBounds) state.map.fitBounds(layer.getBounds(), { padding: [30, 30] });
  });
  document.getElementById("clearZoneButton").addEventListener("click", () => setActiveSchoolZone(null));
  openMobilePanel("details");
}

function renderSchoolLocationDetails(feature) {
  const props = feature.properties;
  document.getElementById("detailsTitle").textContent = props.name || "Elementary school";
  document.getElementById("detailsBody").innerHTML = `
    <div class="summary-box">
      <h3>School Location</h3>
      <p>${escapeHtml(props.address || "Address unknown")}</p>
      <p class="meta">${escapeHtml(props.grade_range || "grade range unknown")} - ${escapeHtml(props.source_name || "Source unknown")}</p>
      ${schoolRankingsHtml(props.ranking_statuses || props.academic_profiles || [])}
      <div class="warning">School location and ranking context are factual/source-labeled aids. Verify assignment with the district before relying.</div>
    </div>
  `;
  openMobilePanel("details");
}

function renderParksTrailsDetails(feature) {
  const props = feature.properties;
  document.getElementById("detailsTitle").textContent = props.name || "Park/trail feature";
  document.getElementById("detailsBody").innerHTML = `
    <div class="summary-box">
      <h3>${escapeHtml((props.category || "feature").replaceAll("_", " "))}</h3>
      <p class="meta">${escapeHtml(props.source_name || "Source unknown")} - ${escapeHtml(props.confidence || "unknown")} confidence</p>
      <div class="warning">OpenStreetMap coverage depends on community tagging. Verify park, trail, and playground details locally.</div>
    </div>
  `;
  openMobilePanel("details");
}

function renderNoteDetails(feature) {
  const props = feature.properties;
  document.getElementById("detailsTitle").textContent = props.title || "Map note";
  document.getElementById("detailsBody").innerHTML = `
    <div class="summary-box">
      <h3>${escapeHtml(props.note_type || "Observation")}</h3>
      <p>${escapeHtml(props.body || "")}</p>
      <div class="tag-list">${(props.tags || []).map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join("")}</div>
    </div>
  `;
  openMobilePanel("details");
}

async function saveArea(feature, geometry) {
  const tags = [...document.querySelectorAll(".checkbox-grid input:checked")].map((input) => input.value);
  const payload = {
    name: document.getElementById("areaName").value,
    rating: document.getElementById("areaRating").value,
    city: document.getElementById("areaCity").value,
    notes: document.getElementById("areaNotes").value,
    tags,
    geometry,
  };
  if (feature?.properties?.id) {
    await api(`/api/neighborhoods/${feature.properties.id}`, { method: "PUT", body: payload });
  } else {
    await api("/api/neighborhoods", { method: "POST", body: payload });
  }
  state.selected = null;
  await loadMapData();
}

async function deleteArea(id) {
  await api(`/api/neighborhoods/${id}`, { method: "DELETE" });
  state.selected = null;
  await loadMapData();
}

async function saveHighlight(feature, geometry) {
  const tags = [...document.querySelectorAll(".checkbox-grid input:checked")].map((input) => input.value);
  const payload = {
    name: document.getElementById("highlightName").value,
    highlight_type: document.getElementById("highlightType").value,
    sentiment: document.getElementById("highlightSentiment").value,
    notes: document.getElementById("highlightNotes").value,
    tags,
    geometry,
  };
  if (feature?.properties?.id) {
    await api(`/api/map-highlights/${feature.properties.id}`, { method: "PUT", body: payload });
  } else {
    await api("/api/map-highlights", { method: "POST", body: payload });
  }
  state.selected = null;
  await loadMapData();
}

async function deleteHighlight(id) {
  await api(`/api/map-highlights/${id}`, { method: "DELETE" });
  state.selected = null;
  await loadMapData();
}

async function addHomeFromForm(event) {
  event.preventDefault();
  const addressInput = document.getElementById("newHomeAddress");
  const notesInput = document.getElementById("newHomeNotes");
  const status = document.getElementById("addHomeStatus");
  const button = document.getElementById("addHomeButton");
  const address = addressInput.value.trim();
  if (!address) {
    status.textContent = "Address is required.";
    return;
  }
  const payload = {
    address,
    rating: document.getElementById("newHomeRating").value,
    notes: notesInput.value.trim(),
    geocode: document.getElementById("newHomeGeocode").checked,
  };
  if (document.getElementById("newHomeUseClick").checked) {
    if (!state.lastClickLatLng) {
      status.textContent = "Click the map first, then add the home.";
      return;
    }
    payload.lat = state.lastClickLatLng.lat;
    payload.lon = state.lastClickLatLng.lng;
    payload.latitude = state.lastClickLatLng.lat;
    payload.longitude = state.lastClickLatLng.lng;
  }
  button.disabled = true;
  status.textContent = "Adding home...";
  try {
    const created = await api("/api/homes", { method: "POST", body: payload });
    addressInput.value = "";
    notesInput.value = "";
    document.getElementById("newHomeUseClick").checked = false;
    status.textContent = created.properties?.has_location
      ? "Added and placed on the map."
      : "Added. Map location still needs enrichment or a clicked pin.";
    await loadMapData();
    const feature = (state.data.homes.features || []).find(
      (item) => item.properties.listing_id === created.properties?.listing_id,
    );
    if (feature) {
      state.selected = { type: "home", feature };
      renderHomeDetails(feature);
    }
  } catch (error) {
    status.textContent = error.message || "Could not add home.";
  } finally {
    button.disabled = false;
  }
}

async function saveHomeFeedback(props) {
  await api(`/api/favorites/${props.listing_id}/feedback`, {
    method: "POST",
    body: {
      rating: document.getElementById("homeRating").value,
      notes: document.getElementById("homeNotes").value,
    },
  });
  await loadMapData();
}

async function deleteHome(props) {
  const confirmed = window.confirm(`Delete ${props.address || "this home"} from favorites?`);
  if (!confirmed) return;
  await api(`/api/homes/${props.listing_id}`, { method: "DELETE" });
  state.selected = null;
  await loadMapData();
}

async function createAreaAroundHome(feature) {
  if (!feature.geometry?.coordinates) {
    window.alert("Add a map location before creating a pocket around this home.");
    return;
  }
  const coords = feature.geometry.coordinates;
  await api("/api/neighborhoods", {
    method: "POST",
    body: {
      name: `Pocket near ${feature.properties.address || "home"}`,
      rating: "maybe",
      notes: "Created around a favorited home. Edit after touring.",
      tags: ["needs_more_research", "tour_again"],
      geometry: circlePolygon(coords[0], coords[1], 0.15),
    },
  });
  state.selected = null;
  await loadMapData();
}

async function saveQuickNote() {
  const title = document.getElementById("noteTitle").value.trim();
  const body = document.getElementById("noteBody").value.trim();
  if (!title && !body) return;
  const payload = { title, body, note_type: "tour_observation" };
  if (state.selected?.type === "home") {
    payload.related_property_id = state.selected.feature.properties.property_id;
    const coords = state.selected.feature.geometry?.coordinates;
    if (coords) {
      payload.lon = coords[0];
      payload.lat = coords[1];
    }
  } else if (state.selected?.type === "neighborhood") {
    payload.related_neighborhood_id = state.selected.feature.properties.id;
    payload.geometry = state.selected.feature.geometry;
  } else if (state.lastClickLatLng) {
    payload.lat = state.lastClickLatLng.lat;
    payload.lon = state.lastClickLatLng.lng;
  }
  try {
    await api("/api/map-notes", { method: "POST", body: payload });
  } catch (error) {
    queueOfflineNote(payload);
    window.alert("Saved this note on this phone. It will sync when the app is online.");
  }
  document.getElementById("noteTitle").value = "";
  document.getElementById("noteBody").value = "";
  await loadMapData();
}

function queueOfflineNote(payload) {
  const queued = JSON.parse(localStorage.getItem("homeanalyzeQueuedNotes") || "[]");
  queued.push({ payload, queued_at: new Date().toISOString() });
  localStorage.setItem("homeanalyzeQueuedNotes", JSON.stringify(queued));
}

async function flushQueuedNotes() {
  const queued = JSON.parse(localStorage.getItem("homeanalyzeQueuedNotes") || "[]");
  if (!queued.length || !navigator.onLine) return;
  const remaining = [];
  for (const item of queued) {
    try {
      await api("/api/map-notes", { method: "POST", body: item.payload });
    } catch (_error) {
      remaining.push(item);
    }
  }
  localStorage.setItem("homeanalyzeQueuedNotes", JSON.stringify(remaining));
}

async function ensureLazyLayerLoaded(layerName) {
  if (layerName === "schoolZones" && !state.lazyData.schoolZones) {
    state.lazyData.schoolZones = await api("/api/school-zones");
    renderSchoolZoneLayer(state.lazyData.schoolZones);
  }
  if (layerName === "schoolLocations" && !state.lazyData.schoolLocations) {
    state.lazyData.schoolLocations = await api("/api/school-locations");
    renderSchoolLocationLayer(state.lazyData.schoolLocations);
  }
  if (layerName === "parksTrails" && !state.lazyData.parksTrails) {
    state.lazyData.parksTrails = await api("/api/parks-trails-playgrounds");
    renderParksTrailsLayer(state.lazyData.parksTrails);
  }
}

function beginHighlightMode(mode) {
  clearHighlightMode();
  state.pendingHighlightMode = mode;
  const drawOptions = {
    shapeOptions: highlightStyle({ highlight_type: mode, sentiment: sentimentForHighlightType(mode) }),
  };
  if (mode.includes("street")) {
    state.activeDrawHandler = new L.Draw.Polyline(state.map, drawOptions);
  } else {
    state.activeDrawHandler = new L.Draw.Polygon(state.map, {
      ...drawOptions,
      allowIntersection: false,
      showArea: true,
    });
  }
  state.activeDrawHandler.enable();
  document.querySelectorAll("[data-highlight-mode]").forEach((button) => {
    button.classList.toggle("active", button.dataset.highlightMode === mode);
  });
  document.getElementById("highlightModeHint").textContent =
    mode.includes("street")
      ? "Click along the street segment, then finish the line to save notes."
      : "Click around the pocket boundary, then finish the polygon to save notes.";
}

function clearHighlightMode() {
  if (state.activeDrawHandler) {
    state.activeDrawHandler.disable();
    state.activeDrawHandler = null;
  }
  state.pendingHighlightMode = null;
  document.querySelectorAll("[data-highlight-mode]").forEach((button) => button.classList.remove("active"));
  const hint = document.getElementById("highlightModeHint");
  if (hint) hint.textContent = "Draw exact streets or pockets you liked or want to avoid.";
}

function layerToGeometry(layer) {
  if (layer instanceof L.Circle) {
    const center = layer.getLatLng();
    return circlePolygon(center.lng, center.lat, layer.getRadius() / 1609.344);
  }
  return layer.toGeoJSON().geometry;
}

function circlePolygon(lon, lat, radiusMiles, sides = 48) {
  const points = [];
  const milesPerLat = 69.0;
  const milesPerLon = Math.max(0.0001, 69.0 * Math.cos((lat * Math.PI) / 180));
  for (let i = 0; i < sides; i += 1) {
    const angle = (2 * Math.PI * i) / sides;
    points.push([lon + (Math.cos(angle) * radiusMiles) / milesPerLon, lat + (Math.sin(angle) * radiusMiles) / milesPerLat]);
  }
  points.push(points[0]);
  return { type: "Polygon", coordinates: [points] };
}

function neighborhoodStyle(rating) {
  const active = false;
  return neighborhoodStyleForState(rating, active);
}

function neighborhoodStyleForState(rating, active) {
  const colors = {
    favorite: "#27615a",
    strong_like: "#2f5f92",
    like: "#55936e",
    maybe: "#9a6a16",
    avoid: "#a83b32",
  };
  return {
    color: colors[rating] || "#27615a",
    weight: active ? 5 : 2,
    fillColor: colors[rating] || "#27615a",
    fillOpacity: active ? 0.32 : 0.18,
    opacity: active ? 1 : 0.9,
  };
}

function schoolZoneStyle(feature) {
  const active = feature?.properties?.id === state.activeSchoolZoneId;
  return active
    ? {
        color: "#174ea6",
        weight: 4,
        fillColor: "#4f8df7",
        fillOpacity: 0.38,
        opacity: 1,
      }
    : {
        color: "#2f5f92",
        weight: 1,
        fillColor: "#7aa6c8",
        fillOpacity: 0.11,
        opacity: 0.82,
      };
}

function setActiveSchoolZone(zoneId) {
  state.activeSchoolZoneId = zoneId == null ? null : Number(zoneId);
  applySchoolZoneHighlight();
}

function setActiveNeighborhood(neighborhoodId) {
  state.activeNeighborhoodId = neighborhoodId == null ? null : Number(neighborhoodId);
  applyNeighborhoodHighlight();
}

function applyNeighborhoodHighlight() {
  state.areaLayers.forEach((layer) => {
    if (!layer.setStyle) return;
    const props = layer.featureProperties || {};
    const active = props.id === state.activeNeighborhoodId;
    layer.setStyle(neighborhoodStyleForState(props.rating, active));
    if (active && layer.bringToFront) layer.bringToFront();
  });
}

function applySchoolZoneHighlight() {
  state.schoolZoneLayers.forEach((layer) => {
    if (layer.setStyle && layer.feature) {
      layer.setStyle(schoolZoneStyle(layer.feature));
      if (layer.feature.properties.id === state.activeSchoolZoneId && layer.bringToFront) {
        layer.bringToFront();
      }
    }
  });
}

function highlightStyle(props = {}) {
  const type = props.highlight_type || "tour_note";
  const sentiment = props.sentiment || sentimentForHighlightType(type);
  const geometryIsStreet = type.includes("street");
  const color = sentiment === "avoid" ? "#a83b32" : sentiment === "favorite" ? "#27615a" : "#2f5f92";
  const fillColor = sentiment === "avoid" ? "#d65a4a" : sentiment === "favorite" ? "#3d8a7e" : "#5f99d6";
  return {
    color,
    weight: geometryIsStreet ? 6 : 3,
    opacity: 0.92,
    fillColor,
    fillOpacity: geometryIsStreet ? 0 : sentiment === "avoid" ? 0.26 : 0.22,
    dashArray: sentiment === "avoid" ? "8 6" : "",
  };
}

function homeColor(rating) {
  return {
    strong_like: "#27615a",
    like: "#55936e",
    maybe: "#9a6a16",
    dislike: "#a83b32",
    rejected: "#6f6f6f",
    reject: "#6f6f6f",
  }[rating] || "#2f5f92";
}

function parksColor(category) {
  return {
    park: "#55936e",
    playground: "#9a6a16",
    trail: "#2f5f92",
    nature_reserve: "#27615a",
  }[category] || "#64706a";
}

function matchTag(match) {
  const label = match.relation === "near" && match.distance_miles != null
    ? `${match.name}: ${match.distance_miles.toFixed(2)} mi`
    : `${match.relation.replace("_", " ")}: ${match.name}`;
  return `<span class="tag">${escapeHtml(label)}</span>`;
}

function highlightTag(highlight) {
  const label = highlight.distance_miles != null
    ? `${highlight.sentiment}: ${highlight.name} (${highlight.distance_miles.toFixed(2)} mi)`
    : `${highlight.sentiment}: ${highlight.name}`;
  return `<span class="tag">${escapeHtml(label)}</span>`;
}

function schoolZoneCardHtml(zone, heading) {
  const schoolName = zone.school_name || zone.name || "No imported zone found";
  const district = zone.district_name || "District unknown";
  const year = zone.school_year || "year unknown";
  const boundary = zone.boundary_distance_miles == null
    ? ""
    : `<span>${zone.boundary_distance_miles.toFixed(2)} mi from boundary</span>`;
  const schoolLocation = zone.school_location
    ? `${zone.school_location.name || ""}${zone.school_location.address ? `, ${zone.school_location.address}` : ""}`
    : "School location not imported or matched yet";
  return `
    <h3>${escapeHtml(heading)}</h3>
    <div class="school-zone-card">
      <div>
        <span class="school-label">School</span>
        <strong>${escapeHtml(schoolName)}</strong>
      </div>
      <div class="school-meta-grid">
        <span>${escapeHtml(district)}</span>
        <span>${escapeHtml(year)}</span>
        ${boundary}
      </div>
      <p class="meta">Location: ${escapeHtml(schoolLocation)}</p>
      ${schoolRankingsHtml(zone.ranking_statuses || zone.academic_profiles || [])}
      <div class="warning">${escapeHtml(zone.warning || "Verify school assignment directly with the district before relying.")}</div>
    </div>
  `;
}

function schoolRankingsHtml(rankings) {
  if (!rankings?.length) {
    return `<div class="school-ranking-list"><div class="school-ranking-row muted"><strong>Rankings</strong><span>Ranking not imported</span></div></div>`;
  }
  return `<div class="school-ranking-list">${rankings.map((profile) => {
    const isRanked = !profile.status || profile.status === "ranked";
    const parts = [
      profile.state_rank ? `#${profile.state_rank}` : "",
      profile.rating_label || "",
      profile.student_teacher_ratio ? `${profile.student_teacher_ratio}:1` : "",
      profile.math_proficiency ? `Math ${profile.math_proficiency}%` : "",
      profile.reading_proficiency ? `Read ${profile.reading_proficiency}%` : "",
    ].filter(Boolean);
    const detail = isRanked
      ? parts.join(" ") || profile.display_label || "Ranked"
      : profile.display_label || "Not ranked";
    const source = escapeHtml(profile.source_name || "Source");
    const detailHtml = escapeHtml(detail);
    const className = isRanked ? "school-ranking-row ranked" : "school-ranking-row muted";
    const sourceHtml = profile.source_url
      ? `<a href="${escapeHtml(profile.source_url)}" target="_blank" rel="noopener">${source}</a>`
      : `<strong>${source}</strong>`;
    return `<div class="${className}">${sourceHtml}<span>${detailHtml}</span></div>`;
  }).join("")}</div>`;
}

function schoolTooltipRank(props) {
  if (props.niche_rank) return ` Niche #${props.niche_rank}`;
  if (props.us_news_rank) return ` U.S. News #${props.us_news_rank}`;
  const notRanked = (props.ranking_statuses || []).some((ranking) => ranking.status === "not_ranked");
  return notRanked ? " not ranked" : "";
}

function sentimentForHighlightType(type) {
  if ((type || "").startsWith("avoid")) return "avoid";
  if ((type || "").startsWith("liked")) return "like";
  return "maybe";
}

function defaultHighlightName(type) {
  return {
    liked_area: "Liked pocket",
    avoid_area: "Avoid pocket",
    liked_street: "Liked street",
    avoid_street: "Avoid street",
    question_area: "Needs more research",
    tour_note: "Tour highlight",
  }[type] || "Map highlight";
}

function defaultHighlightTags(type) {
  return {
    liked_area: ["favorite_pocket", "quiet_street"],
    avoid_area: ["feels_too_busy", "road_noise"],
    liked_street: ["quiet_street", "mature_trees"],
    avoid_street: ["feels_too_busy", "road_noise"],
    question_area: ["needs_more_research", "tour_again"],
    tour_note: ["tour_again"],
  }[type] || [];
}

function metric(label, value) {
  return `<div class="metric"><strong>${escapeHtml(value ?? "Unknown")}</strong><span>${escapeHtml(label)}</span></div>`;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    method: options.method || "GET",
    headers: { "Content-Type": "application/json" },
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || `Request failed: ${response.status}`);
  }
  return payload;
}

function formatMoney(value) {
  if (value == null || Number.isNaN(Number(value))) return "Unknown";
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(Number(value));
}

function formatNumber(value) {
  if (value == null || Number.isNaN(Number(value))) return "Unknown";
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 }).format(Number(value));
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
