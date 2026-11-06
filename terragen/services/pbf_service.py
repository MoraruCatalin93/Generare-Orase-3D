"""Service for detecting, inspecting, and safely downloading OSM PBF extracts."""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import threading
from pathlib import Path
from typing import Any

from terragen import __version__

PBF_URL = "https://download.geofabrik.de/europe/romania-latest.osm.pbf"
MAX_PBF_BYTES = 4 * 1024**3


def detect_pbf(custom_path: str | Path | None = None) -> str | None:
    """Finds an .osm.pbf extract in project root, parent directory, Downloads, or Desktop."""
    if custom_path and Path(custom_path).exists():
        return str(Path(custom_path).resolve())
    env = os.environ.get("TERRAGEN_PBF")
    if env and Path(env).exists():
        return str(Path(env).resolve())

    candidates: list[Path] = []
    search_dirs = (
        Path.cwd(),
        Path.cwd().parent,
        Path.home() / "Downloads",
        Path.home() / "Desktop",
    )
    for base in search_dirs:
        with contextlib.suppress(Exception):
            candidates += sorted(base.glob("*.osm.pbf"))

    for c in candidates:
        if c.is_file() and c.stat().st_size > 1_000_000:
            return str(c.resolve())
    return None


def osm_reader_name() -> str:
    """Returns human-readable name of available OSM PBF reader."""
    try:
        from terragen.osmdata import osmium

        if osmium is not None:
            return "pyosmium"
    except Exception:
        pass
    if shutil.which("osmium"):
        return "osmium-tool"
    return "pbf-intern (Python pur)"


class PbfDownloadService:
    """Thread-safe background downloader for Geofabrik OSM PBF extracts."""

    def __init__(self, target_dir: Path | None = None):
        self.target_dir = target_dir or Path.cwd()
        self.lock = threading.Lock()
        self.state: dict[str, Any] = {
            "status": "idle",  # idle | running | done | error
            "done": 0,
            "total": 0,
            "error": None,
            "path": None,
        }

    def get_status(self) -> dict[str, Any]:
        with self.lock:
            total = self.state.get("total") or 0
            done = self.state.get("done") or 0
            pct = round(done * 100 / total, 1) if total > 0 else None
            return {
                **self.state,
                "percent": pct,
                "done_mb": round(done / 1e6, 1),
                "total_mb": round(total / 1e6, 1),
            }

    def start_download(self, url: str = PBF_URL) -> tuple[bool, str | None]:
        with self.lock:
            if self.state["status"] == "running":
                return False, "descărcarea e deja în curs"
            self.state.update(status="running", done=0, total=0, error=None, path=None)
            threading.Thread(target=self._run_download, args=(url,), daemon=True).start()
            return True, None

    def _run_download(self, url: str) -> None:
        import requests as rq

        target = self.target_dir / "romania.osm.pbf"
        part = target.with_suffix(".part")
        try:
            from urllib.parse import urljoin, urlsplit

            current = url
            for _ in range(4):
                r = rq.get(
                    current,
                    stream=True,
                    timeout=120,
                    allow_redirects=False,
                    headers={"User-Agent": f"terragen/{__version__}"},
                )
                if not 300 <= r.status_code < 400:
                    break
                location = r.headers.get("Location", "")
                r.close()
                current = urljoin(current, location)
                parsed = urlsplit(current)
                if (
                    parsed.scheme != "https"
                    or parsed.netloc != "download.geofabrik.de"
                    or not re.fullmatch(
                        r"/europe/romania-(?:latest|[0-9]{6})[.]osm[.]pbf", parsed.path
                    )
                    or parsed.query
                    or parsed.fragment
                ):
                    raise ValueError("redirecționare PBF nepermisă")
            else:
                raise ValueError("prea multe redirecționări PBF")

            with r:
                r.raise_for_status()
                total = int(r.headers.get("Content-Length") or 0)
                if total > MAX_PBF_BYTES:
                    raise ValueError("fișierul PBF depășește limita de 4 GiB")
                with self.lock:
                    self.state["total"] = total

                with open(part, "wb") as fh:
                    for chunk in r.iter_content(chunk_size=1 << 19):
                        if chunk:
                            with self.lock:
                                if self.state["done"] + len(chunk) > MAX_PBF_BYTES:
                                    raise ValueError("fișierul PBF depășește limita de 4 GiB")
                                fh.write(chunk)
                                self.state["done"] += len(chunk)

            part.replace(target)
            with self.lock:
                self.state.update(status="done", path=str(target))
        except Exception as exc:
            with self.lock:
                self.state.update(status="error", error=f"{type(exc).__name__}: {exc}")
            part.unlink(missing_ok=True)

