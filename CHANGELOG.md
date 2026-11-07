# Changelog

Toate schimbările notabile ale proiectului. Formatul urmează
[Keep a Changelog](https://keepachangelog.com/ro/1.1.0/), versiunile
[SemVer](https://semver.org/lang/ro/).

## [2.1.2] — 2026-11-07

### Unificare și optimizare pipeline generare
- **Unificare motor de generare**: conectat fluxul de generare simplu (`cli.generate`) cu cel bazat pe proiecte (`ProjectManager` și `GenerationService`).
- **Suport relief sculptat**: modificatorii aplicați în editorul de teren (`TerrainEditor`) se propagă automat prin `ModifiedDem` în modelele 3D finale (teren, drumuri, clădiri) și în heightmap.
- **Eliminat cod duplicat**: `ChunkEngine` reutilizează funcțiile partajate din `terragen/build.py` (`building_height`, `building_color`, `road_width`), asigurând aspect vizual identic între bucățile de lume (chunk-uri) și întreaga hartă.
- **Păstrat formatul compatibil**: fișierele și structura din `sites/` rămân 100% neschimbate, compatibile cu viewer-ul 3D.
- **Suită de teste**: 169 de teste automate active, toate verzi.

## [2.1.1] — 2026-11-07

### Corectat și actualizat (Documentație)
- **Eliminat contradicțiile legate de Blender**: Actualizat documentația pentru a reflecta pipeline-ul dedicat de export pentru Blender (Layer Mode, Chunk Mode, script importator autonom `import_to_blender.py`, colecții și deduplicare de materiale).
- **Corectat pașii de pornire în `CUM-INCEPI.md`**: Eliminat referințele către fișiere inexistente (`terragen-setup.sh`, `terragen-run.sh`, `terragen-web.zip`); rescris ghidul pas cu pas pe baza fluxului real din `README.md` (`git clone` și `./start.sh` / `.\start.ps1`).
- **Corectat numărul de teste în toate documentele**: Actualizat la numărul real verificat de **163 de teste** automate (100% offline).
- **Linkuri relative în `docs/DEVELOPER_GUIDE.md`**: Înlocuit toate legăturile absolute de tip `file:///Users/morarucatalin/...` cu căi relative portabile (`../terragen/...`).
- **Actualizat structura modulelor în `README.md`**: Documentat noile subsisteme (`blender_export.py`, `terrain_editor.py`, `styles.py`, `projects/`, `services/`) și clarificat suportul pentru vegetație procedurală stilizată.

## [2.1.0] — 2026-11-06

### Adăugat
- **Relații OSM `type=multipolygon`** — clădiri cu curte interioară (mall-uri, mănăstiri, muzee),
  parcuri/păduri cu lacuri și orice zonă definită ca `relation` ajung acum în oraș, cu **găuri reale**:
  zidurile apar și pe conturul interior, iar suprafața verde/apă nu acoperă golul.
  - modul nou `terragen/multipolygon.py`: închide inelele sparte în mai multe way-uri, atribuie
    găurile conturului care le conține, ignoră inelele rupte (extract tăiat la bbox);
  - citite din **toate** sursele: Overpass (`rel[...] ... out body geom`), pyosmium (handler `relation`),
    osmium-tool (`osmium export` păstrează acum găurile) și cititorul intern PBF (câmpul 4 = Relation);
  - `Way` are acum `rings` (exterior + găuri); `CityBuilder.shape()` construiește poligonul cu găuri
    pentru clădiri **și** pentru straturile de zone;
  - statistici noi în `manifest.json`: `buildings_from_relations`, `areas_from_relations`.

### Reparat
- `terragen --version` raporta versiunea hardcodată; acum ia `__version__` dintr-un singur loc.
- Overpass: interogarea cerută include relațiile, iar răspunsul e cerut cu `out body geom` (fără
  `body`, Overpass nu trimite membrii relației).

### Teste
- 33 de teste (de la 24): asamblare inele (spart/găuri/rupt), un `.osm.pbf` **sintetic** construit
  octet cu octet în test pentru relații, plus construcție reală (biserică cu curte, parc cu lac).

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
- Curățenie (audit cu `vulture` + `ruff`): scos `render.py` (preview-ul îl face viewer-ul),
  funcțiile nefolosite din `facade.py` și `Mesh.bounds`, cheile de paletă moarte
  (`asphalt_minor`, `grass`, `roof`), parametrul `preview` din `generate()` și variabilele
  nefolosite din `pbflite.py`. Versiunea are acum o singură sursă (`terragen/__init__.py`).
- 6 teste de igienă noi (`tests/test_project_hygiene.py`): versiune unică, `.gitignore` complet,
  nimic generat în git, fără CDN în UI/viewer, fără rămășițe de debug (`console.log`, `breakpoint()`).
- Config de editor: `.vscode/` (settings, launch pentru serve/teste, extensii recomandate),
  `.editorconfig` actualizat.

### Scos
- `unity/` (shader URP + `RoadNetworkLoader`), `blender/` (importer), editorul separat de zone,
  `out/` cu artefacte, `repomix-output.xml`.

### Reparat
- Cache-ul DEM se scrie atomic (două generări simultane nu mai corup tile-urile).
- Golurile din mozaicul DEM se umplu cu cel mai apropiat pixel valid (nu cu mediana).

## [1.5.0] — versiunea anterioară
Editor de zone pe hartă, cartiere și dale de 400 m, import Blender, shader Unity URP cu ferestre
procedurale. Codul e păstrat pe branch-ul `legacy-1.x`.
