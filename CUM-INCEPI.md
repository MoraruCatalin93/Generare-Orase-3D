# terragen-web — cum începi (macOS + VS Code)

**Ce e:** selectezi o zonă pe hartă → apeși un buton → primești orașul 3D (teren cu relief, drumuri,
trotuare, clădiri cu acoperișuri și ferestre) și îl vezi **în browser**, fără Unity, fără Blender.
Exportul e GLB standard, bun pentru orice engine.

---

## Fișierele din folderul de descărcări

| fișier | ce faci cu el |
|---|---|
| **`terragen-setup.sh`** (≈780 KB) | **îl rulezi o dată.** Conține tot proiectul înăuntru (self-extracting). Instalează în `~/terragen-web`, creează `.venv`, instalează dependențele, rulează testele. |
| **`terragen-run.sh`** (≈2 KB) | pornește aplicația de fiecare dată (fără să instaleze nimic). Găsește singur instalarea. |
| `terragen-web.zip` (≈1,2 MB) | alternativă: proiectul ca arhivă normală, dacă preferi să-l deschizi direct în VS Code. |

---

## Pași (2 minute)

### 1. Descarcă `terragen-setup.sh` în `~/Downloads`

### 2. Deschide VS Code → `Terminal → New Terminal` și rulează:

```bash
cd ~/Downloads
bash terragen-setup.sh
```

Ce vei vedea (pe scurt):

```
▸ caut Python…            ✔ Python 3.12 (/usr/bin/python3)
▸ despachetez proiectul…  ✔ despachetat (2.5M)
▸ copiez în /Users/<tu>/terragen-web …
▸ creez mediul virtual .venv…
▸ instalez dependențele (1–2 minute prima dată)…  ✔
▸ verific instalarea…     ✔ teste: 18 passed
✔ Gata!
```

> Dacă macOS cere instalarea *Command Line Tools* pentru `python3`, acceptă (sau `brew install python@3.12`).

### 3. Pornește aplicația

```bash
cd ~/terragen-web
./start.sh
```

Se deschide automat **http://127.0.0.1:8777**. (Sau, de oriunde: `bash ~/Downloads/terragen-run.sh`.)

### 4. În aplicație

1. **Harta** — centrată pe Târgu Mureș. Taste: `t` = relief on/off, `s` = satelit.
2. **„▭ Selectează pe hartă"** → tragi un dreptunghi (sau scrii lat/lon + latura în metri).
3. Alegi detaliul terenului + seed-ul → **„⬢ Generează orașul 3D"**.
4. Progresul curge în timp real (6 pași). La final:
   - **◱ Deschide viewer 3D** — orașul, în browser (umbre, straturi on/off, oră din zi, export PNG);
   - **⬇ ZIP** — tot folderul, dacă vrei să-l muți;
   - **📂 folder** — deschide `sites/<nume>/` în Finder.

### 5. Datele OSM (recomandat, o singură dată)

Fără fișier local, aplicația ia datele din **Overpass API** — merge pentru zone de 1–2 km, dar e
uneori aglomerat (poate dura 30–60 s). Pentru viteză:

- apasă **„⬇ descarcă România (~330 MB)"** în panou — salvează `romania.osm.pbf` în proiect;
- de atunci generarea e **offline** și de ~10× mai rapidă (prima citire ~2–4 min, apoi cache).

---

## Ce iese în `sites/<nume>/`

```
manifest.json            tot: zonă, sisteme de coordonate, statistici
glb/terrain.glb          teren cu relief (culori după altitudine/pantă)
glb/roads.glb            carosabil (mesh, lățimi reale din OSM)
glb/sidewalks.glb        trotuare, poteci, treceri
glb/buildings.glb        clădiri + acoperișuri + FERESTRE (textura e în GLB)
glb/green|forest|water|pitch|parking|industrial|other.glb
data/roads_network.json  noduri + segmente (trafic / AI / minimap)
terrain/…257.r16         heightmap 16 biți (dacă vrei Terrain în orice engine)
```

**Import:** Godot 4 (drag & drop), Unreal (glTF), Blender (`File → Import → glTF 2.0`), three.js
(`GLTFLoader`), Unity (`glTFast`). 1 unitate = 1 metru, originea = centrul zonei.

---

## Comenzi utile (CLI)

```bash
python3 -m terragen serve                      # aplicația (hartă + generator + viewer)
python3 -m terragen generate --center cluj --size 1500 --out sites/cluj
python3 -m terragen generate --center 46.5446,24.5612 --size 800 --seed 42
python3 -m terragen build --site sites/cluj --out dist    # site static (viewer inclus)
python3 -m terragen check                      # verifică instalarea
python3 -m pytest tests -q                     # 18 teste, offline
```

`./start.sh --port 8780` dacă portul 8777 e ocupat. `Ctrl+C` oprește serverul.

---

## Dacă ceva nu merge

| simptom | soluție |
|---|---|
| `permission denied: ./start.sh` | `bash start.sh` sau `chmod +x start.sh` |
| `python3: command not found` | `brew install python@3.12` (sau instalează de pe python.org) |
| `Port 8777 is in use` | `./start.sh --port 8780` |
| harta apare goală | e nevoie de internet pentru tile-uri (generarea, după ce ai `.osm.pbf`, merge offline) |
| „Overpass API" dă erori | descarcă `.osm.pbf`-ul din buton, sau încearcă o zonă mai mică (700–1000 m) |
| vreau să resetez tot | `rm -rf ~/terragen-web/.venv && ./start.sh` |

---

## Licențe

Cod: **MIT**. Datele: © OpenStreetMap contributors (**ODbL**, atribuire obligatorie) · relief:
Terrarium/AWS. Harta din UI folosește tile-uri OSM publice și, opțional, imagini satelit Esri
(pentru uz comercial, pune propriul tile server).
