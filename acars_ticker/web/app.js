/* acars-ticker front-end — live map + message feed. Author: iceo96 */
(() => {
  "use strict";

  const BUCKETS = ["weather", "ops", "mechanical", "ATC", "misc"];
  const BCOLOR = { weather: "#4dd0e1", ops: "#81c784", mechanical: "#ff8a65", ATC: "#ba68c8", misc: "#607d8b" };
  const MAX_ROWS = 400;

  const state = {
    rows: new Map(),            // id -> message row
    aircraft: new Map(),        // tail -> { ...latest, marker }  (ACARS talkers)
    traffic: new Map(),         // hex  -> { marker, ... }        (all nearby ADS-B)
    trafficLayer: null,
    acarsRegs: new Set(),       // registrations that have sent ACARS
    showTraffic: true,
    filters: { bucket: new Set(), airline: new Set(), freq: new Set() },
    quiet: false,
    seenAirlines: new Map(),
    seenFreqs: new Set(),
    map: null,
    homeMarker: null,
    youMarker: null,
    home: { lat: 31.962958, lon: 35.812154, zoom: 8 },
    fitted: false,       // auto-fit to planes once, until the user pans
    userMoved: false,
  };

  const $ = (s) => document.querySelector(s);
  const feedBody = $("#feed-body");

  // ---------- map ----------
  function initMap() {
    state.map = L.map("map", { attributionControl: false, zoomControl: true, worldCopyJump: true });
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 13, attribution: "&copy; OSM",
    }).addTo(state.map);
    state.map.setView([state.home.lat, state.home.lon], state.home.zoom);

    const homeIcon = L.divIcon({ className: "", html: '<div class="home-ico">🏠</div>', iconSize: [20, 20] });
    state.homeMarker = L.marker([state.home.lat, state.home.lon], { icon: homeIcon, zIndexOffset: -500 })
      .addTo(state.map).bindPopup("home / receiver");

    state.trafficLayer = L.layerGroup().addTo(state.map);

    // Once the user drags/zooms, stop auto-fitting so we don't yank the view.
    state.map.on("dragstart zoomstart", () => { state.userMoved = true; });
  }

  // ---------- live traffic (all nearby aircraft, FR24-style) ----------
  function trafficPopup(a) {
    const isAcars = a.registration && state.acarsRegs.has(a.registration.toUpperCase());
    const flight = (a.callsign || "").trim();
    const line2 = [a.icao_type, a.registration].filter(Boolean).join(" · ");
    const alt = a.alt != null ? `${Math.round(a.alt)} ft` : "";
    const gs = a.gs != null ? ` · ${Math.round(a.gs)} kt` : "";
    const thread = isAcars
      ? `<div class="pop-link" data-tail="${esc(a.registration.toUpperCase())}">open ACARS thread →</div>`
      : `<div style="color:var(--dim);margin-top:4px">${isAcars ? "" : "no ACARS yet"}</div>`;
    return `<div class="pop-reg">${esc(flight || a.registration || a.hex || "unknown")}</div>
      <div style="color:var(--dim)">${esc(line2)}</div>
      <div style="color:var(--dim)">${esc(alt + gs)}</div>${thread}`;
  }

  async function refreshTraffic() {
    if (!state.showTraffic || !state.map) return;
    try {
      const url = `/api/traffic?lat=${state.home.lat}&lon=${state.home.lon}`;
      const data = await (await fetch(url)).json();
      if (!data.enabled) return;
      const seen = new Set();
      for (const a of data.aircraft || []) {
        const key = a.hex || a.registration || (a.lat + "," + a.lon);
        seen.add(key);
        const isAcars = a.registration && state.acarsRegs.has(a.registration.toUpperCase());
        const color = isAcars ? "#06e0ff" : "#f5c542";
        const icon = planeSvgIcon(color, a.track, isAcars ? 22 : 16, isAcars);
        let t = state.traffic.get(key);
        if (t && t.marker) {
          t.marker.setLatLng([a.lat, a.lon]);
          t.marker.setIcon(icon);
          if (!t.marker.isPopupOpen()) t.marker.setPopupContent(trafficPopup(a));
        } else {
          const marker = L.marker([a.lat, a.lon], { icon, zIndexOffset: isAcars ? 400 : 0 })
            .bindPopup(trafficPopup(a)).addTo(state.trafficLayer);
          state.traffic.set(key, { marker });
        }
      }
      // Drop aircraft that left the area.
      for (const [key, t] of state.traffic) {
        if (!seen.has(key)) { state.trafficLayer.removeLayer(t.marker); state.traffic.delete(key); }
      }
      updateCount();
    } catch (_) { /* ignore */ }
  }

  function updateCount() {
    $("#ac-count").textContent = state.traffic.size || state.aircraft.size;
  }

  function fitToPlanes() {
    const pts = [];
    for (const ac of state.aircraft.values()) {
      if (ac.marker) pts.push(ac.marker.getLatLng());
    }
    if (!pts.length) return;
    pts.push([state.home.lat, state.home.lon]);
    state.map.fitBounds(L.latLngBounds(pts).pad(0.2), { maxZoom: 9 });
  }

  async function loadConfig() {
    try {
      const c = await (await fetch("/api/config")).json();
      if (c.home) state.home = c.home;
      if (c.show_traffic === false) state.showTraffic = false;
      $("#rx-detail").dataset.mode = c.mode || "";
      initMap();
      if (c.geolocate) tryGeolocate();
    } catch (_) { initMap(); }
  }

  function tryGeolocate() {
    if (!navigator.geolocation) return;
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        const { latitude: lat, longitude: lon } = pos.coords;
        placeYou(lat, lon);
        state.map.setView([lat, lon], Math.max(state.home.zoom, 8));
        refreshTraffic();
      },
      () => { /* denied / unavailable — keep config home */ },
      { enableHighAccuracy: false, timeout: 8000, maximumAge: 600000 }
    );
  }

  function placeYou(lat, lon) {
    const icon = L.divIcon({ className: "", html: '<div class="home-ico">📍</div>', iconSize: [20, 20] });
    if (state.youMarker) state.map.removeLayer(state.youMarker);
    state.youMarker = L.marker([lat, lon], { icon }).addTo(state.map).bindPopup("you are here");
    state.home = { lat, lon, zoom: Math.max(state.home.zoom, 8) };
  }

  $("#recenter").addEventListener("click", () => {
    state.userMoved = true;
    state.map.setView([state.home.lat, state.home.lon], state.home.zoom);
    tryGeolocate();
  });

  // A plane silhouette pointing north (0°), rotated to the aircraft's track.
  function planeSvgIcon(color, track, size, ring) {
    const rot = (track == null ? 0 : track);
    const s = size || 18;
    const stroke = ring ? '#06e0ff' : '#000';
    const sw = ring ? 1.4 : 0.6;
    const html =
      `<svg width="${s}" height="${s}" viewBox="0 0 24 24" ` +
      `style="transform:rotate(${rot}deg);overflow:visible">` +
      `<path d="M12 1.5 L13.4 10.5 L22 14.5 L22 16.5 L13.4 13.8 L12.9 19.5 L15.5 21.5 L15.5 22.5 ` +
      `L12 21.3 L8.5 22.5 L8.5 21.5 L11.1 19.5 L10.6 13.8 L2 16.5 L2 14.5 L10.6 10.5 Z" ` +
      `fill="${color}" stroke="${stroke}" stroke-width="${sw}" stroke-linejoin="round"/></svg>`;
    return L.divIcon({ className: "", html, iconSize: [s, s], iconAnchor: [s / 2, s / 2] });
  }

  function planeIcon(d) {
    const track = (d.meta && d.meta.track != null) ? d.meta.track : null;
    return planeSvgIcon(d.airline_color || "#06e0ff", track, 20, true);
  }

  function popupHtml(d) {
    const type = d.icao_type ? ` · ${esc(d.icao_type)}` : "";
    const al = d.airline_name ? `<div style="color:${d.airline_color}">${esc(d.airline_name)}${type}</div>` : "";
    const m = d.meta || {};
    const alt = m.alt != null ? ` · ${Math.round(m.alt)} ft` : "";
    return `<div class="pop-reg">${esc(d.tail)}</div>${al}
      <div style="color:var(--dim)">${d.freq ? d.freq.toFixed(3) + " MHz" : ""}${alt}
        <span class="tag ${d.bucket}" style="font-size:8px">${d.bucket}</span></div>
      <div class="pop-msg">${esc(d.text || "")}</div>
      <div class="pop-link" data-tail="${esc(d.tail)}">open thread →</div>`;
  }

  function upsertMarker(d) {
    if (d.tail) state.acarsRegs.add(d.tail.toUpperCase());
    const m = d.meta || {};
    if (m.lat == null || m.lon == null || !d.tail) return;
    let ac = state.aircraft.get(d.tail);
    if (ac && ac.marker) {
      ac.marker.setLatLng([m.lat, m.lon]);
      ac.marker.setIcon(planeIcon(d));
      if (ac.marker.getPopup() && !ac.marker.isPopupOpen()) {
        ac.marker.setPopupContent(popupHtml(d));
      }
      state.aircraft.set(d.tail, { ...ac, ...d });
    } else {
      const marker = L.marker([m.lat, m.lon], { icon: planeIcon(d) })
        .addTo(state.map).bindPopup(popupHtml(d));
      state.aircraft.set(d.tail, { ...d, marker });
    }
    updateCount();
  }

  async function refreshAircraft() {
    try {
      const data = await (await fetch("/api/aircraft")).json();
      (data.aircraft || []).forEach(upsertMarker);
      // With the live traffic layer on, the map already sits over your home
      // area full of planes, so don't yank the view out to a distant ACARS
      // aircraft. Only auto-frame ACARS planes when traffic is disabled.
      if (!state.showTraffic && !state.fitted && !state.userMoved && state.aircraft.size) {
        fitToPlanes();
        state.fitted = true;
      }
    } catch (_) { /* ignore */ }
  }

  // ---------- WebSocket ----------
  let ws, reconnectTimer;
  function connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws`);
    ws.onmessage = (ev) => handle(JSON.parse(ev.data));
    ws.onclose = () => {
      setBanner("connection lost — reconnecting…", true);
      clearTimeout(reconnectTimer);
      reconnectTimer = setTimeout(connect, 2000);
    };
    ws.onopen = () => setBanner(null);
  }

  function handle(msg) {
    if (msg.type === "message") { addRow(msg.data, true); upsertMarker(msg.data); }
    else if (msg.type === "backfill") msg.data.forEach((d) => { addRow(d, false); upsertMarker(d); });
    else if (msg.type === "enrich") applyEnrich(msg.data);
    else if (msg.type === "status") setStatus(msg.data);
  }

  // ---------- status / banner ----------
  function setStatus(s) {
    const dot = $("#rx-dot");
    dot.className = "dot " + (s.state === "up" || s.state === "replay" ? s.state
      : s.state === "down" ? "down" : s.state === "retry" ? "retry" : "");
    $("#rx-detail").textContent = s.detail || s.state;
    if (s.state === "down") setBanner("RECEIVER DOWN — " + (s.detail || ""), true);
    else if (s.state === "retry") setBanner("receiver reconnecting — " + (s.detail || ""), true);
    else setBanner(null);
  }
  function setBanner(text, show) {
    const b = $("#banner");
    if (!show || !text) { b.classList.add("hidden"); return; }
    b.textContent = text; b.classList.remove("hidden");
  }

  // ---------- feed rows ----------
  const fmtTime = (ts) => new Date(ts * 1000).toTimeString().slice(0, 8);

  function addRow(d, isNew) {
    if (state.rows.has(d.id)) { state.rows.set(d.id, { ...state.rows.get(d.id), ...d }); return; }
    state.rows.set(d.id, d);
    registerFacets(d);

    const tr = document.createElement("tr");
    tr.dataset.id = d.id;
    tr.className = (isNew ? "row-new " : "") + (securityTag(d).hot ? "sec-hot" : "");
    tr.innerHTML = rowHtml(d);
    tr.addEventListener("click", () => openPanel(d.tail));
    tr.addEventListener("mouseenter", (e) => showTooltip(e, d));
    tr.addEventListener("mousemove", moveTooltip);
    tr.addEventListener("mouseleave", hideTooltip);

    feedBody.insertBefore(tr, feedBody.firstChild);
    applyFilterTo(tr, d);

    while (feedBody.children.length > MAX_ROWS) {
      const last = feedBody.lastChild;
      state.rows.delete(Number(last.dataset.id));
      feedBody.removeChild(last);
    }
    $("#empty").classList.add("hidden");
  }

  function acCell(d) {
    return d.icao_type
      ? `<span class="type">${esc(d.icao_type)}</span> ${esc(d.airline_name || "")}`
      : esc(d.airline_name || d.airline_code || "");
  }

  // Security lens: what does this plaintext message leak to anyone listening?
  const POS_RE = /\b(N\d{2,4}|S\d{2,4}|[EW]\d{3,5}|FL\d{2,3}|POS\b|\d{2,3}\.\d{2,})/;
  function securityTag(d) {
    const t = d.text || "";
    if (d.bucket === "ATC" || POS_RE.test(t)) return { label: "POSITION", cls: "position", hot: true };
    if (d.bucket === "ops") return { label: "OPS INTEL", cls: "ops", hot: true };
    if (d.bucket === "mechanical") return { label: "TECH", cls: "tech", hot: false };
    if (d.tail) return { label: "IDENTITY", cls: "identity", hot: false };
    return { label: "—", cls: "benign", hot: false };
  }

  function rowHtml(d) {
    const sec = securityTag(d);
    const secTitle = "Plaintext ACARS — anyone listening learns: " + sec.label.toLowerCase();
    return `
      <td class="c-time">${fmtTime(d.ts)}</td>
      <td class="c-reg">${esc(d.tail || "—")}</td>
      <td class="c-ac" style="color:${d.airline_color}">${acCell(d)}</td>
      <td class="c-freq">${d.freq ? d.freq.toFixed(3) : "—"}</td>
      <td class="c-tag"><span class="tag ${d.bucket}">${d.bucket}</span></td>
      <td class="c-sec"><span class="sec-tag ${sec.cls}" title="${secTitle}">🔓 ${sec.label}</span></td>
      <td class="c-text">${esc(d.text || "")}</td>`;
  }

  function applyEnrich(e) {
    for (const [id, row] of state.rows) {
      if (row.tail === e.tail && !row.icao_type) {
        row.icao_type = e.meta.icao_type; row.meta = e.meta;
        if (e.meta.operator) row.airline_name = e.meta.operator;
        const el = feedBody.querySelector(`tr[data-id="${id}"] .c-ac`);
        if (el) el.innerHTML = acCell(row);
      }
    }
    // Enrichment may carry a fresh position -> put/refresh the marker.
    const ac = state.aircraft.get(e.tail) || {};
    upsertMarker({ ...ac, tail: e.tail, meta: e.meta,
      icao_type: e.meta.icao_type,
      airline_name: e.meta.operator || ac.airline_name,
      airline_color: ac.airline_color, bucket: ac.bucket || "misc",
      freq: ac.freq, text: ac.text });
  }

  // ---------- facets / chips ----------
  function registerFacets(d) {
    if (d.airline_code) {
      const name = d.airline_name || d.airline_code;
      if (state.seenAirlines.get(d.airline_code) !== name) {
        state.seenAirlines.set(d.airline_code, name);
        renderChips("airline");
      }
    }
    if (d.freq && !state.seenFreqs.has(d.freq.toFixed(3))) {
      state.seenFreqs.add(d.freq.toFixed(3));
      renderChips("freq");
    }
  }
  function renderBucketChips() {
    const g = $("#bucket-chips"); g.innerHTML = "";
    BUCKETS.forEach((b) => g.appendChild(makeChip("bucket", b, b)));
  }
  function renderChips(kind) {
    if (kind === "airline") {
      const g = $("#airline-chips"); g.innerHTML = "";
      [...state.seenAirlines.entries()].sort().forEach(([code, name]) =>
        g.appendChild(makeChip("airline", code, code, name)));
    } else if (kind === "freq") {
      const g = $("#freq-chips"); g.innerHTML = "";
      [...state.seenFreqs].sort().forEach((f) => g.appendChild(makeChip("freq", f, f)));
    }
  }
  function makeChip(kind, value, label, title) {
    const c = document.createElement("span");
    c.className = `chip ${kind}` + (state.filters[kind].has(value) ? " active" : "");
    c.textContent = label;
    if (title) c.title = title;
    c.addEventListener("click", () => {
      const set = state.filters[kind];
      set.has(value) ? set.delete(value) : set.add(value);
      c.classList.toggle("active");
      applyAllFilters();
    });
    return c;
  }

  // ---------- filtering ----------
  function rowVisible(d) {
    if (state.quiet && d.bucket === "misc") return false;
    const f = state.filters;
    if (f.bucket.size && !f.bucket.has(d.bucket)) return false;
    if (f.airline.size && !f.airline.has(d.airline_code)) return false;
    if (f.freq.size && !f.freq.has(d.freq ? d.freq.toFixed(3) : "")) return false;
    return true;
  }
  function applyFilterTo(tr, d) { tr.style.display = rowVisible(d) ? "" : "none"; }
  function applyAllFilters() {
    for (const tr of feedBody.children) {
      const d = state.rows.get(Number(tr.dataset.id));
      if (d) applyFilterTo(tr, d);
    }
  }

  // ---------- tooltip ----------
  const tip = $("#tooltip");
  function showTooltip(e, d) {
    const m = d.meta || {};
    const pos = m.lat != null && m.lon != null
      ? `${m.lat.toFixed(3)}, ${m.lon.toFixed(3)}${m.alt != null ? " · " + Math.round(m.alt) + " ft" : ""}`
      : "no ADS-B position";
    tip.innerHTML = `
      <div class="photo">${d.icao_type ? esc(d.icao_type) + " — photo n/a" : "aircraft photo n/a"}</div>
      <div class="reg">${esc(d.tail || "unknown")}</div>
      <div>${esc(d.airline_name || d.airline_code || "")}${d.icao_type ? " · " + esc(d.icao_type) : ""}</div>
      <div style="color:var(--dim);margin-top:3px">${esc(pos)}</div>`;
    tip.classList.remove("hidden");
    moveTooltip(e);
  }
  function moveTooltip(e) {
    let x = e.clientX + 14, y = e.clientY + 14;
    if (x + 290 > innerWidth) x = e.clientX - 290;
    if (y + 160 > innerHeight) y = e.clientY - 160;
    tip.style.left = x + "px"; tip.style.top = y + "px";
  }
  function hideTooltip() { tip.classList.add("hidden"); }

  // ---------- thread panel ----------
  async function openPanel(tail) {
    if (!tail) return;
    $("#panel").classList.remove("hidden");
    $("#panel-reg").textContent = tail;

    const data = await (await fetch(`/api/thread/${encodeURIComponent(tail)}`)).json();
    const msgs = data.messages || [];
    const latest = msgs[msgs.length - 1] || {};
    const meta = latest.meta || {};
    $("#panel-type").textContent = [meta.icao_type, latest.airline_name].filter(Boolean).join(" · ");
    $("#panel-pos").textContent = (meta.lat != null && meta.lon != null)
      ? `position when last seen: ${meta.lat.toFixed(4)}, ${meta.lon.toFixed(4)}`
        + (meta.alt != null ? ` · ${Math.round(meta.alt)} ft` : "")
        + (meta.squawk ? ` · squawk ${meta.squawk}` : "")
      : "no ADS-B position available for this aircraft.";

    const t = $("#panel-thread"); t.innerHTML = "";
    [...msgs].reverse().forEach((m) => {
      const div = document.createElement("div");
      div.className = "thread-msg";
      div.innerHTML = `<div class="meta"><span>${fmtTime(m.ts)}</span>
        <span>${esc(m.label || "")}</span><span>${m.freq ? m.freq.toFixed(3) : ""}</span>
        <span class="tag ${m.bucket}" style="font-size:8px">${m.bucket}</span></div>
        <div class="body">${esc(m.text || "(no text)")}</div>`;
      t.appendChild(div);
    });

    // Fly the main map to this aircraft and open its marker popup.
    const ac = state.aircraft.get(tail);
    if (ac && ac.marker) {
      const ll = ac.marker.getLatLng();
      state.map.flyTo(ll, Math.max(state.map.getZoom(), 7), { duration: 0.6 });
      ac.marker.openPopup();
    }
  }
  $("#panel-close").addEventListener("click", () => $("#panel").classList.add("hidden"));

  // ---------- quiet + stats ----------
  $("#quiet-toggle").addEventListener("change", (e) => {
    state.quiet = e.target.checked; applyAllFilters();
  });

  async function pollStats() {
    try {
      const s = await (await fetch("/api/stats")).json();
      $("#st-rate").textContent = s.msgs_per_min ?? 0;
      $("#st-uniq").textContent = s.unique_aircraft ?? 0;
      $("#st-err").textContent = Math.round((s.error_rate || 0) * 100) + "%";
      renderChannels(s.channels || [], s.per_channel || {});
      renderDonut(s.buckets || {});
    } catch (_) { /* ignore */ }
  }
  function renderChannels(channels, per) {
    const wrap = $("#st-channels"); wrap.innerHTML = "";
    const counts = channels.map((c) => per[c] || per[Number(c)] || 0);
    const max = Math.max(1, ...counts);
    channels.forEach((c, i) => {
      const bar = document.createElement("div");
      bar.className = "ch-bar";
      bar.style.height = (4 + (counts[i] / max) * 28) + "px";
      bar.title = `${c} MHz — ${counts[i]} msgs/hr`;
      wrap.appendChild(bar);
    });
  }
  function renderDonut(buckets) {
    const total = Object.values(buckets).reduce((a, b) => a + b, 0);
    const svg = $("#donut"); svg.innerHTML = "";
    const leg = $("#donut-legend"); leg.innerHTML = "";
    const cx = 37, cy = 37, r = 28, w = 10;
    if (!total) {
      svg.innerHTML = `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="#1e2a35" stroke-width="${w}"/>`;
      leg.textContent = "no data"; return;
    }
    let a0 = -Math.PI / 2;
    BUCKETS.forEach((b) => {
      const v = buckets[b] || 0; if (!v) return;
      const frac = v / total, a1 = a0 + frac * 2 * Math.PI, large = frac > 0.5 ? 1 : 0;
      const x0 = cx + r * Math.cos(a0), y0 = cy + r * Math.sin(a0);
      const x1 = cx + r * Math.cos(a1), y1 = cy + r * Math.sin(a1);
      const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
      p.setAttribute("d", `M ${x0} ${y0} A ${r} ${r} 0 ${large} 1 ${x1} ${y1}`);
      p.setAttribute("fill", "none"); p.setAttribute("stroke", BCOLOR[b]); p.setAttribute("stroke-width", w);
      svg.appendChild(p); a0 = a1;
      leg.innerHTML += `<div><i style="background:${BCOLOR[b]}"></i>${b} ${Math.round(frac * 100)}%</div>`;
    });
  }

  // ---------- utils ----------
  function esc(s) {
    return String(s ?? "").replace(/[&<>"]/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }

  // Delegated: "open thread →" links inside map popups survive content refreshes.
  document.addEventListener("click", (e) => {
    const link = e.target.closest && e.target.closest(".pop-link");
    if (link && link.dataset.tail) openPanel(link.dataset.tail);
  });

  // ---------- intro splash ----------
  function dismissIntro() {
    const el = $("#intro");
    if (el && !el.classList.contains("gone")) {
      el.classList.add("gone");
      setTimeout(() => el.remove(), 600);
      setTimeout(() => state.map && state.map.invalidateSize(), 650);
    }
  }
  $("#intro").addEventListener("click", dismissIntro);
  $("#intro-start").addEventListener("click", (e) => { e.stopPropagation(); dismissIntro(); });
  setTimeout(dismissIntro, 8000);  // auto-advance if they just start talking

  // ---------- security lens ----------
  $("#seclens-toggle").addEventListener("change", (e) => {
    document.body.classList.toggle("seclens", e.target.checked);
  });

  // ---------- presentation / fullscreen ----------
  function toggleFullscreen() {
    if (!document.fullscreenElement) document.documentElement.requestFullscreen?.();
    else document.exitFullscreen?.();
  }
  function togglePresent() {
    document.body.classList.toggle("present");
    setTimeout(() => state.map && state.map.invalidateSize(), 50);
  }
  $("#present-btn").addEventListener("click", togglePresent);
  document.addEventListener("keydown", (e) => {
    if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;
    const k = e.key.toLowerCase();
    const intro = $("#intro");
    if (intro && !intro.classList.contains("gone")) { dismissIntro(); return; }
    if (k === "f") toggleFullscreen();
    else if (k === "p") togglePresent();
    else if (k === "s") $("#seclens-toggle").click();
  });

  // ---------- boot ----------
  renderBucketChips();
  loadConfig().then(() => {
    refreshAircraft(); setInterval(refreshAircraft, 5000);
    refreshTraffic(); setInterval(refreshTraffic, 12000);
  });
  connect();
  pollStats();
  setInterval(pollStats, 5000);
})();
