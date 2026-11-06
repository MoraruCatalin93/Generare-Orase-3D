# Changelog

Toate schimbările notabile ale proiectului. Formatul urmează
[Keep a Changelog](https://keepachangelog.com/ro/1.1.0/), versiunile
[SemVer](https://semver.org/lang/ro/).

## [2.0.0] — 2026-11-06

Rescriere ca pipeline web: **fără Unity, fără Blender**. Rezultatul se vede direct în browser.

### Adăugat
- **Viewer 3D în browser** (three.js, local, fără CDN): straturi on/off, umbre, oră din zi,
  wireframe, rotire automată, export PNG.
- **Textură de fațadă procedurală** (`terragen/facade.py`): o celulă generată în cod, inclusă în GLB,
  înmulțită cu culoarea per vertex → ferestre corecte în orice engine, fără shader custom.
- **CLI unificat**: `python3 -m terragen serve | generate | build | check`.
- **Site static** (`terragen/site.py`): un oraș generat devine folder autonom (viewer inclus), gata de
  urcat pe orice hosting.
- **Progres în timp real** prin Server-Sent Events, cu buton de anulare.
- **18 teste** pytest, offline (DEM fals + OSM sintetic), plus CI pe Python 3.10 / 3.12 / 3.13.
- `pyproject.toml`, `LICENSE` (MIT), `ruff` pentru lint și formatare.
- Curățare automată a joburilor vechi și o singură generare concurentă (semafor).

### Schimbat
- **Pereți flat-shaded**: fiecare muchie de contur are propriile vârfuri → colțurile clădirilor sunt
  ascuțite (înainte, normalele medii „umflau" colțurile).
- **Drumuri drapate pe teren** (contururi densificate + altitudine din DEM) în loc de altitudinea
  celui mai apropiat punct de pe axă (care terasa asfaltul pe pante).
- **Acoperișurile**: corectat winding-ul fețelor în două ape (normalele ieșeau în jos).
- Curățenie: scos `render.py` (preview-ul îl face viewer-ul), parametri morți, funcții nefolosite.

### Scos
- `unity/` (shader URP + `RoadNetworkLoader`), `blender/` (importer), editorul separat de zone,
  `out/` cu artefacte, `repomix-output.xml`.

### Reparat
- Cache-ul DEM se scrie atomic (două generări simultane nu mai corup tile-urile).
- Golurile din mozaicul DEM se umplu cu cel mai apropiat pixel valid (nu cu mediana).

## [1.5.0] — versiunea anterioară
Editor de zone pe hartă, cartiere și dale de 400 m, import Blender, shader Unity URP cu ferestre
procedurale. Codul e păstrat pe branch-ul `legacy-1.x`.
