"""Serverul aplicației: hartă pentru selectarea zonei + generare + viewer 3D local.

Rulat cu:  python3 -m terragen serve        (sau ./start.sh)
Deschide:  http://127.0.0.1:8777
"""

from __future__ import annotations

import contextlib
import json
import os
import platform
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
import traceback
import uuid
import zipfile
from pathlib import Path

from flask import Flask, Response, abort, jsonify, request, send_file, send_from_directory

from . import __version__
from .cli import PRESETS, GenerationCancelled, generate
from .dem import Dem

WEB_DIR = Path(__file__).resolve().parent / "web"
UI_DIR = WEB_DIR / "ui"
VIEWER_DIR = WEB_DIR / "viewer"

SITES_DIR = Path(os.environ.get("TERRAGEN_SITES", Path.cwd() / "sites")).resolve()

app = Flask(__name__, static_folder=None)
# Limitează corpurile cererilor către aplicație, nu fișierul PBF descărcat.
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024


@app.before_request
def protect_browser_requests():
    """Blochează cererile de modificare din alte origini; nu este autentificare."""
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return None
    origin = request.headers.get("Origin")
    if origin and origin != request.host_url.rstrip("/"):
        return jsonify({"error": "origine nepermisă"}), 403
    if request.headers.get("Sec-Fetch-Site") == "cross-site":
        return jsonify({"error": "cerere cross-site nepermisă"}), 403
    # Endpointul cancel nu trimite corp; celelalte POST-uri folosesc JSON.
    if request.method == "POST" and not request.path.endswith("/cancel"):
        if not request.is_json:
            return jsonify({"error": "este necesar Content-Type application/json"}), 415
    return None

CONFIG: dict = {"pbf": None, "default_size": 1200.0, "terrain_res": 256}

PBF_URL = "https://download.geofabrik.de/europe/romania-latest.osm.pbf"


# --------------------------------------------------------------------- utilitare
def detect_pbf() -> str | None:
    """Caută un extract .osm.pbf în folderul proiectului / părinte / Downloads, Desktop."""
    if CONFIG.get("pbf") and Path(CONFIG["pbf"]).exists():
        return str(Path(CONFIG["pbf"]).resolve())
    env = os.environ.get("TERRAGEN_PBF")
    if env and Path(env).exists():
        return str(Path(env).resolve())
    candidates: list[Path] = []
    for base in (Path.cwd(), Path.cwd().parent, Path.home() / "Downloads", Path.home() / "Desktop"):
        with contextlib.suppress(Exception):
            candidates += sorted(base.glob("*.osm.pbf"))
    for c in candidates:
        if c.is_file() and c.stat().st_size > 1_000_000:
            return str(c.resolve())
    return None


def osm_reader() -> str:
    try:
        from .osmdata import osmium

        if osmium is not None:
            return "pyosmium"
    except Exception:
        pass
    if shutil.which("osmium"):
        return "osmium-tool"
    return "pbf-intern (Python pur)"


def safe_name(name: str) -> str:
    name = (name or "").strip() or "zona"
    name = re.sub(r"[^\w\-. ]+", "", name, flags=re.UNICODE).strip().replace(" ", "-")
    return name[:48].lower() or "zona"


def site_path(name: str) -> Path:
    p = (SITES_DIR / safe_name(name)).resolve()
    # Nu permite nici directorul rădăcină, nici ieșirea din acesta.
    if p == SITES_DIR or SITES_DIR not in p.parents:
        abort(400)
    return p


def is_local_request() -> bool:
    return request.remote_addr in ("127.0.0.1", "::1", "localhost")


# ------------------------------------------------------------------- descărcare OSM
DL = {"status": "idle", "done": 0, "total": 0, "error": None, "path": None}
DL_LOCK = threading.Lock()
MAX_PBF_BYTES = 4 * 1024**3


def _download_pbf(url: str):
    import requests as rq

    target = Path.cwd() / "romania.osm.pbf"
    part = target.with_suffix(".part")
    DL.update(status="running", done=0, total=0, error=None, path=None)
    try:
        from urllib.parse import urljoin, urlsplit

        # Geofabrik poate redirecționa către un extract datat, pe același host.
        current = url
        for _ in range(4):
            r = rq.get(current, stream=True, timeout=120, allow_redirects=False,
                       headers={"User-Agent": f"terragen/{__version__}"})
            if not 300 <= r.status_code < 400:
                break
            location = r.headers.get("Location", "")
            r.close()
            current = urljoin(current, location)
            parsed = urlsplit(current)
            if (parsed.scheme != "https" or parsed.netloc != "download.geofabrik.de"
                    or not re.fullmatch(r"/europe/romania-(?:latest|[0-9]{6})[.]osm[.]pbf", parsed.path)
                    or parsed.query or parsed.fragment):
                raise ValueError("redirecționare PBF nepermisă")
        else:
            raise ValueError("prea multe redirecționări PBF")
        with r:
            r.raise_for_status()
            if 300 <= r.status_code < 400:
                raise ValueError("redirecționările PBF nu sunt permise")
            DL["total"] = int(r.headers.get("Content-Length") or 0)
            if DL["total"] > MAX_PBF_BYTES:
                raise ValueError("fișierul PBF depășește limita de 4 GiB")
            with open(part, "wb") as fh:
                for chunk in r.iter_content(chunk_size=1 << 19):
                    if chunk:
                        if DL["done"] + len(chunk) > MAX_PBF_BYTES:
                            raise ValueError("fișierul PBF depășește limita de 4 GiB")
                        fh.write(chunk)
                        DL["done"] += len(chunk)
        part.replace(target)
        CONFIG["pbf"] = str(target)
        DL.update(status="done", path=str(target))
    except Exception as exc:
        DL.update(status="error", error=f"{type(exc).__name__}: {exc}")
        part.unlink(missing_ok=True)


@app.post("/api/download-pbf")
def api_download_pbf():
    d = request.get_json(silent=True) or {}
    if not isinstance(d, dict):
        return jsonify({"error": "obiect JSON invalid"}), 400
    url = d.get("url") or PBF_URL
    if url != PBF_URL:
        return jsonify({"error": "este permisă doar sursa Geofabrik configurată"}), 400
    with DL_LOCK:
        if DL["status"] == "running":
            return jsonify({"error": "descărcarea e deja în curs"}), 409
        DL.update(status="running", done=0, total=0, error=None, path=None)
        try:
            threading.Thread(target=_download_pbf, args=(url,), daemon=True).start()
        except Exception:
            DL.update(status="error", error="nu am putut porni descărcarea")
            raise
    return jsonify({"ok": True, "url": url})


@app.get("/api/download-status")
def api_download_status():
    pct = round(DL["done"] * 100 / DL["total"], 1) if DL.get("total") else None
    return jsonify(
        {
            **DL,
            "percent": pct,
            "done_mb": round(DL["done"] / 1e6, 1),
            "total_mb": round((DL["total"] or 0) / 1e6, 1),
        }
    )


# ------------------------------------------------------------------------- joburi
class Job:
    def __init__(self, params: dict):
        self.id = uuid.uuid4().hex[:12]
        self.params = params
        self.status = "queued"  # queued | running | done | error | cancelled
        self.lines: list[str] = []
        self.result: dict | None = None
        self.error: str | None = None
        self.t0 = time.time()
        self.cancelled = False
        self.publishing = False
        self.q: queue.Queue = queue.Queue()

    def log(self, msg: str) -> None:
        line = time.strftime("%H:%M:%S") + "  " + str(msg)
        self.lines.append(line)
        self.q.put(line)

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "lines": self.lines[-400:],
            "error": self.error,
            "result": self.result,
            "elapsed": round(time.time() - self.t0, 1),
            "params": {k: v for k, v in self.params.items() if k != "pbf"},
        }


JOBS: dict[str, Job] = {}
LOCK = threading.Lock()
SEM = threading.Semaphore(1)  # o singură generare odată (altfel 2 job-uri își fură CPU)
MAX_JOBS = 25


def _publish(job: Job) -> None:
    with LOCK:
        JOBS[job.id] = job
        while len(JOBS) > MAX_JOBS:  # curățăm joburile vechi, ca să nu crească la infinit
            for k in sorted(JOBS, key=lambda j: JOBS[j].t0):
                if JOBS[k].status in ("done", "error", "cancelled"):
                    JOBS.pop(k, None)
                    break
            else:
                break


def _generate_staged(job: Job, p: dict) -> dict:
    """Construiește separat; păstrează versiunea veche dacă generarea eșuează."""
    SITES_DIR.mkdir(parents=True, exist_ok=True)
    dest = site_path(p["name"])
    work = Path(tempfile.mkdtemp(prefix=".terragen-", dir=SITES_DIR))
    staged = work / "city"
    backup = work / "backup"
    try:
        manifest = generate(
            center=(p["lat"], p["lon"]),
            size=float(p["size"]),
            out=str(staged),
            pbf=p.get("pbf"),
            overpass=bool(p.get("overpass")),
            terrain_res=int(p.get("terrain_res", 256)),
            dem_zoom=int(p.get("dem_zoom", 15)),
            formats=tuple(p.get("formats", ["glb"])),
            roofs=bool(p.get("roofs", True)),
            seed=int(p.get("seed", 7)),
            name=p["name"],
            log=job.log,
            is_cancelled=lambda: job.cancelled,
        )
        # De aici publicarea nu mai poate fi anulată; evită cursa cu endpointul.
        with LOCK:
            if job.cancelled:
                raise GenerationCancelled("Generarea a fost anulată")
            job.publishing = True
        if dest.exists():
            dest.rename(backup)
        try:
            staged.rename(dest)
        except Exception:
            if backup.exists():
                backup.rename(dest)
            raise
        if backup.exists():
            try:
                shutil.rmtree(backup)
            except OSError:
                job.log("Avertisment: copia veche a rămas în " + str(backup))
        return manifest
    finally:
        # Dacă restaurarea sau ștergerea copiei eșuează, nu o distrugem.
        if not backup.exists():
            shutil.rmtree(work, ignore_errors=True)
        else:
            job.log("Copia veche a fost păstrată în " + str(backup))


def run_job(job: Job) -> None:
    p = job.params
    with SEM:
        if job.cancelled:
            job.status = "cancelled"
            job.q.put(None)
            return
        job.status = "running"
        try:
            manifest = _generate_staged(job, p)
            name = safe_name(p["name"])
            job.result = {
                "name": name,
                "out": str(site_path(name)),
                "manifest": manifest,
                "viewer": f"/viewer/?city=../sites/{name}&back=/",
                "zip": f"/api/zip/{name}",
                "stats": manifest.get("stats", {}),
                "triangles": sum(manifest.get("layers_triangles", {}).values()),
            }
            job.status = "done"
            job.log("✔ Gata. Fișierele sunt în " + str(site_path(name)))
        except GenerationCancelled:
            job.status = "cancelled"
            job.log("Generare anulată. Versiunea anterioară a fost păstrată.")
        except Exception as exc:
            job.error = f"{type(exc).__name__}: {exc}"
            job.status = "error"
            job.log("✖ EROARE: " + job.error)
            job.log(traceback.format_exc(limit=4))
        finally:
            job.q.put(None)


def _start_job(params: dict) -> Job:
    job = Job(params)
    _publish(job)
    threading.Thread(target=run_job, args=(job,), daemon=True).start()
    return job


@app.post("/api/generate")
def api_generate():
    d = request.get_json(force=True, silent=True) or {}
    if not isinstance(d, dict):
        return jsonify({"error": "obiect JSON invalid"}), 400
    try:
        lat = float(d.get("lat"))
        lon = float(d.get("lon"))
        size = float(d.get("size", 1200))
    except Exception:
        return jsonify({"error": "lat / lon / size invalide"}), 400
    if not (-85 <= lat <= 85 and -180 <= lon <= 180):
        return jsonify({"error": "coordonate în afara limitelor"}), 400
    if not (200 <= size <= 6000):
        return jsonify({"error": "latura zonei trebuie să fie între 200 și 6000 m"}), 400

    try:
        # Refuzăm valori fracționare, booleene și valori în afara limitelor.
        def integer_field(key, default, minimum, maximum):
            value = d.get(key, default)
            if isinstance(value, bool):
                raise ValueError(key)
            if isinstance(value, int):
                number = value
            elif isinstance(value, str) and re.fullmatch(r"[+-]?[0-9]{1,10}", value.strip()):
                number = int(value)
            else:
                raise ValueError(key)
            if not minimum <= number <= maximum:
                raise ValueError(key)
            return number

        terrain_res = integer_field("terrain_res", 256, 32, 512)
        dem_zoom = integer_field("dem_zoom", 15, 10, 16)
        seed = integer_field("seed", 7, -2147483648, 2147483647)
        name_value = d.get("name")
        if name_value is not None and not isinstance(name_value, str):
            raise ValueError("name")
        for key in ("overpass", "obj", "roofs"):
            if key in d and not isinstance(d[key], bool):
                raise ValueError(key)
    except (ValueError, TypeError):
        return jsonify({"error": "parametri invalizi: terrain_res 32–512, dem_zoom 10–16, seed întreg; opțiunile trebuie să fie booleene"}), 400

    pbf = detect_pbf()
    overpass = bool(d.get("overpass")) or pbf is None
    name = safe_name(d.get("name") or f"{lat:.4f}-{lon:.4f}")
    params = {
        "lat": lat,
        "lon": lon,
        "size": size,
        "name": name,
        "pbf": pbf,
        "overpass": overpass,
        "terrain_res": terrain_res,
        "dem_zoom": dem_zoom,
        "formats": ["glb", "obj"] if d.get("obj") else ["glb"],
        "roofs": bool(d.get("roofs", True)),
        "seed": seed,
    }
    job = _start_job(params)
    return jsonify(
        {"job": job.id, "name": name, "source": "Overpass API" if overpass else Path(pbf).name}
    )


@app.get("/api/job/<jid>")
def api_job(jid: str):
    job = JOBS.get(jid)
    if not job:
        return jsonify({"error": "job inexistent"}), 404
    return jsonify(job.to_json())


@app.get("/api/job/<jid>/stream")
def api_job_stream(jid: str):
    """Progres în timp real (Server-Sent Events)."""
    job = JOBS.get(jid)
    if not job:
        return jsonify({"error": "job inexistent"}), 404

    def gen():
        sent = 0
        while True:
            while sent < len(job.lines):
                yield f"data: {json.dumps({'line': job.lines[sent]})}\n\n"
                sent += 1
            if job.status in ("done", "error", "cancelled"):
                yield f"data: {json.dumps({'final': job.to_json()})}\n\n"
                return
            time.sleep(0.35)

    return Response(
        gen(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/job/<jid>/cancel")
def api_job_cancel(jid: str):
    job = JOBS.get(jid)
    if not job:
        return jsonify({"error": "job inexistent"}), 404
    with LOCK:
        if job.status in ("done", "error", "cancelled") or job.publishing:
            return jsonify({"error": "jobul este încheiat sau publicarea a început"}), 409
        job.cancelled = True
        if job.status == "queued":
            job.status = "cancelled"
    return jsonify({"ok": True})


# ------------------------------------------------------------------------- date
@app.get("/api/config")
def api_config():
    pbf = detect_pbf()
    return jsonify(
        {
            "version": __version__,
            "pbf": pbf,
            "pbf_name": Path(pbf).name if pbf else None,
            "osm_reader": osm_reader(),
            "presets": PRESETS,
            "home": PRESETS["targu-mures"],
            "sites_dir": str(SITES_DIR),
            "sites": list_sites(),
            "default_size": CONFIG["default_size"],
            "terrain_res": CONFIG["terrain_res"],
            "allow_open_folder": is_local_request(),
            "env": {"python": platform.python_version(), "os": platform.system()},
        }
    )


def list_sites() -> list[dict]:
    out = []
    if not SITES_DIR.exists():
        return out
    for d in sorted(SITES_DIR.iterdir()):
        if d.name.startswith(".terragen-"):
            continue
        mf = d / "manifest.json"
        if not (d.is_dir() and mf.exists()):
            continue
        try:
            m = json.loads(mf.read_text())
        except Exception:
            continue
        out.append(
            {
                "name": d.name,
                "area": m.get("area", {}),
                "stats": m.get("stats", {}),
                "mtime": int(mf.stat().st_mtime),
                "triangles": sum(m.get("layers_triangles", {}).values()),
                "viewer": f"/viewer/?city=../sites/{d.name}&back=/",
            }
        )
    return sorted(out, key=lambda s: -s["mtime"])


@app.get("/api/sites")
def api_sites():
    return jsonify({"sites": list_sites(), "dir": str(SITES_DIR)})


@app.delete("/api/sites/<name>")
def api_site_delete(name: str):
    p = site_path(name)
    if not p.exists():
        return jsonify({"error": "inexistent"}), 404
    shutil.rmtree(p)
    return jsonify({"ok": True})


_DEM_CACHE: dict = {}


@app.post("/api/elevation")
def api_elevation():
    """Altitudinea reală sub cursor (DEM Terrarium) — pentru afișajul din hartă."""
    d = request.get_json(force=True, silent=True) or {}
    if not isinstance(d, dict):
        return jsonify({"error": "obiect JSON invalid"}), 400
    try:
        pts = d.get("points", [])
        if not isinstance(pts, list) or len(pts) > 8:
            raise ValueError("points")
        if not pts:
            return jsonify({"elevations": []})
        if any(not isinstance(p, (list, tuple)) or len(p) != 2 for p in pts):
            raise ValueError("points")
        lats = [float(p[0]) for p in pts]
        lons = [float(p[1]) for p in pts]
        if not all(-85 <= lat <= 85 for lat in lats):
            raise ValueError("lat")
        if not all(-180 <= lon <= 180 for lon in lons):
            raise ValueError("lon")
        # Împiedică alocarea unui mozaic DEM pentru puncte foarte îndepărtate.
        if max(lats) - min(lats) > 0.1 or max(lons) - min(lons) > 0.1:
            raise ValueError("points span")
    except (TypeError, ValueError, OverflowError):
        return jsonify({"error": "puncte invalide sau prea îndepărtate"}), 400
    s, w = min(lats) - 0.006, min(lons) - 0.008
    n, e = max(lats) + 0.006, max(lons) + 0.008
    key = (round(s, 3), round(w, 3), round(n, 3), round(e, 3))
    try:
        dem = _DEM_CACHE.get(key)
        if dem is None:
            dem = Dem((s, w, n, e), zoom=14)
            _DEM_CACHE.clear()
            _DEM_CACHE[key] = dem
        return jsonify(
            {
                "elevations": [
                    round(dem.height(lo, la), 1) for lo, la in zip(lons, lats, strict=False)
                ]
            }
        )
    except Exception as exc:
        return jsonify({"elevations": [], "error": str(exc)})


@app.get("/api/zip/<name>")
def api_zip(name: str):
    p = site_path(name)
    if not p.exists():
        abort(404)
    zpath = p.parent / f"_{p.name}.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in p.rglob("*"):
            if f.is_file():
                z.write(f, f.relative_to(p.parent))
    return send_file(zpath, as_attachment=True, download_name=f"{p.name}.zip")


@app.post("/api/open-folder")
def api_open_folder():
    """Deschide folderul în Finder/Explorer. Doar de pe localhost."""
    if not is_local_request():
        return jsonify({"error": "disponibil doar local"}), 403
    d = request.get_json(force=True, silent=True) or {}
    p = site_path(d.get("name", ""))
    if not p.exists():
        return jsonify({"error": "folderul nu există"}), 404
    try:
        if platform.system() == "Windows":
            os.startfile(str(p))  # type: ignore[attr-defined]
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", str(p)])
        else:
            subprocess.Popen(["xdg-open", str(p)])
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True, "path": str(p)})


# ------------------------------------------------------------------- fișiere
@app.get("/")
def index():
    return send_from_directory(UI_DIR, "index.html")


@app.get("/ui/<path:f>")
def ui_files(f: str):
    return send_from_directory(UI_DIR, f)


@app.get("/vendor/<path:f>")
def vendor_files(f: str):
    return send_from_directory(UI_DIR / "vendor", f)


@app.get("/viewer/")
@app.get("/viewer/index.html")
def viewer_index():
    return send_from_directory(VIEWER_DIR, "index.html")


@app.get("/viewer/<path:f>")
def viewer_files(f: str):
    return send_from_directory(VIEWER_DIR, f)


@app.get("/sites/<path:f>")
def site_files(f: str):
    return send_from_directory(SITES_DIR, f)


@app.get("/api/health")
def health():
    return jsonify({"ok": True, "version": __version__})


def main(argv=None) -> None:
    import argparse
    import webbrowser

    ap = argparse.ArgumentParser(
        prog="terragen serve", description="Aplicația locală: hartă + generator de orașe 3D"
    )
    ap.add_argument("--pbf", default=None, help="extract .osm.pbf local (altfel se caută automat)")
    ap.add_argument("--port", type=int, default=8777)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument(
        "--sites", default=None, help="folderul în care se scriu orașele (implicit ./sites)"
    )
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args(argv)

    global SITES_DIR
    if a.sites:
        SITES_DIR = Path(a.sites).expanduser().resolve()
    elif os.environ.get("TERRAGEN_SITES"):
        SITES_DIR = Path(os.environ["TERRAGEN_SITES"]).expanduser().resolve()
    SITES_DIR.mkdir(parents=True, exist_ok=True)

    if a.pbf:
        if Path(a.pbf).exists():
            CONFIG["pbf"] = a.pbf
        else:
            print(f"  ! Nu găsesc '{a.pbf}' — folosesc detectarea automată / Overpass.")

    pbf = detect_pbf()
    print("─" * 68)
    print(f"  terragen {__version__} — generator de orașe 3D (fără Unity, fără Blender)")
    print(f"  OSM:    {pbf if pbf else 'Overpass API (nu am găsit niciun .osm.pbf local)'}")
    print(f"  Ieșire: {SITES_DIR}")
    print(f"  URL:    http://{a.host}:{a.port}")
    print("─" * 68)
    if not a.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(f"http://{a.host}:{a.port}")).start()
    app.run(host=a.host, port=a.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
