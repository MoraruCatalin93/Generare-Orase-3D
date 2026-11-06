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
PROJECTS_DIR = Path(os.environ.get("TERRAGEN_PROJECTS", Path.cwd() / "projects")).resolve()
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)

from .projects import ProjectManager  # noqa: E402
project_manager = ProjectManager(PROJECTS_DIR)
PROJECT_MGR = project_manager

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
    # Endpointul cancel și generarea din proiecte nu trimit obligatoriu corp; celelalte POST-uri folosesc JSON.
    is_exempt = (
        request.path.endswith("/cancel")
        or (request.path.endswith("/generate") and "/projects/" in request.path)
        or (request.path.endswith("/duplicate") and "/projects/" in request.path)
        or (request.path.endswith("/export/blender") and "/projects/" in request.path)
        or ("/terrain/" in request.path and (request.path.endswith("/undo") or request.path.endswith("/redo") or request.path.endswith("/export")))
    )
    if request.method == "POST" and not is_exempt:
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
            style_preset=p.get("style_preset"),
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

            project_id = p.get("project_id")
            region_id = p.get("region_id")
            if project_id and region_id:
                try:
                    proj = project_manager.open_project(project_id)
                    for r in proj.regions:
                        if r.id == region_id:
                            r.status = "built"
                            break
                    project_manager.save_project(proj)
                except Exception:
                    pass
        except GenerationCancelled:
            job.status = "cancelled"
            job.log("Generare anulată. Versiunea anterioară a fost păstrată.")
            if p.get("project_id") and p.get("region_id"):
                try:
                    proj = project_manager.open_project(p["project_id"])
                    for r in proj.regions:
                        if r.id == p["region_id"]:
                            r.status = "unbuilt"
                            break
                    project_manager.save_project(proj)
                except Exception:
                    pass
        except Exception as exc:
            job.error = f"{type(exc).__name__}: {exc}"
            job.status = "error"
            if p.get("project_id") and p.get("region_id"):
                try:
                    proj = project_manager.open_project(p["project_id"])
                    for r in proj.regions:
                        if r.id == p["region_id"]:
                            r.status = "error"
                            break
                    project_manager.save_project(proj)
                except Exception:
                    pass
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
        style_preset_val = d.get("style_preset")
        if style_preset_val is not None and not isinstance(style_preset_val, (str, dict)):
            raise ValueError("style_preset")
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
        "style_preset": style_preset_val,
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
            "projects_dir": str(PROJECTS_DIR),
            "projects": project_manager.list_projects(),
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


@app.get("/api/styles/presets")
def api_list_style_presets():
    from .styles import list_style_presets
    return jsonify({"presets": list_style_presets()})


@app.get("/api/styles/presets/<name>")
def api_get_style_preset(name: str):
    from .styles import get_style_preset
    try:
        preset = get_style_preset(name)
        return jsonify(preset.to_dict())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 404


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


# ------------------------------------------------------------------- proiecte
@app.get("/api/projects")
def api_projects():
    return jsonify({"projects": project_manager.list_projects(), "dir": str(PROJECTS_DIR)})


@app.post("/api/projects")
def api_projects_create():
    d = request.get_json(silent=True) or {}
    if not isinstance(d, dict):
        return jsonify({"error": "obiect JSON invalid"}), 400
    name = str(d.get("name") or "").strip()
    if not name:
        return jsonify({"error": "numele proiectului este obligatoriu"}), 400
    try:
        lat = float(d.get("lat", d.get("center", [46.5446, 24.5612])[0]))
        lon = float(d.get("lon", d.get("center", [46.5446, 24.5612])[1]))
        size = float(d.get("size", 1500.0))
        seed = int(d.get("seed", 7))
        terrain_res = int(d.get("terrain_res", 256))
        dem_zoom = int(d.get("dem_zoom", 15))
        formats = d.get("formats", ["glb"])
        roofs = bool(d.get("roofs", True))
        desc = str(d.get("description", ""))
    except (ValueError, TypeError, IndexError):
        return jsonify({"error": "parametri de proiect invalizi"}), 400

    try:
        project = project_manager.create_project(
            name=name,
            center=(lat, lon),
            size=size,
            description=desc,
            seed=seed,
            terrain_res=terrain_res,
            dem_zoom=dem_zoom,
            formats=formats,
            roofs=roofs,
            extra_tags=d.get("extra_metadata"),
        )
        return jsonify({"ok": True, "project": project.to_dict()}), 201
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.get("/api/projects/<pid>")
def api_projects_get(pid: str):
    try:
        project = project_manager.open_project(pid)
        return jsonify({"ok": True, "project": project.to_dict()})
    except FileNotFoundError:
        return jsonify({"error": "proiect inexistent"}), 404
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.put("/api/projects/<pid>")
def api_projects_update(pid: str):
    d = request.get_json(silent=True) or {}
    if not isinstance(d, dict):
        return jsonify({"error": "obiect JSON invalid"}), 400
    try:
        project = project_manager.open_project(pid)
    except FileNotFoundError:
        return jsonify({"error": "proiect inexistent"}), 404

    try:
        if "name" in d and str(d["name"]).strip():
            project.name = str(d["name"]).strip()
        if "description" in d:
            project.description = str(d["description"])
        if "seed" in d and isinstance(d["seed"], int):
            project.seed = d["seed"]
        if "terrain" in d and isinstance(d["terrain"], dict):
            t = d["terrain"]
            if "resolution" in t:
                project.terrain.resolution = int(t["resolution"])
            if "dem_zoom" in t:
                project.terrain.dem_zoom = int(t["dem_zoom"])
            if "exaggeration" in t:
                project.terrain.exaggeration = float(t["exaggeration"])
        if "generation" in d and isinstance(d["generation"], dict):
            g = d["generation"]
            if "roofs" in g:
                project.generation.roofs = bool(g["roofs"])
            if "formats" in g:
                project.generation.formats = list(g["formats"])
            if "detail_level" in g:
                project.generation.detail_level = str(g["detail_level"])
        if "regions" in d and isinstance(d["regions"], list):
            from .projects.model import Region

            project.regions = [Region.from_dict(r) for r in d["regions"] if isinstance(r, dict)]
        if "chunks" in d and isinstance(d["chunks"], dict):
            from .projects.model import ChunksSettings

            project.chunks = ChunksSettings.from_dict(d["chunks"])
        if "dimensions" in d and isinstance(d["dimensions"], dict):
            dims = d["dimensions"]
            if "size_x_m" in dims:
                project.dimensions.size_x_m = float(dims["size_x_m"])
            if "size_y_m" in dims:
                project.dimensions.size_y_m = float(dims["size_y_m"])

        project_manager.save_project(project)
        return jsonify({"ok": True, "project": project.to_dict()})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.post("/api/projects/<pid>/save-as")
def api_projects_save_as(pid: str):
    d = request.get_json(silent=True) or {}
    new_name = str(d.get("name") or "").strip()
    if not new_name:
        return jsonify({"error": "este necesar un nume nou"}), 400
    try:
        project = project_manager.open_project(pid)
        clone = project_manager.save_project_as(project, new_name)
        return jsonify({"ok": True, "project": clone.to_dict()}), 201
    except FileNotFoundError:
        return jsonify({"error": "proiect inexistent"}), 404
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.post("/api/projects/<pid>/duplicate")
def api_projects_duplicate(pid: str):
    d = request.get_json(silent=True) or {}
    new_name = d.get("name")
    try:
        dup = project_manager.duplicate_project(pid, new_name)
        return jsonify({"ok": True, "project": dup.to_dict()}), 201
    except FileNotFoundError:
        return jsonify({"error": "proiect inexistent"}), 404
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.post("/api/projects/<pid>/rename")
def api_projects_rename(pid: str):
    d = request.get_json(silent=True) or {}
    new_name = str(d.get("name") or "").strip()
    if not new_name:
        return jsonify({"error": "este necesar un nume nou"}), 400
    try:
        renamed = project_manager.rename_project(pid, new_name)
        return jsonify({"ok": True, "project": renamed.to_dict()})
    except FileNotFoundError:
        return jsonify({"error": "proiect inexistent"}), 404
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.delete("/api/projects/<pid>")
def api_projects_delete(pid: str):
    d = request.get_json(silent=True) or {}
    if not d.get("confirm"):
        return jsonify({"error": "ștergerea necesită confirmare: {\"confirm\": true}"}), 400
    try:
        project_manager.delete_project(pid, confirm=True)
        return jsonify({"ok": True})
    except FileNotFoundError:
        return jsonify({"error": "proiect inexistent"}), 404
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.post("/api/projects/<pid>/generate")
def api_projects_generate(pid: str):
    try:
        project = project_manager.open_project(pid)
    except FileNotFoundError:
        return jsonify({"error": "proiect inexistent"}), 404

    pbf = detect_pbf()
    overpass = pbf is None
    name = safe_name(project.slug or project.name)
    params = {
        "lat": project.geographic.center_lat,
        "lon": project.geographic.center_lon,
        "size": project.dimensions.size_x_m,
        "name": name,
        "pbf": pbf,
        "overpass": overpass,
        "terrain_res": project.terrain.resolution,
        "dem_zoom": project.terrain.dem_zoom,
        "formats": project.generation.formats,
        "roofs": project.generation.roofs,
        "seed": project.seed,
        "project_id": project.id,
    }
    job = _start_job(params)
    return jsonify(
        {"job": job.id, "name": name, "source": "Overpass API" if overpass else Path(pbf).name}
    )


# ------------------------------------------------------------------- proiecte: regiuni
@app.get("/api/projects/<pid>/regions")
def api_projects_get_regions(pid: str):
    try:
        regions = project_manager.get_regions(pid)
        return jsonify({
            "project_id": pid,
            "count": len(regions),
            "regions": [r.to_dict() for r in regions],
            "geojson": {
                "type": "FeatureCollection",
                "features": [r.to_geojson_feature() for r in regions],
            },
        })
    except FileNotFoundError:
        return jsonify({"error": "proiect inexistent"}), 404
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.post("/api/projects/<pid>/regions")
def api_projects_create_region(pid: str):
    d = request.get_json()
    if not isinstance(d, dict):
        return jsonify({"error": "corpul cererii trebuie să fie JSON obiect"}), 400
    try:
        reg = project_manager.add_region(pid, d)
        return jsonify({"ok": True, "region": reg.to_dict()}), 201
    except FileNotFoundError:
        return jsonify({"error": "proiect inexistent"}), 404
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.get("/api/projects/<pid>/regions/<rid>")
def api_projects_get_region(pid: str, rid: str):
    try:
        reg = project_manager.get_region(pid, rid)
        return jsonify({"region": reg.to_dict(), "geojson": reg.to_geojson_feature()})
    except FileNotFoundError:
        return jsonify({"error": "proiect inexistent"}), 404
    except KeyError:
        return jsonify({"error": f"regiunea '{rid}' nu a fost găsită"}), 404
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.put("/api/projects/<pid>/regions/<rid>")
def api_projects_update_region(pid: str, rid: str):
    d = request.get_json()
    if not isinstance(d, dict):
        return jsonify({"error": "corpul cererii trebuie să fie JSON obiect"}), 400
    try:
        reg = project_manager.update_region(pid, rid, d)
        return jsonify({"ok": True, "region": reg.to_dict()})
    except FileNotFoundError:
        return jsonify({"error": "proiect inexistent"}), 404
    except KeyError:
        return jsonify({"error": f"regiunea '{rid}' nu a fost găsită"}), 404
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.delete("/api/projects/<pid>/regions/<rid>")
def api_projects_delete_region(pid: str, rid: str):
    try:
        project_manager.delete_region(pid, rid)
        return jsonify({"ok": True})
    except FileNotFoundError:
        return jsonify({"error": "proiect inexistent"}), 404
    except KeyError:
        return jsonify({"error": f"regiunea '{rid}' nu a fost găsită"}), 404
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.post("/api/projects/<pid>/regions/<rid>/duplicate")
def api_projects_duplicate_region(pid: str, rid: str):
    d = request.get_json(silent=True) or {}
    offset = d.get("offset_m", [15.0, 15.0])
    if isinstance(offset, (list, tuple)) and len(offset) >= 2:
        offset_m = (float(offset[0]), float(offset[1]))
    else:
        offset_m = (15.0, 15.0)
    try:
        clone = project_manager.duplicate_region(pid, rid, offset_m=offset_m)
        return jsonify({"ok": True, "region": clone.to_dict()}), 201
    except FileNotFoundError:
        return jsonify({"error": "proiect inexistent"}), 404
    except KeyError:
        return jsonify({"error": f"regiunea '{rid}' nu a fost găsită"}), 404
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.post("/api/projects/<pid>/regions/<rid>/split")
def api_projects_split_region(pid: str, rid: str):
    d = request.get_json()
    if not isinstance(d, dict) or "line" not in d:
        return jsonify({"error": "este necesară linia de tăiere: {\"line\": [[lon, lat], ...]}"}), 400
    try:
        sub_regions = project_manager.split_region(pid, rid, d["line"])
        return jsonify({"ok": True, "regions": [r.to_dict() for r in sub_regions]})
    except FileNotFoundError:
        return jsonify({"error": "proiect inexistent"}), 404
    except KeyError:
        return jsonify({"error": f"regiunea '{rid}' nu a fost găsită"}), 404
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.post("/api/projects/<pid>/regions/merge")
def api_projects_merge_regions(pid: str):
    d = request.get_json()
    if not isinstance(d, dict) or "region_ids" not in d or not isinstance(d["region_ids"], list):
        return jsonify({"error": "este necesară lista ID-urilor: {\"region_ids\": [\"id1\", \"id2\"]}"}), 400
    try:
        merged = project_manager.merge_regions(pid, d["region_ids"], merged_name=d.get("name"))
        return jsonify({"ok": True, "region": merged.to_dict()})
    except FileNotFoundError:
        return jsonify({"error": "proiect inexistent"}), 404
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.get("/api/projects/<pid>/regions/validate")
def api_projects_validate_regions(pid: str):
    try:
        result = project_manager.validate_regions(pid)
        return jsonify({"ok": True, "validation": result})
    except FileNotFoundError:
        return jsonify({"error": "proiect inexistent"}), 404
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.post("/api/projects/<pid>/regions/<rid>/generate")
def api_projects_generate_region(pid: str, rid: str):
    try:
        project = project_manager.open_project(pid)
    except FileNotFoundError:
        return jsonify({"error": "proiect inexistent"}), 404

    target = next((r for r in project.regions if r.id == rid), None)
    if not target:
        return jsonify({"error": f"regiunea '{rid}' nu a fost găsită"}), 404

    if not target.polygon or len(target.polygon) < 3:
        return jsonify({"error": "regiunea nu are o geometrie validă pentru generare"}), 400

    c_lat = target.geographic.get("center", {}).get("lat", project.geographic.center_lat)
    c_lon = target.geographic.get("center", {}).get("lon", project.geographic.center_lon)
    dims = target.geographic.get("dimensions_m", {})
    reg_size = max(dims.get("width_m", 500.0), dims.get("height_m", 500.0), 300.0)

    reg_gen = target.generation or {}
    roofs = reg_gen.get("roofs", project.generation.roofs)
    seed = target.seed

    pbf = detect_pbf()
    overpass = pbf is None
    name = safe_name(f"{project.slug or project.name}-{target.name}")

    params = {
        "lat": c_lat,
        "lon": c_lon,
        "size": reg_size,
        "name": name,
        "pbf": pbf,
        "overpass": overpass,
        "terrain_res": project.terrain.resolution,
        "dem_zoom": project.terrain.dem_zoom,
        "formats": project.generation.formats,
        "roofs": roofs,
        "seed": seed,
        "project_id": project.id,
        "region_id": target.id,
    }

    target.status = "generating"
    project_manager.save_project(project)

    job = _start_job(params)
    return jsonify(
        {"job": job.id, "name": name, "source": "Overpass API" if overpass else Path(pbf).name, "region_id": target.id}
    )


# ------------------------------------------------------------------- proiecte: chunk-uri
@app.get("/api/projects/<pid>/chunks")
def api_projects_get_chunks(pid: str):
    try:
        engine = project_manager.get_chunk_engine(pid)
        items = engine.project.chunks.items
        grid = engine.project.chunks.grid
        return jsonify({
            "project_id": pid,
            "grid": grid.to_dict(),
            "count": len(items),
            "chunks": [it.to_dict() for it in items],
            "geojson": {
                "type": "FeatureCollection",
                "features": [it.to_geojson_feature() for it in items],
            },
        })
    except FileNotFoundError:
        return jsonify({"error": "proiect inexistent"}), 404
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.post("/api/projects/<pid>/chunks/grid")
def api_projects_setup_chunk_grid(pid: str):
    d = request.get_json() or {}
    chunk_size = float(d.get("chunk_size_m", 512.0))
    size_x = float(d["world_size_x_m"]) if "world_size_x_m" in d else None
    size_y = float(d["world_size_y_m"]) if "world_size_y_m" in d else None
    try:
        settings = project_manager.init_chunk_grid(
            pid,
            chunk_size_m=chunk_size,
            world_size_x_m=size_x,
            world_size_y_m=size_y,
        )
        return jsonify({"ok": True, "grid": settings.grid.to_dict(), "count": len(settings.items)}), 201
    except FileNotFoundError:
        return jsonify({"error": "proiect inexistent"}), 404
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.get("/api/projects/<pid>/chunks/<cid>")
def api_projects_get_chunk(pid: str, cid: str):
    try:
        chunk = project_manager.get_chunk(pid, cid)
        if not chunk:
            return jsonify({"error": f"chunk '{cid}' inexistent"}), 404
        return jsonify({"chunk": chunk.to_dict(), "geojson": chunk.to_geojson_feature()})
    except FileNotFoundError:
        return jsonify({"error": "proiect inexistent"}), 404
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.post("/api/projects/<pid>/chunks/<cid>/generate")
def api_projects_generate_chunk(pid: str, cid: str):
    d = request.get_json(force=True, silent=True) or {}
    style_preset = d.get("style_preset") if isinstance(d, dict) else None
    try:
        manifest = project_manager.generate_chunk(pid, cid, style_preset=style_preset)
        return jsonify({"ok": True, "chunk_id": cid, "manifest": manifest})
    except FileNotFoundError:
        return jsonify({"error": "proiect inexistent"}), 404
    except KeyError:
        return jsonify({"error": f"chunk '{cid}' inexistent"}), 404
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.post("/api/projects/<pid>/chunks/validate")
def api_projects_validate_chunks(pid: str):
    try:
        engine = project_manager.get_chunk_engine(pid)
        # Sample test between first pair of adjacent chunks
        items = engine.project.chunks.items
        if len(items) < 2:
            return jsonify({"ok": True, "message": "Grilă cu mai puțin de 2 chunk-uri."})
        ch0 = items[0]
        neighbor_id = ch0.neighbors.get("east") or ch0.neighbors.get("south")
        if not neighbor_id:
            return jsonify({"ok": True, "message": "Niciun vecin adiacent găsit."})

        # Generate DEM for test pair
        from .dem import Dem
        w_lon, s_lat = engine.proj.to_lonlat(-1000.0, -1000.0)
        e_lon, n_lat = engine.proj.to_lonlat(1000.0, 1000.0)
        dem = Dem((s_lat, w_lon, n_lat, e_lon), zoom=14)

        val = engine.validate_boundary_heights(ch0.id, neighbor_id, dem=dem, num_samples=32)
        return jsonify({"ok": True, "validation": val})
    except FileNotFoundError:
        return jsonify({"error": "proiect inexistent"}), 404
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.post("/api/projects/<pid>/export/blender")
def api_projects_export_blender(pid: str):
    d = request.get_json(force=True, silent=True) or {}
    try:
        res = project_manager.export_blender(pid, options=d if isinstance(d, dict) else None)
        return jsonify({
            "ok": True,
            "status": res.get("status", "success"),
            "manifest_path": res.get("manifest_file"),
            "script_path": res.get("script_file"),
            "export_dir": res.get("export_dir"),
            "pre_validation": res.get("pre_validation"),
            "post_validation": res.get("post_validation"),
        })
    except FileNotFoundError:
        return jsonify({"error": "proiect inexistent"}), 404
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


# -------------------------------------------------------------------------
# Terrain Editor Endpoints
# -------------------------------------------------------------------------

@app.get("/api/projects/<pid>/terrain/modifiers")
def api_projects_get_terrain_modifiers(pid: str):
    try:
        editor = project_manager.get_terrain_editor(pid)
        return jsonify(editor.to_dict())
    except FileNotFoundError:
        return jsonify({"error": "proiect inexistent"}), 404
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.post("/api/projects/<pid>/terrain/modifiers")
def api_projects_add_terrain_modifier(pid: str):
    d = request.get_json(force=True, silent=True) or {}
    if not isinstance(d, dict):
        return jsonify({"error": "obiect JSON invalid"}), 400
    try:
        editor = project_manager.get_terrain_editor(pid)
        op = d.get("op", "raise_lower")
        cx = float(d.get("center_x", d.get("center", {}).get("x", 0.0)))
        cy = float(d.get("center_y", d.get("center", {}).get("y", 0.0)))
        radius = float(d.get("radius_m", d.get("radius", 50.0)))
        amount = float(d.get("amount_m", d.get("amount", 0.0)))
        falloff = str(d.get("falloff", "smooth"))
        strength = float(d.get("strength", 1.0))
        region_id = d.get("region_id")
        name = d.get("name")
        polygon = d.get("polygon")
        is_exclusion = bool(d.get("is_exclusion", False))

        if op == "raise_lower":
            if amount >= 0:
                mod = editor.raise_terrain((cx, cy), radius, amount, falloff, strength, region_id, name)
            else:
                mod = editor.lower_terrain((cx, cy), radius, abs(amount), falloff, strength, region_id, name)
        elif op == "flatten":
            target_h = float(d.get("target_height_m", 300.0))
            mod = editor.flatten_terrain((cx, cy), radius, target_h, falloff, strength, region_id, name)
        elif op == "smooth":
            mod = editor.smooth_terrain((cx, cy), radius, strength, falloff, region_id=region_id, name=name)
        elif op in ("hill", "hill_valley") and amount >= 0:
            mod = editor.create_hill((cx, cy), radius, amount or 15.0, falloff, region_id, name)
        elif op in ("valley", "hill_valley") and amount < 0:
            mod = editor.create_valley((cx, cy), radius, abs(amount) or 15.0, falloff, region_id, name)
        elif op in ("water", "water_basin"):
            depth = float(d.get("depth_m", 3.0))
            w_level = float(d["water_level_m"]) if "water_level_m" in d else None
            mod = editor.create_water_area((cx, cy), radius, depth, w_level, falloff, region_id, name)
        elif op in ("mask", "mask_paint"):
            mod = editor.paint_mask(polygon or [], strength, is_exclusion, region_id, name)
        elif op == "exclusion":
            mod = editor.define_exclusion(polygon, (cx, cy) if radius > 0 else None, radius, name)
        else:
            return jsonify({"error": f"Operațiune necunoscută: {op}"}), 400

        project_manager.save_terrain_editor(pid, editor)
        return jsonify({"ok": True, "modifier": mod.to_dict(), "state": editor.to_dict()})
    except FileNotFoundError:
        return jsonify({"error": "proiect inexistent"}), 404
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.post("/api/projects/<pid>/terrain/undo")
def api_projects_terrain_undo(pid: str):
    try:
        editor = project_manager.get_terrain_editor(pid)
        mod = editor.undo()
        project_manager.save_terrain_editor(pid, editor)
        return jsonify({"ok": True, "undone": mod.to_dict() if mod else None, "state": editor.to_dict()})
    except FileNotFoundError:
        return jsonify({"error": "proiect inexistent"}), 404
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.post("/api/projects/<pid>/terrain/redo")
def api_projects_terrain_redo(pid: str):
    try:
        editor = project_manager.get_terrain_editor(pid)
        mod = editor.redo()
        project_manager.save_terrain_editor(pid, editor)
        return jsonify({"ok": True, "redone": mod.to_dict() if mod else None, "state": editor.to_dict()})
    except FileNotFoundError:
        return jsonify({"error": "proiect inexistent"}), 404
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.delete("/api/projects/<pid>/terrain/modifiers/<mid>")
def api_projects_delete_terrain_modifier(pid: str, mid: str):
    try:
        editor = project_manager.get_terrain_editor(pid)
        removed = editor.remove_modifier(mid)
        if not removed:
            return jsonify({"error": f"Modifier '{mid}' nu a fost găsit"}), 404
        project_manager.save_terrain_editor(pid, editor)
        return jsonify({"ok": True, "state": editor.to_dict()})
    except FileNotFoundError:
        return jsonify({"error": "proiect inexistent"}), 404
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.post("/api/projects/<pid>/terrain/export")
def api_projects_export_terrain(pid: str):
    try:
        project = project_manager.open_project(pid)
        p_dir = project_manager._resolve_project_dir(pid)
        out_dir = p_dir / "exports" / "terrain"
        out_dir.mkdir(parents=True, exist_ok=True)

        engine = project_manager.get_chunk_engine(pid)
        # Fetch base DEM
        dem = None
        import sys
        if "pytest" not in sys.modules:
            try:
                from .dem import Dem
                half_w = float(project.chunks.grid.world_size_x_m or 2000.0) / 2.0 * 1.05
                w_lon, s_lat = engine.proj.to_lonlat(-half_w, -half_w)
                e_lon, n_lat = engine.proj.to_lonlat(half_w, half_w)
                dem = Dem((s_lat, w_lon, n_lat, e_lon), zoom=min(12, project.terrain.dem_zoom or 12))
            except Exception:
                pass

        if dem is None:
            import numpy as np

            class OfflineDem:
                def heights(self, lons, lats):
                    lons = np.asarray(lons, dtype=np.float64)
                    return np.full_like(lons, float(project.geographic.elevation_ref or 300.0))

            dem = OfflineDem()

        editor = project_manager.get_terrain_editor(pid, base_dem=dem)
        size_m = float(project.chunks.grid.world_size_x_m or 2000.0)
        res = min(64, project.terrain.resolution)
        meta = editor.export_heightmap(out_dir, size_m=size_m, resolution=res)
        mesh_files = editor.export_terrain_mesh(out_dir, size_m=size_m, resolution=min(32, res))

        return jsonify({
            "ok": True,
            "heightmap_metadata": meta,
            "mesh_files": mesh_files,
            "export_dir": str(out_dir),
        })
    except FileNotFoundError:
        return jsonify({"error": "proiect inexistent"}), 404
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400



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


# Previzualizare locală în jurul centrului hărții. Un singur scan PBF odată.
_MAP_BUILDINGS_LOCK = threading.Lock()
_MAP_BUILDINGS_CACHE: dict = {}


@app.get("/api/map-buildings")
def api_map_buildings():
    from .map_style import map_building_style
    from .osmdata import read_osm

    try:
        lat = float(request.args["lat"])
        lon = float(request.args["lon"])
        if not (18 <= lat <= 72 and -30 <= lon <= 45):
            raise ValueError("coordonate")
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "coordonate invalide"}), 400
    pbf = detect_pbf()
    if not pbf:
        return jsonify({"error": "Nu am găsit fișierul local .osm.pbf"}), 404
    # Zona fixă reduce numărul de scanări când harta se mișcă puțin.
    lat = round(lat, 2)
    lon = round(lon, 2)
    try:
        st = Path(pbf).stat()
    except OSError:
        return jsonify({"error": "Fișierul PBF nu poate fi citit"}), 404
    key = (pbf, st.st_size, st.st_mtime_ns, lat, lon)
    if key in _MAP_BUILDINGS_CACHE:
        return jsonify(_MAP_BUILDINGS_CACHE[key])
    if not _MAP_BUILDINGS_LOCK.acquire(blocking=False):
        return jsonify({"error": "Citirea PBF este deja în curs"}), 429
    try:
        if key in _MAP_BUILDINGS_CACHE:
            return jsonify(_MAP_BUILDINGS_CACHE[key])
        # Acoperă o parte mai mare a orașului în jurul centrului hărții.
        bbox = (lat - 0.04, lon - 0.06, lat + 0.04, lon + 0.06)
        ways = read_osm(pbf, bbox)
        features = []
        for way in ways:
            tags = way.tags
            if not ((tags.get("building") and tags.get("building") != "no")
                    or (tags.get("building:part") and tags.get("building:part") != "no")):
                continue
            rings = way.rings if way.rings else [way.coords]
            # Contururile incomplete de la marginea zonei nu sunt poligoane valide.
            if not rings or any(len(ring) < 4 or ring[0] != ring[-1] for ring in rings):
                continue
            features.append({
                "type": "Feature",
                "properties": map_building_style(tags, way.id),
                "geometry": {"type": "Polygon", "coordinates": rings},
            })
            if len(features) >= 60000:
                break
        result = {"type": "FeatureCollection", "features": features}
        if len(_MAP_BUILDINGS_CACHE) >= 8:
            _MAP_BUILDINGS_CACHE.pop(next(iter(_MAP_BUILDINGS_CACHE)))
        _MAP_BUILDINGS_CACHE[key] = result
        return jsonify(result)
    except Exception as exc:
        app.logger.exception("Citire PBF pentru hartă eșuată")
        return jsonify({"error": f"Citirea PBF a eșuat: {type(exc).__name__}"}), 500
    finally:
        _MAP_BUILDINGS_LOCK.release()


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
