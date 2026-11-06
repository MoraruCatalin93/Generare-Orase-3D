"""CLI: generează orașul 3D dintr-un centru + dimensiune (plus build complet de site web)."""

from __future__ import annotations

import time
from pathlib import Path

from .build import CityBuilder
from .dem import Dem
from .export import build_network_graph, write_glb, write_heightmap, write_json, write_obj
from .facade import facade_png
from .geom import FLOOR_H, MODULE_U
from .osmdata import fetch_overpass, read_osm
from .project import LocalProjection

__version__ = "2.0.0"

PRESETS = {
    "targu-mures": (46.5446, 24.5612),
    "cluj": (46.7712, 23.6236),
    "brasov": (45.6520, 25.6100),
    "sibiu": (45.7970, 24.1520),
    "bucuresti": (44.4323, 26.1010),
    "oradea": (47.0570, 21.9300),
    "timisoara": (45.7489, 21.2087),
    "berlin": (52.5200, 13.4050),
    "paris": (48.8566, 2.3522),
}

LAYERS = (
    "terrain",
    "roads",
    "sidewalks",
    "buildings",
    "green",
    "forest",
    "water",
    "pitch",
    "parking",
    "industrial",
    "other",
)

TEXTURED_LAYERS = ("buildings",)


def parse_center(txt: str):
    """`targu-mures` sau `46.5446,24.5612` -> (lat, lon)."""
    if txt in PRESETS:
        return PRESETS[txt]
    lat, lon = [float(x) for x in txt.replace(" ", "").split(",")]
    return lat, lon


def generate(
    center,
    size: float = 1500.0,
    out: str = "out/city",
    pbf: str | None = None,
    overpass: bool = False,
    terrain_res: int = 256,
    dem_zoom: int = 15,
    formats=("glb",),
    roofs: bool = True,
    preview: bool = True,
    seed: int = 7,
    name: str | None = None,
    ways=None,
    log=None,
):
    """Generează straturile 3D și scrie manifestul. Returnează manifestul (dict)."""
    log = log or (lambda msg: print(msg, flush=True))
    t0 = time.time()
    lat0, lon0 = center
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    proj = LocalProjection(lat0, lon0)
    bbox = proj.bbox_of_square(size * 1.05)

    if ways is None:
        src = "Overpass API" if (overpass or not pbf) else f"fișier local {Path(pbf).name}"
        log(f"[1/6] Date OSM ({src}) …")
    log(f"      bbox {tuple(round(v, 5) for v in bbox)}")
    if ways is None:
        if overpass or not pbf:
            ways = fetch_overpass(bbox)
        else:
            last = [-10]

            def _prog(done, total):
                pct = int(done * 100 / max(1, total))
                if pct >= last[0] + 10:
                    last[0] = pct
                    log(
                        f"      … citit {pct}% din fișierul OSM "
                        f"({done / 1e6:.0f}/{total / 1e6:.0f} MB)"
                    )

            ways = read_osm(
                pbf,
                bbox,
                cache_dir=str(Path.home() / ".cache" / "terragen" / "osm"),
                progress=_prog,
                log=log,
            )
    log(f"      {len(ways)} obiecte utile (clădiri / drumuri / zone)")

    log(f"[2/6] Relief — DEM zoom {dem_zoom} …")
    dem = Dem(bbox, zoom=dem_zoom)
    log(
        f"      mozaic {dem.mosaic.shape[1]}×{dem.mosaic.shape[0]} px, "
        f"~{dem.meters_per_pixel(lat0):.1f} m/pixel"
    )

    b = CityBuilder(ways, proj, dem, size_m=size, terrain_res=terrain_res, seed=seed, roofs=roofs)

    log("[3/6] Teren …")
    terrain = b.build_terrain()
    log(f"      {len(terrain.f):,} triunghiuri, altitudini {b.stats['terrain_elev_range']} m")

    log("[4/6] Drumuri + trotuare …")
    roads, walks, network = b.build_roads()
    log(
        f"      carosabil {len(roads.f):,} tri · trotuare {len(walks.f):,} · {len(network)} segmente"
    )

    log("[5/6] Clădiri + zone verzi / apă / parcări …")
    buildings = b.build_buildings()
    areas = b.build_areas()
    log(
        f"      clădiri {b.stats['buildings']:,} ({len(buildings.f):,} tri) · "
        f"zone {b.stats['areas']}"
    )

    log("[6/6] Export …")
    layers = {"terrain": terrain, "roads": roads, "sidewalks": walks, "buildings": buildings}
    for k in LAYERS[4:]:
        if k in areas and len(areas[k].f):
            layers[k] = areas[k]

    tex = facade_png()
    files: dict = {}
    if "glb" in formats:
        for k, m in layers.items():
            n = write_glb(
                m, out / "glb" / f"{k}.glb", texture_png=tex if k in TEXTURED_LAYERS else None
            )
            if n:
                files.setdefault(k, {})["glb"] = f"glb/{k}.glb"
                files[k]["glb_mb"] = round(n / 1e6, 2)
    if "obj" in formats:
        for k, m in layers.items():
            n = write_obj(
                m,
                out / "obj" / f"{k}.obj",
                texture_png=tex if k in TEXTURED_LAYERS else None,
                layer=k,
            )
            if n:
                files.setdefault(k, {})["obj"] = f"obj/{k}.obj"
                files[k]["obj_mb"] = round(n / 1e6, 2)

    hm = write_heightmap(b.terrain_z, out / "terrain", size_m=size)
    graph = build_network_graph(network)
    write_json(graph, out / "data" / "roads_network.json")
    write_json(
        {
            "layers": [k for k in LAYERS if k in files],
            "stats": b.stats,
        },
        out / "data" / "summary.json",
    )

    # centrul zonei, ca viewer-ul și harta să poată sări exact acolo
    write_json({"center_lat": lat0, "center_lon": lon0, "size_m": size}, out / "data" / "site.json")

    manifest = {
        "generator": f"terragen-web {__version__}",
        "area": {
            "name": name or f"{lat0:.4f},{lon0:.4f}",
            "center_lat": lat0,
            "center_lon": lon0,
            "size_m": size,
            "terrain_res": terrain_res,
            "dem_zoom": dem_zoom,
            "seed": seed,
            "facade_module_m": MODULE_U,
            "floor_height_m": FLOOR_H,
        },
        "coordinate_systems": {
            "glb_files": "glTF standard: X = Est, Y = Sus, Z = -Nord (metri, origine = centrul zonei)",
            "obj_files": "X = Est, Y = Sus, Z = Nord (metri, origine = centrul zonei)",
            "roads_network_json": "puncte [x = Est, y = Sus, z = Nord]",
        },
        "files": files,
        "layers": [k for k in LAYERS if k in files],
        "heightmap": hm,
        "stats": b.stats,
        "layers_triangles": {k: len(m.f) for k, m in layers.items()},
        "network": {"nodes": len(graph["nodes"]), "edges": len(graph["edges"])},
        "source": "OpenStreetMap (© contributori OSM, ODbL) · DEM Terrarium (AWS/Mapzen)",
    }
    write_json(manifest, out / "manifest.json")

    log(f"Gata în {time.time() - t0:.1f}s -> {out}")
    return manifest
