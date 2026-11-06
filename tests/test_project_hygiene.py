"""Teste „de igienă": apără curățenia repo-ului, nu doar geometria.

Rulează în CI, deci prind regresii de tipul: fișiere generate urcate în git,
cod mort readăugat, versiune duplicată.
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_version_has_single_source():
    """Versiunea stă într-un singur loc (`terragen/__init__.py`)."""
    others = []
    for py in (ROOT / "terragen").rglob("*.py"):
        if py.name == "__init__.py":
            continue
        if re.search(r"^__version__\s*=", py.read_text(encoding="utf-8"), re.M):
            others.append(str(py.relative_to(ROOT)))
    assert others == [], f"versiune duplicată în: {others}"


def test_package_imports_without_side_effects():
    out = subprocess.run(
        [sys.executable, "-c", "import terragen; print(terragen.__version__)"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip().count(".") == 2


def test_gitignore_covers_generated_stuff():
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for pattern in (".venv/", "sites/", "dist/", "*.osm.pbf", "__pycache__/", ".ruff_cache/"):
        assert pattern in ignored, f"lipsește {pattern} din .gitignore"


def test_generated_dirs_are_not_tracked():
    """Local poți genera în sites/ — dar nimic de acolo nu are voie în git."""
    if not (ROOT / ".git").exists():
        pytest.skip("fără repo git (arhivă/CI fără .git)")
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    leaked = [
        f
        for f in tracked
        if f.startswith(("sites/", "dist/", "out/")) or f.endswith((".osm.pbf", ".zip"))
    ]
    assert leaked == [], f"fișiere generate urcate în git: {leaked[:5]}"


def test_vendor_dir_is_present_but_not_ours():
    """Viewer-ul și harta trebuie să fie locale (fără CDN)."""
    assert (ROOT / "terragen/web/viewer/vendor/three.module.js").is_file()
    assert (ROOT / "terragen/web/ui/vendor/maplibre-gl.js").is_file()
    html = (ROOT / "terragen/web/viewer/index.html").read_text(encoding="utf-8")
    assert "unpkg.com" not in html and "cdn.jsdelivr.net" not in html
    ui = (ROOT / "terragen/web/ui/index.html").read_text(encoding="utf-8")
    assert "unpkg.com" not in ui and "cdn.jsdelivr.net" not in ui


def test_no_leftover_debug_markers():
    bad = []
    for path in list((ROOT / "terragen").rglob("*.py")) + list(
        (ROOT / "terragen/web").rglob("*.js")
    ):
        if "vendor" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for marker in ("pdb.set_trace", "breakpoint()", "console.log(", "debugger;"):
            if marker in text:
                bad.append(f"{path.relative_to(ROOT)}: {marker}")
    assert bad == [], f"rămășițe de debug: {bad}"
