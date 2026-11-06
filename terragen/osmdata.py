"""Citirea datelor OpenStreetMap pentru un bbox (din .osm.pbf local sau Overpass)."""

from __future__ import annotations

from dataclasses import dataclass, field

try:  # pyosmium e nevoie doar pentru citirea fișierelor .osm.pbf
    import osmium
except Exception:
    osmium = None

# ---- ce ne interesează pentru un oraș de joc ----
AREA_NATURAL = {"water", "wood", "scrub", "grassland", "heath", "wetland", "bare_rock", "sand"}
AREA_LANDUSE = {
    "grass",
    "forest",
    "meadow",
    "village_green",
    "cemetery",
    "allotments",
    "orchard",
    "farmland",
    "railway",
    "recreation_ground",
    "flowerbed",
}
AREA_LEISURE = {
    "park",
    "garden",
    "pitch",
    "playground",
    "nature_reserve",
    "golf_course",
    "sports_centre",
}
AREA_WATERWAY = {"riverbank", "dock", "boatyard"}
SKIP_HIGHWAY = {
    "proposed",
    "construction",
    "abandoned",
    "platform",
    "bus_stop",
    "elevator",
    "corridor",
    "raceway",
    "busway",
}


@dataclass
class Way:
    id: int
    tags: dict
    coords: list = field(default_factory=list)  # [(lon, lat), ...]


def _wanted(tags) -> bool:
    if "building" in tags and tags["building"] not in ("no",):
        return True
    if "building:part" in tags:
        return True
    hw = tags.get("highway")
    if hw and hw not in SKIP_HIGHWAY:
        return True
    if tags.get("natural") in AREA_NATURAL:
        return True
    if tags.get("landuse") in AREA_LANDUSE:
        return True
    if tags.get("leisure") in AREA_LEISURE:
        return True
    if tags.get("waterway") in AREA_WATERWAY:
        return True
    return tags.get("amenity") in {"parking", "school", "hospital", "university"}


def read_pbf(path: str, bbox, cache_dir: str | None = None) -> list[Way]:
    """O singură trecere prin fișier (nodurile vin înaintea way-urilor în PBF).

    Rezultatul se cachează (pickle) ca să nu rescanezi 300+ MB la fiecare rulare.
    """
    import hashlib
    import pickle
    from pathlib import Path

    if osmium is None:
        raise RuntimeError(
            "Lipsește modulul pyosmium (necesar pentru citirea fișierelor .osm.pbf).\n"
            "  Instalează-l:  pip install pyosmium\n"
            "  Sau rulează fără fișier local:  python -m terragen.app   (folosește Overpass API)"
        )

    cache = None
    if cache_dir:
        key = hashlib.md5(
            (f"{Path(path).resolve()}|" + ",".join(f"{v:.6f}" for v in bbox)).encode()
        ).hexdigest()[:16]
        cache = Path(cache_dir) / f"ways_{Path(path).stem}_{key}.pkl"
        if cache.exists():
            try:
                return pickle.loads(cache.read_bytes())
            except Exception:
                pass

    s, w, n, e = bbox

    class _Collector(osmium.SimpleHandler):
        def __init__(self):
            super().__init__()
            self.nodes: dict[int, tuple[float, float]] = {}
            self.ways: list[Way] = []

        def node(self, nd):
            try:
                lon, lat = nd.location.lon, nd.location.lat
            except osmium.InvalidLocationError:
                return
            if s <= lat <= n and w <= lon <= e:
                self.nodes[nd.id] = (lon, lat)

        def way(self, way):
            tags = dict(way.tags)
            if not _wanted(tags):
                return
            runs, cur = [], []
            for nd in way.nodes:
                c = self.nodes.get(nd.ref)
                if c is None:
                    if len(cur) >= 2:
                        runs.append(cur)
                    cur = []
                else:
                    cur.append(c)
            if len(cur) >= 2:
                runs.append(cur)
            for r in runs:
                self.ways.append(Way(id=way.id, tags=tags, coords=r))

    h = _Collector()
    h.apply_file(path, locations=False)
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(pickle.dumps(h.ways, protocol=4))
    return h.ways


def read_osmium_cli(path: str, bbox, cache_dir: str | None = None) -> list[Way]:
    """Citire .osm.pbf fără pyosmium, prin utilitarul `osmium` (osmium-tool).

    Necesită: brew install osmium-tool   (macOS) / apt install osmium-tool   (Linux)
    Folosește `osmium extract` pentru zonă + `osmium export` în GeoJSON.
    """
    import hashlib
    import pickle
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path

    binary = shutil.which("osmium")
    if not binary:
        raise RuntimeError("Nu găsesc utilitarul `osmium` în PATH.")

    cache = None
    if cache_dir:
        key = hashlib.md5(
            (f"cli|{Path(path).resolve()}|" + ",".join(f"{v:.6f}" for v in bbox)).encode()
        ).hexdigest()[:16]
        cache = Path(cache_dir) / f"ways_cli_{Path(path).stem}_{key}.pkl"
        if cache.exists():
            try:
                return pickle.loads(cache.read_bytes())
            except Exception:
                pass

    s_, w_, n_, e_ = bbox
    with tempfile.TemporaryDirectory(prefix="terragen_") as tmp:
        ext = Path(tmp) / "area.osm.pbf"
        gj = Path(tmp) / "area.geojson"
        subprocess.run(
            [
                binary,
                "extract",
                "-b",
                f"{w_},{s_},{e_},{n_}",
                "-f",
                "pbf",
                "-o",
                str(ext),
                "--overwrite",
                str(path),
            ],
            check=True,
            capture_output=True,
            timeout=1800,
        )
        subprocess.run(
            [
                binary,
                "export",
                "-f",
                "geojson",
                "-o",
                str(gj),
                "--overwrite",
                "-a",
                "type,id",
                "--geometry-types",
                "linestring,polygon",
                str(ext),
            ],
            check=True,
            capture_output=True,
            timeout=1800,
        )
        ways = _parse_geojson_seq(gj.read_text(encoding="utf-8", errors="replace"))

    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(pickle.dumps(ways, protocol=4))
    return ways


def _parse_geojson_seq(text: str) -> list[Way]:
    """Parsare ieșire `osmium export` (FeatureCollection sau GeoJSON Text Sequence)."""
    import json

    text = text.strip()
    try:
        doc = json.loads(text)
        features = (
            doc.get("features", [])
            if isinstance(doc, dict) and doc.get("type") == "FeatureCollection"
            else [doc]
        )
    except Exception:
        features = []
        for chunk in text.split("\x1e"):
            chunk = chunk.strip()
            if not chunk.startswith("{"):
                continue
            try:
                features.append(json.loads(chunk))
            except Exception:
                continue

    # suprafețele apar și ca LineString închis + Polygon; păstrăm Polygon-ul
    poly_keys = set()
    for f in features:
        g = (f or {}).get("geometry") or {}
        if g.get("type") not in ("Polygon", "MultiPolygon"):
            continue
        try:
            ring = g["coordinates"][0] if g["type"] == "Polygon" else g["coordinates"][0][0]
            key = (
                tuple(sorted((f.get("properties") or {}).items())),
                round(ring[0][0], 7),
                round(ring[0][1], 7),
            )
            poly_keys.add(key)
        except Exception:
            pass

    out: list[Way] = []
    for f in features:
        if not isinstance(f, dict) or f.get("type") != "Feature":
            continue
        raw = f.get("properties") or {}
        props = {k: v for k, v in raw.items() if not k.startswith("@") and k != "type"}
        if not _wanted(props):
            continue
        geom = f.get("geometry") or {}
        gt, c = geom.get("type"), geom.get("coordinates")
        if not c:
            continue
        try:
            if gt == "LineString":
                if len(c) >= 4 and c[0] == c[-1]:
                    key = (tuple(sorted(raw.items())), round(c[0][0], 7), round(c[0][1], 7))
                    if key in poly_keys:
                        continue  # way închis deja exportat ca Polygon
                coords = c
            elif gt == "Polygon":
                coords = c[0]
            elif gt == "MultiLineString":
                coords = max(c, key=len)
            elif gt == "MultiPolygon":
                coords = max((poly[0] for poly in c), key=len)
            else:
                continue
            coords = [(float(p[0]), float(p[1])) for p in coords]
        except Exception:
            continue
        if len(coords) >= 2:
            try:
                wid = int(raw.get("@id", 0) or 0)
            except Exception:
                wid = 0
            out.append(Way(id=wid, tags=props, coords=coords))
    return out


def read_pbflite(path: str, bbox, cache_dir: str | None = None, progress=None) -> list[Way]:
    """Citire .osm.pbf în Python pur (fără pyosmium / osmium-tool)."""
    import hashlib
    import pickle
    from pathlib import Path

    from .pbflite import read_ways

    cache = None
    if cache_dir:
        key = hashlib.md5(
            (f"lite|{Path(path).resolve()}|" + ",".join(f"{v:.6f}" for v in bbox)).encode()
        ).hexdigest()[:16]
        cache = Path(cache_dir) / f"ways_lite_{Path(path).stem}_{key}.pkl"
        if cache.exists():
            try:
                return pickle.loads(cache.read_bytes())
            except Exception:
                pass

    raw = read_ways(path, bbox, _wanted, progress=progress)
    ways = [Way(id=w["id"], tags=w["tags"], coords=w["coords"]) for w in raw]
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(pickle.dumps(ways, protocol=4))
    return ways


def read_osm(path: str, bbox, cache_dir: str | None = None, progress=None, log=None) -> list[Way]:
    """Citește un extract OSM local. Ordine: pyosmium → osmium-tool → cititor PBF intern."""
    if osmium is not None:
        if log:
            log("      cititor: pyosmium")
        return read_pbf(path, bbox, cache_dir=cache_dir)

    import shutil

    if shutil.which("osmium"):
        if log:
            log("      cititor: osmium-tool (CLI)")
        return read_osmium_cli(path, bbox, cache_dir=cache_dir)

    if log:
        log(
            "      cititor: PBF intern (Python pur) — prima citire a unui fișier mare durează câteva minute"
        )
    return read_pbflite(path, bbox, cache_dir=cache_dir, progress=progress)


def fetch_overpass(bbox, timeout: int = 180, endpoint: str | None = None) -> list[Way]:
    """Alternativă la extractul local: descarcă direct din Overpass API."""
    import requests

    s, w, n, e = bbox
    q = f"""[out:json][timeout:{timeout}];
(
  way["building"]({s},{w},{n},{e});
  way["building:part"]({s},{w},{n},{e});
  way["highway"]({s},{w},{n},{e});
  way["natural"~"^(water|wood|scrub|grassland|heath|wetland|bare_rock|sand)$"]({s},{w},{n},{e});
  way["landuse"]({s},{w},{n},{e});
  way["leisure"~"^(park|garden|pitch|playground|nature_reserve|golf_course|sports_centre)$"]({s},{w},{n},{e});
  way["waterway"~"^(riverbank|dock|boatyard)$"]({s},{w},{n},{e});
  way["amenity"~"^(parking|school|hospital|university)$"]({s},{w},{n},{e});
);
out tags geom;"""
    endpoints = (
        [endpoint]
        if endpoint
        else [
            "https://overpass-api.de/api/interpreter",
            "https://overpass.openstreetmap.fr/api/interpreter",
            "https://overpass.private.coffee/api/interpreter",
            "https://overpass.kumi.systems/api/interpreter",
        ]
    )
    last = None
    for attempt in range(2):
        for ep in endpoints:
            try:
                r = requests.post(
                    ep,
                    data={"data": q},
                    timeout=timeout + 30,
                    headers={"User-Agent": "terragen-web/2.0 (local city mesh generator)"},
                )
                r.raise_for_status()
                data = r.json()
                out = []
                for el in data.get("elements", []):
                    geom = el.get("geometry") or []
                    if len(geom) < 2:
                        continue
                    out.append(
                        Way(
                            id=el["id"],
                            tags=el.get("tags", {}),
                            coords=[(p["lon"], p["lat"]) for p in geom],
                        )
                    )
                if out:
                    return out
            except Exception as exc:
                last = exc
        if attempt == 0:
            import time as _t

            _t.sleep(5)  # mica pauză înainte de a doua rundă
    raise RuntimeError(
        f"Toate serverele Overpass au eșuat ({last}).\n"
        + "Soluția recomandată: descarcă o singură dată extractul OSM "
        "(butonul 'Descarcă datele OSM' din aplicație, sau manual de la "
        "https://download.geofabrik.de/europe/romania-latest.osm.pbf) "
        "și pune-l în folderul proiectului. Aplicația îl citește singură, offline."
    )
