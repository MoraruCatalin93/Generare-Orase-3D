/* =========================================================================
   Terragen · Terrain Sculpting & Modification Engine UI
   Non-destructive brushes: Raise/Lower, Smooth, Flatten, Hill, Valley, Water
   ========================================================================= */
(() => {
  const el = (id) => document.getElementById(id);

  let activeProjectId = null;
  let currentProject = null;
  let currentBrush = 'raise_lower';
  let canUndo = false;
  let canRedo = false;

  const BRUSH_NAMES = {
    raise_lower: 'Ridică / Coboară',
    smooth: 'Netezește',
    flatten: 'Aplatizează',
    hill: 'Deal',
    valley: 'Vale',
    water: 'Excavare apă',
  };

  /* --------------------------------------------------------------- Toast */
  let toastTimer = null;
  function toast(msg, ms = 2600) {
    const t = el('toast');
    if (!t) return;
    t.textContent = msg;
    t.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.remove('show'), ms);
  }

  function message(msg, isError = false) {
    const st = el('status');
    if (!st) return;
    st.textContent = msg;
    st.style.color = isError ? 'var(--color-danger)' : 'var(--color-accent)';
  }

  /* ----------------------------------------------------------- Coordonate */
  function lonLatToLocal(lon, lat, centerLon, centerLat) {
    const rad = Math.PI / 180;
    const y = (lat - centerLat) * 111139.0;
    const x = (lon - centerLon) * (111139.0 * Math.cos(centerLat * rad));
    return { x, y };
  }

  function localToLonLat(x, y, centerLon, centerLat) {
    const rad = Math.PI / 180;
    const lat = centerLat + y / 111139.0;
    const lon = centerLon + x / (111139.0 * Math.cos(centerLat * rad));
    return { lon, lat };
  }

  function makeCircleFeature(lon, lat, radiusM, points = 48) {
    const rad = Math.PI / 180;
    const dLat = radiusM / 111139.0;
    const dLon = radiusM / (111139.0 * Math.cos(lat * rad));
    const coords = [];
    for (let i = 0; i <= points; i++) {
      const theta = (i / points) * 2 * Math.PI;
      coords.push([
        lon + dLon * Math.cos(theta),
        lat + dLat * Math.sin(theta),
      ]);
    }
    return {
      type: 'Feature',
      geometry: { type: 'Polygon', coordinates: [coords] },
      properties: {},
    };
  }

  /* ------------------------------------------------------------- MapLibre */
  const HOME = [24.5612, 46.5446];
  const map = new maplibregl.Map({
    container: 'map',
    style: {
      version: 8,
      sources: {
        osm: {
          type: 'raster',
          tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
          tileSize: 256,
          attribution: '© OpenStreetMap contributors',
        },
        dem: {
          type: 'raster-dem',
          tiles: ['https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png'],
          tileSize: 256,
          encoding: 'terrarium',
          maxzoom: 15,
        },
      },
      layers: [
        { id: 'osm', type: 'raster', source: 'osm', paint: { 'raster-saturation': -0.3, 'raster-brightness-max': 0.95 } },
      ],
    },
    center: HOME,
    zoom: 14.8,
    pitch: 58,
    bearing: -15,
    maxPitch: 85,
    attributionControl: false,
  });

  map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), 'bottom-right');
  map.addControl(new maplibregl.ScaleControl({ maxWidth: 100, unit: 'metric' }), 'bottom-left');

  map.on('load', () => {
    try {
      if (map.getSource('dem')) {
        map.setTerrain({ source: 'dem', exaggeration: 1.45 });
      }
    } catch {
      /* fallback relief */
    }

    // Strat previzualizare cerc pensulă sub cursor
    map.addSource('brush-preview', {
      type: 'geojson',
      data: { type: 'FeatureCollection', features: [] },
    });

    map.addLayer({
      id: 'brush-preview-fill',
      type: 'fill',
      source: 'brush-preview',
      paint: {
        'fill-color': '#35d0a5',
        'fill-opacity': 0.22,
      },
    });

    map.addLayer({
      id: 'brush-preview-line',
      type: 'line',
      source: 'brush-preview',
      paint: {
        'line-color': '#35d0a5',
        'line-width': 2.2,
        'line-dasharray': [3, 1.5],
      },
    });

    // Strat afișare modificări existente
    map.addSource('existing-modifiers', {
      type: 'geojson',
      data: { type: 'FeatureCollection', features: [] },
    });

    map.addLayer({
      id: 'existing-modifiers-fill',
      type: 'fill',
      source: 'existing-modifiers',
      paint: {
        'fill-color': ['get', 'color'],
        'fill-opacity': 0.14,
      },
    });

    map.addLayer({
      id: 'existing-modifiers-line',
      type: 'line',
      source: 'existing-modifiers',
      paint: {
        'line-color': ['get', 'color'],
        'line-width': 1.8,
      },
    });

    // Limite proiect
    map.addSource('project-bounds', {
      type: 'geojson',
      data: { type: 'FeatureCollection', features: [] },
    });

    map.addLayer({
      id: 'project-bounds-line',
      type: 'line',
      source: 'project-bounds',
      paint: {
        'line-color': '#9cb0c4',
        'line-width': 1.5,
        'line-dasharray': [4, 2],
      },
    });
  });

  // Previzualizare dinamică a pensulei când utilizatorul mișcă cursorul pe hartă
  map.on('mousemove', (e) => {
    const r = parseFloat(el('brushRadius').value) || 150;
    const circle = makeCircleFeature(e.lngLat.lng, e.lngLat.lat, r);
    const src = map.getSource('brush-preview');
    if (src) {
      src.setData({ type: 'FeatureCollection', features: [circle] });
    }
    el('hudCursor').textContent = `${e.lngLat.lat.toFixed(4)}, ${e.lngLat.lng.toFixed(4)}`;
  });

  map.on('mouseout', () => {
    const src = map.getSource('brush-preview');
    if (src) {
      src.setData({ type: 'FeatureCollection', features: [] });
    }
    el('hudCursor').textContent = '–';
  });

  // Clic pe hartă pentru a aplica pensula
  map.on('click', async (e) => {
    if (!activeProjectId) {
      toast('Selectați un proiect activ înainte de a sculpta relieful');
      el('noProjectAlert').style.display = 'block';
      return;
    }
    await applyBrush(e.lngLat.lng, e.lngLat.lat);
  });

  /* ---------------------------------------------------- Comutare Pensulă */
  function updateBrushUI() {
    el('hudBrush').textContent = BRUSH_NAMES[currentBrush] || currentBrush;
    const r = el('brushRadius').value;
    const falloff = el('brushFalloff').value;
    el('hudRadius').textContent = `${r}m (${falloff})`;
    el('brushRadiusVal').textContent = `${r} m`;

    const intVal = parseFloat(el('brushIntensity').value) || 0;
    const intLabel = el('intensityLabel');
    const intValBadge = el('brushIntensityVal');
    const intGroup = el('intensityGroup');

    if (currentBrush === 'smooth') {
      intLabel.textContent = 'Forță netezire';
      intValBadge.textContent = `${Math.round(Math.abs(intVal) / 60 * 100)}%`;
      intGroup.style.display = 'block';
    } else if (currentBrush === 'flatten') {
      intLabel.textContent = 'Cotă țintă (înălțime)';
      const elev = 300 + intVal;
      intValBadge.textContent = `${elev} m`;
      intGroup.style.display = 'block';
    } else if (currentBrush === 'water') {
      intLabel.textContent = 'Adâncime excavare apă';
      intValBadge.textContent = `${Math.abs(intVal) || 4} m`;
      intGroup.style.display = 'block';
    } else if (currentBrush === 'hill') {
      intLabel.textContent = 'Înălțime deal';
      intValBadge.textContent = `+${Math.abs(intVal) || 15} m`;
      intGroup.style.display = 'block';
    } else if (currentBrush === 'valley') {
      intLabel.textContent = 'Adâncime vale';
      intValBadge.textContent = `-${Math.abs(intVal) || 15} m`;
      intGroup.style.display = 'block';
    } else {
      intLabel.textContent = 'Intensitate deplasare';
      intValBadge.textContent = `${intVal >= 0 ? '+' : ''}${intVal} m`;
      intGroup.style.display = 'block';
    }
  }

  document.querySelectorAll('.brush-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.brush-btn').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      currentBrush = btn.dataset.op;
      updateBrushUI();
      toast(`Pensulă activată: ${BRUSH_NAMES[currentBrush]}`);
    });
  });

  el('brushRadius').addEventListener('input', updateBrushUI);
  el('brushIntensity').addEventListener('input', updateBrushUI);
  el('brushFalloff').addEventListener('change', updateBrushUI);

  /* ------------------------------------------------- Aplicare Pensulă */
  async function applyBrush(clickLon, clickLat) {
    if (!activeProjectId || !currentProject) return;

    const cLon = currentProject.geographic?.center?.lon ?? clickLon;
    const cLat = currentProject.geographic?.center?.lat ?? clickLat;
    const { x, y } = lonLatToLocal(clickLon, clickLat, cLon, cLat);

    const radius = parseFloat(el('brushRadius').value) || 150;
    const rawIntensity = parseFloat(el('brushIntensity').value) || 15;
    const falloff = el('brushFalloff').value;

    const payload = {
      op: currentBrush,
      center_x: x,
      center_y: y,
      radius_m: radius,
      falloff,
      strength: 1.0,
      name: `${BRUSH_NAMES[currentBrush]} la (${Math.round(x)}m, ${Math.round(y)}m)`,
    };

    if (currentBrush === 'raise_lower') {
      payload.amount_m = rawIntensity;
    } else if (currentBrush === 'smooth') {
      payload.strength = Math.min(1.0, Math.max(0.1, Math.abs(rawIntensity) / 60));
    } else if (currentBrush === 'flatten') {
      payload.target_height_m = 300.0 + rawIntensity;
    } else if (currentBrush === 'hill') {
      payload.amount_m = Math.abs(rawIntensity) || 20.0;
    } else if (currentBrush === 'valley') {
      payload.amount_m = -(Math.abs(rawIntensity) || 20.0);
    } else if (currentBrush === 'water') {
      payload.depth_m = Math.abs(rawIntensity) || 5.0;
    }

    try {
      message('Aplicare sculptare relief...');
      const res = await fetch(`/api/projects/${encodeURIComponent(activeProjectId)}/terrain/modifiers`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Eroare la aplicare');

      toast(`Sculptat: ${BRUSH_NAMES[currentBrush]} (${radius}m)`);
      message(`Modificare adăugată (#${data.modifier?.id || ''})`);
      await loadModifiers();
    } catch (err) {
      toast(err.message);
      message(err.message, true);
    }
  }

  /* ------------------------------------------------ Istoric & Undo/Redo */
  async function performUndo() {
    if (!activeProjectId || !canUndo) return;
    try {
      message('Pas înapoi (Undo)...');
      const res = await fetch(`/api/projects/${encodeURIComponent(activeProjectId)}/terrain/undo`, {
        method: 'POST',
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Undo imposibil');
      toast('Pas înapoi (Undo) efectuat');
      await loadModifiers();
    } catch (err) {
      toast(err.message);
    }
  }

  async function performRedo() {
    if (!activeProjectId || !canRedo) return;
    try {
      message('Pas înainte (Redo)...');
      const res = await fetch(`/api/projects/${encodeURIComponent(activeProjectId)}/terrain/redo`, {
        method: 'POST',
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Redo imposibil');
      toast('Pas înainte (Redo) efectuat');
      await loadModifiers();
    } catch (err) {
      toast(err.message);
    }
  }

  el('btnUndo').addEventListener('click', performUndo);
  el('btnRedo').addEventListener('click', performRedo);

  // Scurtături de tastatură Ctrl+Z / Ctrl+Y
  document.addEventListener('keydown', (e) => {
    if (e.target.matches('input, select, textarea')) return;
    const isCtrlOrCmd = e.ctrlKey || e.metaKey;
    if (!isCtrlOrCmd) return;

    if (e.key === 'z' || e.key === 'Z') {
      if (e.shiftKey) {
        e.preventDefault();
        performRedo();
      } else {
        e.preventDefault();
        performUndo();
      }
    } else if (e.key === 'y' || e.key === 'Y') {
      e.preventDefault();
      performRedo();
    }
  });

  /* ----------------------------------------- Încărcare & Randare Modificatori */
  async function loadModifiers() {
    if (!activeProjectId) return;
    try {
      const res = await fetch(`/api/projects/${encodeURIComponent(activeProjectId)}/terrain/modifiers`);
      if (!res.ok) return;
      const data = await res.json();

      canUndo = Boolean(data.can_undo);
      canRedo = Boolean(data.can_redo);
      el('btnUndo').disabled = !canUndo;
      el('btnRedo').disabled = !canRedo;

      renderModifiersList(data.modifiers || []);
      renderModifiersOnMap(data.modifiers || []);
    } catch {
      /* eroare la citire */
    }
  }

  function renderModifiersList(mods) {
    const box = el('modifiersList');
    el('modifierCount').textContent = mods.length;

    if (!mods.length) {
      box.innerHTML = '<div class="hint">Nicio modificare aplicată încă.</div>';
      return;
    }

    box.innerHTML = '';
    // Afișăm în ordine inversă (cele mai recente primele)
    [...mods].reverse().forEach((m, revIdx) => {
      const originalIdx = mods.length - revIdx;
      const card = document.createElement('div');
      card.className = 'zone-card';
      card.style.margin = '4px 0';
      card.style.padding = '8px 10px';

      let opBadge = m.op;
      let valText = '';
      if (m.amount_m !== undefined && m.amount_m !== 0) {
        valText = `${m.amount_m > 0 ? '+' : ''}${m.amount_m}m`;
      } else if (m.target_height_m) {
        valText = `${m.target_height_m}m`;
      }

      card.innerHTML = `
        <div class="zone-header">
          <span style="font-weight:600; font-size:12px;">#${originalIdx} · ${escapeHtml(BRUSH_NAMES[m.op] || m.op)}</span>
          <span class="status-pill status-built">${valText || 'activ'}</span>
        </div>
        <div class="zone-meta" style="margin-top:4px;">
          <span>Rază: ${m.radius_m}m · Falloff: ${escapeHtml(m.falloff || 'smooth')}</span>
          <button class="btn-sm danger btn-del-mod" data-id="${m.id}" title="Șterge modificarea" style="padding:2px 8px;">✕</button>
        </div>
      `;

      card.querySelector('.btn-del-mod').addEventListener('click', async (ev) => {
        ev.stopPropagation();
        await deleteModifier(m.id);
      });

      box.appendChild(card);
    });
  }

  async function deleteModifier(mid) {
    if (!activeProjectId) return;
    try {
      message('Ștergere modificare...');
      const res = await fetch(`/api/projects/${encodeURIComponent(activeProjectId)}/terrain/modifiers/${encodeURIComponent(mid)}`, {
        method: 'DELETE',
      });
      if (!res.ok) throw new Error('Eroare la ștergerea modificării');
      toast('Modificare eliminată');
      await loadModifiers();
    } catch (err) {
      toast(err.message);
      message(err.message, true);
    }
  }

  function renderModifiersOnMap(mods) {
    if (!currentProject || !map.getSource('existing-modifiers')) return;
    const cLon = currentProject.geographic?.center?.lon;
    const cLat = currentProject.geographic?.center?.lat;
    if (!Number.isFinite(cLon) || !Number.isFinite(cLat)) return;

    const colorMap = {
      raise_lower: '#35d0a5',
      smooth: '#a855f7',
      flatten: '#eab308',
      hill: '#22c55e',
      valley: '#f97316',
      water: '#38bdf8',
    };

    const features = mods.map((m) => {
      const cx = m.center?.x ?? 0;
      const cy = m.center?.y ?? 0;
      const { lon, lat } = localToLonLat(cx, cy, cLon, cLat);
      const feat = makeCircleFeature(lon, lat, m.radius_m || 50);
      feat.properties = {
        color: colorMap[m.op] || '#35d0a5',
        op: m.op,
      };
      return feat;
    });

    map.getSource('existing-modifiers').setData({
      type: 'FeatureCollection',
      features,
    });
  }

  /* ------------------------------------- Aplică și Regenerează Terenul */
  el('btnRegenerateTerrain').addEventListener('click', async () => {
    if (!activeProjectId) {
      toast('Selectați un proiect activ pentru regenerare');
      return;
    }

    const btn = el('btnRegenerateTerrain');
    const wrap = el('regenProgressWrap');
    const bar = el('regenProgressBar');
    const statusText = el('regenStatusText');
    const pctText = el('regenPctText');
    const resBox = el('regenResultBox');
    const details = el('regenDetails');

    btn.disabled = true;
    wrap.classList.remove('hidden');
    resBox.classList.add('hidden');
    bar.style.width = '15%';
    pctText.textContent = '15%';
    statusText.textContent = '1/3 · Calculare matrice altitudini...';

    try {
      await new Promise((r) => setTimeout(r, 200));
      bar.style.width = '45%';
      pctText.textContent = '45%';
      statusText.textContent = '2/3 · Generare heightmap 16-bit PNG...';

      const res = await fetch(`/api/projects/${encodeURIComponent(activeProjectId)}/terrain/export`, {
        method: 'POST',
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Eroare la exportul reliefului');

      bar.style.width = '85%';
      pctText.textContent = '85%';
      statusText.textContent = '3/3 · Export geometrie 3D teren (GLB)...';

      await new Promise((r) => setTimeout(r, 250));
      bar.style.width = '100%';
      pctText.textContent = '100%';
      statusText.textContent = 'Finalizat cu succes!';

      resBox.classList.remove('hidden');
      const meta = data.heightmap_metadata || {};
      const meshes = data.mesh_files || [];

      details.innerHTML = `
        <div>Matrice: <b>${meta.resolution || 256} × ${meta.resolution || 256} px</b> (16-bit)</div>
        <div>Interval înălțimi: <b>${meta.min_elevation_m ?? '–'}m ... ${meta.max_elevation_m ?? '–'}m</b></div>
        <div>Fișiere 3D: <b>${meshes.length} bucăți GLB generate</b></div>
        <div style="margin-top:4px; color:var(--color-text-secondary); word-break:break-all;">Folder: ${escapeHtml(data.export_dir || '')}</div>
      `;

      toast('Teren regenerat cu succes!');
      message('Regenerare relief finalizată cu succes!');
    } catch (err) {
      toast(err.message);
      message(err.message, true);
      statusText.textContent = 'Eroare la procesare';
    } finally {
      btn.disabled = false;
    }
  });

  /* ------------------------------------------------ Gestiune Proiect Activ */
  async function loadProjects() {
    try {
      const res = await fetch('/api/projects');
      if (!res.ok) return;
      const data = await res.json();
      const sel = el('projectSelect');
      sel.innerHTML = '<option value="">Alegeți un proiect...</option>';

      if (Array.isArray(data.projects)) {
        data.projects.forEach((p) => {
          const opt = document.createElement('option');
          opt.value = p.id;
          opt.textContent = `${p.name} (${p.slug || p.id.slice(0, 8)})`;
          sel.appendChild(opt);
        });
      }

      const saved = localStorage.getItem('terragen_active_project_id');
      if (saved && data.projects?.some((p) => p.id === saved)) {
        sel.value = saved;
        await onProjectChanged(saved);
      } else {
        onProjectChanged('');
      }
    } catch {
      message('Eroare la contactarea serverului', true);
    }
  }

  async function onProjectChanged(pid) {
    activeProjectId = pid || null;
    const alertBox = el('noProjectAlert');
    const editorContent = el('editorContent');
    const statusPill = el('projectSyncStatus');

    if (!pid) {
      localStorage.removeItem('terragen_active_project_id');
      statusPill.textContent = 'Fără proiect';
      statusPill.className = 'status-pill';
      alertBox.style.display = 'block';
      editorContent.style.opacity = '0.45';
      editorContent.style.pointerEvents = 'none';
      currentProject = null;
      renderModifiersList([]);
      renderModifiersOnMap([]);
      return;
    }

    localStorage.setItem('terragen_active_project_id', pid);
    statusPill.textContent = 'Sincronizat';
    statusPill.className = 'status-pill status-built';
    alertBox.style.display = 'none';
    editorContent.style.opacity = '1';
    editorContent.style.pointerEvents = 'auto';

    try {
      message('Încărcare detalii proiect...');
      const res = await fetch(`/api/projects/${encodeURIComponent(pid)}`);
      if (!res.ok) throw new Error('Proiectul nu a putut fi citit');
      const data = await res.json();
      currentProject = data.project;

      const lat = currentProject.geographic?.center?.lat;
      const lon = currentProject.geographic?.center?.lon;
      const size = currentProject.dimensions?.size_x_m || 1500;

      if (Number.isFinite(lat) && Number.isFinite(lon)) {
        map.easeTo({ center: [lon, lat], zoom: 14.6, duration: 600 });

        // Desenare perimetru proiect
        const half = size / 2.0;
        const rad = Math.PI / 180;
        const dLat = half / 111139.0;
        const dLon = half / (111139.0 * Math.cos(lat * rad));
        const boxCoords = [
          [lon - dLon, lat - dLat],
          [lon + dLon, lat - dLat],
          [lon + dLon, lat + dLat],
          [lon - dLon, lat + dLat],
          [lon - dLon, lat - dLat],
        ];

        const src = map.getSource('project-bounds');
        if (src) {
          src.setData({
            type: 'FeatureCollection',
            features: [
              {
                type: 'Feature',
                geometry: { type: 'Polygon', coordinates: [boxCoords] },
                properties: {},
              },
            ],
          });
        }
      }

      await loadModifiers();
      message(`Proiect activ: ${currentProject.name}`);
    } catch (err) {
      toast(err.message);
      message(err.message, true);
    }
  }

  el('projectSelect').addEventListener('change', (e) => onProjectChanged(e.target.value));
  el('refreshProjects').addEventListener('click', () => {
    loadProjects();
    toast('Listă proiecte reîmprospătată');
  });

  const escapeHtml = (s) => String(s ?? '').replace(/[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  updateBrushUI();
  loadProjects();
})();
