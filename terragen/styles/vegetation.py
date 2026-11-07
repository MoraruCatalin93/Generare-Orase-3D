"""Procedural Vegetation Meshes & Spatial Distribution Engine."""

from __future__ import annotations

import hashlib
import math
from typing import Any

import numpy as np
from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

from ..geom import Mesh
from .models import RGB, VegetationStyle


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
    elevation_scale: float = 1.0,
    elevation_offset_m: float = 0.0,
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
            raw_elev = float(np.asarray(dem.heights(np.array([lon]), np.array([lat]))).ravel()[0])
            elev = raw_elev * elevation_scale + elevation_offset_m

            # Sample slope using 2m delta
            dx_lon, _ = proj.to_lonlat(x + 2.0, y)
            _, dy_lat = proj.to_lonlat(x, y + 2.0)
            elev_dx = float(np.asarray(dem.heights(np.array([dx_lon]), np.array([lat]))).ravel()[0]) * elevation_scale + elevation_offset_m
            elev_dy = float(np.asarray(dem.heights(np.array([lon]), np.array([dy_lat]))).ravel()[0]) * elevation_scale + elevation_offset_m
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
