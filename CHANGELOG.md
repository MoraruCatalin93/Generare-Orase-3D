# Changelog

Toate schimbările notabile ale proiectului. Formatul urmează
[Keep a Changelog](https://keepachangelog.com/ro/1.1.0/), versiunile
[SemVer](https://semver.org/lang/ro/).

## [2.6.0] — 2026-11-07

### Game World Generator — Etapa 6: Blender Pipeline & Production Studio Scene Organization
- **Organizare Automată a Scenei în 9 Colecții de Studio sub `WORLD`**:
  - `WORLD`: Colecția rădăcină a proiectului.
  - `TERRAIN`: Chunks și relief continental.
  - `ROADS`: Rețea stradală și trotuare.
  - `BUILDINGS`: Arhitectură 3D extrudată cu fațade PBR și acoperișuri.
  - `WATER`: Corpuri hidrografice.
  - `VEGETATION`: Instanțiere arbori, arbuști și vegetație (Linked Duplicates).
  - `PROPS`: Instanțiere mobilier urban (felinare, bănci, ruine).
  - `COLLISION`: Geometrie simplificată pentru fizică (`hide_render = True`, afișare `WIRE`).
  - `LOD`: Variante de nivel de detaliu cu subcolecțiile `LOD_1` și `LOD_2` (`hide_render = True`, `hide_viewport = True`).
  - `REFERENCE`: Punct de referință `World_Origin` la $(0, 0, 0)$ cu metadata WGS84 și chenarele 3D ale grilei de chunk-uri (`Chunk_Bounds`).
- **Geometrie de Coliziune Dedicată**:
  - `terrain_collision.glb`: Teren decimat ($32 \times 32$) pentru motoare fizice (PhysX).
  - `buildings_collision.glb`: Box colliders simplificate calculate per clădire.
- **Variante LOD Generate Automat**:
  - `terrain_lod1.glb` (50% densitate poligoane) și `terrain_lod2.glb` (proxy).
- **Import Idempotent și Repetabil**:
  - Curățarea curată a obiectelor anterioare la re-import, garantând zero duplicate de tip `.001`, `.002`.
- **Filtrare Selectivă la Reimport**: Suport CLI (`--collections`, `--chunks`, `--layers`).
- **Verificare Headless în Blender 5.2.2 LTS**:
  - Adăugat `tests/test_blender_pipeline_headless.py`. Total: **206 teste automate**, 100% trecute (29.06s).

## [2.5.0] — 2026-11-07

### Game World Generator — Etapa 4: Procedural World Dressing & Object Instancing Engine
- **Pachet Modular de World Dressing (`terragen/dressing/`)**: Subsistem dedicat populării procedurale a lumii cu mii de instanțe 3D organizate pe categorii (Natură, Infrastructură, Environment Props).
- **Catalog de Prototipuri 3D (`AssetCatalog`)**:
  - Generatoare procedurale încorporate pentru prototipuri proxy ușoare: conifere (`tree_pine_01`), arbori de foioase (`tree_deciduous_01`), arbuști (`bush_temperate_01`), bolovani de granit (`rock_granite_boulder_01`), pietriș de țărm (`stone_pebble_cluster_01`), stâlpi de iluminat stradal (`lamp_post_urban_01`), bănci de parc (`bench_park_01`), ruine antice (`ruin_stone_colonnade_01`).
  - Suport complet pentru încărcare de modele externe GLB și variații LOD.
  - Export automat al prototipurilor unice în directorul `assets/prototypes/*.glb`.
- **Reguli Ecologice și Urbane Biome-Aware (`create_default_placement_rules()`)**:
  - Distribuție bazată pe matricea de biomi din Etapa 3 (densități țintă per hectar, multiplicatori pe biomi).
  - Pante minime și maxime permise (ex. bolovanii tolerează stânci până la $55^\circ$, în timp ce băncile și ruinele cer teren plat $<12^\circ$).
  - Zone tampon de protecție (Clearance Buffers): distanțe minime garantate față de axul drumurilor (`road_buffer_m`), contururile clădirilor (`building_buffer_m`) și corpurile de apă (`water_buffer_m`).
- **Indexare Spațială de Mare Viteză (`SpatialHashGrid`)**: Verificare de coliziune în $O(1)$ cu eliminarea oricărei suprapuneri între volumele de coliziune ale instanțelor și geometriei existente.
- **Plasare Liniară pentru Infrastructură Stradală**:
  - Eșantionare de-a lungul poliliniilor rutiere OSM la intervale regulate (28 m).
  - Calcul tangent și vector normal perpendicular pentru determinarea offset-ului pe marginea carosabilului și orientarea unghiulară (yaw) cu fața la stradă.
- **Grupare Naturală (Clustering)**:
  - Generare procedurală de pâlcuri și crânguri (boschete, arbuști, bolovani) în jurul instanțelor părinte, cu variație de scală ($0.65 - 1.0\times$) și dispersie radială.
- **Instanțiere Zero-VRAM pentru Blender**:
  - Export grupat pe colecții Blender (`Nature_Tree`, `Infrastructure_Lamp_Post`, `Environment_Props_Ruin`).
  - Scriptul `import_to_blender.py` importă un singur mesh per prototip și generează mii de **Linked Duplicates** (`bpy.data.objects.new(name, proto.data)`), reducând consumul VRAM la minimum absolut.
- **Determinizare Matematică și Integrare în ChunkEngine**:
  - Hash SHA-256 pe seed global, ID prototip și coordonate celulă garantează regenerare selectivă 100% reproductibilă la nivel de chunk.
  - Integrare completă în `ChunkEngine.populate_chunk_dressing()` și manifestul de chunk.
- **Suită de Teste**: Adăugat `tests/test_world_dressing.py` (13 teste noi). Total: **205 teste automate**, 100% trecute (19.62s).

### Game World Generator — Etapa 3: Terrain & Biome Generator
- **Pachet Modular de Biomi (`terragen/biomes/`)**: Creat subsistem procedural avansat pentru generarea și clasificarea a 10 biomi de joc: *Grassland*, *Forest*, *Dense Forest*, *Alpine*, *Rocky Mountains*, *Desert*, *Wetlands*, *Coastal*, *Snow* și *Urban*.
- **Analiză Topografică și de Mediu Vectorizată (`EnvironmentalAnalyzer`)**:
  - Calcul continuu pentru altitudine, pantă (gradient central cu inel de gardă), orientare teren (aspect pe $360^\circ$), umiditate estimată, distanță euclidiană la apă (EDT), temperatură (model adiabatic lapse rate: $T = T_{\text{base}} - 0.0065 \times Z$) și mască urbană.
- **Tranziții Continue & Ponderi Normalizate**: Membrii fuzzy calculați prin curbe cosinusoidale $C^1$-smooth; ponderile tuturor biomilor se normalizează la exact $1.0$ pe fiecare celulă a grilei.
- **Modificatori Manuali Reversibili (`BiomeOverrideManager`)**: Suport pentru poligoane arbitrare de override cu estompare perimetrică (`blend_distance_m`), menținând datele geografice originale 100% neatinse.
- **Continuitate Fără Cusături la Granițele Chunk-urilor**: Evaluare în coordonate metrice globale și guard-band de 1 punct exterior asigură discrepanță de zero la limita dintre oricare două chunk-uri adiacente.
- **Export Blender & Splatmaps**:
  - Generare automată de texturi splatmap RGBA pe 8 biți pentru nodurile de shader Blender/Unity (`biome_splatmap_0.png`), previzualizare RGB a biomilor dominanți (`biome_colormap.png`), manifest JSON cu procente de acoperire și integrare în `blender_manifest.json`.
  - Interpolare culori de biom per vertex (`sample_mesh_vertex_colors`) pentru vizualizare directă în glTF.
- **Scalabilitate pe Lumi Masive**:
  - Validat pe lumi de **10 km** ($10.000\text{m} \times 10.000\text{m}$ = 251.001 puncte) în **0.18s** și de **20 km** ($20.000\text{m} \times 20.000\text{m}$ = 641.601 puncte) în **0.46s**.
- **Suită de Teste**: Adăugat `tests/test_biome_engine.py` (10 teste). Total: **192 de teste automate**, 100% trecute (4.34s).

## [2.3.0] — 2026-11-07

### Game World Generator — Etapa 2: Visual Style Engine
- **Arhitectură Modulară (`terragen/styles/`)**: Decuplat datele geografice brute (DEM, OSM) de stilizarea vizuală. Creat clase tipizate pentru clădiri, vegetație, relief, drumuri, apă, iluminare și materiale PBR.
- **Cele 6 Stiluri Inițiale Implementate**:
  1. *Low Poly*: geometrie simplificată, flat shading, paletă pastel vibrată, copaci fațetați.
  2. *Hand Painted*: nuanțe calde saturate, finisaj mat tip guașă/pânză, iluminare de poveste.
  3. *Stylized Realistic* (pilot): exagerare relief 15%, materiale PBR calibrate (teren 0.85, clădiri 0.82, apă 0.12, asfalt 0.70), soare auriu cald și ceață de adâncime.
  4. *Cozy Cartoon*: forme rotunjite, clădiri compacte cu streșini mari, ape turcoaz.
  5. *Dark Fantasy*: relief abrupt (1.35x), culori desaturate, roci vulcanice închise, ceață joasă densă, soare palid la asfințit.
  6. *Post Apocalyptic*: beton crăpat, oxid de fier, vegetație uscată, pâclă ocru.
- **Exagerare Non-Distructivă a Reliefului**: Aplicat `elevation_scale` dinamic la nivel de geometrie, păstrând DEM-ul original imuabil.
- **Integrare Iluminare și PBR în Blender**:
  - Inclus datele de iluminare (`lighting`) și proprietățile PBR în manifestul Blender.
  - Scriptul `import_to_blender.py` configurează automat soarele (azimut, elevație, intensitate, culoare) și fundalul World ambient.
- **Suită de Teste**: Adăugat `tests/test_visual_style_engine.py` (8 teste).

## [2.2.0] — 2026-11-07

### Game World Generator — Etapa 2: Corecții Geometrice, Normale & Culling
- **Normale fără cusături (Seamless Chunk Borders)**: Eșantionare teren cu inel de gardă (guard-band) de 1 punct perimetric (`resolution + 3`) în `ChunkEngine`; derivatele centrale și normalele la granițele comune dintre chunk-uri adiacente sunt bit-for-bit identice ($\Delta \theta < 10^{-5}$ grade, eliminând complet cusăturile de iluminare în Blender și Unity).
- **Winding CCW și Orientare Normale Spre Exterior**: Corectat ordinea vârfurilor la extrudarea pereților clădirilor în `terragen/geom.py:extrude` și inversat indicii de fețe (`faces[:, [0, 2, 1]]`) la conversia pe axa glTF ($Z \to -Z$). Toate fețele sunt orientate spre exterior (outward).
- **Materiale Single-Sided pentru Motoare de Joc**: Trecut materialele glTF pe `doubleSided: False` pe clădiri și teren, permițând utilizarea eficientă a hardware back-face culling-ului GPU în Unity.
- **Invalidare Completă de Cache în ChunkEngine**: Cheia de hash ia acum în calcul modificatorii de sculptare din `project.terrain.modifiers`, schimbările de `style_preset` și apartenența la regiuni, eliminând servirea de chunk-uri învechite la re-generare.
- **Suită de Teste Extinsă**: Adăugat `tests/test_game_geometry_and_normals.py` (5 teste noi). Total: **174 de teste automate**, 100% trecute (3.07s).

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
