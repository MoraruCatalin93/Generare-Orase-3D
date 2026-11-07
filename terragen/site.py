"""Export static: un oraș generat devine un folder autonom, care merge pus pe orice hosting.

    python3 -m terragen build --site sites/targu-mures --out dist

Rezultat: dist/targu-mures/{index.html, vendor/, manifest.json, glb/, data/, terrain/}
— viewer-ul three.js e copiat lângă date, fără CDN, fără server. Dublu-click pe index.html
funcționează dacă îl servești (ex. `python3 -m http.server`) sau îl urci pe Netlify/GitHub Pages.
"""

from __future__ import annotations

import shutil
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent / "web"
VIEWER_DIR = WEB_DIR / "viewer"

LIST_TEMPLATE = """<!DOCTYPE html>
<meta charset="utf-8"><title>terragen — orașe generate</title>
<style>
  body {{ background:#0e1116; color:#e9eef4; font:14px/1.6 -apple-system,"Segoe UI",Roboto,sans-serif;
         margin:0; padding:48px clamp(20px,8vw,120px); }}
  h1 {{ font-size:22px; margin:0 0 4px; }}
  p  {{ color:#8fa0b2; margin:0 0 26px; font-size:13px; }}
  a  {{ color:#35d0a5; text-decoration:none; }}
  .card {{ display:block; padding:14px 16px; border:1px solid #27313d; border-radius:12px;
           margin-bottom:9px; background:#141920; transition:border-color .15s; }}
  .card:hover {{ border-color:#35d0a5; }}
  .card b {{ color:#e9eef4; font-weight:600; }}
  .card span {{ color:#8fa0b2; font-size:12px; display:block; margin-top:2px; }}
  code {{ color:#8fa0b2; }}
</style>
<h1>terragen — orașe 3D generate</h1>
<p>date: © OpenStreetMap contributors (ODbL) · relief: Terrarium / AWS</p>
{cards}
"""

CARD_TEMPLATE = """<a class="card" href="./{name}/">
  <b>{name}</b>
  <span>{buildings} clădiri · {tris} triunghiuri · {size} × {size} m · seed {seed}</span>
</a>"""


def build_static(site_dir: Path, out_dir: Path) -> Path:
    """Copiază viewer-ul + datele unui oraș într-un folder autonom."""
    import json

    site_dir = Path(site_dir).resolve()
    mf = site_dir / "manifest.json"
    if not mf.exists():
        raise FileNotFoundError(f"nu găsesc {mf} — generează întâi orașul")
    dest = Path(out_dir).resolve() / site_dir.name
    # Ștergerea destinației nu trebuie să atingă sursa, nici invers.
    if dest == site_dir or dest in site_dir.parents or site_dir in dest.parents:
        raise ValueError("directorul sursă și destinația exportului nu trebuie să se suprapună")
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)

    shutil.copytree(VIEWER_DIR / "vendor", dest / "vendor")
    shutil.copy2(VIEWER_DIR / "index.html", dest / "index.html")
    theme_src = WEB_DIR / "ui" / "theme.css"
    if theme_src.exists():
        shutil.copy2(theme_src, dest / "theme.css")
    shutil.copy2(mf, dest / "manifest.json")
    for sub in ("glb", "obj", "data", "terrain"):
        src = site_dir / sub
        if src.exists():
            shutil.copytree(src, dest / sub)
    for extra in site_dir.glob("*.png"):
        shutil.copy2(extra, dest / extra.name)

    # index cu toate orașele din folderul de ieșire
    cards = []
    for cand in sorted(Path(out_dir).resolve().iterdir()):
        cmf = cand / "manifest.json"
        if not (cand.is_dir() and cmf.exists()):
            continue
        m = json.loads(cmf.read_text())
        cards.append(
            CARD_TEMPLATE.format(
                name=cand.name,
                buildings=m.get("stats", {}).get("buildings", 0),
                tris=sum(m.get("layers_triangles", {}).values()),
                size=round(m.get("area", {}).get("size_m", 0)),
                seed=m.get("area", {}).get("seed", "–"),
            )
        )
    (Path(out_dir).resolve() / "index.html").write_text(
        LIST_TEMPLATE.format(cards="\n".join(cards) or "<p><code>niciun oraș încă</code></p>"),
        encoding="utf-8",
    )
    return dest
