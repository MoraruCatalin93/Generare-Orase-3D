"""Punctul de intrare: `python3 -m terragen [serve|generate|build|check]`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _cmd_serve(a) -> None:
    from . import server

    argv = ["--port", str(a.port), "--host", a.host, "--sites", str(Path(a.sites).expanduser())]
    if a.pbf:
        argv += ["--pbf", a.pbf]
    if a.no_browser:
        argv += ["--no-browser"]
    server.main(argv)


def _cmd_generate(a) -> None:
    from .cli import generate, parse_center

    center = parse_center(a.center)
    formats = ("glb", "obj") if a.obj else ("glb",)
    manifest = generate(
        center=center,
        size=a.size,
        out=a.out,
        pbf=None if a.overpass else a.pbf,
        overpass=a.overpass,
        terrain_res=a.terrain_res,
        dem_zoom=a.dem_zoom,
        formats=formats,
        roofs=not a.no_roofs,
        seed=a.seed,
        name=a.name or Path(a.out).name,
    )
    st = manifest["stats"]
    print()
    print(
        f"  ✔ {st.get('buildings', 0):,} clădiri · "
        f"{sum(manifest['layers_triangles'].values()):,} triunghiuri · "
        f"{manifest['network']['edges']:,} segmente de drum"
    )
    print(f"  ✔ {a.out}/manifest.json")
    print(
        f"\n  vezi orașul:  python3 -m terragen serve   →  "
        f"http://127.0.0.1:8777/viewer/?city=../sites/{Path(a.out).name}\n"
    )


def _cmd_build(a) -> None:
    from .site import build_static

    dest = build_static(Path(a.site), Path(a.out))
    print(f"  ✔ site static: {dest}")
    print(f"  servește-l cu:  python3 -m http.server -d {a.out} 8080\n")


def _cmd_check(a) -> None:
    import importlib
    import platform
    import shutil

    cmd = Path(__file__).resolve().parent
    from . import __version__

    print(f"\nterragen {__version__} · Python {platform.python_version()} · {platform.system()}")
    print(f"pachet: {cmd}\n")
    required = ["numpy", "scipy", "shapely", "pyproj", "mapbox_earcut", "PIL", "requests", "flask"]
    missing = []
    for mod in required:
        try:
            m = importlib.import_module(mod)
            print(f"  ✔ {mod} {getattr(m, '__version__', '')}")
        except Exception as exc:
            missing.append(mod)
            print(f"  ✖ {mod} — {exc}")
    try:
        import osmium  # noqa: F401

        print("  ✔ pyosmium (citire .pbf rapidă)")
    except Exception:
        print("  • pyosmium lipsă — normal pe Python 3.13+; se folosește cititorul intern")
    print(
        f"  {'✔' if shutil.which('osmium') else '•'} osmium-tool "
        f"{'găsit' if shutil.which('osmium') else 'lipsă (opțional)'}"
    )
    print("\nOSM:")
    try:
        from .server import detect_pbf, osm_reader

        pbf = detect_pbf()
        print(f"  {'✔ fișier local: ' + pbf if pbf else '• niciun .osm.pbf local → Overpass API'}")
        if pbf:
            print(f"    citit cu: {osm_reader()}")
    except Exception as exc:
        print(f"  ✖ {exc}")
    if missing:
        print(f"\n✖ Lipsește: {', '.join(missing)}")
        print("  pip install -r requirements.txt")
        sys.exit(2)
    print("\n✔ totul e în ordine:  ./start.sh   (sau  python3 -m terragen serve)\n")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(
        prog="terragen",
        description="Orașe 3D din date reale (OpenStreetMap + DEM) — fără Unity, fără Blender.",
    )
    ap.add_argument("--version", action="version", version="terragen 2.0.0")
    sub = ap.add_subparsers(dest="cmd")

    s = sub.add_parser("serve", help="pornește aplicația locală (hartă + generator + viewer)")
    s.add_argument("--port", type=int, default=8777)
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--sites", default="sites", help="folderul cu orașele generate")
    s.add_argument("--pbf", default=None, help="extract .osm.pbf local")
    s.add_argument("--no-browser", action="store_true")
    s.set_defaults(func=_cmd_serve)

    g = sub.add_parser("generate", help="generează un oraș din linia de comandă")
    g.add_argument("--center", default="targu-mures", help="preset sau `lat,lon`")
    g.add_argument("--size", type=float, default=1500.0, help="latura zonei, în metri")
    g.add_argument("--out", default="sites/oras", help="folderul de ieșire")
    g.add_argument("--pbf", default=None, help="extract .osm.pbf local")
    g.add_argument("--overpass", action="store_true", help="forță Overpass API")
    g.add_argument("--terrain-res", type=int, default=256, dest="terrain_res")
    g.add_argument("--dem-zoom", type=int, default=15, dest="dem_zoom")
    g.add_argument("--seed", type=int, default=7)
    g.add_argument("--obj", action="store_true", help="exportă și OBJ")
    g.add_argument("--no-roofs", action="store_true")
    g.add_argument("--name", default=None)
    g.set_defaults(func=_cmd_generate)

    b = sub.add_parser("build", help="împachetează un oraș ca site static (viewer inclus)")
    b.add_argument("--site", required=True, help="folderul orașului generat")
    b.add_argument("--out", default="dist", help="folderul de ieșire")
    b.set_defaults(func=_cmd_build)

    c = sub.add_parser("check", help="verifică instalarea")
    c.set_defaults(func=_cmd_check)

    a = ap.parse_args(argv)
    if not getattr(a, "func", None):
        a = ap.parse_args(["serve"] + (argv or []))
    if a.cmd == "generate" and not a.pbf and not a.overpass:
        from .server import detect_pbf

        a.pbf = detect_pbf()
    a.func(a)


if __name__ == "__main__":
    main()
