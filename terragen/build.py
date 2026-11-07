"""Construiește meshele 3D (teren, drumuri, clădiri, zone verzi, apă) din date OSM + DEM."""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict

import numpy as np
from shapely import segmentize
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union

from .geom import FLOOR_H, MODULE_U, Mesh, extrude, resample, smooth_1d, triangulate_polygon
from .geom import box as box_geom

# ------------------------------------------------------------------ stil / paletă
# Tabelele de stil sunt ținute compact intenționat (se citesc ca un tabel) — de aceea
# sunt protejate de formatter cu fmt: off.
# fmt: off
WALK_HIGHWAYS = {"footway", "path", "pedestrian", "cycleway", "steps", "bridleway", "corridor"}
SKIP = {"proposed", "construction", "abandoned", "platform", "bus_stop", "elevator",
        "raceway", "busway"}

BASE_WIDTH = {
    "motorway": 14.0, "motorway_link": 8.0, "trunk": 13.0, "trunk_link": 8.0,
    "primary": 11.0, "primary_link": 7.0, "secondary": 9.5, "secondary_link": 7.0,
    "tertiary": 8.5, "tertiary_link": 6.5, "residential": 7.0, "unclassified": 6.5,
    "living_street": 6.0, "service": 4.5, "track": 3.5, "pedestrian": 5.0,
    "footway": 2.2, "path": 1.8, "cycleway": 2.5, "steps": 2.2, "bridleway": 2.0,
}

C = {
    "terrain_low": (146, 154, 136), "terrain_high": (170, 162, 138), "rock": (122, 120, 116),
    "asphalt": (72, 76, 84), "walk": (163, 160, 152),
    "green": (110, 158, 86), "forest": (72, 114, 66),
    "pitch": (96, 150, 82), "water": (68, 128, 165), "parking": (86, 90, 96),
    "industrial": (116, 112, 102), "other": (138, 132, 120),
}

ROOF_COLORS = [        # țiglă / tablă / gudron
    (178, 88, 68), (166, 76, 60), (150, 92, 70), (132, 84, 68),
    (110, 96, 90), (96, 102, 108), (124, 78, 64), (160, 104, 74),
]
DETAIL_COLORS = [(120, 116, 110), (104, 102, 100), (138, 132, 124)]

BUILDING_PALETTE = [
    (216, 202, 178), (201, 181, 156), (223, 212, 192), (185, 172, 152),
    (209, 190, 166), (172, 178, 178), (226, 218, 203), (194, 168, 143),
    (207, 197, 181), (166, 158, 148),
]

HEIGHT_DEFAULTS = {
    "house": 7.5, "detached": 8.0, "semidetached_house": 8.0, "terrace": 8.5,
    "bungalow": 5.0, "apartments": 17.0, "residential": 15.0, "dormitory": 16.0,
    "commercial": 11.0, "retail": 9.0, "kiosk": 3.0, "supermarket": 8.0,
    "industrial": 10.0, "warehouse": 11.0, "factory": 12.0, "garage": 3.0,
    "garages": 3.0, "shed": 2.8, "hut": 2.8, "roof": 4.0, "church": 20.0,
    "cathedral": 30.0, "chapel": 12.0, "school": 12.0, "university": 15.0,
    "hospital": 20.0, "office": 18.0, "hotel": 16.0, "civic": 14.0,
    "public": 13.0, "train_station": 12.0, "yes": 9.0,
}
# fmt: on


def _num(txt):
    try:
        return float(str(txt).replace("m", "").replace(",", ".").strip().split()[0])
    except Exception:
        return None


def road_width(tags) -> float:
    hw = tags.get("highway", "")
    w = _num(tags.get("width")) if tags.get("width") else None
    if w and 1.0 <= w <= 40.0:
        return w
    base = BASE_WIDTH.get(hw, 6.0)
    lanes = _num(tags.get("lanes"))
    if lanes and lanes >= 1:
        base = max(base, lanes * 3.0 + 1.0)
    if tags.get("sidewalk") in ("both", "yes"):
        base += 1.6
    return base


def building_height(tags, wid: int, seed: int = 0) -> float:
    for key in ("height", "building:height"):
        h = _num(tags.get(key)) if tags.get(key) else None
        if h and 2.0 <= h <= 200.0:
            return h
    lv = _num(tags.get("building:levels")) if tags.get("building:levels") else None
    if lv and 1 <= lv <= 60:
        return lv * 3.1 + 1.2
    bt = tags.get("building") or tags.get("building:part") or "yes"
    base = HEIGHT_DEFAULTS.get(bt, 9.0)
    rnd = (int(hashlib.md5(f"{wid}-{seed}".encode()).hexdigest()[:6], 16) % 1000) / 1000.0
    return max(2.6, base * (0.8 + 0.5 * rnd))


def area_kind(tags):
    natural = tags.get("natural")
    landuse = tags.get("landuse")
    leisure = tags.get("leisure")
    waterway = tags.get("waterway")
    amenity = tags.get("amenity")
    if (
        natural == "water"
        or waterway in {"riverbank", "dock", "boatyard"}
        or landuse == "reservoir"
    ):
        return "water"
    if natural == "wood" or landuse == "forest":
        return "forest"
    if natural in {"scrub", "grassland", "heath", "wetland", "sand", "bare_rock"}:
        return "green"
    if leisure == "pitch":
        return "pitch"
    if leisure in {
        "park",
        "garden",
        "nature_reserve",
        "golf_course",
        "sports_centre",
        "playground",
    }:
        return "green"
    if amenity == "parking":
        return "parking"
    if landuse in {"industrial", "railway"}:
        return "industrial"
    if landuse in {
        "grass",
        "meadow",
        "village_green",
        "orchard",
        "allotments",
        "farmland",
        "recreation_ground",
        "flowerbed",
    }:
        return "green"
    if landuse == "cemetery" or amenity in {"school", "hospital", "university"}:
        return "other"
    return None


def building_color(wid: int, seed: int = 0):
    i = int(hashlib.md5(f"{wid}-{seed}".encode()).hexdigest()[:6], 16) % len(BUILDING_PALETTE)
    return BUILDING_PALETTE[i]


def _polys(geom):
    if geom is None or geom.is_empty:
        return []
    if geom.geom_type == "Polygon":
        return [geom]
    if geom.geom_type == "MultiPolygon":
        return list(geom.geoms)
    if hasattr(geom, "geoms"):
        out = []
        for g in geom.geoms:
            out.extend(_polys(g))
        return out
    return []


class CityBuilder:
    def __init__(
        self,
        ways,
        proj,
        dem,
        size_m: float,
        terrain_res: int = 256,
        seed: int = 7,
        roofs: bool = True,
        style_preset: Any = None,
    ):
        self.ways = ways
        self.proj = proj
        self.dem = dem
        self.size = float(size_m)
        self.res = int(terrain_res)
        self.half = self.size / 2.0
        self.step = self.size / self.res
        self.box = box(-self.half, -self.half, self.half, self.half)
        self.seed = seed
        self.roofs = roofs

        # Style preset initialization
        if style_preset is not None:
            from .styles import StylePreset, get_style_preset
            self.style_preset = style_preset if isinstance(style_preset, StylePreset) else get_style_preset(style_preset)
        else:
            self.style_preset = None

        self.terrain_z = None
        self.stats = {}
        self._road_polys: list[Polygon] = []
        self._building_polys: list[Polygon] = []
        self._water_polys: list[Polygon] = []
        self._forest_polys: list[Polygon] = []

    # ---------------------------------------------------------------- utilitare
    def local(self, coords) -> np.ndarray:
        arr = np.asarray(coords, dtype=np.float64)
        x, y = self.proj.to_xy(arr[:, 0], arr[:, 1])
        return np.column_stack([np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64)])

    def shape(self, w) -> Polygon | None:
        """Way/relație → poligon în coordonate locale, cu găuri dacă are (multipolygon)."""
        rings = w.rings if getattr(w, "rings", None) else [w.coords]
        ext = self.local(rings[0])
        if len(ext) < 3:
            return None
        holes = []
        for r in rings[1:]:
            if len(r) < 3:
                continue
            h = self.local(r)
            if len(h) >= 3:
                holes.append(h)
        try:
            poly = Polygon(ext, holes)
        except Exception:
            return None
        if not poly.is_valid:
            poly = poly.buffer(0)
        if poly.is_empty:
            return None
        return poly

    def hz(self, x, y):
        """Altitudinea terenului din grila generată (interpolare biliniară)."""
        x = np.asarray(x, dtype=np.float64)
        y = np.asarray(y, dtype=np.float64)
        u = np.clip((x + self.half) / self.step, 0, self.res - 1e-4)
        v = np.clip((y + self.half) / self.step, 0, self.res - 1e-4)
        i0 = v.astype(np.int32)
        j0 = u.astype(np.int32)
        fv, fu = v - i0, u - j0
        z = self.terrain_z
        out = (
            z[i0, j0] * (1 - fu) * (1 - fv)
            + z[i0, j0 + 1] * fu * (1 - fv)
            + z[i0 + 1, j0] * (1 - fu) * fv
            + z[i0 + 1, j0 + 1] * fu * fv
        )
        return out

    # ---------------------------------------------------------------- teren
    def build_terrain(self):
        n = self.res + 1
        xs = -self.half + np.arange(n) * self.step
        ys = -self.half + np.arange(n) * self.step
        X, Y = np.meshgrid(xs, ys)  # X: est, Y: nord
        lon, lat = self.proj.to_lonlat(X, Y)
        Z = self.dem.heights(np.asarray(lon), np.asarray(lat))
        if self.style_preset and (self.style_preset.terrain.elevation_scale != 1.0 or self.style_preset.terrain.elevation_offset_m != 0.0):
            scale = self.style_preset.terrain.elevation_scale
            offset = self.style_preset.terrain.elevation_offset_m
            zmin_raw = float(Z.min())
            Z = (Z - zmin_raw) * scale + zmin_raw + offset
        self.terrain_z = Z.astype(np.float64)

        # vârfuri (x=Est, y=Sus, z=Nord)
        verts = np.column_stack([X.ravel(), Z.ravel(), Y.ravel()])

        i, j = np.meshgrid(np.arange(self.res), np.arange(self.res), indexing="ij")
        v00 = (i * n + j).ravel()
        v10 = (i * n + j + 1).ravel()
        v01 = ((i + 1) * n + j).ravel()
        v11 = ((i + 1) * n + j + 1).ravel()
        faces = np.vstack(
            [
                np.column_stack([v00, v10, v11]),
                np.column_stack([v00, v11, v01]),
            ]
        )

        # culoare: verde -> galben-verde pe altitudine, gri pe pantă mare
        gy, gx = np.gradient(Z, self.step)
        slope = np.degrees(np.arctan(np.hypot(gx, gy)))
        zmin, zmax = float(Z.min()), float(Z.max())
        t = (Z - zmin) / max(1e-6, (zmax - zmin))

        if self.style_preset:
            lo = np.array(self.style_preset.terrain.low_color, float)
            hi = np.array(self.style_preset.terrain.high_color, float)
            rock = np.array(self.style_preset.terrain.rock_color, float)
            slope_thresh = self.style_preset.terrain.slope_threshold_deg
        else:
            lo = np.array(C["terrain_low"], float)
            hi = np.array(C["terrain_high"], float)
            rock = np.array(C["rock"], float)
            slope_thresh = 18.0

        col = lo[None, :] * (1 - t.ravel()[:, None]) + hi[None, :] * t.ravel()[:, None]
        w = np.clip((slope.ravel() - slope_thresh) / 20.0, 0, 1)[:, None]
        col = col * (1 - w) + rock[None, :] * w

        if self.style_preset and self.style_preset.terrain.peak_color and zmax > zmin + 30.0:
            snow = np.array(self.style_preset.terrain.peak_color, float)
            w_snow = np.clip((Z.ravel() - (zmin + (zmax - zmin) * 0.72)) / max(1.0, (zmax - zmin) * 0.28), 0, 1)[:, None]
            col = col * (1 - w_snow) + snow[None, :] * w_snow

        colors = np.clip(col, 0, 255).astype(np.uint8)

        # Normale analitice pe relief din derivatele pantei
        nx = -gx.ravel()
        ny = np.ones_like(nx)
        nz = -gy.ravel()
        n_len = np.sqrt(nx**2 + ny**2 + nz**2)
        n_len[n_len == 0] = 1.0
        terrain_normals = np.column_stack([nx / n_len, ny / n_len, nz / n_len])

        m = Mesh()
        m.add(verts, faces, colors, normals=terrain_normals)
        self.stats["terrain_elev_range"] = [round(zmin, 1), round(zmax, 1)]
        return m

    # ---------------------------------------------------------------- drumuri
    def build_roads(self):
        road_polys, walk_polys = [], []
        road_pts, road_z, walk_pts, walk_z = [], [], [], []
        network = []

        for w in self.ways:
            hw = w.tags.get("highway")
            if not hw or hw in SKIP:
                continue
            if w.tags.get("tunnel") in ("yes", "building_passage"):
                continue
            xy = self.local(w.coords)
            if len(xy) < 2:
                continue
            line = LineString(xy)
            if not line.intersects(self.box):
                continue
            width = road_width(w.tags)
            if self.style_preset:
                width *= self.style_preset.roads.width_scale
            is_walk = hw in WALK_HIGHWAYS
            r = resample(xy, 4.0)
            if len(r) < 2:
                continue
            z = self.hz(r[:, 0], r[:, 1]) + (0.10 if is_walk else 0.16)
            z = smooth_1d(z, 7)
            try:
                poly = LineString(r).buffer(
                    width / 2.0, cap_style="flat", join_style="round", quad_segs=2
                )
            except Exception:
                continue
            if poly.is_empty:
                continue
            if is_walk:
                walk_polys.append(poly)
                walk_pts.append(r)
                walk_z.append(z)
            else:
                road_polys.append(poly)
                road_pts.append(r)
                road_z.append(z)
                network.append(
                    {
                        "id": w.id,
                        "name": w.tags.get("name", ""),
                        "class": hw,
                        "width": round(width, 2),
                        "lanes": w.tags.get("lanes", ""),
                        "oneway": w.tags.get("oneway", "no"),
                        "maxspeed": w.tags.get("maxspeed", ""),
                        "surface": w.tags.get("surface", ""),
                        "bridge": w.tags.get("bridge", "no"),
                        "points": np.column_stack([r[:, 0], z, r[:, 1]]).round(2).tolist(),
                    }
                )

        self._road_polys = list(road_polys) + list(walk_polys)
        asphalt_c = self.style_preset.roads.asphalt_color if self.style_preset else C["asphalt"]
        walk_c = self.style_preset.roads.sidewalk_color if self.style_preset else C["walk"]
        roads = self._buffers_to_mesh(road_polys, asphalt_c)
        walks = self._buffers_to_mesh(walk_polys, walk_c)
        self.stats["road_ways"] = len(network)
        self.stats["walk_ways"] = sum(
            1 for w in self.ways if w.tags.get("highway") in WALK_HIGHWAYS
        )
        return roads, walks, network

    def _buffers_to_mesh(self, polys, color):
        """Poligoane de carosabil/trotuar -> mesh drapat pe teren.

        Altitudinea fiecărui vârf vine din **teren** (self.hz), nu din cel mai apropiat punct
        de pe axa drumului — altfel, pe pante, asfaltul iese terasat în trepte și terenul
        poate ieși prin el.

        Contururile sunt densificate la ~8 m înainte de triangulare, ca poligoanele lungi și
        înguste (benzile de drum) să aibă destule vârfuri ca să urmeze relieful.
        """
        if not polys:
            return Mesh()
        try:
            merged = unary_union(polys)
        except Exception:
            merged = unary_union([p.buffer(0) for p in polys])

        mesh = Mesh()
        for part in _polys(merged):
            for piece in _polys(part.intersection(self.box)):
                if piece.area < 1.0:
                    continue
                piece = segmentize(piece, 8.0)
                v2, f = triangulate_polygon(piece)
                if not len(f):
                    continue
                z = self.hz(v2[:, 0], v2[:, 1]) + 0.16
                v3 = np.column_stack([v2[:, 0], z, v2[:, 1]])
                mesh.add(v3, f, color)
        return mesh

    # ---------------------------------------------------------------- clădiri
    def build_buildings(self):
        mesh = Mesh()
        self._current_roof = Mesh()
        count = 0
        skipped = 0
        roofed = 0
        counts_mp = 0

        for w in self.ways:
            if "building" not in w.tags and "building:part" not in w.tags:
                continue
            if w.tags.get("building") == "no":
                continue
            poly = self.shape(w)
            if poly is None:
                skipped += 1
                continue
            if self.style_preset and self.style_preset.building.simplification_tol > 0:
                try:
                    poly = poly.simplify(self.style_preset.building.simplification_tol, preserve_topology=True)
                except Exception:
                    pass
            if getattr(w, "rings", None):
                counts_mp += 1
            parts = _polys(poly.intersection(self.box))
            if not parts:
                skipped += 1
                continue

            h = building_height(w.tags, w.id, self.seed)
            if self.style_preset:
                h = h * self.style_preset.building.height_scale
                if self.style_preset.building.height_variation > 0:
                    rnd_h = (
                        (int(hashlib.md5(f"hvar{w.id}-{self.seed}".encode()).hexdigest()[:6], 16) % 1000) / 1000.0 - 0.5
                    ) * 2.0
                    h = max(2.6, h * (1.0 + rnd_h * self.style_preset.building.height_variation))

            hsh = int(hashlib.md5(f"b{w.id}-{self.seed}".encode()).hexdigest()[:6], 16)
            if self.style_preset and self.style_preset.building.facade_palette:
                wall = self.style_preset.building.facade_palette[hsh % len(self.style_preset.building.facade_palette)]
            else:
                wall = building_color(w.id, self.seed)

            if self.style_preset and self.style_preset.building.roof_palette:
                roof_rgb = self.style_preset.building.roof_palette[hsh % len(self.style_preset.building.roof_palette)]
            else:
                roof_rgb = ROOF_COLORS[hsh % len(ROOF_COLORS)]

            min_area = self.style_preset.building.min_area if self.style_preset else 8.0
            roof_styles = self.style_preset.building.roof_styles if self.style_preset else ["gable", "flat"]
            can_gable = "gable" in roof_styles
            can_flat = "flat" in roof_styles
            overhang = self.style_preset.building.roof_overhang if self.style_preset else 0.45
            ridge_scale = self.style_preset.building.roof_ridge_scale if self.style_preset else 1.0
            add_chimneys = self.style_preset.building.add_chimneys if self.style_preset else True

            for part in parts:
                area = float(part.area)
                if area < min_area:
                    continue
                self._building_polys.append(part)
                ring = np.asarray(part.exterior.coords)
                base = float(np.min(self.hz(ring[:, 0], ring[:, 1]))) - 0.35
                top = base + h

                v, f, cap, uv = extrude(part, base, top, floor_h=FLOOR_H, module_u=MODULE_U)
                if v is None:
                    continue
                wall_rgb = np.array(wall, dtype=np.float64)
                cols = np.zeros((len(v), 4), dtype=np.uint8)
                cols[:, :3] = wall_rgb.astype(np.uint8)
                cols[:, 3] = 255
                base_row = np.isclose(v[:, 1], base)
                darkening = self.style_preset.building.ground_floor_darkening if self.style_preset else 0.82
                cols[base_row, :3] = np.clip(wall_rgb * darkening, 0, 255).astype(
                    np.uint8
                )  # parter mai închis
                cols[cap, :3] = np.array(roof_rgb, dtype=np.uint8)
                cols[cap, 3] = 255
                mesh.add(v, f, cols, uv)
                count += 1

                wall_t = tuple(int(c) for c in wall_rgb)
                frame = self._mrr_frame(part)
                fill = (area / (frame[3] * frame[4])) if frame else 0.0

                # cât de mult din dreptunghiul streașinii stă chiar pe clădire
                cover = 0.0
                if frame:
                    center, u, v_, L, D = frame
                    o = overhang
                    quad = Polygon(
                        [
                            (
                                center[0] + u[0] * (-L / 2 - o) + v_[0] * (-D / 2 - o),
                                center[1] + u[1] * (-L / 2 - o) + v_[1] * (-D / 2 - o),
                            ),
                            (
                                center[0] + u[0] * (L / 2 + o) + v_[0] * (-D / 2 - o),
                                center[1] + u[1] * (L / 2 + o) + v_[1] * (-D / 2 - o),
                            ),
                            (
                                center[0] + u[0] * (L / 2 + o) + v_[0] * (D / 2 + o),
                                center[1] + u[1] * (L / 2 + o) + v_[1] * (D / 2 + o),
                            ),
                            (
                                center[0] + u[0] * (-L / 2 - o) + v_[0] * (D / 2 + o),
                                center[1] + u[1] * (-L / 2 - o) + v_[1] * (D / 2 + o),
                            ),
                        ]
                    )
                    try:
                        cover = float(quad.intersection(part.buffer(0.4)).area) / max(
                            quad.area, 1e-6
                        )
                    except Exception:
                        cover = 0.0

                # case mici/medii, aproximativ dreptunghiulare -> acoperiș în două ape
                if (
                    self.roofs
                    and can_gable
                    and h < 14.0
                    and 25.0 < area < 1500.0
                    and frame
                    and fill > 0.55
                    and cover > 0.70
                ):
                    ridge = self._gable_roof(part, top, wall_t, roof_rgb, overhang=overhang, ridge_scale=ridge_scale)
                    roofed += 1
                    # horn
                    if ridge and add_chimneys and hsh % 3 == 0 and area < 500:
                        center, u, v_, L, D = frame
                        off = (hsh % 100 / 100.0 - 0.5) * L * 0.5
                        cx = center[0] + u[0] * off
                        cz = center[1] + u[1] * off
                        self._box(
                            cx, cz, 0.7, 0.7, top + ridge * 0.55, top + ridge + 1.0, (128, 96, 84)
                        )
                elif self.roofs and can_flat:
                    self._flat_roof(part, top, wall_t, roof_rgb, w.id, area)
                    roofed += 1

        self.stats["buildings"] = count
        self.stats["buildings_skipped"] = skipped
        self.stats["buildings_with_roof_detail"] = roofed
        self.stats["buildings_from_relations"] = counts_mp
        return mesh.merged(self._current_roof)

    # ------------------------------------------------------------ acoperișuri
    @staticmethod
    def _mrr_frame(poly: Polygon):
        """Cadru al dreptunghiului minim: (centru, axă_lungă, axă_scurtă, L, D)."""
        try:
            mrr = poly.minimum_rotated_rectangle
            pts = np.asarray(mrr.exterior.coords)[:4]
        except Exception:
            return None
        if len(pts) < 4:
            return None
        e1, e2 = pts[1] - pts[0], pts[2] - pts[1]
        l1, l2 = float(np.linalg.norm(e1)), float(np.linalg.norm(e2))
        if l1 < 1e-6 or l2 < 1e-6:
            return None
        if l1 < l2:  # u = axa lungă
            e1, e2, l1, l2 = e2, e1, l2, l1
        u = e1 / l1
        v = np.array([-u[1], u[0]])
        return pts.mean(axis=0), u, v, l1, l2

    def _box(self, cx, cz, w, d, y0, y1, rgb):
        """Cutie fără fund (horn, casetă tehnică, antenă) adăugată pe mesh-ul de acoperișuri."""
        verts, faces = box_geom(cx, cz, w, d, y0, y1)
        self._current_roof.add(verts, faces, rgb)

    def _gable_roof(
        self,
        poly: Polygon,
        top: float,
        wall_rgb,
        roof_rgb,
        overhang: float = 0.45,
        ridge_scale: float = 1.0,
    ):
        """Acoperiș în două ape, cu streașină, orientat pe latura lungă."""
        frame = self._mrr_frame(poly)
        if frame is None:
            return False
        center, u, v, L, D = frame
        L2, D2 = L / 2.0 + overhang, D / 2.0 + overhang
        ridge_h = float(np.clip(0.34 * D * ridge_scale, 1.2, 5.5))

        def P(a, b, z):
            p = center + u * a + v * b
            return [float(p[0]), float(z), float(p[1])]

        A = P(-L2, -D2, top)
        B = P(L2, -D2, top)
        C = P(L2, D2, top)
        Dd = P(-L2, D2, top)
        R1 = P(-L2, 0, top + ridge_h)
        R2 = P(L2, 0, top + ridge_h)

        roof = np.array([A, B, R2, A, R2, R1, C, Dd, R1, C, R1, R2])
        # winding inversat: altfel normalele streașinii ies în jos și acoperișul se umbrește greșit
        roof_f = np.array([[0, 2, 1], [3, 5, 4], [6, 8, 7], [9, 11, 10]])
        gables = np.array([A, R1, Dd, B, C, R2])
        gable_f = np.array([[0, 2, 1], [3, 5, 4]])
        self._mesh_roof(roof, roof_f, roof_rgb)
        self._mesh_roof(gables, gable_f, wall_rgb)
        return ridge_h

    def _mesh_roof(self, verts, faces, rgb):
        cols = np.full((len(verts), 4), 255, dtype=np.uint8)
        cols[:, :3] = rgb
        self._current_roof.add(np.asarray(verts), np.asarray(faces), cols)

    def _flat_roof(self, poly: Polygon, top: float, wall_rgb, roof_rgb, wid: int, area: float):
        """Acoperiș plat: atic (parapet) + casete tehnice / hornuri."""
        hsh = int(hashlib.md5(f"roof{wid}-{self.seed}".encode()).hexdigest()[:6], 16)
        # atic
        try:
            band = poly.buffer(0).difference(poly.buffer(-0.32, join_style="mitre"))
            for part in _polys(band):
                v, f, cap, uv = extrude(part, top - 0.05, top + 0.55)
                if v is None:
                    continue
                cols = np.full((len(v), 4), 255, dtype=np.uint8)
                cols[:, :3] = wall_rgb
                cols[~cap, :3] = np.clip(np.array(wall_rgb, float) * 1.03, 0, 255).astype(np.uint8)
                self._current_roof.add(v, f, cols, uv)
        except Exception:
            pass

        # casete tehnice pe acoperiș
        n_boxes = 1 if area < 400 else 2
        rp = poly.representative_point()
        for k in range(n_boxes):
            ang = (hsh + k * 211) % 360 * math.pi / 180
            rad = min(6.0, math.sqrt(area) * 0.22) * (((hsh >> (k * 3)) % 100) / 100.0)
            cx = rp.x + math.cos(ang) * rad
            cz = rp.y + math.sin(ang) * rad
            if not poly.contains(Point(cx, cz)):
                cx, cz = rp.x, rp.y
            w = 2.0 + (hsh >> (k * 5)) % 30 / 10.0
            d = 1.6 + (hsh >> (k * 7)) % 24 / 10.0
            hgt = 1.2 + (hsh >> (k * 2)) % 16 / 10.0
            self._box(cx, cz, w, d, top, top + hgt, DETAIL_COLORS[(hsh + k) % len(DETAIL_COLORS)])

        # antenă pe clădirile înalte
        if area > 250 and (hsh % 7 == 0):
            self._box(rp.x, rp.y, 0.16, 0.16, top, top + 3.0, (90, 92, 96))

    # ---------------------------------------------------------------- zone
    def build_areas(self):
        layers = defaultdict(Mesh)
        counts = defaultdict(int)
        counts_mp = 0
        self._water_polys = []
        self._forest_polys = []
        for w in self.ways:
            tags = w.tags
            if "building" in tags or "highway" in tags:
                continue
            kind = area_kind(tags)
            if kind is None:
                continue
            poly = self.shape(w)
            if poly is None:
                continue
            if getattr(w, "rings", None):
                counts_mp += 1
            for part in _polys(poly.intersection(self.box)):
                if part.area < 30.0:
                    continue
                v2, f = triangulate_polygon(part)
                if not len(f):
                    continue
                if kind == "water":
                    self._water_polys.append(part)
                    zs = self.hz(v2[:, 0], v2[:, 1])
                    offset = self.style_preset.water.surface_offset_m if self.style_preset else -0.35
                    level = float(np.percentile(zs, 5)) + offset
                    z = np.full(len(v2), level)
                    water_col = self.style_preset.water.color if self.style_preset else C["water"]
                    v3 = np.column_stack([v2[:, 0], z, v2[:, 1]])
                    layers[kind].add(v3, f, water_col)
                else:
                    if kind == "forest":
                        self._forest_polys.append(part)
                    z = self.hz(v2[:, 0], v2[:, 1]) + 0.06
                    v3 = np.column_stack([v2[:, 0], z, v2[:, 1]])
                    layers[kind].add(v3, f, C[kind])
                counts[kind] += 1
        self.stats["areas"] = dict(counts)
        self.stats["areas_from_relations"] = counts_mp
        return dict(layers)

    # ---------------------------------------------------------------- vegetație
    def build_vegetation(self) -> Mesh:
        """Generează vegetație procedurală (arbori low-poly conifere / foioase) conform stilului."""
        if (
            not self.style_preset
            or not getattr(self.style_preset, "vegetation", None)
            or not self.style_preset.vegetation.enabled
        ):
            return Mesh()
        from .styles import generate_vegetation_layer

        bbox_m = (-self.half, -self.half, self.half, self.half)
        elev_scale = self.style_preset.terrain.elevation_scale if self.style_preset else 1.0
        elev_offset = self.style_preset.terrain.elevation_offset_m if self.style_preset else 0.0
        return generate_vegetation_layer(
            bbox_m=bbox_m,
            dem=self.dem,
            proj=self.proj,
            style=self.style_preset.vegetation,
            seed=self.seed,
            forest_polygons=getattr(self, "_forest_polys", None),
            water_polygons=getattr(self, "_water_polys", None),
            road_polygons=getattr(self, "_road_polys", None),
            building_polygons=getattr(self, "_building_polys", None),
            elevation_scale=elev_scale,
            elevation_offset_m=elev_offset,
        )
