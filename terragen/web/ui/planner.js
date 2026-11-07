/* Planificator local. Grila este o previzualizare geografică aproximativă. */
(() => {
  const key = 'terragen-grid-plan-v1';
  const blank = () => ({ type: 'FeatureCollection', features: [] });
  let plan = null;

  function valid(p) {
    return p && p.version === 1 && typeof p.name === 'string' && p.name.length >= 1 && p.name.length <= 32 &&
      Number.isFinite(p.lat) && p.lat >= -80 && p.lat <= 80 &&
      Number.isFinite(p.lon) && p.lon >= -179 && p.lon <= 179 &&
      Number.isInteger(p.size) && p.size >= 200 && p.size <= 6000 &&
      Number.isInteger(p.rows) && p.rows >= 1 && p.rows <= 8 &&
      Number.isInteger(p.cols) && p.cols >= 1 && p.cols <= 8;
  }

  function features(p) {
    if (!valid(p)) return blank();
    const dy = p.size / 111320;
    const dx = dy / Math.cos(p.lat * Math.PI / 180);
    const west = p.lon - p.cols * dx / 2;
    const south = p.lat - p.rows * dy / 2;
    const cells = [];
    for (let row = 0; row < p.rows; row++) {
      for (let col = 0; col < p.cols; col++) {
        const w = west + col * dx, e = west + (col + 1) * dx;
        const n = south + (p.rows - row) * dy, s = south + (p.rows - row - 1) * dy;
        cells.push({ type: 'Feature', properties: { row: row + 1, col: col + 1,
          lat: (s + n) / 2, lon: (w + e) / 2 },
          geometry: { type: 'Polygon', coordinates: [[[w,s],[e,s],[e,n],[w,n],[w,s]]] } });
      }
    }
    return { type: 'FeatureCollection', features: cells };
  }

  function render() {
    if (!map.isStyleLoaded()) return;
    if (!map.getSource('zone-grid')) map.addSource('zone-grid', { type: 'geojson', data: features(plan) });
    else map.getSource('zone-grid').setData(plan ? features(plan) : blank());
    if (!map.getLayer('zone-grid-fill')) map.addLayer({ id: 'zone-grid-fill', type: 'fill', source: 'zone-grid',
      paint: { 'fill-color': '#35d0a5', 'fill-opacity': 0.12 } });
    if (!map.getLayer('zone-grid-lines')) map.addLayer({ id: 'zone-grid-lines', type: 'line', source: 'zone-grid',
      paint: { 'line-color': '#35d0a5', 'line-width': 2, 'line-opacity': 0.85 } });
  }

  function apply(p, remember = true) {
    if (!valid(p)) throw new Error('Proiect invalid sau în afara limitelor.');
    plan = p;
    $('planName').value = p.name;
    $('planSize').value = p.size;
    $('planRows').value = p.rows;
    $('planCols').value = p.cols;
    $('planInfo').textContent = `${p.name}: ${p.rows} × ${p.cols} zone de ${p.size} m. Apasă o celulă.`;
    if (remember) { try { localStorage.setItem(key, JSON.stringify(p)); } catch (_) {} }
    render();
  }

  $('planCreate').addEventListener('click', () => {
    const c = map.getCenter();
    const p = { version: 1, name: $('planName').value.trim(), lat: c.lat, lon: c.lng,
      size: Number($('planSize').value), rows: Number($('planRows').value), cols: Number($('planCols').value) };
    try { apply(p); toast('Grila a fost creată. Selectează o celulă pe hartă.'); }
    catch (e) { toast(e.message, 5000); }
  });
  $('planSave').addEventListener('click', () => {
    if (!plan) { toast('Creează mai întâi o grilă.'); return; }
    const blob = new Blob([JSON.stringify(plan, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob), a = document.createElement('a');
    a.href = url; a.download = 'terragen-proiect-zone.json'; a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  $('planLoad').addEventListener('click', () => $('planFile').click());
  $('planFile').addEventListener('change', async (event) => {
    const file = event.target.files[0];
    if (!file) return;
    try {
      if (file.size > 10000) throw new Error('Fișierul este prea mare.');
      const loaded = JSON.parse(await file.text());
      apply(loaded);
      map.easeTo({ center: [loaded.lon, loaded.lat], duration: 500 });
      toast('Proiectul a fost încărcat.');
    } catch (e) { toast(`Nu pot încărca proiectul: ${e.message}`, 6000); }
    event.target.value = '';
  });
  $('planClear').addEventListener('click', () => {
    plan = null;
    try { localStorage.removeItem(key); } catch (_) {}
    $('planInfo').textContent = 'Grila a fost ascunsă. Poți crea alta sau încărca un proiect salvat.';
    render();
  });
  map.on('style.load', render);
  map.on('click', (event) => {
    if (!plan || state.selecting || !map.getLayer('zone-grid-fill')) return;
    const hit = map.queryRenderedFeatures(event.point, { layers: ['zone-grid-fill'] })[0];
    if (!hit) return;
    const row = Number(hit.properties.row), col = Number(hit.properties.col);
    setZone(Number(hit.properties.lat), Number(hit.properties.lon), plan.size);
    const slug = plan.name.toLowerCase().replace(/[^a-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '') || 'proiect';
    $('name').value = `${slug}-r${String(row).padStart(2, '0')}-c${String(col).padStart(2, '0')}`.slice(0, 48);
    $('planInfo').textContent = `Selectată: ${$('name').value}. Poți apăsa „Generează orașul 3D”.`;
  });
  try { const saved = JSON.parse(localStorage.getItem(key) || 'null'); if (valid(saved)) apply(saved, false); }
  catch (_) { /* un proiect local deteriorat este ignorat */ }
  if (map.isStyleLoaded()) render();
})();
