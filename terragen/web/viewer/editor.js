/**
 * Game World Editor (Etapa 5) - Interactive 3D World Inspection & Editing Engine
 * Built on Three.js (r160) with OrbitControls & GLTFLoader.
 *
 * Features:
 * - Free 3D navigation (Orbit / Pan / Zoom / Keyboard Fly)
 * - Progressive Chunk Streaming based on camera distance (LOD / Memory Bounded)
 * - 3D Chunk Grid Overlay with state colors (ready / generating / unrendered / selected)
 * - Layer Toggles (Terrain, Roads, Buildings, Water, Vegetation, Props, Biomes, Wireframe, Shadows)
 * - 3D Object Inspector via Raycasting (Instance ID, Category, Metric Coords, Altitude, Biome)
 * - Live World Controls: Style Preset dropdown, Vegetation Density slider
 * - Exclusion Zone toggle for selected chunk
 * - Selective Chunk Regeneration with live progress and zero page-reload geometry swap
 * - Before / After comparison mode
 */

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';

const $ = (id) => document.getElementById(id);
const fmt = (n) => (n != null ? Number(n).toLocaleString('ro-RO') : '–');

export class GameWorldEditor {
  constructor() {
    this.params = new URLSearchParams(window.location.search);
    this.projectId = this.params.get('project') || null;
    this.cityParam = this.params.get('city') || null;

    this.state = {
      world: null,
      presets: [],
      chunks: new Map(), // chunk_id -> chunk data
      loadedChunkMeshes: new Map(), // chunk_id -> THREE.Group
      activeChunkId: null,
      selectedObject: null,
      streamingRadiusM: 1800.0,
      isBeforeAfterActive: false,
      layersVisible: {
        terrain: true,
        roads: true,
        buildings: true,
        water: true,
        vegetation: true,
        props: true,
        biomes: false,
        chunkGrid: true,
        collision: false,
      },
      stats: {
        totalTriangles: 0,
        totalObjects: 0,
        fps: 0,
      },
    };

    this.initThree();
    this.initEvents();
  }

  /* ------------------------------------------------------------- 1. Three.js Engine Init */
  initThree() {
    const appEl = $('app');
    this.renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5));
    this.renderer.setSize(window.innerWidth, window.innerHeight);
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.05;
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    appEl.appendChild(this.renderer.domElement);

    this.scene = new THREE.Scene();
    this.scene.fog = new THREE.Fog(0xc9d6e2, 1200, 4500);

    this.camera = new THREE.PerspectiveCamera(48, window.innerWidth / window.innerHeight, 1, 50000);
    this.camera.position.set(450, 420, 650);

    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.06;
    this.controls.maxPolarAngle = Math.PI * 0.495;
    this.controls.minDistance = 5;
    this.controls.maxDistance = 35000;
    this.controls.screenSpacePanning = false;

    // Groups
    this.rootGroup = new THREE.Group();
    this.rootGroup.name = "WORLD_ROOT";
    this.scene.add(this.rootGroup);

    this.chunkGridGroup = new THREE.Group();
    this.chunkGridGroup.name = "CHUNK_GRID_OVERLAY";
    this.scene.add(this.chunkGridGroup);

    this.layersGroup = new THREE.Group();
    this.layersGroup.name = "LAYERS_GROUP";
    this.rootGroup.add(this.layersGroup);

    // Sky Dome
    const skyGeo = new THREE.SphereGeometry(45000, 32, 16);
    const skyMat = new THREE.ShaderMaterial({
      side: THREE.BackSide,
      depthWrite: false,
      uniforms: {
        top: { value: new THREE.Color(0x4a7fb5) },
        bottom: { value: new THREE.Color(0xdfe8ef) },
      },
      vertexShader: `varying float h; void main(){ h = normalize(position).y;
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }`,
      fragmentShader: `varying float h; uniform vec3 top; uniform vec3 bottom;
        void main(){ float t = clamp(h*1.15+0.15, 0.0, 1.0);
        gl_FragColor = vec4(mix(bottom, top, pow(t,0.85)), 1.0); }`,
    });
    this.scene.add(new THREE.Mesh(skyGeo, skyMat));

    // Sun & Ambient Lighting
    this.hemiLight = new THREE.HemisphereLight(0xbcd6f2, 0x6b6a5e, 1.05);
    this.scene.add(this.hemiLight);

    this.sunLight = new THREE.DirectionalLight(0xfff2dc, 2.35);
    this.sunLight.castShadow = true;
    this.sunLight.shadow.mapSize.set(2048, 2048);
    this.sunLight.shadow.camera.near = 10;
    this.sunLight.shadow.bias = -0.0005;
    this.scene.add(this.sunLight);
    this.scene.add(this.sunLight.target);

    this.loader = new GLTFLoader();
    this.raycaster = new THREE.Raycaster();
    this.mouse = new THREE.Vector2();
    this.clock = new THREE.Clock();

    this.spinning = false;
    this.updateSun();
  }

  /* ------------------------------------------------------------- 2. Event Listeners & UI Binding */
  initEvents() {
    window.addEventListener('resize', () => {
      this.camera.aspect = window.innerWidth / window.innerHeight;
      this.camera.updateProjectionMatrix();
      this.renderer.setSize(window.innerWidth, window.innerHeight);
    });

    // 3D Raycasting / Object Selection
    this.renderer.domElement.addEventListener('click', (e) => this.onCanvasClick(e));

    // Keyboard Shortcuts
    window.addEventListener('keydown', (e) => {
      if (e.code === 'Space' && e.target.tagName !== 'INPUT' && e.target.tagName !== 'SELECT') {
        e.preventDefault();
        this.spinning = !this.spinning;
        if ($('spin')) $('spin').checked = this.spinning;
      } else if (e.code === 'KeyC') {
        this.toggleBeforeAfterCompare();
      } else if (e.code === 'KeyF' && this.state.activeChunkId) {
        this.focusOnChunk(this.state.activeChunkId);
      }
    });

    // Standard Buttons
    if ($('sun')) $('sun').addEventListener('input', () => this.updateSun());
    if ($('shadows')) $('shadows').addEventListener('change', (e) => {
      this.renderer.shadowMap.enabled = e.target.checked;
      this.scene.traverse((o) => { if (o.isMesh && o.material) o.material.needsUpdate = true; });
    });
    if ($('wire')) $('wire').addEventListener('change', (e) => {
      this.rootGroup.traverse((o) => {
        if (o.isMesh && o.material) o.material.wireframe = e.target.checked;
      });
    });
    if ($('spin')) $('spin').addEventListener('change', (e) => { this.spinning = e.target.checked; });
    if ($('reset')) $('reset').addEventListener('click', () => this.frameView());
    if ($('shot')) $('shot').addEventListener('click', () => this.takeScreenshot());
    if ($('back')) $('back').addEventListener('click', () => {
      window.location.href = this.params.get('back') || '/';
    });

    // Editor Controls
    if ($('stylePresetSelect')) {
      $('stylePresetSelect').addEventListener('change', (e) => this.updateWorldSettings({ style_preset: e.target.value }));
    }
    if ($('vegDensityRange')) {
      $('vegDensityRange').addEventListener('input', (e) => {
        const val = parseFloat(e.target.value);
        if ($('vegDensityLabel')) $('vegDensityLabel').textContent = `${val.toFixed(1)}x`;
      });
      $('vegDensityRange').addEventListener('change', (e) => {
        this.updateWorldSettings({ dressing_density: parseFloat(e.target.value) });
      });
    }
    if ($('btnRegenChunk')) {
      $('btnRegenChunk').addEventListener('click', () => this.regenerateSelectedChunk());
    }
    if ($('btnToggleExclusion')) {
      $('btnToggleExclusion').addEventListener('click', () => this.toggleChunkExclusion());
    }
    if ($('btnCompareBeforeAfter')) {
      $('btnCompareBeforeAfter').addEventListener('click', () => this.toggleBeforeAfterCompare());
    }
    if ($('btnInspectClose')) {
      $('btnInspectClose').addEventListener('click', () => this.clearSelection());
    }
  }

  /* ------------------------------------------------------------- 3. Boot & Data Fetching */
  async boot() {
    try {
      if (this.projectId) {
        await this.bootProjectMode(this.projectId);
      } else if (this.cityParam) {
        await this.bootLegacyCityMode(this.cityParam);
      } else {
        await this.showProjectPicker();
      }
    } catch (err) {
      console.error(err);
      this.showError(`Eroare la pornire editor:\n${err.message}`);
    }
  }

  async bootProjectMode(pid) {
    this.setLoader(true, `Încarc starea lumii pentru proiectul ${pid}…`, 15);
    const r = await fetch(`/api/projects/${pid}/world-state`);
    if (!r.ok) throw new Error(`HTTP ${r.status} la /api/projects/${pid}/world-state`);
    const worldData = await r.json();
    this.state.world = worldData;

    // Load available style presets
    try {
      const pr = await fetch('/api/styles/presets');
      if (pr.ok) {
        const presetsData = await pr.json();
        this.state.presets = presetsData.presets || [];
        this.populatePresetsDropdown(worldData.style_preset);
      }
    } catch (e) {
      console.warn("Nu s-au putut încărca preset-urile de stil:", e);
    }

    // Set Header
    if ($('siteName')) $('siteName').textContent = worldData.name || pid;
    if ($('siteSub')) {
      const dim = worldData.dimensions;
      $('siteSub').textContent =
        `${((dim.size_x_m || 0)/1000).toFixed(1)} × ${((dim.size_y_m || 0)/1000).toFixed(1)} km · ` +
        `${dim.rows}×${dim.cols} chunk-uri (${dim.chunk_size_m}m) · Stil: ${worldData.style_preset}`;
    }

    // Initialize chunks map
    this.state.chunks.clear();
    for (const ch of (worldData.chunks || [])) {
      this.state.chunks.set(ch.id, ch);
    }

    // Build 3D Chunk Grid Overlay
    this.buildChunkGridOverlay();

    // Populate Layers Panel
    this.initLayersControl();

    // Set vegetation slider value
    if ($('vegDensityRange')) {
      const d = worldData.dressing_density ?? 1.0;
      $('vegDensityRange').value = d;
      if ($('vegDensityLabel')) $('vegDensityLabel').textContent = `${d.toFixed(1)}x`;
    }

    // Initial progressive chunk load
    await this.updateProgressiveStreaming(true);

    this.frameView();
    this.updateStats();
    this.setLoader(false);
  }

  async bootLegacyCityMode(cityPath) {
    this.setLoader(true, `Încarc orașul din ${cityPath}…`, 25);
    const r = await fetch(`${cityPath}/manifest.json`);
    if (!r.ok) throw new Error(`Nu găsesc ${cityPath}/manifest.json`);
    const manifest = await r.json();

    if ($('siteName')) $('siteName').textContent = manifest.area?.name || 'oraș 3D';
    if ($('siteSub')) {
      const s = ((manifest.area?.size_m || 0) / 1000).toFixed(2);
      $('siteSub').textContent = `${s} × ${s} km · ${manifest.generator}`;
    }

    const layers = manifest.layers || Object.keys(manifest.files || {});
    let done = 0;
    for (const layer of layers) {
      const file = manifest.files?.[layer]?.glb || `glb/${layer}.glb`;
      this.setLoader(true, `Încarc stratul ${layer}…`, Math.round(25 + (done / layers.length) * 70));
      try {
        const gltf = await this.loadGLB(`${cityPath}/${file}`);
        const root = gltf.scene;
        root.name = `layer-${layer}`;
        this.prepareMesh(root, layer);
        this.layersGroup.add(root);
        done++;
      } catch (e) {
        console.warn(`Layer ${layer} missing:`, e);
      }
    }

    this.frameView();
    this.setLoader(false);
  }

  async showProjectPicker() {
    this.setLoader(true, "Încarc lista de proiecte…", 50);
    const r = await fetch('/api/projects');
    const data = await r.json();
    const projects = data.projects || [];

    if (projects.length === 0) {
      this.showError("Nu există niciun proiect creat. Deschide generatorul din meniu pentru a crea primul proiect.");
      return;
    }

    // Modal selector
    let html = `<div class="project-picker-modal panel">
      <h2>Selectează o Lume pentru Inspecție & Editare</h2>
      <div class="projects-list">`;
    for (const p of projects) {
      html += `
        <div class="project-card" data-pid="${p.id}">
          <h3>${p.name}</h3>
          <div class="meta">${(p.dimensions?.size_x_m/1000).toFixed(1)} km · ${p.chunks?.count || 0} chunk-uri · ${p.status || 'nou'}</div>
          <button class="btn btn-primary" onclick="window.location.search='?project=${p.id}'">Deschide Lumea 3D</button>
        </div>`;
    }
    html += `</div></div>`;

    const wrap = document.createElement('div');
    wrap.className = 'modal-backdrop';
    wrap.innerHTML = html;
    document.body.appendChild(wrap);
    this.setLoader(false);
  }

  /* ------------------------------------------------------------- 4. Progressive Chunk Streaming */
  async updateProgressiveStreaming(forceInitial = false) {
    if (!this.state.world) return;
    const target = this.controls.target;

    for (const [cid, ch] of this.state.chunks.entries()) {
      const bp = ch.bounds_projected;
      if (!bp) continue;

      const cx = (bp.min_x + bp.max_x) / 2.0;
      const cz = (bp.min_y + bp.max_y) / 2.0;

      // Distance to camera target
      const dist = Math.hypot(target.x - cx, target.z - (-cz));
      const shouldBeLoaded = dist <= this.state.streamingRadiusM;

      if (shouldBeLoaded && !this.state.loadedChunkMeshes.has(cid)) {
        // Stream in chunk
        this.loadChunkMesh(cid);
      } else if (!shouldBeLoaded && !forceInitial && this.state.loadedChunkMeshes.has(cid)) {
        // Stream out chunk to free VRAM
        if (dist > this.state.streamingRadiusM * 1.5) {
          this.unloadChunkMesh(cid);
        }
      }
    }
  }

  async loadChunkMesh(cid) {
    if (!this.projectId) return;
    try {
      // Chunk GLB path on backend
      const glbUrl = `/api/projects/${this.projectId}/files/chunks/${cid}/chunk.glb`;
      const gltf = await this.loadGLB(glbUrl);
      const root = gltf.scene;
      root.name = `chunk-${cid}`;
      root.userData = { chunk_id: cid, type: 'chunk_root' };

      root.traverse((child) => {
        if (child.isMesh) {
          child.userData.chunk_id = cid;
          child.castShadow = true;
          child.receiveShadow = true;
          if (child.material) {
            child.material.wireframe = $('wire')?.checked || false;
          }
        }
      });

      this.layersGroup.add(root);
      this.state.loadedChunkMeshes.set(cid, root);
      this.updateChunkBoxVisual(cid, 'loaded');
    } catch (e) {
      // Chunk not yet generated or missing file
      this.updateChunkBoxVisual(cid, 'unrendered');
    }
  }

  unloadChunkMesh(cid) {
    const mesh = this.state.loadedChunkMeshes.get(cid);
    if (!mesh) return;

    this.layersGroup.remove(mesh);
    mesh.traverse((o) => {
      if (o.geometry) o.geometry.dispose();
      if (o.material) {
        if (Array.isArray(o.material)) o.material.forEach((m) => m.dispose());
        else o.material.dispose();
      }
    });
    this.state.loadedChunkMeshes.delete(cid);
    this.updateChunkBoxVisual(cid, 'unloaded');
  }

  /* ------------------------------------------------------------- 5. 3D Chunk Grid Overlay */
  buildChunkGridOverlay() {
    while (this.chunkGridGroup.children.length > 0) {
      this.chunkGridGroup.remove(this.chunkGridGroup.children[0]);
    }

    for (const ch of this.state.chunks.values()) {
      const bp = ch.bounds_projected;
      if (!bp) continue;

      const w = bp.max_x - bp.min_x;
      const h = bp.max_y - bp.min_y;
      const cx = (bp.min_x + bp.max_x) / 2.0;
      const cz = (bp.min_y + bp.max_y) / 2.0;

      // 3D Wireframe Box bounding chunk
      const boxGeo = new THREE.BoxGeometry(w, 80.0, h);
      const edges = new THREE.EdgesGeometry(boxGeo);
      const isReady = ch.status === 'ready';
      const color = isReady ? 0x35d0a5 : 0x728394;

      const lineMat = new THREE.LineBasicMaterial({
        color: color,
        transparent: true,
        opacity: isReady ? 0.65 : 0.25,
      });

      const lineObj = new THREE.LineSegments(edges, lineMat);
      lineObj.name = `box-${ch.id}`;
      lineObj.position.set(cx, 40.0, -cz);
      lineObj.userData = { chunk_id: ch.id, type: 'chunk_grid_box' };

      this.chunkGridGroup.add(lineObj);
    }
  }

  updateChunkBoxVisual(cid, status) {
    const box = this.chunkGridGroup.getObjectByName(`box-${cid}`);
    if (!box) return;

    if (cid === this.state.activeChunkId) {
      box.material.color.setHex(0x38bdf8);
      box.material.opacity = 0.95;
    } else if (status === 'loaded') {
      box.material.color.setHex(0x35d0a5);
      box.material.opacity = 0.65;
    } else {
      box.material.color.setHex(0x728394);
      box.material.opacity = 0.25;
    }
  }

  /* ------------------------------------------------------------- 6. Interactive Raycast Inspector */
  onCanvasClick(event) {
    // Only process pure click, not drag
    const rect = this.renderer.domElement.getBoundingClientRect();
    this.mouse.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    this.mouse.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;

    this.raycaster.setFromCamera(this.mouse, this.camera);

    // 1. Check intersection with Chunk Grid Boxes
    const gridHits = this.raycaster.intersectObjects(this.chunkGridGroup.children, true);
    if (gridHits.length > 0) {
      const hitBox = gridHits[0].object;
      const cid = hitBox.userData.chunk_id;
      if (cid) {
        this.selectChunk(cid);
        return;
      }
    }

    // 2. Check intersection with 3D Scene Objects
    const sceneHits = this.raycaster.intersectObjects(this.layersGroup.children, true);
    if (sceneHits.length > 0) {
      const hit = sceneHits[0];
      this.inspectObject(hit);
    }
  }

  selectChunk(cid) {
    const prevCid = this.state.activeChunkId;
    this.state.activeChunkId = cid;

    if (prevCid) this.updateChunkBoxVisual(prevCid, 'unloaded');
    this.updateChunkBoxVisual(cid, 'selected');

    const ch = this.state.chunks.get(cid);
    if ($('selectedChunkLabel')) {
      $('selectedChunkLabel').textContent = `${cid} (r${ch?.row ?? '–'} c${ch?.col ?? '–'})`;
    }
    if ($('btnRegenChunk')) $('btnRegenChunk').disabled = false;
    if ($('btnToggleExclusion')) $('btnToggleExclusion').disabled = false;

    // Show chunk panel
    if ($('chunkInspectorPanel')) $('chunkInspectorPanel').style.display = 'block';
  }

  inspectObject(hit) {
    const obj = hit.object;
    const pt = hit.point;
    this.state.selectedObject = obj;

    const inspector = $('objectInspectorPanel');
    if (!inspector) return;

    inspector.style.display = 'block';

    const objName = obj.name || 'Obiect 3D';
    const cid = obj.userData?.chunk_id || 'Global';
    const proto = obj.userData?.terragen_prototype || obj.parent?.name || 'Mesh';
    const cat = obj.userData?.terragen_category || 'Teren / Structură';

    if ($('insName')) $('insName').textContent = objName;
    if ($('insCategory')) $('insCategory').textContent = cat;
    if ($('insPrototype')) $('insPrototype').textContent = proto;
    if ($('insChunk')) $('insChunk').textContent = cid;
    if ($('insCoords')) $('insCoords').textContent = `X: ${pt.x.toFixed(1)}m, Y: ${pt.y.toFixed(1)}m, Z: ${(-pt.z).toFixed(1)}m`;
    if ($('insAltitude')) $('insAltitude').textContent = `${pt.y.toFixed(2)} m`;
  }

  clearSelection() {
    if ($('objectInspectorPanel')) $('objectInspectorPanel').style.display = 'none';
    this.state.selectedObject = null;
  }

  focusOnChunk(cid) {
    const ch = this.state.chunks.get(cid);
    if (!ch || !ch.bounds_projected) return;
    const bp = ch.bounds_projected;
    const cx = (bp.min_x + bp.max_x) / 2.0;
    const cz = (bp.min_y + bp.max_y) / 2.0;

    this.controls.target.set(cx, 0.0, -cz);
    this.camera.position.set(cx + 400, 350, -cz + 450);
    this.controls.update();
  }

  /* ------------------------------------------------------------- 7. Live World Settings & Regeneration */
  populatePresetsDropdown(activePreset) {
    const sel = $('stylePresetSelect');
    if (!sel) return;
    sel.innerHTML = '';
    for (const p of this.state.presets) {
      const opt = document.createElement('option');
      opt.value = p.preset_id;
      opt.textContent = `${p.name} (${p.preset_id})`;
      if (p.preset_id === activePreset) opt.selected = true;
      sel.appendChild(opt);
    }
  }

  async updateWorldSettings(payload) {
    if (!this.projectId) return;
    try {
      const r = await fetch(`/api/projects/${this.projectId}/world-settings`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const res = await r.json();
      if (res.ok) {
        if (payload.style_preset && $('siteSub')) {
          this.state.world.style_preset = payload.style_preset;
        }
        this.showToast("Setările lumii au fost actualizate.");
      }
    } catch (e) {
      console.error(e);
      this.showError("Nu s-au putut salva setările lumii.");
    }
  }

  async regenerateSelectedChunk() {
    const cid = this.state.activeChunkId;
    if (!this.projectId || !cid) return;

    const btn = $('btnRegenChunk');
    if (btn) {
      btn.disabled = true;
      btn.textContent = '⏳ Regenerare…';
    }

    try {
      const r = await fetch(`/api/projects/${this.projectId}/chunks/${cid}/generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ style_preset: this.state.world?.style_preset }),
      });
      const data = await r.json();
      if (data.ok) {
        // Swap out old geometry and load fresh one
        this.unloadChunkMesh(cid);
        await this.loadChunkMesh(cid);
        this.showToast(`Chunk-ul ${cid} a fost regenerat cu succes!`);
      } else {
        this.showError(`Eroare la regenerare: ${data.error || 'necunoscută'}`);
      }
    } catch (e) {
      console.error(e);
      this.showError(`Eroare rețea: ${e.message}`);
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.textContent = '⚡ Regenerează Chunk';
      }
    }
  }

  toggleChunkExclusion() {
    const cid = this.state.activeChunkId;
    if (!cid) return;
    const ch = this.state.chunks.get(cid);
    if (!ch) return;

    ch.is_excluded = !ch.is_excluded;
    const btn = $('btnToggleExclusion');
    if (btn) {
      btn.textContent = ch.is_excluded ? 'Anulează Excluderea' : 'Marchează ca Exclus';
      btn.classList.toggle('btn-warning', ch.is_excluded);
    }
    this.showToast(`Chunk-ul ${cid} ${ch.is_excluded ? 'a fost exclus' : 'a fost inclus'}.`);
  }

  toggleBeforeAfterCompare() {
    this.state.isBeforeAfterActive = !this.state.isBeforeAfterActive;
    const active = this.state.isBeforeAfterActive;

    // Toggle dressing and props visibility
    this.layersGroup.traverse((o) => {
      if (o.userData?.terragen_category === 'nature' || o.userData?.terragen_category === 'prop') {
        o.visible = !active;
      }
    });

    if ($('compareBadge')) {
      $('compareBadge').style.display = active ? 'inline-block' : 'none';
    }
    this.showToast(active ? "Mod Comparare activat: Afișez terenul brut fără dressing." : "Revenit la modul complet stilizat.");
  }

  /* ------------------------------------------------------------- 8. Layer Controls & Helpers */
  initLayersControl() {
    const container = $('layers');
    if (!container) return;
    container.innerHTML = '';

    const layerItems = [
      { id: 'terrain', label: 'Teren (Relief)', color: '#8fa07a' },
      { id: 'roads', label: 'Drumuri & Trotuare', color: '#4d5561' },
      { id: 'buildings', label: 'Clădiri', color: '#d8cab2' },
      { id: 'water', label: 'Ape & Maluri', color: '#4a90a8' },
      { id: 'vegetation', label: 'Vegetație (Arbori)', color: '#487242' },
      { id: 'props', label: 'Mobilier & Props', color: '#e5b367' },
      { id: 'chunkGrid', label: 'Grilă Chunk-uri', color: '#38bdf8' },
    ];

    for (const item of layerItems) {
      const row = document.createElement('div');
      row.className = 'row layer-row';
      row.innerHTML = `
        <label>
          <input type="checkbox" checked data-layer="${item.id}" />
          <span class="swatch" style="background:${item.color}"></span>
          ${item.label}
        </label>
      `;
      container.appendChild(row);

      row.querySelector('input').addEventListener('change', (e) => {
        this.toggleLayer(item.id, e.target.checked);
      });
    }
  }

  toggleLayer(layerId, isVisible) {
    this.state.layersVisible[layerId] = isVisible;
    if (layerId === 'chunkGrid') {
      this.chunkGridGroup.visible = isVisible;
      return;
    }

    this.layersGroup.traverse((o) => {
      const name = (o.name || '').toLowerCase();
      const cat = (o.userData?.terragen_category || '').toLowerCase();
      if (name.includes(layerId) || cat === layerId) {
        o.visible = isVisible;
      }
    });
  }

  loadGLB(url) {
    return new Promise((res, rej) => {
      this.loader.load(
        url,
        res,
        undefined,
        (e) => rej(new Error(`Nu pot încărca ${url}: ${e?.message || e}`))
      );
    });
  }

  prepareMesh(mesh, layer) {
    mesh.traverse((o) => {
      if (!o.isMesh) return;
      const g = o.geometry;
      if (!g.attributes.normal) g.computeVertexNormals();
      const mat = new THREE.MeshStandardMaterial({
        vertexColors: !!g.attributes.color,
        roughness: 0.88,
        metalness: 0.05,
        side: THREE.FrontSide,
        flatShading: false,
      });
      o.material = mat;
      o.castShadow = true;
      o.receiveShadow = true;
    });
  }

  frameView() {
    const box = new THREE.Box3().setFromObject(this.rootGroup);
    if (box.isEmpty()) return;
    const center = box.getCenter(new THREE.Vector3());
    const size = box.getSize(new THREE.Vector3());
    const span = Math.max(size.x, size.z);

    this.controls.target.copy(center);
    this.camera.position.set(center.x + span * 0.7, center.y + span * 0.8, center.z + span * 1.1);
    this.controls.update();
  }

  updateSun() {
    const sunInput = $('sun');
    if (!sunInput) return;
    const h = parseFloat(sunInput.value || '14');
    const t = (h - 6) / 13;
    const az = Math.PI * (0.15 + 0.7 * t);
    const el = Math.sin(Math.PI * Math.max(0.02, Math.min(0.98, t))) * 1.15 + 0.06;
    const dist = 3500;
    const c = this.controls.target;

    this.sunLight.position.set(c.x + Math.cos(az) * dist, c.y + Math.sin(el) * dist, c.z + Math.sin(az * 0.8) * dist);
    this.sunLight.target.position.copy(c);

    const warm = 1 - Math.min(1, Math.abs(h - 13) / 7);
    this.sunLight.color.setHSL(0.10 - 0.06 * warm, 0.55 * warm, 0.62 + 0.18 * warm);
    this.sunLight.intensity = 0.6 + 2.0 * Math.min(1, Math.sin(el) * 1.5);
    this.hemiLight.intensity = 0.5 + 0.7 * Math.min(1, Math.sin(el) * 1.4);

    const hh = Math.floor(h);
    if ($('sunLabel')) {
      $('sunLabel').textContent = `soare ${String(hh).padStart(2, '0')}:${String(Math.round((h - hh) * 60)).padStart(2, '0')}`;
    }
  }

  takeScreenshot() {
    this.renderer.render(this.scene, this.camera);
    const a = document.createElement('a');
    a.href = this.renderer.domElement.toDataURL('image/png');
    a.download = `terragen_world_${this.projectId || 'scene'}.png`;
    a.click();
  }

  updateStats() {
    let triCount = 0;
    this.layersGroup.traverse((o) => {
      if (o.isMesh && o.geometry?.index) {
        triCount += o.geometry.index.count / 3;
      }
    });

    if ($('stats')) {
      $('stats').innerHTML =
        `<b>${fmt(triCount)}</b> triunghiuri în memorie · ` +
        `<b>${this.state.loadedChunkMeshes.size}</b> / ${this.state.chunks.size} chunk-uri active · ` +
        `<span class="fps"></span>`;
    }
  }

  setLoader(show, msg = '', pct = 0) {
    const l = $('loader');
    if (!l) return;
    if (show) {
      l.classList.remove('done');
      if ($('loadMsg')) $('loadMsg').textContent = msg;
      if ($('loadBar')) $('loadBar').style.width = `${pct}%`;
    } else {
      l.classList.add('done');
    }
  }

  showToast(msg) {
    const t = document.createElement('div');
    t.className = 'toast-message';
    t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(() => t.classList.add('fade-out'), 3000);
    setTimeout(() => t.remove(), 3500);
  }

  showError(msg) {
    const e = $('err');
    if (e) {
      e.style.display = 'block';
      e.textContent = msg;
    }
    this.setLoader(false);
  }

  /* ------------------------------------------------------------- 9. Render Loop */
  animate() {
    let acc = 0, frames = 0, fps = 0;
    const tick = () => {
      const dt = this.clock.getDelta();
      if (this.spinning) {
        const a = 0.12 * dt;
        const p = this.camera.position.clone().sub(this.controls.target);
        const cos = Math.cos(a), sin = Math.sin(a);
        this.camera.position.set(
          this.controls.target.x + p.x * cos - p.z * sin,
          this.camera.position.y,
          this.controls.target.z + p.x * sin + p.z * cos
        );
        this.controls.update();
      }

      this.controls.update();

      // Progressive streaming check on movement
      if (frames % 60 === 0) {
        this.updateProgressiveStreaming();
      }

      this.renderer.render(this.scene, this.camera);

      acc += dt;
      frames++;
      if (acc > 0.6) {
        fps = Math.round(frames / acc);
        acc = 0;
        frames = 0;
        const f = $('stats')?.querySelector('.fps');
        if (f) f.textContent = ` · ${fps} fps`;
      }
      requestAnimationFrame(tick);
    };
    tick();
  }
}

