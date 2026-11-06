"""Service for asynchronous staged procedural city generation and job tracking."""

from __future__ import annotations

import json
import queue
import shutil
import tempfile
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Any, Callable

from terragen.cli import GenerationCancelled, generate
from terragen.projects.manager import ProjectManager
from terragen.projects.model import ExportRecord, Project
from terragen.services.site_service import SiteService, safe_site_name


class Job:
    """Tracks status, real-time log lines, result, and cooperative cancellation of a generation run."""

    def __init__(self, params: dict[str, Any]):
        self.id = uuid.uuid4().hex[:12]
        self.params = params
        self.status = "queued"  # queued | running | done | error | cancelled
        self.lines: list[str] = []
        self.result: dict[str, Any] | None = None
        self.error: str | None = None
        self.t0 = time.time()
        self.cancelled = False
        self.publishing = False
        self.q: queue.Queue = queue.Queue()

    def log(self, msg: str) -> None:
        line = time.strftime("%H:%M:%S") + "  " + str(msg)
        self.lines.append(line)
        self.q.put(line)

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "lines": self.lines[-400:],
            "error": self.error,
            "result": self.result,
            "elapsed": round(time.time() - self.t0, 1),
            "params": {k: v for k, v in self.params.items() if k != "pbf"},
        }


class GenerationService:
    """Orchestrates staged generation jobs, cooperative cancellation, and concurrency control."""

    def __init__(
        self,
        site_service: SiteService | None = None,
        project_manager: ProjectManager | None = None,
        max_jobs: int = 25,
    ):
        self.site_service = site_service or SiteService()
        self.project_manager = project_manager or ProjectManager()
        self.max_jobs = max_jobs
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()
        self.sem = threading.Semaphore(1)

    def get_job(self, jid: str) -> Job | None:
        return self.jobs.get(jid)

    def cancel_job(self, jid: str) -> tuple[bool, str | None, int]:
        job = self.jobs.get(jid)
        if not job:
            return False, "job inexistent", 404
        with self.lock:
            if job.status in ("done", "error", "cancelled") or job.publishing:
                return False, "jobul este încheiat sau publicarea a început", 409
            job.cancelled = True
            if job.status == "queued":
                job.status = "cancelled"
        return True, None, 200

    def submit_job(self, params: dict[str, Any]) -> Job:
        job = Job(params)
        self._publish_job(job)
        threading.Thread(target=self._run_job, args=(job,), daemon=True).start()
        return job

    def _publish_job(self, job: Job) -> None:
        with self.lock:
            self.jobs[job.id] = job
            while len(self.jobs) > self.max_jobs:
                for k in sorted(self.jobs, key=lambda j: self.jobs[j].t0):
                    if self.jobs[k].status in ("done", "error", "cancelled"):
                        self.jobs.pop(k, None)
                        break
                else:
                    break

    def _generate_staged(self, job: Job, p: dict[str, Any]) -> dict[str, Any]:
        """Generates into a temporary folder first; preserves existing site on failure or cancellation."""
        sites_dir = self.site_service.root
        sites_dir.mkdir(parents=True, exist_ok=True)
        dest = self.site_service.site_path(p["name"])
        work = Path(tempfile.mkdtemp(prefix=".terragen-", dir=sites_dir))
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
            with self.lock:
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

            # Record in associated project if project_id is given
            project_id = p.get("project_id")
            if project_id:
                try:
                    proj = self.project_manager.open_project(project_id)
                    stats = manifest.get("stats", {})
                    proj.assets.build_dir = str(dest)
                    proj.assets.manifest_path = str(dest / "manifest.json")
                    proj.assets.last_generated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    proj.assets.last_build_stats = stats
                    proj.export_history.append(
                        ExportRecord(
                            id=uuid.uuid4().hex[:8],
                            timestamp=proj.assets.last_generated_at,
                            format=",".join(p.get("formats", ["glb"])),
                            output_path=str(dest),
                            stats=stats,
                            status="success",
                        )
                    )
                    self.project_manager.save_project(proj)
                except Exception as proj_err:
                    job.log(f"Metadatele proiectului nu au putut fi actualizate: {proj_err}")

            return manifest
        finally:
            if not backup.exists():
                shutil.rmtree(work, ignore_errors=True)
            else:
                job.log("Copia veche a fost păstrată în " + str(backup))

    def _run_job(self, job: Job) -> None:
        p = job.params
        with self.sem:
            if job.cancelled:
                job.status = "cancelled"
                job.q.put(None)
                return
            job.status = "running"
            try:
                manifest = self._generate_staged(job, p)
                name = safe_site_name(p["name"])
                dest_path = self.site_service.site_path(name)
                job.result = {
                    "name": name,
                    "out": str(dest_path),
                    "manifest": manifest,
                    "viewer": f"/viewer/?city=../sites/{name}&back=/",
                    "zip": f"/api/zip/{name}",
                    "stats": manifest.get("stats", {}),
                    "triangles": sum(manifest.get("layers_triangles", {}).values()),
                }
                job.status = "done"
                job.log("✔ Gata. Fișierele sunt în " + str(dest_path))
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

