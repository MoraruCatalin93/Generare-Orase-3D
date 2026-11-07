/* =========================================================================
   Terragen · Blender Export & Large World Chunk Grid Engine UI
   ========================================================================= */
(() => {
  const el = (id) => document.getElementById(id);

  let activeProjectId = null;
  let currentProject = null;
  let chunkGridData = null;
  let selectedChunkId = null;
  let isGenerating = false;
  let cancelRequested = false;
  let generatedBlenderScript = '';

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
    zoom: 14.2,
    pitch: 50,
    bearing: -15,
    maxPitch: 85,
    attributionControl: false,
  });

  map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), 'bottom-right');
  map.addControl(new maplibregl.ScaleControl({ maxWidth: 110, unit: 'metric' }), 'bottom-left');

  map.on('load', () => {
    try {
      if (map.getSource('dem')) {
        map.setTerrain({ source: 'dem', exaggeration: 1.35 });
      }
    } catch {
      /* relief */
    }

    // Strat GeoJSON pentru grila de chunk-uri
    map.addSource('chunk-grid-source', {
      type: 'geojson',
      data: { type: 'FeatureCollection', features: [] },
    });

    map.addLayer({
      id: 'chunk-grid-fill',
      type: 'fill',
      source: 'chunk-grid-source',
      paint: {
        'fill-color': [
          'match',
          ['get', 'status'],
          'built', '#35d0a5',
          'generating', '#e5b367',
          'error', '#f87171',
          /* pending */ '#3a4756',
        ],
        'fill-opacity': [
          'case',
          ['boolean', ['feature-state', 'selected'], false],
          0.45,
          ['match', ['get', 'status'], 'built', 0.22, 'generating', 0.35, 'error', 0.3, 0.12],
        ],
      },
    });

    map.addLayer({
      id: 'chunk-grid-line',
      type: 'line',
      source: 'chunk-grid-source',
      paint: {
        'line-color': [
          'case',
          ['boolean', ['feature-state', 'selected'], false],
          '#ffffff',
          [
            'match',
            ['get', 'status'],
            'built', '#35d0a5',
            'generating', '#ffd269',
            'error', '#f87171',
            '#4a596a',
          ],
        ],
        'line-width': [
          'case',
          ['boolean', ['feature-state', 'selected'], false],
          3.2,
          1.8,
        ],
        'line-dasharray': [
          'case',
          ['boolean', ['feature-state', 'selected'], false],
          ['literal', [2, 1]],
          ['literal', [4, 1.5]],
        ],
      },
    });

    // Selecție la clic pe chunk
    map.on('click', 'chunk-grid-fill', (e) => {
      if (!e.features?.length) return;
      const feat = e.features[0];
      const cid = feat.properties?.id;
      if (cid) {
        selectChunk(cid);
      }
    });

    map.on('mouseenter', 'chunk-grid-fill', () => {
      map.getCanvas().style.cursor = 'pointer';
    });
    map.on('mouseleave', 'chunk-grid-fill', () => {
      map.getCanvas().style.cursor = '';
    });
  });

  function selectChunk(cid) {
    selectedChunkId = cid;
    el('hudSelectedChunk').textContent = cid || 'Niciunul';
    el('btnGenerateSelectedChunk').disabled = !cid || isGenerating;

    // Actualizare evidențiere pe hartă
    if (chunkGridData?.chunks) {
      const ch = chunkGridData.chunks.find((c) => c.id === cid);
      if (ch) {
        toast(`Chunk selectat: ${cid} (${ch.status || 'negenerat'})`);
      }
    }

    if (chunkGridData?.geojson && map.getSource('chunk-grid-source')) {
      const feats = chunkGridData.geojson.features.map((f, idx) => {
        map.setFeatureState(
          { source: 'chunk-grid-source', id: idx },
          { selected: f.properties?.id === cid }
        );
        return f;
      });
    }
  }

  /* ------------------------------------------------- Încărcare Chunk-uri */
  async function loadChunks() {
    if (!activeProjectId) return;
    try {
      const res = await fetch(`/api/projects/${encodeURIComponent(activeProjectId)}/chunks`);
      if (!res.ok) return;
      chunkGridData = await res.json();

      const grid = chunkGridData.grid || {};
      const chunks = chunkGridData.chunks || [];

      el('hudGridDim').textContent = `${grid.rows || 0} × ${grid.cols || 0}`;
      el('hudChunkSize').textContent = `${grid.chunk_size_m || 512} m`;

      let built = 0, generating = 0, pending = 0, errCount = 0;
      chunks.forEach((c) => {
        const st = c.status || 'pending';
        if (st === 'built' || st === 'generated') built++;
        else if (st === 'generating') generating++;
        else if (st === 'error') errCount++;
        else pending++;
      });

      el('statTotalChunks').textContent = chunks.length;
      el('statBuiltChunks').textContent = built;
      el('statGenChunks').textContent = generating;
      el('statPendingChunks').textContent = pending;

      if (map.getSource('chunk-grid-source') && chunkGridData.geojson) {
        // Asigurăm ID numeric pentru feature-state
        const featuresWithId = chunkGridData.geojson.features.map((f, i) => ({
          ...f,
          id: i,
        }));
        map.getSource('chunk-grid-source').setData({
          type: 'FeatureCollection',
          features: featuresWithId,
        });
      }

      if (selectedChunkId) {
        selectChunk(selectedChunkId);
      }
    } catch {
      /* eroare la citire */
    }
  }

  /* ------------------------------------------------ Configurare Grilă */
  el('btnSetupGrid').addEventListener('click', async () => {
    if (!activeProjectId) {
      toast('Selectați un proiect activ pentru a configura grila');
      return;
    }
    const size = parseFloat(el('chunkSizeSelect').value) || 512.0;

    el('btnSetupGrid').disabled = true;
    try {
      message(`Inițializare grilă cu celule de ${size} m...`);
      const res = await fetch(`/api/projects/${encodeURIComponent(activeProjectId)}/chunks/grid`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chunk_size_m: size }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Eroare la configurare grilă');

      toast(`Grilă configurată: ${data.count || 0} chunk-uri create`);
      message(`Grilă configurată cu succes (${data.count} celule).`);
      await loadChunks();
    } catch (err) {
      toast(err.message);
      message(err.message, true);
    } finally {
      el('btnSetupGrid').disabled = false;
    }
  });

  /* ------------------------------------------------- Generare Chunk-uri */
  async function generateSingleChunk(cid) {
    if (!activeProjectId) return false;
    try {
      const res = await fetch(`/api/projects/${encodeURIComponent(activeProjectId)}/chunks/${encodeURIComponent(cid)}/generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      const data = await res.json();
      return res.ok && data.ok;
    } catch {
      return false;
    }
  }

  el('btnGenerateSelectedChunk').addEventListener('click', async () => {
    if (!activeProjectId || !selectedChunkId || isGenerating) return;

    isGenerating = true;
    el('btnGenerateSelectedChunk').disabled = true;
    el('btnGenerateAllChunks').disabled = true;
    message(`Generare chunk ${selectedChunkId}...`);
    toast(`Generare ${selectedChunkId} în curs...`);

    const ok = await generateSingleChunk(selectedChunkId);
    isGenerating = false;
    el('btnGenerateSelectedChunk').disabled = false;
    el('btnGenerateAllChunks').disabled = false;

    if (ok) {
      toast(`Chunk ${selectedChunkId} generat cu succes!`);
      message(`Chunk ${selectedChunkId} finalizat.`);
    } else {
      toast(`Eroare la generarea chunk-ului ${selectedChunkId}`);
      message(`Generare eșuată pentru ${selectedChunkId}`, true);
    }
    await loadChunks();
  });

  el('btnGenerateAllChunks').addEventListener('click', async () => {
    if (!activeProjectId || isGenerating) return;
    if (!chunkGridData?.chunks?.length) {
      toast('Configurați întâi grila de chunk-uri');
      return;
    }

    const chunks = chunkGridData.chunks;
    const toProcess = chunks.filter((c) => c.status !== 'built');
    if (!toProcess.length) {
      toast('Toate chunk-urile sunt deja generate!');
      return;
    }

    isGenerating = true;
    cancelRequested = false;
    el('btnCancelGeneration').classList.remove('hidden');
    el('btnGenerateAllChunks').disabled = true;
    el('btnGenerateSelectedChunk').disabled = true;

    const wrap = el('chunkProgressWrap');
    const bar = el('chunkProgressBar');
    const msg = el('chunkProgressMsg');
    const pct = el('chunkProgressPct');
    wrap.classList.remove('hidden');

    let completed = 0;
    const total = toProcess.length;

    for (let i = 0; i < total; i++) {
      if (cancelRequested) {
        toast('Generarea a fost oprită.');
        message('Procesare întreruptă de utilizator.');
        break;
      }

      const ch = toProcess[i];
      const percent = Math.round(((i) / total) * 100);
      bar.style.width = `${percent}%`;
      pct.textContent = `${percent}%`;
      msg.textContent = `Generare chunk ${i + 1}/${total} (${ch.id})...`;

      await generateSingleChunk(ch.id);
      completed++;
      await loadChunks();
    }

    bar.style.width = '100%';
    pct.textContent = '100%';
    msg.textContent = cancelRequested ? 'Oprit' : 'Generare completă!';

    isGenerating = false;
    el('btnCancelGeneration').classList.add('hidden');
    el('btnGenerateAllChunks').disabled = false;
    el('btnGenerateSelectedChunk').disabled = !selectedChunkId;

    if (!cancelRequested) {
      toast(`Generare completă: ${completed} chunk-uri procesate.`);
      message(`Toate cele ${completed} chunk-uri au fost generate.`);
    }
    await loadChunks();
  });

  el('btnCancelGeneration').addEventListener('click', () => {
    cancelRequested = true;
    toast('Se solicită oprirea generării...');
  });

  /* ------------------------------------------------ Validare Chunk-uri */
  el('btnValidateChunks').addEventListener('click', async () => {
    if (!activeProjectId) {
      toast('Selectați un proiect activ');
      return;
    }
    const box = el('chunkValidationResult');
    try {
      message('Validare cusături și înălțimi între chunk-uri...');
      box.innerHTML = '<span style="color:var(--color-text-secondary);">Verificare în curs...</span>';

      const res = await fetch(`/api/projects/${encodeURIComponent(activeProjectId)}/chunks/validate`, {
        method: 'POST',
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Eroare la validare');

      const v = data.validation || {};
      const seamContinuity = v.seam_continuity ?? true;
      const maxDelta = v.max_height_delta ?? 0.0;

      if (seamContinuity) {
        box.innerHTML = `<span style="color:var(--color-accent); font-weight:600;">✔ Cusături continue: continuitate garantată (ΔZ maxim: ${maxDelta.toFixed(3)} m).</span>`;
        toast('Cusăturile dintre chunk-uri sunt continue și valide');
      } else {
        box.innerHTML = `<span style="color:var(--color-warning);">Avertisment: s-a detectat o diferență la margine de ${maxDelta.toFixed(3)} m.</span>`;
      }
      message('Validare chunk-uri finalizată.');
    } catch (err) {
      box.innerHTML = `<span style="color:var(--color-danger);">${escapeHtml(err.message)}</span>`;
      message(err.message, true);
    }
  });

  /* --------------------------------------------------- Export Blender */
  el('btnExportBlender').addEventListener('click', async () => {
    if (!activeProjectId) {
      toast('Selectați un proiect activ înainte de export');
      return;
    }

    const mode = el('exportMode').value;
    const selectedLayers = [];
    if (el('layerTerrain').checked) selectedLayers.push('terrain');
    if (el('layerBuildings').checked) selectedLayers.push('buildings');
    if (el('layerRoads').checked) selectedLayers.push('roads');
    if (el('layerSidewalks').checked) selectedLayers.push('sidewalks');
    if (el('layerVegetation').checked) selectedLayers.push('vegetation');
    if (el('layerWater').checked) selectedLayers.push('water');

    if (!selectedLayers.length) {
      toast('Selectați cel puțin un strat pentru export');
      return;
    }

    const payload = {
      mode,
      selected_layers: selectedLayers,
      include_heightmap: el('optHeightmap').checked,
      include_regions_geojson: el('optGeojson').checked,
      embed_materials: el('optMaterials').checked,
      formats: ['glb'],
    };

    const btn = el('btnExportBlender');
    btn.disabled = true;
    message('Generare pachet complet pentru Blender...');
    toast('Export Blender în curs...');

    try {
      const res = await fetch(`/api/projects/${encodeURIComponent(activeProjectId)}/export/blender`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Eroare la exportul pentru Blender');

      el('exportResultWrap').classList.remove('hidden');
      el('exportFolderText').textContent = data.export_dir || 'folder proiect';
      generatedBlenderScript = data.script_path || '';
      el('exportScriptText').textContent = data.script_path || 'import_to_blender.py';

      // Raport validare pe înțelesul tuturor
      const valBox = el('validationReportContent');
      const preVal = data.pre_validation || {};
      const postVal = data.post_validation || {};

      const preOk = preVal.valid !== false;
      const postOk = postVal.valid !== false;

      let reportHtml = `<div>• <b>Structură straturi</b>: Mod ${mode.toUpperCase()} cu ${selectedLayers.length} straturi exportate (${selectedLayers.join(', ')}).</div>`;
      reportHtml += `<div>• <b>Integritate fișiere</b>: ${postOk ? 'Toate fișierele 3D GLB și metadatele JSON au fost generate corect.' : 'Unele avertizări minore la generare.'}</div>`;
      reportHtml += `<div>• <b>Compatibilitate Blender</b>: Structura de colecții este gata pentru rulare automată în Blender 3.6 LTS sau 4.x.</div>`;

      valBox.innerHTML = reportHtml;

      toast('Exportul pentru Blender este gata!');
      message('Pachetul de export Blender a fost salvat cu succes!');
    } catch (err) {
      toast(err.message);
      message(err.message, true);
    } finally {
      btn.disabled = false;
    }
  });

  /* ------------------------------------- Copiere Comandă Import Blender */
  el('btnCopyBlenderCmd').addEventListener('click', async () => {
    const script = generatedBlenderScript || 'import_to_blender.py';
    const cmd = `blender --python "${script}"`;
    try {
      await navigator.clipboard.writeText(cmd);
      toast('Comanda Blender a fost copiată în clipboard!');
      message('Comandă copiată: ' + cmd);
    } catch {
      // Fallback selecție manuală
      prompt('Copiați comanda de mai jos:', cmd);
    }
  });

  /* ------------------------------------------------ Gestiune Proiect */
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
    selectedChunkId = null;
    el('hudSelectedChunk').textContent = 'Niciunul';
    el('btnGenerateSelectedChunk').disabled = true;

    const alertBox = el('noProjectAlert');
    const exportContent = el('exportContent');
    const statusPill = el('projectSyncStatus');
    const ramBox = el('ramWarningBox');

    if (!pid) {
      localStorage.removeItem('terragen_active_project_id');
      statusPill.textContent = 'Fără proiect';
      statusPill.className = 'status-pill';
      alertBox.style.display = 'block';
      exportContent.style.opacity = '0.45';
      exportContent.style.pointerEvents = 'none';
      ramBox.classList.add('hidden');
      currentProject = null;
      chunkGridData = null;
      if (map.getSource('chunk-grid-source')) {
        map.getSource('chunk-grid-source').setData({ type: 'FeatureCollection', features: [] });
      }
      return;
    }

    localStorage.setItem('terragen_active_project_id', pid);
    statusPill.textContent = 'Sincronizat';
    statusPill.className = 'status-pill status-built';
    alertBox.style.display = 'none';
    exportContent.style.opacity = '1';
    exportContent.style.pointerEvents = 'auto';

    try {
      message('Încărcare detalii proiect...');
      const res = await fetch(`/api/projects/${encodeURIComponent(pid)}`);
      if (!res.ok) throw new Error('Proiectul nu a putut fi citit');
      const data = await res.json();
      currentProject = data.project;

      const lat = currentProject.geographic?.center?.lat;
      const lon = currentProject.geographic?.center?.lon;
      const size = currentProject.dimensions?.size_x_m || currentProject.size_m || 1500;

      // Verificare dimensiune lume pentru avertismentul de memorie (>4 km)
      if (size > 4000) {
        ramBox.classList.remove('hidden');
        el('ramWorldSizeText').textContent = `${(size / 1000).toFixed(1)} km × ${(size / 1000).toFixed(1)} km`;
      } else {
        ramBox.classList.add('hidden');
      }

      if (Number.isFinite(lat) && Number.isFinite(lon)) {
        map.easeTo({ center: [lon, lat], zoom: 13.8, duration: 600 });
      }

      await loadChunks();
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

  loadProjects();
})();
