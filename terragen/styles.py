"""Stylized World Generation: Style Presets, Procedural Vegetation, and Stylized Geometry.

Provides configurable, deterministic visual style presets derived from real OSM and DEM data:
- Stylized European Town
- Stylized Rural
- Stylized Forest
- Stylized Mountains
- Custom (User-defined overrides)
- Realistic (Standard baseline)

Features:
- Building simplification, stylized roof profiles, and harmonic color palettes.
- Procedural low-poly vegetation generation (conifers, deciduous, poplars).
- Spatial exclusion rules for vegetation: slope, water, road, building, and region constraints.
- Stylized terrain elevation and slope shader coloring.
- 100% deterministic, zero external asset dependencies.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
import hashlib
import math
from typing import Any

import numpy as np
from shapely.geometry import Point, Polygon, box
from shapely.ops import unary_union

from .geom import Mesh, extrude


# -----------------------------------------------------------------------------
# Color & Style Configuration Dataclasses
# -----------------------------------------------------------------------------

RGB = tuple[int, int, int]


@dataclass
class BuildingStyle:
    simplification_tol: float = 0.8  # Shapely simplify tolerance in meters
    min_area: float = 12.0          # Minimum footprint area to generate
    height_scale: float = 1.0       # Overall height multiplier
    height_variation: float = 0.35  # Deterministic random height perturbation
    facade_palette: list[RGB] = field(default_factory=list)
    roof_palette: list[RGB] = field(default_factory=list)
    roof_styles: list[str] = field(default_factory=lambda: ["gable", "flat", "hip"])
    roof_overhang: float = 0.45     # Eaves overhang in meters
    roof_ridge_scale: float = 1.0   # Ridge height scale for gabled roofs
    ground_floor_darkening: float = 0.85  # Contrast ratio for ground level
    add_chimneys: bool = True


@dataclass
class VegetationStyle:
    enabled: bool = True
    density_per_ha: float = 15.0         # Trees per hectare in open terrain
    forest_density_per_ha: float = 140.0 # Trees per hectare in forest areas
    tree_types: list[str] = field(default_factory=lambda: ["pine", "deciduous"])
    foliage_palette: list[RGB] = field(default_factory=list)
    trunk_palette: list[RGB] = field(default_factory=lambda: [(102, 72, 48), (85, 62, 42)])
    scale_min: float = 0.75
    scale_max: float = 1.35
    max_slope_deg: float = 34.0          # Maximum terrain slope for tree growth
    exclude_roads_buffer_m: float = 3.0  # Buffer distance around roads
    exclude_buildings_buffer_m: float = 2.0


@dataclass
class TerrainStyle:
    low_color: RGB = (135, 175, 105)      # Lush valley green
    mid_color: RGB = (160, 185, 120)      # Upland meadow
    high_color: RGB = (175, 170, 145)     # Subalpine transition
    rock_color: RGB = (120, 122, 128)     # Cliff slate rock
    peak_color: RGB | None = None         # Snow / high alpine peak
    slope_threshold_deg: float = 20.0
    slope_blend_range_deg: float = 16.0


@dataclass
class RoadStyle:
    asphalt_color: RGB = (68, 72, 80)
    sidewalk_color: RGB = (168, 164, 155)
    track_color: RGB = (145, 130, 110)
    width_scale: float = 1.0


@dataclass
class WaterStyle:
    color: RGB = (62, 138, 182)
    opacity: float = 0.85
    surface_offset_m: float = -0.30


@dataclass
class MaterialSettings:
    roughness: float = 0.80  # Matte stylized finish
    metallic: float = 0.04


@dataclass
class StylePreset:
    name: str
    label: str
    description: str
    building: BuildingStyle = field(default_factory=BuildingStyle)
    vegetation: VegetationStyle = field(default_factory=VegetationStyle)
    terrain: TerrainStyle = field(default_factory=TerrainStyle)
    roads: RoadStyle = field(default_factory=RoadStyle)
    water: WaterStyle = field(default_factory=WaterStyle)
    material: MaterialSettings = field(default_factory=MaterialSettings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "description": self.description,
            "building": {
                "simplification_tol": self.building.simplification_tol,
                "height_scale": self.building.height_scale,
                "height_variation": self.building.height_variation,
                "roof_overhang": self.building.roof_overhang,
                "roof_ridge_scale": self.building.roof_ridge_scale,
                "add_chimneys": self.building.add_chimneys,
                "facade_palette": list(self.building.facade_palette),
                "roof_palette": list(self.building.roof_palette),
            },
            "vegetation": {
                "enabled": self.vegetation.enabled,
                "density_per_ha": self.vegetation.density_per_ha,
                "forest_density_per_ha": self.vegetation.forest_density_per_ha,
                "tree_types": list(self.vegetation.tree_types),
                "max_slope_deg": self.vegetation.max_slope_deg,
            },
            "terrain": {
                "low_color": self.terrain.low_color,
                "mid_color": self.terrain.mid_color,
                "high_color": self.terrain.high_color,
                "rock_color": self.terrain.rock_color,
                "peak_color": self.terrain.peak_color,
            },
            "roads": {
                "asphalt_color": self.roads.asphalt_color,
                "width_scale": self.roads.width_scale,
            },
            "water": {
                "color": self.water.color,
            },
        }


# -----------------------------------------------------------------------------
# Built-In Presets
# -----------------------------------------------------------------------------

def _make_stylized_european_town() -> StylePreset:
    return StylePreset(
        name="stylized_european_town",
        label="Stylized European Town",
        description="Terracotta gabled roofs, warm pastel facades, cobblestone-tinted roads, and lush town parks.",
        building=BuildingStyle(
            simplification_tol=0.75,
            min_area=10.0,
            height_scale=1.05,
            height_variation=0.35,
            facade_palette=[
                (242, 230, 210), (235, 215, 190), (222, 202, 182),
                (245, 238, 222), (218, 220, 208), (208, 192, 175),
                (228, 210, 195), (238, 225, 205),
            ],
            roof_palette=[
                (196, 80, 54), (182, 70, 48), (210, 96, 68),
                (162, 64, 46), (145, 78, 62), (170, 88, 60),
            ],
            roof_styles=["gable", "hip", "flat"],
            roof_overhang=0.45,
            roof_ridge_scale=1.1,
            ground_floor_darkening=0.82,
            add_chimneys=True,
        ),
        vegetation=VegetationStyle(
            enabled=True,
            density_per_ha=18.0,
            forest_density_per_ha=130.0,
            tree_types=["deciduous", "pine"],
            foliage_palette=[
                (84, 168, 76), (68, 148, 62), (102, 185, 88), (76, 138, 58)
            ],
        ),
        terrain=TerrainStyle(
            low_color=(135, 178, 105),
            mid_color=(162, 188, 122),
            high_color=(175, 172, 145),
            rock_color=(122, 122, 126),
            peak_color=None,
        ),
        roads=RoadStyle(
            asphalt_color=(64, 68, 76),
            sidewalk_color=(172, 168, 158),
            width_scale=1.0,
        ),
        water=WaterStyle(
            color=(60, 135, 178),
        ),
    )


def _make_stylized_rural() -> StylePreset:
    return StylePreset(
        name="stylized_rural",
        label="Stylized Rural",
        description="Rustic timber cottages, barn reds, agricultural pasture greens, and earthen trails.",
        building=BuildingStyle(
            simplification_tol=1.0,
            min_area=14.0,
            height_scale=0.92,
            height_variation=0.40,
            facade_palette=[
                (168, 62, 50),   # Barn Red
                (186, 142, 98),  # Weathered Pine
                (236, 226, 206), # Farmhouse White
                (142, 112, 82),  # Dark Timber
                (210, 195, 172), # Warm Straw
            ],
            roof_palette=[
                (128, 98, 82), (148, 118, 96), (98, 98, 94), (160, 75, 60)
            ],
            roof_styles=["gable"],
            roof_overhang=0.55,
            roof_ridge_scale=1.2,
            add_chimneys=True,
        ),
        vegetation=VegetationStyle(
            enabled=True,
            density_per_ha=32.0,
            forest_density_per_ha=150.0,
            tree_types=["deciduous", "pine", "poplar"],
            foliage_palette=[
                (92, 168, 72), (115, 182, 80), (145, 185, 82), (72, 138, 55)
            ],
        ),
        terrain=TerrainStyle(
            low_color=(142, 184, 102),
            mid_color=(172, 194, 118),
            high_color=(182, 175, 138),
            rock_color=(128, 124, 118),
        ),
        roads=RoadStyle(
            asphalt_color=(85, 88, 92),
            track_color=(148, 132, 108),
            width_scale=0.95,
        ),
        water=WaterStyle(
            color=(55, 142, 172),
        ),
    )


def _make_stylized_forest() -> StylePreset:
    return StylePreset(
        name="stylized_forest",
        label="Stylized Forest",
        description="High-density evergreen canopies, mossy forest floors, dark timber cabins, and shaded water.",
        building=BuildingStyle(
            simplification_tol=1.0,
            min_area=15.0,
            height_scale=0.88,
            height_variation=0.30,
            facade_palette=[
                (115, 88, 66), (138, 108, 82), (155, 125, 95), (96, 75, 58)
            ],
            roof_palette=[
                (88, 72, 60), (108, 88, 72), (76, 82, 74)
            ],
            roof_styles=["gable"],
            roof_overhang=0.6,
            add_chimneys=True,
        ),
        vegetation=VegetationStyle(
            enabled=True,
            density_per_ha=65.0,
            forest_density_per_ha=220.0,
            tree_types=["pine", "deciduous"],
            foliage_palette=[
                (42, 108, 52), (35, 92, 45), (55, 128, 62), (62, 135, 70)
            ],
        ),
        terrain=TerrainStyle(
            low_color=(68, 122, 62),    # Deep forest green
            mid_color=(85, 135, 72),
            high_color=(118, 128, 98),
            rock_color=(108, 110, 112),
        ),
        roads=RoadStyle(
            asphalt_color=(76, 80, 84),
            track_color=(122, 108, 88),
            width_scale=0.9,
        ),
        water=WaterStyle(
            color=(42, 118, 155),
        ),
    )


def _make_stylized_mountains() -> StylePreset:
    return StylePreset(
        name="stylized_mountains",
        label="Stylized Mountains",
        description="High-relief alpine crags, snow-capped peaks, rugged stone lodges, and azure glacial lakes.",
        building=BuildingStyle(
            simplification_tol=0.9,
            min_area=12.0,
            height_scale=0.95,
            height_variation=0.30,
            facade_palette=[
                (142, 138, 132), (160, 155, 148), (175, 168, 160), (128, 125, 120)
            ],
            roof_palette=[
                (85, 88, 95), (105, 108, 115), (135, 95, 75)
            ],
            roof_styles=["gable"],
            roof_overhang=0.5,
            add_chimneys=True,
        ),
        vegetation=VegetationStyle(
            enabled=True,
            density_per_ha=14.0,
            forest_density_per_ha=95.0,
            tree_types=["pine"],
            foliage_palette=[
                (48, 102, 65), (38, 88, 55), (60, 118, 75)
            ],
            max_slope_deg=28.0,
        ),
        terrain=TerrainStyle(
            low_color=(120, 162, 95),     # Valley green
            mid_color=(155, 168, 128),    # Subalpine meadow
            high_color=(145, 145, 150),   # Slate crags
            rock_color=(112, 114, 118),   # Steep rock
            peak_color=(238, 244, 250),   # Alpine snow
            slope_threshold_deg=18.0,
        ),
        roads=RoadStyle(
            asphalt_color=(68, 72, 78),
            width_scale=0.95,
        ),
        water=WaterStyle(
            color=(48, 145, 192),
        ),
    )


def _make_realistic_preset() -> StylePreset:
    """Exact baseline matching original Terragen palette."""
    return StylePreset(
        name="realistic",
        label="Realistic Baseline",
        description="Original realistic Terragen material palette based on photographic OSM and satellite tones.",
        building=BuildingStyle(
            simplification_tol=0.0,
            min_area=8.0,
            height_scale=1.0,
            height_variation=0.5,
            facade_palette=[
                (216, 202, 178), (201, 181, 156), (223, 212, 192), (185, 172, 152),
                (209, 190, 166), (172, 178, 178), (226, 218, 203), (194, 168, 143),
                (207, 197, 181), (166, 158, 148),
            ],
            roof_palette=[
                (178, 88, 68), (166, 76, 60), (150, 92, 70), (132, 84, 68),
                (110, 96, 90), (96, 102, 108), (124, 78, 64), (160, 104, 74),
            ],
            roof_styles=["gable", "flat"],
            roof_overhang=0.45,
            roof_ridge_scale=1.0,
            add_chimneys=True,
        ),
        vegetation=VegetationStyle(
            enabled=False,  # realistic baseline handles trees via area layers
        ),
        terrain=TerrainStyle(
            low_color=(146, 154, 136),
            high_color=(170, 162, 138),
            rock_color=(122, 120, 116),
        ),
        roads=RoadStyle(
            asphalt_color=(72, 76, 84),
            sidewalk_color=(163, 160, 152),
            width_scale=1.0,
        ),
        water=WaterStyle(
            color=(68, 128, 165),
        ),
    )


STYLE_PRESETS: dict[str, StylePreset] = {
    "stylized_european_town": _make_stylized_european_town(),
    "stylized_rural": _make_stylized_rural(),
    "stylized_forest": _make_stylized_forest(),
    "stylized_mountains": _make_stylized_mountains(),
    "realistic": _make_realistic_preset(),
    "default": _make_stylized_european_town(),
}


def get_style_preset(name: str | None = None, overrides: dict[str, Any] | None = None) -> StylePreset:
    """Returns a style preset by key, with optional user overrides applied."""
    if name is not None:
        key = str(name).lower()
        if key not in STYLE_PRESETS:
            raise KeyError(f"Style preset '{name}' not found")
        base = STYLE_PRESETS[key]
    else:
        base = STYLE_PRESETS["stylized_european_town"]
    preset = copy.deepcopy(base)

    if overrides and isinstance(overrides, dict):
        preset.name = overrides.get("name", "custom")
        preset.label = overrides.get("label", "Custom Preset")
        b = overrides.get("building", {})
        if "simplification_tol" in b:
            preset.building.simplification_tol = float(b["simplification_tol"])
        if "height_scale" in b:
            preset.building.height_scale = float(b["height_scale"])
        if "roof_palette" in b and b["roof_palette"]:
            preset.building.roof_palette = [tuple(c) for c in b["roof_palette"]]
        if "facade_palette" in b and b["facade_palette"]:
            preset.building.facade_palette = [tuple(c) for c in b["facade_palette"]]

        v = overrides.get("vegetation", {})
        if "enabled" in v:
            preset.vegetation.enabled = bool(v["enabled"])
        if "density_per_ha" in v:
            preset.vegetation.density_per_ha = float(v["density_per_ha"])

        t = overrides.get("terrain", {})
        if "low_color" in t:
            preset.terrain.low_color = tuple(t["low_color"])
        if "rock_color" in t:
            preset.terrain.rock_color = tuple(t["rock_color"])

    return preset


def list_style_presets() -> list[dict[str, Any]]:
    """Returns a list of all available style presets with summary info."""
    out = []
    seen = set()
    for key, p in STYLE_PRESETS.items():
        if key == "default" or p.name in seen:
            continue
        seen.add(p.name)
        out.append({
            "name": p.name,
            "label": p.label,
            "description": p.description,
            "vegetation_enabled": p.vegetation.enabled,
            "tree_types": p.vegetation.tree_types,
            "roof_styles": p.building.roof_styles,
        })
    return out


# -----------------------------------------------------------------------------
# Procedural Low-Poly Vegetation Mesh Generator
# -----------------------------------------------------------------------------

def build_tree_mesh(
    tree_type: str,
    x: float,
    z: float,
    y: float,
    scale: float = 1.0,
    foliage_color: RGB = (76, 155, 68),
    trunk_color: RGB = (95, 68, 45),
) -> Mesh:
    """Constructs an optimized low-poly stylized tree mesh (20 to 48 triangles).

    Coordinates: (x=East, z=Elevation/Up, y=North)
    """
    mesh = Mesh()
    h = 6.0 * scale
    r = 2.0 * scale

    # Trunk: 5-sided prism
    trunk_h = h * 0.32
    trunk_r = r * 0.16
    n_seg = 5
    angles = np.linspace(0, 2 * math.pi, n_seg, endpoint=False)

    b_pts = np.column_stack([
        x + trunk_r * np.cos(angles),
        np.full(n_seg, z),
        y + trunk_r * np.sin(angles),
    ])
    t_pts = np.column_stack([
        x + trunk_r * 0.8 * np.cos(angles),
        np.full(n_seg, z + trunk_h),
        y + trunk_r * 0.8 * np.sin(angles),
    ])
    trunk_verts = np.vstack([b_pts, t_pts])

    trunk_faces = []
    for i in range(n_seg):
        nxt = (i + 1) % n_seg
        trunk_faces.append([i, nxt, nxt + n_seg])
        trunk_faces.append([i, nxt + n_seg, i + n_seg])

    mesh.add(trunk_verts, np.array(trunk_faces), trunk_color)

    # Foliage
    if tree_type == "pine":
        # 3 stacked cones
        cone_count = 3
        for k in range(cone_count):
            base_z = z + trunk_h * 0.8 + k * (h * 0.22)
            cone_h = h * 0.38
            cone_r = r * (1.0 - k * 0.25)
            c_tip = np.array([x, base_z + cone_h, y])

            ring_pts = np.column_stack([
                x + cone_r * np.cos(angles),
                np.full(n_seg, base_z),
                y + cone_r * np.sin(angles),
            ])
            cone_verts = np.vstack([c_tip[None, :], ring_pts])
            cone_faces = [[0, 1 + i, 1 + (i + 1) % n_seg] for i in range(n_seg)]
            # cone base bottom cap
            c_bottom = np.array([x, base_z, y])
            cone_verts = np.vstack([cone_verts, c_bottom[None, :]])
            b_idx = len(cone_verts) - 1
            for i in range(n_seg):
                cone_faces.append([b_idx, 1 + (i + 1) % n_seg, 1 + i])

            mesh.add(cone_verts, np.array(cone_faces), foliage_color)
    else:
        # Deciduous: Low-poly faceted foliage sphere (dodecahedron / icosahedron style)
        c_center = np.array([x, z + trunk_h + r * 0.9, y])
        phi = (1.0 + math.sqrt(5.0)) / 2.0
        ico_verts = np.array([
            [-1, phi, 0], [1, phi, 0], [-1, -phi, 0], [1, -phi, 0],
            [0, -1, phi], [0, 1, phi], [0, -1, -phi], [0, 1, -phi],
            [phi, 0, -1], [phi, 0, 1], [-phi, 0, -1], [-phi, 0, 1]
        ], dtype=np.float64)
        ico_verts /= np.linalg.norm(ico_verts, axis=1, keepdims=True)
        ico_verts = c_center + ico_verts * r

        ico_faces = np.array([
            [0, 11, 5], [0, 5, 1], [0, 1, 7], [0, 7, 10], [0, 10, 11],
            [1, 5, 9], [5, 11, 4], [11, 10, 2], [10, 7, 6], [7, 1, 8],
            [3, 9, 4], [3, 4, 2], [3, 2, 6], [3, 6, 8], [3, 8, 9],
            [4, 9, 5], [2, 4, 11], [6, 2, 10], [8, 6, 7], [9, 8, 1]
        ], dtype=np.int64)

        mesh.add(ico_verts, ico_faces, foliage_color)

    return mesh


# -----------------------------------------------------------------------------
# Vegetation Distribution Engine
# -----------------------------------------------------------------------------

def generate_vegetation_layer(
    bbox_m: tuple[float, float, float, float],
    dem: Any,
    proj: Any,
    style: VegetationStyle,
    seed: int = 7,
    forest_polygons: list[Polygon] | None = None,
    water_polygons: list[Polygon] | None = None,
    road_polygons: list[Polygon] | None = None,
    building_polygons: list[Polygon] | None = None,
) -> Mesh:
    """Generates procedurally placed stylized vegetation complying with spatial exclusion rules.

    Returns a combined Mesh containing all generated trees for the bounds.
    """
    if not style.enabled:
        return Mesh()

    min_x, min_y, max_x, max_y = bbox_m
    width_m = max_x - min_x
    height_m = max_y - min_y
    area_ha = (width_m * height_m) / 10000.0

    if area_ha <= 0:
        return Mesh()

    # Pre-union exclusion geometries for fast spatial queries
    water_union = unary_union(water_polygons) if water_polygons else None
    roads_union = unary_union(road_polygons) if road_polygons else None
    buildings_union = unary_union(building_polygons) if building_polygons else None
    forest_union = unary_union(forest_polygons) if forest_polygons else None

    # Base grid step from density
    base_density = max(1.0, style.density_per_ha)
    step_m = math.sqrt(10000.0 / base_density)

    cols = max(1, int(width_m // step_m))
    rows = max(1, int(height_m // step_m))

    combined = Mesh()
    foliage_colors = style.foliage_palette or [(74, 158, 66), (88, 172, 78)]
    trunk_colors = style.trunk_palette or [(95, 68, 45)]

    for r in range(rows):
        for c in range(cols):
            # Deterministic hash for this grid cell
            raw = f"veg:{seed}:{r}:{c}"
            hsh = int(hashlib.sha256(raw.encode()).hexdigest()[:8], 16)

            # Jitter within cell
            jx = ((hsh % 1000) / 1000.0 - 0.5) * step_m * 0.8
            jy = (((hsh >> 10) % 1000) / 1000.0 - 0.5) * step_m * 0.8

            x = min_x + (c + 0.5) * step_m + jx
            y = min_y + (r + 0.5) * step_m + jy

            if not (min_x <= x <= max_x and min_y <= y <= max_y):
                continue

            pt = Point(x, y)

            # 1. Exclusion: Water
            if water_union and water_union.contains(pt):
                continue

            # 2. Exclusion: Roads
            if roads_union and roads_union.distance(pt) < style.exclude_roads_buffer_m:
                continue

            # 3. Exclusion: Buildings
            if buildings_union and buildings_union.distance(pt) < style.exclude_buildings_buffer_m:
                continue

            # 4. Density & Region boosting: Forest areas
            in_forest = forest_union and forest_union.contains(pt)
            if not in_forest and (hsh % 100) > 40:
                # Outside forests, lower density
                continue

            # 5. Elevation & Slope restriction
            lon, lat = proj.to_lonlat(x, y)
            elev = float(np.asarray(dem.heights(np.array([lon]), np.array([lat]))).ravel()[0])

            # Sample slope using 2m delta
            dx_lon, _ = proj.to_lonlat(x + 2.0, y)
            _, dy_lat = proj.to_lonlat(x, y + 2.0)
            elev_dx = float(np.asarray(dem.heights(np.array([dx_lon]), np.array([lat]))).ravel()[0])
            elev_dy = float(np.asarray(dem.heights(np.array([lon]), np.array([dy_lat]))).ravel()[0])
            slope_deg = math.degrees(math.atan(math.hypot(elev_dx - elev, elev_dy - elev) / 2.0))

            if slope_deg > style.max_slope_deg:
                continue

            # 6. Tree attributes
            tree_scale = style.scale_min + (((hsh >> 20) % 1000) / 1000.0) * (style.scale_max - style.scale_min)
            t_type = style.tree_types[(hsh >> 4) % len(style.tree_types)]
            f_color = foliage_colors[(hsh >> 8) % len(foliage_colors)]
            tr_color = trunk_colors[(hsh >> 12) % len(trunk_colors)]

            # Build and append tree mesh
            t_mesh = build_tree_mesh(
                tree_type=t_type,
                x=x,
                z=elev,
                y=y,
                scale=tree_scale,
                foliage_color=f_color,
                trunk_color=tr_color,
            )

            if len(t_mesh.v):
                combined.add(t_mesh.v, t_mesh.f, t_mesh.c)

    return combined
