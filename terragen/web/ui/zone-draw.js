/* Region Editor: manipulare poligoane, găuri (holes), split, merge, metrici și persistență */
(() => {
  const el = id => document.getElementById(id);
  const storageKey = 'terragen-drawn-zones-v1';
  const emptyFC = () => ({ type: 'FeatureCollection', features: [] });

  const TYPE_COLORS = {
    urban: '#e74c3c',
    suburban: '#e67e22',
    industrial: '#7f8c8d',
    forest: '#27ae60',
    grassland: '#2ecc71',
    mountain: '#8e44ad',
    agricultural: '#f39c12',
    water: '#2980b9',
    custom: '#16a085'
  };

  const TYPE_STYLES = {
    urban: 'dense_urban',
    suburban: 'residential',
    industrial: 'commercial_park',
    forest: 'dense_woodland',
    grassland: 'open_plains',
    mountain: 'rugged_terrain',
    agricultural: 'cropland_fields',
    water: 'water_basin',
    custom: 'default'
  };

  let activeProjectId = '';
  let regions = [];
  let selectedRegionId = null;
  let multiSelectedIds = new Set();

  // Drawing states: 'SELECT' | 'DRAW_REGION' | 'DRAW_HOLE' | 'EDIT_VERTICES' | 'SPLIT'
  let currentMode = 'DRAW_REGION';
  let points = [];
  let isClosed = false;
  let activeVertexIndex = -1;
  let activeRingIndex = 0; // 0: exterior, 1+: holes

  // Map initialization
  const map = new maplibregl.Map({
    container: 'map',
    center: [24.5612, 46.5446],
    zoom: 14,
    pitch: 0,
    bearing: 0,
    maxPitch: 0,
    attributionControl: true,
    style: {
      version: 8,
      sources: {
        osm: {
          type: 'raster',
          tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
          tileSize: 256,
          maxzoom: 19,
          attribution: '© OpenStreetMap contributors'
        }
      },
      layers: [{ id: 'osm', type: 'raster', source: 'osm' }]
    }
  });

  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-left');
  map.doubleClickZoom.disable();

  function message(text) {
    el('status').textContent = text;
  }

  // Geographic planar calculations for real-time HUD metrics
  function calculateMetrics(ring) {
    if (!Array.isArray(ring) || ring.length < 3) {
      return { area_m2: 0, width_m: 0, height_m: 0, valid: false };
    }
    const lat0 = ring[0][1];
    const cosLat = Math.cos(lat0 * Math.PI / 180.0);
    const mPerDegLat = 111320.0;
    const mPerDegLon = 111320.0 * cosLat;

    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    const ptsM = ring.map(pt => {
      const x = pt[0] * mPerDegLon;
      const y = pt[1] * mPerDegLat;
      if (x < minX) minX = x;
      if (x > maxX) maxX = x;
      if (y < minY) minY = y;
      if (y > maxY) maxY = y;
      return [x, y];
    });

    let doubleArea = 0;
    for (let i = 0; i < ptsM.length - 1; i++) {
      doubleArea += ptsM[i][0] * ptsM[i + 1][1] - ptsM[i + 1][0] * ptsM[i][1];
    }
    const areaM2 = Math.abs(doubleArea) / 2.0;
    const widthM = Math.max(0, maxX - minX);
    const heightM = Math.max(0, maxY - minY);

    return {
      area_m2: Math.round(areaM2),
      width_m: Math.round(widthM),
      height_m: Math.round(heightM),
      valid: ringValid(ring)
    };
  }

  function updateHUD(metrics, customStatus) {
    if (!metrics) {
      el('hudArea').textContent = '0 m²';
      el('hudDims').textContent = '0 × 0 m';
      el('hudStatus').textContent = customStatus || 'Pregătit';
      el('hudStatus').className = 'hud-val';
      return;
    }
    const m2 = metrics.area_m2 || 0;
    let areaStr = `${m2.toLocaleString()} m²`;
    if (m2 >= 10000) {
      const ha = (m2 / 10000).toFixed(2);
      areaStr += ` (${ha} ha)`;
    }
    el('hudArea').textContent = areaStr;
    el('hudDims').textContent = `${metrics.width_m} × ${metrics.height_m} m`;

    if (customStatus) {
      el('hudStatus').textContent = customStatus;
      el('hudStatus').className = 'hud-val hud-warn';
    } else if (metrics.valid) {
      el('hudStatus').textContent = '✔ Validă';
      el('hudStatus').className = 'hud-val';
    } else {
      el('hudStatus').textContent = '⚠ Auto-intersectare / Invalidă';
      el('hudStatus').className = 'hud-val hud-warn';
    }
  }

  function ringValid(ring) {
    if (!Array.isArray(ring) || ring.length < 4 || ring.length > 501) return false;
    if (ring.some(p => !Array.isArray(p) || p.length !== 2 || !p.every(Number.isFinite) || Math.abs(p[0]) > 180 || Math.abs(p[1]) > 85)) return false;
    if (ring[0][0] !== ring[ring.length - 1][0] || ring[0][1] !== ring[ring.length - 1][1]) return false;

    let area = 0;
    for (let i = 0; i < ring.length - 1; i++) {
      const a = ring[i], b = ring[i + 1];
      if (a[0] === b[0] && a[1] === b[1]) return false;
      area += a[0] * b[1] - b[0] * a[1];
    }
    if (Math.abs(area) < 1e-12) return false;

    const cross = (a, b, c) => (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);
    const on = (a, b, p) => Math.abs(cross(a, b, p)) < 1e-12 &&
      p[0] >= Math.min(a[0], b[0]) - 1e-12 && p[0] <= Math.max(a[0], b[0]) + 1e-12 &&
      p[1] >= Math.min(a[1], b[1]) - 1e-12 && p[1] <= Math.max(a[1], b[1]) + 1e-12;
    const intersects = (a, b, c, d) => {
      const x = cross(a, b, c), y = cross(a, b, d), z = cross(c, d, a), w = cross(c, d, b);
      return (x * y < 0 && z * w < 0) || on(a, b, c) || on(a, b, d) || on(c, d, a) || on(c, d, b);
    };

    const count = ring.length - 1;
    for (let i = 0; i < count; i++) {
      for (let j = i + 1; j < count; j++) {
        if (j === i + 1 || (i === 0 && j === count - 1)) continue;
        if (intersects(ring[i], ring[i + 1], ring[j], ring[j + 1])) return false;
      }
    }
    return true;
  }

  function setMode(mode) {
    currentMode = mode;
    document.querySelectorAll('.mode-btn').forEach(btn => btn.classList.remove('active'));
    if (mode === 'SELECT') el('modeSelect').classList.add('active');
    else if (mode === 'DRAW_REGION') el('modeDrawRegion').classList.add('active');
    else if (mode === 'DRAW_HOLE') el('modeDrawHole').classList.add('active');
    else if (mode === 'EDIT_VERTICES') el('modeEditVertices').classList.add('active');
    else if (mode === 'SPLIT') el('modeSplit').classList.add('active');

    points = [];
    isClosed = false;
    updateControls();
    updateMapLayers();
  }

  function updateControls() {
    const isDrawing = (currentMode === 'DRAW_REGION' || currentMode === 'DRAW_HOLE' || currentMode === 'SPLIT') && points.length > 0;
    el('start').disabled = isDrawing || isClosed;
    el('undo').disabled = points.length === 0;
    el('finish').disabled = currentMode === 'SPLIT' ? points.length < 2 : points.length < 3;
    el('cancel').disabled = points.length === 0 && !isClosed;
    el('save').disabled = !isClosed && !selectedRegionId;

    const hasSelected = !!selectedRegionId;
    el('duplicateBtn').disabled = !hasSelected;
    el('deleteBtn').disabled = !hasSelected;
    el('generateRegionBtn').disabled = !hasSelected || !activeProjectId;
    el('mergeBtn').disabled = multiSelectedIds.size < 2;
  }

  function renderRegionList() {
    const container = el('zones');
    container.replaceChildren();
    el('regionCount').textContent = regions.length;

    if (!regions.length) {
      container.textContent = 'Nicio regiune salvată.';
      return;
    }

    regions.forEach(r => {
      const card = document.createElement('div');
      card.className = 'zone-card' + (r.id === selectedRegionId ? ' selected' : '');

      const header = document.createElement('div');
      header.className = 'zone-header';

      const left = document.createElement('div');
      left.style.display = 'flex';
      left.style.alignItems = 'center';
      left.style.gap = '6px';

      const chk = document.createElement('input');
      chk.type = 'checkbox';
      chk.checked = multiSelectedIds.has(r.id);
      chk.onclick = (e) => {
        e.stopPropagation();
        if (chk.checked) multiSelectedIds.add(r.id);
        else multiSelectedIds.delete(r.id);
        updateControls();
      };

      const swatch = document.createElement('span');
      swatch.className = 'color-swatch';
      swatch.style.background = r.color || TYPE_COLORS[r.region_type] || '#e74c3c';

      const title = document.createElement('strong');
      title.textContent = r.name || 'Regiune';

      left.append(chk, swatch, title);

      const right = document.createElement('div');
      const badge = document.createElement('span');
      badge.className = 'type-badge';
      badge.style.background = r.color || TYPE_COLORS[r.region_type] || '#e74c3c';
      badge.textContent = r.region_type || 'urban';

      const statusPill = document.createElement('span');
      statusPill.className = `status-pill status-${r.status || 'unbuilt'}`;
      statusPill.textContent = r.status || 'unbuilt';

      right.append(badge, document.createTextNode(' '), statusPill);
      header.append(left, right);
      card.appendChild(header);

      const meta = document.createElement('div');
      meta.className = 'zone-meta';
      const m2 = r.geographic?.area_m2 || 0;
      const ha = (m2 / 10000).toFixed(2);
      meta.innerHTML = `<span>${m2.toLocaleString()} m² (${ha} ha)</span><span>${r.locked ? '🔒 Blocat' : '🔓 Editabil'}</span>`;
      card.appendChild(meta);

      const actions = document.createElement('div');
      actions.className = 'zone-actions';

      const focusBtn = document.createElement('button');
      focusBtn.textContent = 'Centru';
      focusBtn.onclick = (e) => {
        e.stopPropagation();
        selectRegion(r.id);
        focusRegion(r);
      };

      const editBtn = document.createElement('button');
      editBtn.textContent = '✎ Vârfuri';
      editBtn.onclick = (e) => {
        e.stopPropagation();
        selectRegion(r.id);
        setMode('EDIT_VERTICES');
      };

      const visBtn = document.createElement('button');
      visBtn.textContent = r.visible !== false ? '👁 Ascunde' : '👁‍🗨 Arată';
      visBtn.onclick = (e) => {
        e.stopPropagation();
        r.visible = r.visible === false;
        persistOrSync();
        updateMapLayers();
        renderRegionList();
      };

      const delBtn = document.createElement('button');
      delBtn.textContent = 'Șterge';
      delBtn.style.color = '#ff9b94';
      delBtn.onclick = (e) => {
        e.stopPropagation();
        deleteRegionById(r.id);
      };

      actions.append(focusBtn, editBtn, visBtn, delBtn);
      card.appendChild(actions);

      card.onclick = () => selectRegion(r.id);
      container.appendChild(card);
    });
  }

  function selectRegion(id) {
    selectedRegionId = id;
    const r = regions.find(x => x.id === id);
    if (!r) {
      el('inspectorModeLabel').textContent = 'Nouă';
      updateControls();
      renderRegionList();
      return;
    }
    el('inspectorModeLabel').textContent = `Selectată (${r.name})`;
    el('zoneName').value = r.name || '';
    el('zoneType').value = r.region_type || 'urban';
    el('zoneColor').value = r.color || TYPE_COLORS[r.region_type] || '#e74c3c';
    el('zoneSeed').value = r.seed || 7;
    el('zoneStylePreset').value = r.style_preset || TYPE_STYLES[r.region_type] || 'default';
    el('zoneRoofs').checked = r.generation?.roofs !== false;
    el('zoneLocked').checked = !!r.locked;
    el('zoneVisible').checked = r.visible !== false;

    if (r.polygon && r.polygon.length >= 3) {
      const metrics = calculateMetrics(r.polygon);
      updateHUD(metrics);
    }
    updateControls();
    renderRegionList();
    updateMapLayers();
  }

  function focusRegion(r) {
    if (!r.polygon || !r.polygon.length) return;
    const bounds = new maplibregl.LngLatBounds();
    r.polygon.forEach(pt => bounds.extend(pt));
    map.fitBounds(bounds, { padding: 60, maxZoom: 17 });
  }

  function buildGeoJSON() {
    const features = [];
    regions.forEach(r => {
      if (r.visible === false || !r.polygon || r.polygon.length < 3) return;
      const coords = [r.polygon];
      if (r.holes && r.holes.length) {
        r.holes.forEach(h => {
          if (h && h.length >= 3) coords.push(h);
        });
      }
      features.push({
        type: 'Feature',
        id: r.id,
        properties: {
          id: r.id,
          name: r.name,
          color: r.color || TYPE_COLORS[r.region_type] || '#e74c3c',
          region_type: r.region_type,
          selected: r.id === selectedRegionId
        },
        geometry: { type: 'Polygon', coordinates: coords }
      });
    });
    return { type: 'FeatureCollection', features };
  }

  function buildDraftGeoJSON() {
    const features = [];
    if (points.length >= 2) {
      const isSplit = currentMode === 'SPLIT';
      const coords = (!isSplit && isClosed) ? [...points, points[0]] : points;
      features.push({
        type: 'Feature',
        properties: { draft: true },
        geometry: { type: 'LineString', coordinates: coords }
      });
    }
    points.forEach((pt, idx) => {
      features.push({
        type: 'Feature',
        properties: { index: idx },
        geometry: { type: 'Point', coordinates: pt }
      });
    });

    // Vertex edit handles if in EDIT_VERTICES mode
    if (currentMode === 'EDIT_VERTICES' && selectedRegionId) {
      const r = regions.find(x => x.id === selectedRegionId);
      if (r && r.polygon) {
        r.polygon.forEach((pt, idx) => {
          if (idx < r.polygon.length - 1) {
            features.push({
              type: 'Feature',
              properties: { vertexHandle: true, ring: 0, index: idx },
              geometry: { type: 'Point', coordinates: pt }
            });
          }
        });
      }
    }
    return { type: 'FeatureCollection', features };
  }

  function updateMapLayers() {
    if (!map.isStyleLoaded()) return;
    if (map.getSource('regions-src')) {
      map.getSource('regions-src').setData(buildGeoJSON());
    }
    if (map.getSource('draft-src')) {
      map.getSource('draft-src').setData(buildDraftGeoJSON());
    }
  }

  // Persistence & API synchronization
  async function loadProjects() {
    try {
      const res = await fetch('/api/projects');
      if (!res.ok) return;
      const data = await res.json();
      const sel = el('projectSelect');
      sel.innerHTML = '<option value="">Fără proiect (stocare browser / fișier)</option>';
      if (Array.isArray(data.projects)) {
        data.projects.forEach(p => {
          const opt = document.createElement('option');
          opt.value = p.id;
          opt.textContent = `${p.name} (${p.slug})`;
          sel.appendChild(opt);
        });
      }
      const saved = localStorage.getItem('terragen_active_project_id');
      if (saved && data.projects?.some(p => p.id === saved)) {
        sel.value = saved;
        loadRegionsForProject(saved);
      }
    } catch (_) {}
  }

  async function loadRegionsForProject(pid) {
    if (!pid) {
      activeProjectId = '';
      localStorage.removeItem('terragen_active_project_id');
      el('projectSyncStatus').textContent = 'Local (Browser)';
      loadFromLocalStorage();
      return;
    }
    localStorage.setItem('terragen_active_project_id', pid);
    try {
      message('Încărcare regiuni din proiect...');
      const res = await fetch(`/api/projects/${pid}/regions`);
      if (!res.ok) throw new Error('Eroare la citire regiuni');
      const data = await res.json();
      activeProjectId = pid;
      regions = data.regions || [];
      el('projectSyncStatus').textContent = 'Sincronizat cu backend';
      message(`Încărcat cu succes: ${regions.length} regiuni.`);
      if (regions.length) focusRegion(regions[0]);
      renderRegionList();
      updateMapLayers();
    } catch (e) {
      message(`Nu am putut încărca proiectul: ${e.message}`);
    }
  }

  function loadFromLocalStorage() {
    try {
      const saved = JSON.parse(localStorage.getItem(storageKey) || '[]');
      if (Array.isArray(saved)) regions = saved;
    } catch (_) {
      regions = [];
    }
    renderRegionList();
    updateMapLayers();
  }

  async function persistOrSync() {
    if (!activeProjectId) {
      try {
        localStorage.setItem(storageKey, JSON.stringify(regions));
        return true;
      } catch (_) {
        message('Eroare la salvare în browser.');
        return false;
      }
    }
    return true;
  }

  async function deleteRegionById(rid) {
    if (!confirm('Sigur doriți să ștergeți această regiune?')) return;
    if (activeProjectId) {
      try {
        const res = await fetch(`/api/projects/${activeProjectId}/regions/${rid}`, { method: 'DELETE' });
        if (!res.ok) throw new Error('Eroare la ștergerea de pe server');
      } catch (e) {
        message(e.message);
        return;
      }
    }
    regions = regions.filter(x => x.id !== rid);
    if (selectedRegionId === rid) selectRegion(null);
    multiSelectedIds.delete(rid);
    persistOrSync();
    renderRegionList();
    updateMapLayers();
    message('Regiune ștearsă.');
  }

  // Map click interaction
  map.on('click', event => {
    const coords = [event.lngLat.wrap().lng, event.lngLat.lat];

    if (currentMode === 'SELECT') {
      const hits = map.queryRenderedFeatures(event.point, { layers: ['regions-fill'] });
      if (hits.length) {
        selectRegion(hits[0].properties.id);
      } else {
        selectRegion(null);
      }
      return;
    }

    if (currentMode === 'DRAW_REGION' || currentMode === 'DRAW_HOLE' || currentMode === 'SPLIT') {
      if (isClosed) return;
      if (points.length >= 500) {
        message('Maxim 500 de puncte.');
        return;
      }
      points.push(coords);
      const metrics = calculateMetrics(points.length >= 3 ? [...points, points[0]] : points);
      updateHUD(metrics);
      updateControls();
      updateMapLayers();
    }
  });

  // Mode button events
  el('modeSelect').onclick = () => setMode('SELECT');
  el('modeDrawRegion').onclick = () => setMode('DRAW_REGION');
  el('modeDrawHole').onclick = () => {
    if (!selectedRegionId) {
      message('Selectați o regiune pentru a-i desena o gaură.');
      return;
    }
    setMode('DRAW_HOLE');
  };
  el('modeEditVertices').onclick = () => {
    if (!selectedRegionId) {
      message('Selectați mai întâi o regiune pentru editarea vârfurilor.');
      return;
    }
    setMode('EDIT_VERTICES');
  };
  el('modeSplit').onclick = () => {
    if (!selectedRegionId) {
      message('Selectați o regiune pentru tăiere / split.');
      return;
    }
    setMode('SPLIT');
  };

  // Drawing toolbar events
  el('start').onclick = () => {
    points = [];
    isClosed = false;
    updateControls();
    updateMapLayers();
    message('Apasă pe hartă pentru a adăuga puncte conturului.');
  };

  el('undo').onclick = () => {
    points.pop();
    updateControls();
    updateMapLayers();
    if (points.length >= 3) updateHUD(calculateMetrics([...points, points[0]]));
    else updateHUD(null);
  };

  el('cancel').onclick = () => {
    points = [];
    isClosed = false;
    updateControls();
    updateMapLayers();
    updateHUD(null, 'Anulat');
    message('Desenul a fost anulat.');
  };

  el('finish').onclick = async () => {
    if (currentMode === 'SPLIT') {
      if (points.length < 2) {
        message('Linia de tăiere necesită cel puțin 2 puncte.');
        return;
      }
      if (!selectedRegionId || !activeProjectId) {
        message('Tăierea necesită un proiect activ și o regiune selectată.');
        return;
      }
      try {
        message('Se procesează tăierea geometriei...');
        const res = await fetch(`/api/projects/${activeProjectId}/regions/${selectedRegionId}/split`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ line: points })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Tăiere eșuată');
        message(`Regiunea a fost împărțită în ${data.regions.length} sub-regiuni.`);
        await loadRegionsForProject(activeProjectId);
        setMode('SELECT');
      } catch (e) {
        message(`Eroare la tăiere: ${e.message}`);
      }
      return;
    }

    // DRAW_REGION or DRAW_HOLE
    const ring = [...points, points[0]];
    if (!ringValid(ring)) {
      message('Formă invalidă: asigurați-vă că liniile nu se auto-intersectează.');
      updateHUD(null, 'Formă invalidă');
      return;
    }
    isClosed = true;
    updateHUD(calculateMetrics(ring));
    updateControls();
    updateMapLayers();
    message('Forma este închisă. Verificați proprietățile și apăsați „Salvează regiunea”.');
  };

  // Region properties synchronization
  el('zoneType').onchange = () => {
    const t = el('zoneType').value;
    if (TYPE_COLORS[t]) el('zoneColor').value = TYPE_COLORS[t];
    if (TYPE_STYLES[t]) el('zoneStylePreset').value = TYPE_STYLES[t];
  };

  el('save').onclick = async () => {
    const name = el('zoneName').value.trim() || 'Regiune Nouă';
    const regType = el('zoneType').value;
    const color = el('zoneColor').value;
    const seed = parseInt(el('zoneSeed').value, 10) || 7;
    const stylePreset = el('zoneStylePreset').value;
    const roofs = el('zoneRoofs').checked;
    const locked = el('zoneLocked').checked;
    const visible = el('zoneVisible').checked;

    if (currentMode === 'DRAW_HOLE' && selectedRegionId) {
      const r = regions.find(x => x.id === selectedRegionId);
      if (r) {
        if (!r.holes) r.holes = [];
        r.holes.push([...points, points[0]]);
        if (activeProjectId) {
          await fetch(`/api/projects/${activeProjectId}/regions/${r.id}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ holes: r.holes })
          });
        }
        persistOrSync();
        points = [];
        isClosed = false;
        setMode('SELECT');
        renderRegionList();
        updateMapLayers();
        message('Gaura a fost adăugată în regiune.');
        return;
      }
    }

    if (isClosed && points.length >= 3) {
      const exterior = [...points, points[0]];
      const payload = {
        name,
        region_type: regType,
        color,
        seed,
        style_preset: stylePreset,
        polygon: exterior,
        holes: [],
        locked,
        visible,
        generation: { roofs }
      };

      if (activeProjectId) {
        try {
          const res = await fetch(`/api/projects/${activeProjectId}/regions`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
          });
          const data = await res.json();
          if (!res.ok) throw new Error(data.error || 'Eroare la salvare pe server');
          regions.push(data.region);
          selectRegion(data.region.id);
        } catch (e) {
          message(`Eroare la salvare: ${e.message}`);
          return;
        }
      } else {
        const localId = 'reg_' + Math.random().toString(36).substr(2, 8);
        const newReg = { ...payload, id: localId, geographic: calculateMetrics(exterior) };
        regions.push(newReg);
        selectRegion(localId);
      }

      persistOrSync();
      points = [];
      isClosed = false;
      setMode('SELECT');
      renderRegionList();
      updateMapLayers();
      message('Regiune salvată cu succes.');
      return;
    }

    // Updating existing selected region properties
    if (selectedRegionId) {
      const r = regions.find(x => x.id === selectedRegionId);
      if (r) {
        r.name = name;
        r.region_type = regType;
        r.color = color;
        r.seed = seed;
        r.style_preset = stylePreset;
        r.locked = locked;
        r.visible = visible;
        if (!r.generation) r.generation = {};
        r.generation.roofs = roofs;

        if (activeProjectId) {
          try {
            await fetch(`/api/projects/${activeProjectId}/regions/${r.id}`, {
              method: 'PUT',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                name, region_type: regType, color, seed, style_preset: stylePreset,
                locked, visible, generation: r.generation
              })
            });
          } catch (e) {
            message(`Eroare salvare: ${e.message}`);
          }
        }
        persistOrSync();
        renderRegionList();
        updateMapLayers();
        message('Proprietățile regiunii au fost actualizate.');
      }
    }
  };

  el('duplicateBtn').onclick = async () => {
    if (!selectedRegionId) return;
    if (activeProjectId) {
      try {
        const res = await fetch(`/api/projects/${activeProjectId}/regions/${selectedRegionId}/duplicate`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ offset_m: [25.0, 25.0] })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error);
        regions.push(data.region);
        selectRegion(data.region.id);
      } catch (e) {
        message(`Eroare duplicare: ${e.message}`);
        return;
      }
    } else {
      const r = regions.find(x => x.id === selectedRegionId);
      if (r) {
        const clone = JSON.parse(JSON.stringify(r));
        clone.id = 'reg_' + Math.random().toString(36).substr(2, 8);
        clone.name = `${r.name} (Copy)`;
        regions.push(clone);
        selectRegion(clone.id);
      }
    }
    persistOrSync();
    renderRegionList();
    updateMapLayers();
    message('Regiunea a fost duplicată.');
  };

  el('deleteBtn').onclick = () => {
    if (selectedRegionId) deleteRegionById(selectedRegionId);
  };

  el('generateRegionBtn').onclick = async () => {
    if (!selectedRegionId || !activeProjectId) return;
    try {
      message('Pornire generare 3D procedurală pentru această regiune...');
      const res = await fetch(`/api/projects/${activeProjectId}/regions/${selectedRegionId}/generate`, {
        method: 'POST'
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Eroare la pornire generare');
      message(`Generare 3D pornită (Job: ${data.job}). Urmăriți progresul în generator.`);
      const r = regions.find(x => x.id === selectedRegionId);
      if (r) r.status = 'generating';
      renderRegionList();
    } catch (e) {
      message(`Eroare generare: ${e.message}`);
    }
  };

  // Multi-select & Batch operations
  el('selectAllBtn').onclick = () => {
    if (multiSelectedIds.size === regions.length) multiSelectedIds.clear();
    else regions.forEach(r => multiSelectedIds.add(r.id));
    updateControls();
    renderRegionList();
  };

  el('mergeBtn').onclick = async () => {
    if (multiSelectedIds.size < 2) return;
    const ids = Array.from(multiSelectedIds);
    if (!activeProjectId) {
      message('Unirea (Merge) este disponibilă doar în modul Proiect activ.');
      return;
    }
    try {
      message('Unire poligoane...');
      const res = await fetch(`/api/projects/${activeProjectId}/regions/merge`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ region_ids: ids })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error);
      multiSelectedIds.clear();
      await loadRegionsForProject(activeProjectId);
      if (data.region) selectRegion(data.region.id);
      message('Regiunile au fost unite cu succes.');
    } catch (e) {
      message(`Eroare la unire: ${e.message}`);
    }
  };

  el('validateConflictsBtn').onclick = async () => {
    const box = el('conflictList');
    box.replaceChildren();
    if (!activeProjectId) {
      message('Verificarea de conflicte detaliată rulează în cadrul proiectului.');
      return;
    }
    try {
      const res = await fetch(`/api/projects/${activeProjectId}/regions/validate`);
      const data = await res.json();
      const val = data.validation;
      if (val.valid) {
        box.innerHTML = '<div style="color:#4ee0a9; padding:4px 0; font-size:11px;">✔ Niciun conflict sau suprapunere detectată.</div>';
      } else {
        (val.conflicts || []).forEach(c => {
          const div = document.createElement('div');
          div.className = 'conflict-box';
          div.textContent = `⚠ ${c.message}`;
          box.appendChild(div);
        });
      }
    } catch (e) {
      message(`Eroare verificare: ${e.message}`);
    }
  };

  // Project selector change
  el('projectSelect').onchange = (e) => loadRegionsForProject(e.target.value);
  el('refreshProjects').onclick = () => loadProjects();

  // Import / Export
  el('download').onclick = () => {
    const data = JSON.stringify(buildGeoJSON(), null, 2);
    const url = URL.createObjectURL(new Blob([data], { type: 'application/geo+json' }));
    const a = document.createElement('a');
    a.href = url;
    a.download = 'terragen-regions.geojson';
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  el('open').onclick = () => el('file').click();
  el('file').onchange = async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    try {
      if (file.size > 5000000) throw new Error('Fișier prea mare (> 5MB).');
      const text = await file.text();
      const data = JSON.parse(text);
      if (data.type !== 'FeatureCollection' || !Array.isArray(data.features)) {
        throw new Error('Format GeoJSON invalid.');
      }
      const loaded = [];
      data.features.forEach((f, idx) => {
        if (f.geometry && f.geometry.type === 'Polygon' && Array.isArray(f.geometry.coordinates)) {
          const rings = f.geometry.coordinates;
          const ext = rings[0];
          const holes = rings.slice(1);
          const props = f.properties || {};
          loaded.push({
            id: props.id || 'reg_imp_' + idx,
            name: props.name || `Regiune ${idx + 1}`,
            region_type: props.region_type || 'urban',
            color: props.color || TYPE_COLORS[props.region_type] || '#e74c3c',
            polygon: ext,
            holes: holes,
            seed: props.seed || 7,
            style_preset: props.style_preset || 'default',
            visible: true,
            locked: false
          });
        }
      });
      regions = loaded;
      persistOrSync();
      renderRegionList();
      updateMapLayers();
      if (regions.length) focusRegion(regions[0]);
      message(`Au fost importate ${regions.length} regiuni din GeoJSON.`);
    } catch (err) {
      message(`Nu am putut încărca fișierul: ${err.message}`);
    } finally {
      e.target.value = '';
    }
  };

  // Map load configuration
  map.on('load', () => {
    map.addSource('regions-src', { type: 'geojson', data: emptyFC() });
    map.addSource('draft-src', { type: 'geojson', data: emptyFC() });

    // Region fills with dynamic colors
    map.addLayer({
      id: 'regions-fill',
      type: 'fill',
      source: 'regions-src',
      paint: {
        'fill-color': ['coalesce', ['get', 'color'], '#16b8a0'],
        'fill-opacity': ['case', ['boolean', ['get', 'selected'], false], 0.38, 0.22]
      }
    });

    // Region borders
    map.addLayer({
      id: 'regions-line',
      type: 'line',
      source: 'regions-src',
      paint: {
        'line-color': ['coalesce', ['get', 'color'], '#34dfc0'],
        'line-width': ['case', ['boolean', ['get', 'selected'], false], 3.5, 2]
      }
    });

    // Draft line
    map.addLayer({
      id: 'draft-line',
      type: 'line',
      source: 'draft-src',
      filter: ['==', ['geometry-type'], 'LineString'],
      paint: { 'line-color': '#ffd05a', 'line-width': 3 }
    });

    // Draft and vertex handle points
    map.addLayer({
      id: 'draft-points',
      type: 'circle',
      source: 'draft-src',
      filter: ['==', ['geometry-type'], 'Point'],
      paint: {
        'circle-radius': 5,
        'circle-color': '#ffd05a',
        'circle-stroke-color': '#1a212a',
        'circle-stroke-width': 2
      }
    });

    loadProjects();
    loadFromLocalStorage();
    updateControls();
  });
})();
