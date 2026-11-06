# Contribuție

## Setup

```bash
git clone https://github.com/MoraruCatalin93/Generare-Orase-3D.git
cd Generare-Orase-3D
./start.sh                 # creează .venv + instalează dependențele + pornește
# sau, manual:
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && pip install ruff pytest
```

## Reguli de bază

- **Testele trec** înainte de PR: `pytest -q` (rulează offline, fără rețea).
- **Lint curat**: `ruff check . && ruff format --check .` (sau `ruff format .` înainte de commit).
- **Fără artefacte în repo**: `sites/`, `dist/`, `.venv/`, `*.osm.pbf` sunt în `.gitignore`.
  Capturile pentru documentație stau în `docs/`.
- **Un PR = o schimbare**. „Curățenie + feature + refactor" într-un singur PR e greu de revizuit.

## Unde se schimbă ce

| vrei să schimbi | fișier |
|---|---|
| lățimi de drum, înălțimi, culori, acoperișuri | `terragen/build.py` |
| ferestre (proporții, culoare, montanți) | `terragen/facade.py` |
| module de fațadă (3,2 m) și etaj (3,1 m) | `terragen/geom.py` (`MODULE_U`, `FLOOR_H`) |
| triangulare / extrudare / UV-uri | `terragen/geom.py` |
| format GLB / OBJ / heightmap | `terragen/export.py` |
| citire OSM (PBF, Overpass) | `terragen/osmdata.py`, `terragen/pbflite.py` |
| rută API, progres, fișiere | `terragen/server.py` |
| hartă, selecție zonă, panou | `terragen/web/ui/` |
| viewer 3D | `terragen/web/viewer/index.html` |

## Teste

Testele nu ating rețeaua: `tests/conftest.py` definește un **DEM fals** și un **set OSM sintetic**
(grilă de drumuri + clădiri + parc). Dacă adaugi geometrie nouă, adaugă un test care verifică
**proprietatea** (normală în sus, UV în module, suprafață păstrată), nu doar că nu crapă.

## Commit-uri

```
build: generează GLB cu textura de fațadă încorporată
fix(geom): winding corect la acoperișurile în două ape
feat(viewer): slider pentru ora din zi
docs: cum se importă GLB-ul în Godot
```

## Licență

Contribuțiile intră sub licența MIT a proiectului (vezi [LICENSE](LICENSE)).
