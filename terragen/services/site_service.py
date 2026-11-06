"""Service for managing generated city sites (inspection, zip packaging, filesystem)."""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Any


def safe_site_name(name: str) -> str:
    name = (name or "").strip() or "zona"
    name = re.sub(r"[^\w\-. ]+", "", name, flags=re.UNICODE).strip().replace(" ", "-")
    return name[:48].lower() or "zona"


class SiteService:
    """Operations on generated site output directories."""

    def __init__(self, sites_dir: str | Path | None = None):
        if sites_dir:
            self.root = Path(sites_dir).resolve()
        else:
            env = os.environ.get("TERRAGEN_SITES")
            self.root = Path(env).resolve() if env else (Path.cwd() / "sites").resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def site_path(self, name: str) -> Path:
        p = (self.root / safe_site_name(name)).resolve()
        # Protect against path traversal and root deletion
        if p == self.root or self.root not in p.parents:
            raise ValueError(f"Invalid site name or path: {name}")
        return p

    def list_sites(self) -> list[dict[str, Any]]:
        out = []
        if not self.root.exists():
            return out
        for d in sorted(self.root.iterdir()):
            if d.name.startswith(".terragen-"):
                continue
            mf = d / "manifest.json"
            if not (d.is_dir() and mf.exists()):
                continue
            try:
                m = json.loads(mf.read_text(encoding="utf-8"))
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

    def delete_site(self, name: str) -> bool:
        p = self.site_path(name)
        if not p.exists():
            return False
        shutil.rmtree(p)
        return True

    def create_zip(self, name: str) -> Path:
        p = self.site_path(name)
        if not p.exists():
            raise FileNotFoundError(f"Site directory does not exist: {p}")
        zpath = p.parent / f"_{p.name}.zip"
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for f in p.rglob("*"):
                if f.is_file():
                    z.write(f, f.relative_to(p.parent))
        return zpath

    def open_folder(self, name: str) -> Path:
        p = self.site_path(name)
        if not p.exists():
            raise FileNotFoundError(f"Site folder not found: {p}")
        sys_name = platform.system()
        if sys_name == "Windows":
            os.startfile(str(p))  # type: ignore[attr-defined]
        elif sys_name == "Darwin":
            subprocess.Popen(["open", str(p)])
        else:
            subprocess.Popen(["xdg-open", str(p)])
        return p

