/* =========================================================================
   terragen-web · UI: hartă → selectezi zona → generezi → deschizi viewer-ul 3D
   ========================================================================= */
const $ = (id) => document.getElementById(id);
const state = { cfg: null, zone: null, job: null, selecting: false, es: null, activeProjectId: null };

/* ------------------------------------------------------------------ harta */
const HOME = [24.5612, 46.5446];
const RASTER = {
  osm: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
  sat: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
  dem: 'https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png',
};

function buildStyle(satellite) {
  const layers = satellite
    ? [{ id: 'sat', type: 'raster', source: 'sat', paint: { 'raster-brightness-max': 0.92 } }]
    : [{ id: 'osm', type: 'raster', source: 'osm', paint: { 'raster-saturation': -0.35, 'raster-brightness-max': 0.96 } }];
  return {
    version: 8,
    sources: {
      sat: { type: 'raster', tiles: [RASTER.sat], tileSize: 256, maxzoom: 19,
             attribution: 'Imagery © Esri' },
      osm: { type: 'raster', tiles: [RASTER.osm], tileSize: 256, maxzoom: 19,
             attribution: '© OpenStreetMap contributors' },
      dem: { type: 'raster-dem', tiles: [RASTER.dem], tileSize: 256, encoding: 'terrarium',
             maxzoom: 15 },
    },
    layers,
  };
}

const map = new maplibregl.Map({
  container: 'map',
  style: buildStyle(false),
  center: HOME, zoom: 15.5, pitch: 62, bearing: -20,
  maxPitch: 85, antialias: true, attributionControl: false,
  hash: false,
});
map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), 'bottom-right');
map.addControl(new maplibregl.ScaleControl({ maxWidth: 110, unit: 'metric' }), 'bottom-left');
map.addControl(new maplibregl.AttributionControl({ compact: true }), 'bottom-right');

let satOn = false, terrainOn = true;

function applyTerrain() {
  try {
    if (terrainOn && map.getSource('dem')) map.setTerrain({ source: 'dem', exaggeration: 1.35 });
    else map.setTerrain(null);
  } catch (e) { console.warn('terrain', e); }
}

function setupSelectionLayers() {
  if (!map.getSource('sel')) map.addSource('sel', { type: 'geojson', data: emptyFC() });
  if (!map.getLayer('sel-fill')) {
    map.addLayer({ id: 'sel-fill', type: 'fill', source: 'sel',
      paint: { 'fill-color': '#35d0a5', 'fill-opacity': 0.18 } });
  }
  if (!map.getLayer('sel-line')) {
    map.addLayer({ id: 'sel-line', type: 'line', source: 'sel',
      paint: { 'line-color': '#35d0a5', 'line-width': 2.2, 'line-dasharray': [3, 1.6] } });
  }
  redrawSelection();
}

const emptyFC = () => ({ type: 'FeatureCollection', features: [] });

map.on('load', () => {
  $('loading').classList.add('hidden');
  applyTerrain();
  setupSelectionLayers();
});
map.on('style.load', () => { applyTerrain(); setupSelectionLayers(); });

/* ------------------------------------- clădiri 3D din extractul PBF local */
let buildingData = { type: 'FeatureCollection', features: [] };
let buildingTimer = null;
let buildingRequest = 0;
let buildingKey = null;
let buildingNoticeShown = false;

function setupBuildingLayer() {
  if (!map.getSource('local-buildings')) {
    map.addSource('local-buildings', { type: 'geojson', data: buildingData });
  }
  if (!map.getLayer('local-buildings-3d')) {
    map.addLayer({
      id: 'local-buildings-3d', type: 'fill-extrusion', source: 'local-buildings',
      minzoom: 14.5,
      paint: {
        'fill-extrusion-color': ['to-color', ['get', 'color'], '#c9b8a4'],
        'fill-extrusion-height': ['get', 'height'],
        'fill-extrusion-base': ['coalesce', ['get', 'base'], 0],
        'fill-extrusion-opacity': 0.92,
      },
    });
  }
}

function scheduleBuildings(delay = 900) {
  clearTimeout(buildingTimer);
  // Rezultatele anterioare nu trebuie să suprascrie centrul curent.
  const requestId = ++buildingRequest;
  if (map.getZoom() < 14.5) {
    buildingKey = null;
    return;
  }
  const center = map.getCenter();
  const key = `${center.lat.toFixed(2)},${center.lng.toFixed(2)}`;
  if (key === buildingKey) return;
  buildingTimer = setTimeout(async () => {
    if (!buildingNoticeShown) {
      buildingNoticeShown = true;
      toast('Se citesc clădirile din PBF. Prima citire poate dura câteva minute.', 8000);
    }
    try {
      const url = `/api/map-buildings?lat=${encodeURIComponent(center.lat)}&lon=${encodeURIComponent(center.lng)}`;
      const response = await fetch(url);
      if (requestId !== buildingRequest) return;
      if (response.status === 429) { scheduleBuildings(3000); return; }
      const data = await response.json();
      if (!response.ok) { toast(data.error || 'Nu pot încărca clădirile', 6000); return; }
      buildingData = data;
      buildingKey = key;
      if (map.getSource('local-buildings')) map.getSource('local-buildings').setData(data);
      toast(`${data.features.length} clădiri încărcate în jurul centrului hărții`, 3500);
    } catch (error) {
      if (requestId === buildingRequest) toast('Nu pot încărca clădirile locale', 6000);
      console.error(error);
    }
  }, delay);
}

map.on('style.load', () => { setupBuildingLayer(); scheduleBuildings(); });
map.on('moveend', () => scheduleBuildings());

/* ------------------------------------------------------- selectarea zonei */
function squareRing(lon, lat, sizeM) {
  // zona generată e un pătrat (bbox metric), deci desenăm exact asta
  const dLat = (sizeM / 2) / 111320;
  const dLon = (sizeM / 2) / (111320 * Math.cos(lat * Math.PI / 180));
  return [
    [lon - dLon, lat - dLat], [lon + dLon, lat - dLat],
    [lon + dLon, lat + dLat], [lon - dLon, lat + dLat],
    [lon - dLon, lat - dLat],
  ];
}

function redrawSelection() {
  const src = map.getSource('sel');
  if (!src) return;
  if (!state.zone) { src.setData(emptyFC()); return; }
  src.setData({ type: 'FeatureCollection', features: [{
    type: 'Feature', properties: {},
    geometry: { type: 'Polygon', coordinates: [squareRing(state.zone.lon, state.zone.lat, state.zone.size)] },
  }] });
}

function setZone(lat, lon, size) {
  state.zone = { lat: +lat, lon: +lon, size: +size };
  $('lat').value = (+lat).toFixed(5);
  $('lon').value = (+lon).toFixed(5);
  if (size) $('size').value = Math.round(size);
  $('selInfo').innerHTML = `${Math.round(size)} × ${Math.round(size)} m · centru
    <b>${(+lat).toFixed(5)}, ${(+lon).toFixed(5)}</b>`;
  redrawSelection();
}

function setSelecting(on) {
  state.selecting = on;
  $('btnSelect').classList.toggle('active', on);
  $('btnSelect').textContent = on ? '✕ anulează selecția' : '▭ Selectează pe hartă';
  map.getCanvas().classList.toggle('selecting', on);
  if (on) { map.dragPan.disable(); map.boxZoom.disable(); }
  else { map.dragPan.enable(); map.boxZoom.enable(); }
}

let dragStart = null;

map.on('mousedown', (e) => { if (state.selecting) dragStart = e.lngLat; });
map.on('mousemove', (e) => {
  if (state.selecting && dragStart) {
    const lat = (e.lngLat.lat + dragStart.lat) / 2;
    const lon = (e.lngLat.lng + dragStart.lng) / 2;
    const dLat = Math.abs(e.lngLat.lat - dragStart.lat) * 111320;
    const dLon = Math.abs(e.lngLat.lng - dragStart.lng) * 111320 * Math.cos(lat * Math.PI / 180);
    const size = Math.max(200, Math.min(6000, Math.max(dLat, dLon)));
    setZone(lat, lon, size);
  }
});
map.on('mouseup', (e) => {
  if (state.selecting && dragStart) {
    const lat = (e.lngLat.lat + dragStart.lat) / 2;
    const lon = (e.lngLat.lng + dragStart.lng) / 2;
    const dLat = Math.abs(e.lngLat.lat - dragStart.lat) * 111320;
    const dLon = Math.abs(e.lngLat.lng - dragStart.lng) * 111320 * Math.cos(lat * Math.PI / 180);
    const size = Math.max(200, Math.min(6000, Math.max(dLat, dLon)));
    if (size > 260) setZone(lat, lon, size);
    dragStart = null;
    setSelecting(false);
  }
});

$('btnSelect').addEventListener('click', () => {
  if (!state.selecting && !state.zone) {
    const c = map.getCenter();
    setZone(c.lat, c.lng, +$('size').value || 1200);
  }
  setSelecting(!state.selecting);
});

['lat', 'lon', 'size'].forEach((id) => $(id).addEventListener('change', () => {
  const lat = parseFloat($('lat').value), lon = parseFloat($('lon').value);
  if (Number.isFinite(lat) && Number.isFinite(lon)) {
    setZone(lat, lon, parseFloat($('size').value) || 1200);
    map.easeTo({ center: [lon, lat], duration: 500 });
  }
}));

/* -------------------------------------------------- altitudinea sub cursor */
let elevTimer = null, elevPending = null;
map.on('mousemove', (e) => {
  if (!state.cfg) return;
  elevPending = e.lngLat;
  clearTimeout(elevTimer);
  elevTimer = setTimeout(async () => {
    const ll = elevPending;
    try {
      const r = await fetch('/api/elevation', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ points: [[ll.lat, ll.lng]] }),
      });
      const d = await r.json();
      if (d.elevations?.length) {
        $('cursor').innerHTML = `altitudine: <b>${Math.round(d.elevations[0])} m</b>`;
      }
    } catch { /* offline: ignorăm */ }
  }, 260);
});

/* ------------------------------------------------------------- comutatoare */
document.addEventListener('keydown', (e) => {
  if (e.target.matches('input, select, textarea')) return;
  if (e.key === 't') { terrainOn = !terrainOn; applyTerrain(); toast(`relief 3D ${terrainOn ? 'pornit' : 'oprit'}`); }
  if (e.key === 's') {
    satOn = !satOn;
    map.setStyle(buildStyle(satOn), { diff: false });
    map.once('styledata', () => { applyTerrain(); setupSelectionLayers(); });
  }
  if (e.key === 'Escape' && state.selecting) setSelecting(false);
});

/* ------------------------------------------------------------------- toast */
let toastTimer = null;
function toast(msg, ms = 2600) {
  const t = $('toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('show'), ms);
}

/* ------------------------------------------------------------------ config */
async function loadConfig() {
  try {
    state.cfg = await (await fetch('/api/config')).json();
  } catch (e) {
    toast('nu pot contacta serverul (./start.sh rulează?)', 6000);
    return;
  }
  $('ver').textContent = 'v' + state.cfg.version;
  const preset = state.cfg.presets['targu-mures'];
  map.jumpTo({ center: [preset[1], preset[0]] });

  // sursa OSM
  const el = $('srcInfo');
  if (state.cfg.pbf) {
    el.className = 'srcInfo';
    el.innerHTML = `sursă OSM: <b>${state.cfg.pbf_name}</b><br>citit cu ${state.cfg.osm_reader} — rapid, offline`;
  } else {
    el.className = 'srcInfo warn';
    el.innerHTML = `nu am găsit niciun <b>.osm.pbf</b> local → se folosește
      <b>Overpass API</b> (bun pentru zone de 1–2 km, dar poate fi aglomerat).<br>
      <button class="btn" id="btnDl" style="margin-top:6px">⬇ descarcă România (~330 MB)</button>
      <div id="dlInfo" class="hint"></div>`;
    $('btnDl').addEventListener('click', startDownload);
  }
  renderSites(state.cfg.sites);
  if (!state.zone) {
    setZone(preset[0], preset[1], state.cfg.default_size || 1200);
    $('size').value = state.cfg.default_size || 1200;
  }
  $('terrainRes').value = String(state.cfg.terrain_res || 256);
}

async function startDownload() {
  $('btnDl').disabled = true;
  const r = await fetch('/api/download-pbf', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
  });
  const d = await r.json();
  if (d.error) { toast(d.error); $('btnDl').disabled = false; return; }
  const poll = setInterval(async () => {
    try {
      const st = await (await fetch('/api/download-status')).json();
      const info = $('dlInfo');
      if (!info) return clearInterval(poll);
      if (st.status === 'running') {
        info.textContent = st.percent != null
          ? `descarc… ${st.percent}% (${st.done_mb} / ${st.total_mb} MB)`
          : `descarc… ${st.done_mb} MB`;
      } else if (st.status === 'done') {
        clearInterval(poll);
        info.innerHTML = '✔ gata — generarea e de acum <b>offline</b>.';
        loadConfig();
      } else if (st.status === 'error') {
        clearInterval(poll);
        info.textContent = '✖ ' + st.error;
      }
    } catch { /* ignoră */ }
  }, 900);
}

/* ------------------------------------------------------------------- liste */
function renderSites(sites) {
  const box = $('sites');
  box.innerHTML = '';
  $('siteCount').textContent = sites?.length ? `(${sites.length})` : '';
  if (!sites?.length) {
    box.innerHTML = '<div class="hint">niciunul încă — generează primul.</div>';
    return;
  }
  sites.forEach((s) => {
    const el = document.createElement('div');
    el.className = 'site';
    el.innerHTML = `
      <span class="nm">${escapeHtml(s.name)}</span>
      <span class="meta">${(s.stats?.buildings || 0)} clăd. · ${(s.area?.size_m || 0)} m</span>
      <span class="del" title="șterge">✕</span>`;
    el.addEventListener('click', (ev) => {
      if (ev.target.classList.contains('del')) {
        if (confirm(`Șterg „${s.name}"?`)) {
          fetch(`/api/sites/${encodeURIComponent(s.name)}`, { method: 'DELETE' })
            .then(() => fetch('/api/sites')).then((r) => r.json())
            .then((d) => renderSites(d.sites));
        }
        return;
      }
      window.open(s.viewer, '_blank');
    });
    box.appendChild(el);
  });
}

const escapeHtml = (s) => String(s ?? '').replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

/* --------------------------------------------------------------- generare */
$('btnGo').addEventListener('click', async () => {
  if (!state.zone) { toast('alege întâi o zonă'); setSelecting(true); return; }
  if (state.job) return;

  const body = {
    lat: state.zone.lat, lon: state.zone.lon, size: state.zone.size,
    name: $('name').value.trim() || undefined,
    terrain_res: +$('terrainRes').value,
    seed: +$('seed').value,
    roofs: $('roofs').checked,
    obj: $('obj').checked,
    overpass: $('overpass').checked,
  };

  $('btnGo').disabled = true;
  $('progWrap').classList.remove('hidden');
  $('resWrap').classList.add('hidden');
  $('btnCancel').classList.remove('hidden');
  $('log').style.maxHeight = '132px';
  $('log').textContent = '';
  $('progBar').style.width = '2%';
  $('progPct').textContent = '';

  let res;
  try {
    if (state.activeProjectId) {
      // Sincronizează modificările utilizatorului în proiect înainte de generare
      try {
        await fetch(`/api/projects/${encodeURIComponent(state.activeProjectId)}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            name: $('name').value.trim() || undefined,
            seed: +$('seed').value,
            terrain: { resolution: +$('terrainRes').value },
            generation: { roofs: $('roofs').checked, formats: $('obj').checked ? ['glb', 'obj'] : ['glb'] },
            dimensions: { size_x_m: state.zone ? state.zone.size : (+$('size').value || 1500) },
          }),
        });
      } catch {
        /* continuă cu datele de pe server */
      }

      res = await (await fetch(`/api/projects/${encodeURIComponent(state.activeProjectId)}/generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      })).json();
    } else {
      res = await (await fetch('/api/generate', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })).json();
    }
  } catch (e) {
    toast('serverul nu răspunde');
    $('btnGo').disabled = false;
    return;
  }
  if (res.error) { toast(res.error); $('btnGo').disabled = false; return; }

  state.job = res.job;
  state.name = res.name;
  $('progPct').textContent = res.source === 'Overpass API' ? 'via Overpass' : res.source;

  if (state.es) state.es.close();
  state.es = new EventSource(`/api/job/${res.job}/stream`);
  state.es.onmessage = (ev) => {
    const d = JSON.parse(ev.data);
    if (d.line) {
      const log = $('log');
      log.innerHTML += escapeHtml(d.line) + '\n';
      log.scrollTop = log.scrollHeight;
      const m = d.line.match(/\[(\d)\/6\]/);
      if (m) $('progBar').style.width = `${(+m[1]) / 6 * 100 - 8}%`;
    }
    if (d.final) finishJob(d.final);
  };
  state.es.onerror = () => {
    // fallback la polling dacă SSE nu e disponibil
    if (state.job) pollJob(state.job);
  };
});

async function pollJob(id) {
  try {
    const j = await (await fetch('/api/job/' + id)).json();
    if (['done', 'error', 'cancelled'].includes(j.status)) finishJob(j);
    else setTimeout(() => pollJob(id), 600);
  } catch { setTimeout(() => pollJob(id), 1200); }
}

function finishJob(j) {
  if (state.es) { state.es.close(); state.es = null; }
  state.job = null;
  $('btnGo').disabled = false;
  $('progBar').style.width = '100%';

  if (j.status === 'error') {
    $('progPct').textContent = 'eroare';
    toast(j.error || 'generarea a eșuat', 7000);
    return;
  }
  if (j.status === 'cancelled') {
    $('progPct').textContent = 'anulat';
    toast('generare anulată');
    return;
  }

  $('progPct').textContent = `gata în ${j.elapsed}s`;
  $('btnCancel').classList.add('hidden');
  $('log').style.maxHeight = '90px';
  const r = j.result || {};
  const st = r.stats || {};
  $('resWrap').classList.remove('hidden');
  $('stats').innerHTML = `
    <b>${(r.triangles || 0).toLocaleString('ro-RO')}</b> triunghiuri ·
    <b>${(st.buildings || 0).toLocaleString('ro-RO')}</b> clădiri<br>
    <b>${(r.manifest?.network?.edges || 0).toLocaleString('ro-RO')}</b> segmente de drum ·
    altitudini ${st.terrain_elev_range?.[0] ?? '–'}…${st.terrain_elev_range?.[1] ?? '–'} m<br>
    <span style="color:#66717f">${r.out || ''}</span>`;
  $('btnViewer').href = r.viewer || '#';
  $('btnZip').href = r.zip || '#';
  $('btnFolder').onclick = () => fetch('/api/open-folder', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: r.name }),
  }).then((x) => x.json()).then((d) => { if (d.error) toast(d.error); });
  renderSitesFromServer();
  toast('gata — deschide viewer-ul 3D');
}

async function renderSitesFromServer() {
  try {
    const d = await (await fetch('/api/sites')).json();
    renderSites(d.sites);
  } catch { /* ignoră */ }
}

/* ------------------------------------------------------------- proiecte */
async function loadProjects() {
  const sel = $('projectSelect');
  if (!sel) return;
  try {
    const res = await fetch('/api/projects');
    if (!res.ok) return;
    const data = await res.json();
    const currentVal = state.activeProjectId || sel.value;
    sel.innerHTML = '<option value="">Fără proiect (oraș simplu)</option>';
    if (Array.isArray(data.projects)) {
      data.projects.forEach((p) => {
        const opt = document.createElement('option');
        opt.value = p.id;
        opt.textContent = `${p.name} (${p.slug || p.id.slice(0, 8)})`;
        sel.appendChild(opt);
      });
    }

    const savedId = localStorage.getItem('terragen_active_project_id');
    const targetId = currentVal || savedId;
    if (targetId && data.projects?.some((p) => p.id === targetId)) {
      sel.value = targetId;
      await selectProject(targetId);
    } else if (!targetId) {
      sel.value = '';
      selectProject('');
    }
  } catch {
    /* server offline sau eroare */
  }
}

async function selectProject(pid) {
  state.activeProjectId = pid || null;
  const actions = $('projectActions');
  const statusBadge = $('projectSyncStatus');

  if (!pid) {
    localStorage.removeItem('terragen_active_project_id');
    if (actions) actions.classList.add('hidden');
    if (statusBadge) {
      statusBadge.textContent = 'Fără proiect';
      statusBadge.className = 'status-pill';
    }
    $('btnGo').textContent = '⬢ Generează orașul 3D';
    return;
  }

  localStorage.setItem('terragen_active_project_id', pid);
  if (actions) actions.classList.remove('hidden');
  if (statusBadge) {
    statusBadge.textContent = 'Proiect activ';
    statusBadge.className = 'status-pill status-built';
  }
  $('btnGo').textContent = '⬢ Generează proiectul 3D';

  try {
    const res = await fetch(`/api/projects/${encodeURIComponent(pid)}`);
    if (!res.ok) return;
    const data = await res.json();
    const p = data.project;
    if (!p) return;

    if (p.name) $('name').value = p.name;
    const lat = p.geographic?.center?.lat;
    const lon = p.geographic?.center?.lon;
    const size = p.dimensions?.size_x_m || 1500;

    if (Number.isFinite(lat) && Number.isFinite(lon)) {
      $('lat').value = lat;
      $('lon').value = lon;
      $('size').value = size;
      setZone(lat, lon, size);
      map.easeTo({ center: [lon, lat], duration: 500 });
    }

    if (p.terrain?.resolution) {
      $('terrainRes').value = String(p.terrain.resolution);
    }
    if (p.seed !== undefined) {
      $('seed').value = p.seed;
    }
    if (p.generation?.roofs !== undefined) {
      $('roofs').checked = Boolean(p.generation.roofs);
    }
    if (p.generation?.formats) {
      $('obj').checked = p.generation.formats.includes('obj');
    }
  } catch {
    toast('Eroare la încărcarea detaliilor proiectului');
  }
}

function initProjectHandlers() {
  const sel = $('projectSelect');
  if (sel) {
    sel.addEventListener('change', (e) => selectProject(e.target.value));
  }
  const btnRefresh = $('refreshProjects');
  if (btnRefresh) {
    btnRefresh.addEventListener('click', () => {
      loadProjects();
      toast('Lista de proiecte reîmprospătată');
    });
  }

  const btnNew = $('btnNewProject');
  const formBox = $('newProjectForm');
  const btnCancelNew = $('btnCancelNewProject');
  const btnCreateSubmit = $('btnCreateProjectSubmit');

  if (btnNew && formBox) {
    btnNew.addEventListener('click', () => {
      const isHidden = formBox.classList.contains('hidden');
      if (isHidden) {
        const c = map.getCenter();
        const curLat = state.zone ? state.zone.lat : c.lat;
        const curLon = state.zone ? state.zone.lon : c.lng;
        const curSize = state.zone ? state.zone.size : (+$('size').value || 1500);

        $('newProjLat').value = curLat.toFixed(5);
        $('newProjLon').value = curLon.toFixed(5);
        $('newProjSize').value = curSize;
        $('newProjTerrainRes').value = $('terrainRes').value || '256';
        $('newProjName').value = $('name').value.trim() || 'Proiect Nou';
        formBox.classList.remove('hidden');
        $('newProjName').focus();
      } else {
        formBox.classList.add('hidden');
      }
    });
  }

  if (btnCancelNew && formBox) {
    btnCancelNew.addEventListener('click', () => formBox.classList.add('hidden'));
  }

  if (btnCreateSubmit && formBox) {
    btnCreateSubmit.addEventListener('click', async () => {
      const name = $('newProjName').value.trim();
      if (!name) {
        toast('Introduceți un nume pentru proiect');
        $('newProjName').focus();
        return;
      }
      const lat = parseFloat($('newProjLat').value);
      const lon = parseFloat($('newProjLon').value);
      const size = parseFloat($('newProjSize').value) || 1500;
      const terrain_res = parseInt($('newProjTerrainRes').value, 10) || 256;
      const seed = parseInt($('seed').value, 10) || 7;
      const roofs = $('roofs').checked;

      btnCreateSubmit.disabled = true;
      try {
        const res = await (await fetch('/api/projects', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name, lat, lon, size, terrain_res, seed, roofs }),
        })).json();

        if (res.error) {
          toast(res.error);
          btnCreateSubmit.disabled = false;
          return;
        }

        toast(`Proiectul „${name}" a fost creat cu succes`);
        formBox.classList.add('hidden');
        await loadProjects();
        if (res.project?.id) {
          $('projectSelect').value = res.project.id;
          await selectProject(res.project.id);
        }
      } catch {
        toast('Eroare la crearea proiectului pe server');
      } finally {
        btnCreateSubmit.disabled = false;
      }
    });
  }

  const btnRename = $('btnRenameProject');
  if (btnRename) {
    btnRename.addEventListener('click', async () => {
      if (!state.activeProjectId) return;
      const currentName = $('name').value.trim() || 'proiect';
      const newName = prompt('Introduceți noul nume pentru proiect:', currentName);
      if (!newName || !newName.trim() || newName.trim() === currentName) return;

      try {
        const res = await (await fetch(`/api/projects/${encodeURIComponent(state.activeProjectId)}/rename`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: newName.trim() }),
        })).json();

        if (res.error) {
          toast(res.error);
          return;
        }
        $('name').value = newName.trim();
        toast('Proiect redenumit cu succes');
        const active = state.activeProjectId;
        await loadProjects();
        $('projectSelect').value = active;
      } catch {
        toast('Eroare la redenumire');
      }
    });
  }

  const btnDup = $('btnDuplicateProject');
  if (btnDup) {
    btnDup.addEventListener('click', async () => {
      if (!state.activeProjectId) return;
      try {
        const res = await (await fetch(`/api/projects/${encodeURIComponent(state.activeProjectId)}/duplicate`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({}),
        })).json();

        if (res.error) {
          toast(res.error);
          return;
        }
        toast('Proiect duplicat cu succes');
        await loadProjects();
        if (res.project?.id) {
          $('projectSelect').value = res.project.id;
          await selectProject(res.project.id);
        }
      } catch {
        toast('Eroare la duplicare');
      }
    });
  }

  const btnDel = $('btnDeleteProject');
  if (btnDel) {
    btnDel.addEventListener('click', async () => {
      if (!state.activeProjectId) return;
      const pName = $('name').value.trim() || 'acest proiect';
      if (!confirm(`Sigur doriți să ștergeți proiectul „${pName}"? Toate fișierele asociate vor fi eliminate.`)) {
        return;
      }
      try {
        const res = await (await fetch(`/api/projects/${encodeURIComponent(state.activeProjectId)}`, {
          method: 'DELETE',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ confirm: true }),
        })).json();

        if (res.error) {
          toast(res.error);
          return;
        }
        toast(`Proiectul „${pName}" a fost șters`);
        selectProject('');
        await loadProjects();
      } catch {
        toast('Eroare la ștergerea proiectului');
      }
    });
  }
}

$('btnCancel').addEventListener('click', async () => {
  if (!state.job) return;
  await fetch(`/api/job/${state.job}/cancel`, { method: 'POST' });
  toast('anulare cerută…');
});

initProjectHandlers();
loadConfig().then(() => loadProjects());
