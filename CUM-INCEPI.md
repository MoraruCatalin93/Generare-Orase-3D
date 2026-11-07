# Terragen World Builder — Cum începi (Ghid rapid)

**Ce este Terragen:** selectezi o zonă pe hartă → apeși un buton → primești orașul și terenul 3D (relief cu altitudini reale, drumuri drapate, trotuare, clădiri cu fațade și acoperișuri stilizate, vegetație) și îl vezi **direct în browser**. De asemenea, poți exporta pachete complete optimizate pentru **Blender** și pregătite pentru **Unity**.

---

## Pași de pornire (sub 2 minute)

Nu ai nevoie de arhive separate sau scripturi de instalare externe. Totul se rulează direct din repository-ul Git.

### 1. Clonează proiectul și intră în folder

Deschide terminalul (sau VS Code → `Terminal → New Terminal`) și rulează:

```bash
git clone https://github.com/MoraruCatalin93/Generare-Orase-3D.git
cd Generare-Orase-3D
```

### 2. Pornește aplicația

- **Pe macOS sau Linux:**
  ```bash
  ./start.sh
  ```
  *(Dacă primești eroare de permisiuni, rulează `chmod +x start.sh && ./start.sh`)*

- **Pe Windows (PowerShell):**
  ```powershell
  .\start.ps1
  ```

### Ce face scriptul automat:
1. Verifică prezența Python (3.10+).
2. Creează automat mediul virtual `.venv` dacă nu există deja.
3. Instalează toate dependențele necesare din `requirements.txt`.
4. Pornește serverul local și deschide automat browserul la **http://127.0.0.1:8777**.

---

## Cum folosești aplicația în browser

1. **Harta interactivă**:
   - Centrată inițial pe Târgu Mureș.
   - Taste rapide: `t` = comută umbrirea reliefului 3D, `s` = comută pe satelit.
2. **Selectarea zonei**:
   - Apasă butonul **„▭ Selectează pe hartă"** și trage un dreptunghi peste zona dorită (sau introdu manual coordonatele și dimensiunea în metri).
3. **Personalizare și generare**:
   - Alege rezoluția terenului, seed-ul și stilul dorit.
   - Apasă **„⬢ Generează orașul 3D"**.
4. **Vizualizare și export**:
   - Progresul curge în timp real pe ecran.
   - La final, apasă **„◱ Deschide viewer 3D"** pentru a naviga prin oraș direct în browser.
   - Poți exporta scena ca fișiere GLB sau pachete complete pentru Blender prin noul meniu de export.

---

## Datele geografice (OSM) — Online vs. Offline

- **Online (Overpass API)**: Funcționează automat pentru zone mici (1–2 km).
- **Offline (Recomandat pentru zone mari)**:
  - Apasă butonul **„⬇ descarcă România (~330 MB)"** din interfață pentru a salva `romania.osm.pbf` în rădăcina proiectului.
  - Odată descărcat, generarea devine **100% offline** și de aproximativ **10× mai rapidă**.

---

## Comenzi utile din linia de comandă (CLI)

```bash
# Pornire server manual
python3 -m terragen serve

# Generare directă din terminal
python3 -m terragen generate --center targu-mures --size 1500 --out sites/tgm --seed 7

# Rulare teste automate (163 teste offline, rulează în ~3 secunde)
python3 -m pytest tests -q
```

---

## Relația cu Blender și Unity

- **Viewer-ul web**: Îți permite să verifici imediat geometria generată direct în browser, fără să deschizi alte programe.
- **Exportul pentru Blender**: Proiectul include un exportator dedicat (`import_to_blender.py`) care importă automat straturile, configurează vizibilitatea la 50 km și curăță materialele duplicate.
- **Unity**: Modelele pot fi importate în Unity ca FBX/glTF după pregătirea în Blender, iar fișierele de heightmap pe 16 biți (`.r16`) se pot încărca direct în componenta Unity Terrain.

---

## Dacă întâmpini probleme

| Simptom | Soluție simplă |
|---|---|
| `permission denied: ./start.sh` | Rulează `chmod +x start.sh` și apoi `./start.sh` |
| `python3: command not found` | Instalează Python 3.10 sau mai nou de pe [python.org](https://www.python.org/) sau via `brew install python` |
| Portul 8777 este ocupat | Pornește pe alt port: `./start.sh --port 8780` |
| Harta este goală | Asigură-te că ești conectat la internet pentru prima descărcare a tile-urilor hărții |
| Resetare completă a mediului | Șterge folderul `.venv` (`rm -rf .venv`) și rulează din nou `./start.sh` |

---

## Licențe și atribuire

- **Cod sursă**: Licență **MIT** (vezi `LICENSE`).
- **Date OpenStreetMap**: © OpenStreetMap contributors (licență ODbL).
- **Date Elevație (DEM)**: Terrarium / AWS Open Data / Mapzen.
