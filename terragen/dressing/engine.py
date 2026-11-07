"""World Dressing Engine: Procedural Object Instancing & Biome Population."""

from __future__ import annotations

import hashlib
import math
from typing import Any

import numpy as np
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union

from ..biomes.generator import BiomeGenerator
from ..biomes.types import BiomeGridResult
from ..osmdata import Way
from ..project import LocalProjection
from .catalog import AssetCatalog
from .models import (
    AssetCategory,
    InstanceTransform,
    PlacementRule,
)
from .rules import create_default_placement_rules
from .spatial_index import SpatialHashGrid


class WorldDressingEngine:
    """Engine responsible for procedurally dressing game worlds with instanced 3D objects."""

    def __init__(
        self,
        seed: int = 42,
        catalog: AssetCatalog | None = None,
        rules: list[PlacementRule] | None = None,
        density_multiplier: float = 1.0,
    ):
        self.seed = int(seed)
        self.catalog = catalog or AssetCatalog()
        self.rules = list(rules or create_default_placement_rules())
        self.density_multiplier = max(0.0, float(density_multiplier))

    def _deterministic_hash(self, *args: Any) -> int:
        """Returns a 32-bit deterministic integer hash for arbitrary coordinates and identifiers."""
        raw = f"{self.seed}:" + ":".join(str(a) for a in args)
        return int(hashlib.sha256(raw.encode()).hexdigest()[:8], 16)

    def populate_bounds(
        self,
        bounds_m: tuple[float, float, float, float],
        dem: Any,
        proj: LocalProjection,
        biome_grid: BiomeGridResult | None = None,
        ways: list[Way] | None = None,
        protected_polygons: list[Polygon] | None = None,
    ) -> list[InstanceTransform]:
        """Populates the specified bounding box with instanced objects conforming to all rules.

        Returns an ordered list of InstanceTransform objects.
        """
        if self.density_multiplier <= 0.0:
            return []

        min_x, min_y, max_x, max_y = bounds_m
        width_m = max_x - min_x
        height_m = max_y - min_y
        area_ha = (width_m * height_m) / 10000.0

        if area_ha <= 0.0:
            return []

        # 1. Resolve or compute BiomeGridResult
        if biome_grid is None:
            gen = BiomeGenerator(seed=self.seed)
            biome_grid = gen.evaluate_grid(
                bounds_m=bounds_m,
                resolution_m=max(8.0, min(width_m, height_m) / 64.0),
                dem=dem,
                proj=proj,
                ways=ways,
            )

        # 2. Extract Infrastructure and Exclusion Geometries from OSM
        road_lines: list[LineString] = []
        road_polys: list[Polygon] = []
        building_polys: list[Polygon] = []
        water_polys: list[Polygon] = []

        q_box = box(min_x - 30.0, min_y - 30.0, max_x + 30.0, max_y + 30.0)

        if ways:
            for w in ways:
                tags = getattr(w, "tags", {})
                coords = getattr(w, "coords", [])
                if len(coords) < 2:
                    continue

                c_arr = np.asarray(coords, dtype=np.float64)
                w_x, w_y = proj.to_xy(c_arr[:, 0], c_arr[:, 1])
                line = LineString(np.column_stack([w_x, w_y]))

                if not line.intersects(q_box):
                    continue

                is_hw = "highway" in tags and tags["highway"] not in ("proposed", "construction")
                is_bld = "building" in tags or "building:part" in tags
                is_water = tags.get("natural") in ("water", "wetland", "coastline") or "water" in tags or "waterway" in tags

                if is_hw:
                    road_lines.append(line)
                    # 4m road width buffer
                    road_polys.append(line.buffer(3.5, cap_style="flat"))

                if is_bld and len(coords) >= 3:
                    poly = Polygon(np.column_stack([w_x, w_y]))
                    if poly.is_valid and not poly.is_empty:
                        building_polys.append(poly)

                if is_water and len(coords) >= 3:
                    poly = Polygon(np.column_stack([w_x, w_y]))
                    if poly.is_valid and not poly.is_empty:
                        water_polys.append(poly)

        # Union spatial exclusion zones for fast distance evaluation
        roads_union = unary_union(road_polys) if road_polys else None
        buildings_union = unary_union(building_polys) if building_polys else None
        water_union = unary_union(water_polys) if water_polys else None
        protected_union = unary_union(protected_polygons) if protected_polygons else None

        # 3. Spatial Hash Grid for Overlap & Collision Avoidance
        spatial_grid = SpatialHashGrid(cell_size_m=4.0)

        # Seed spatial grid with existing buildings so trees/props never spawn inside them
        if buildings_union:
            for bp in building_polys:
                spatial_grid.insert(bp.centroid.x, bp.centroid.y, math.sqrt(bp.area / math.pi))

        instances: list[InstanceTransform] = []
        placed_count = 0

        # Helper to sample ground elevation and slope normal
        def get_ground_sample(x_c: float, z_c: float) -> tuple[float, float, float, float, float]:
            """Returns (y_elev, slope_deg, norm_x, norm_y, norm_z)."""
            lon, lat = proj.to_lonlat(x_c, z_c)
            elev = float(np.asarray(dem.heights(np.array([lon]), np.array([lat]))).ravel()[0])

            # Sample 2m gradient for normal
            d = 2.0
            l_dx, _ = proj.to_lonlat(x_c + d, z_c)
            _, l_dy = proj.to_lonlat(x_c, z_c + d)
            e_dx = float(np.asarray(dem.heights(np.array([l_dx]), np.array([lat]))).ravel()[0])
            e_dz = float(np.asarray(dem.heights(np.array([lon]), np.array([l_dy]))).ravel()[0])

            gx = (e_dx - elev) / d
            gz = (e_dz - elev) / d
            slope = math.degrees(math.atan(math.hypot(gx, gz)))

            # Analytical upward normal
            nx = -gx
            ny = 1.0
            nz = -gz
            n_len = math.sqrt(nx**2 + ny**2 + nz**2)
            return elev, slope, nx / n_len, ny / n_len, nz / n_len

        # ---------------------------------------------------------------------
        # Phase A: Infrastructure Linear Placement (Street Lamps & Benches)
        # ---------------------------------------------------------------------
        for rule in self.rules:
            if rule.category != AssetCategory.INFRASTRUCTURE:
                continue

            proto = self.catalog.get(rule.prototype_id)
            if not proto:
                continue

            # Linear roadside sampling along highways
            for line in road_lines:
                length = line.length
                if length < 10.0:
                    continue

                step_spacing = 28.0  # e.g. lamp post every 28 meters
                num_steps = int(length // step_spacing)
                if num_steps <= 0:
                    continue

                for s_i in range(num_steps):
                    d_along = (s_i + 0.5) * step_spacing
                    pt = line.interpolate(d_along)
                    x_pt, z_pt = pt.x, pt.y

                    if not (min_x <= x_pt <= max_x and min_y <= z_pt <= max_y):
                        continue

                    # Tangent and perpendicular normal vector for sidewalk offset
                    pt_ahead = line.interpolate(min(length, d_along + 1.0))
                    tangent_x = pt_ahead.x - x_pt
                    tangent_z = pt_ahead.y - z_pt
                    t_len = math.hypot(tangent_x, tangent_z)
                    if t_len == 0:
                        continue
                    perp_x = -tangent_z / t_len
                    perp_z = tangent_x / t_len

                    # Check both sides of the road (Left / Right)
                    for side in (-1.0, 1.0):
                        offset_dist = 2.0  # 2m from centerline
                        cand_x = x_pt + perp_x * offset_dist * side
                        cand_z = z_pt + perp_z * offset_dist * side

                        cand_pt = Point(cand_x, cand_z)

                        # Biome check
                        biome_s = biome_grid.sample_at_xy(cand_x, cand_z)
                        if not rule.is_biome_allowed(biome_s.dominant_biome):
                            continue

                        # Building check
                        if buildings_union and buildings_union.distance(cand_pt) < 1.0:
                            continue

                        # Collision check
                        if not spatial_grid.can_place(cand_x, cand_z, rule.collision_radius_m):
                            continue

                        elev, slope, _, _, _ = get_ground_sample(cand_x, cand_z)
                        if slope > rule.max_slope_deg:
                            continue

                        yaw_deg = math.degrees(math.atan2(-perp_z * side, -perp_x * side))

                        spatial_grid.insert(cand_x, cand_z, rule.collision_radius_m)
                        placed_count += 1
                        instances.append(InstanceTransform(
                            prototype_id=rule.prototype_id,
                            category=rule.category,
                            subcategory=rule.subcategory,
                            x=cand_x,
                            y=elev,
                            z=cand_z,
                            rot_y_deg=yaw_deg,
                            scale_x=1.0,
                            scale_y=1.0,
                            scale_z=1.0,
                            biome=biome_s.dominant_biome.value,
                            id=f"inst_{placed_count}",
                        ))

        # ---------------------------------------------------------------------
        # Phase B: Nature & Props Surface Scattering (Grid + Jitter + Clustering)
        # ---------------------------------------------------------------------
        for rule in self.rules:
            if rule.category == AssetCategory.INFRASTRUCTURE:
                continue

            proto = self.catalog.get(rule.prototype_id)
            if not proto:
                continue

            eff_density = rule.density_per_ha * self.density_multiplier
            if eff_density <= 0:
                continue

            # Grid step from target density
            cell_step_m = math.sqrt(10000.0 / eff_density)
            cols = max(1, int(width_m // cell_step_m))
            rows = max(1, int(height_m // cell_step_m))

            for r in range(rows):
                for c in range(cols):
                    # Deterministic hash for this grid candidate
                    hsh = self._deterministic_hash(rule.prototype_id, r, c)

                    # Jitter position within cell
                    jx = ((hsh % 1000) / 1000.0 - 0.5) * cell_step_m * 0.8
                    jy = (((hsh >> 10) % 1000) / 1000.0 - 0.5) * cell_step_m * 0.8

                    px = min_x + (c + 0.5) * cell_step_m + jx
                    pz = min_y + (r + 0.5) * cell_step_m + jy

                    if not (min_x <= px <= max_x and min_y <= pz <= max_y):
                        continue

                    pt_cand = Point(px, pz)

                    # 1. Protected areas
                    if protected_union and protected_union.contains(pt_cand):
                        continue

                    # 2. Road clearance buffer
                    if roads_union:
                        d_road = roads_union.distance(pt_cand)
                        min_rb, max_rb = rule.road_buffer_m
                        if d_road < min_rb or (max_rb is not None and d_road > max_rb):
                            continue

                    # 3. Building clearance buffer
                    if buildings_union:
                        d_bld = buildings_union.distance(pt_cand)
                        min_bb, max_bb = rule.building_buffer_m
                        if d_bld < min_bb or (max_bb is not None and d_bld > max_bb):
                            continue

                    # 4. Water clearance buffer
                    if water_union:
                        d_water = water_union.distance(pt_cand)
                        min_wb, max_wb = rule.water_buffer_m
                        if d_water < min_wb or (max_wb is not None and d_water > max_wb):
                            continue

                    # 5. Biome suitability check
                    biome_s = biome_grid.sample_at_xy(px, pz)
                    b_weight = rule.get_biome_weight(biome_s.dominant_biome)
                    if b_weight <= 0.0:
                        continue

                    # Stochastic test against biome affinity
                    if ((hsh >> 4) % 1000) / 1000.0 > b_weight:
                        continue

                    # 6. Slope & Elevation check
                    elev, slope, nx, _ny, nz = get_ground_sample(px, pz)
                    if slope < rule.min_slope_deg or slope > rule.max_slope_deg:
                        continue

                    if rule.elevation_range != (None, None):
                        e_min, e_max = rule.elevation_range
                        if (e_min is not None and elev < e_min) or (e_max is not None and elev > e_max):
                            continue

                    # 7. Collision check
                    if not spatial_grid.can_place(px, pz, rule.collision_radius_m):
                        continue

                    # Compute transform
                    sc_min, sc_max = rule.scale_range
                    rand_s = sc_min + (((hsh >> 16) % 1000) / 1000.0) * (sc_max - sc_min)
                    yaw = ((hsh >> 8) % 3600) / 10.0 if rule.random_yaw else 0.0

                    rot_x, rot_z = 0.0, 0.0
                    if rule.align_to_normal:
                        rot_x = math.degrees(math.asin(max(-1.0, min(1.0, -nz))))
                        rot_z = math.degrees(math.asin(max(-1.0, min(1.0, nx))))

                    spatial_grid.insert(px, pz, rule.collision_radius_m * rand_s)
                    placed_count += 1
                    parent_inst = InstanceTransform(
                        prototype_id=rule.prototype_id,
                        category=rule.category,
                        subcategory=rule.subcategory,
                        x=px,
                        y=elev,
                        z=pz,
                        rot_x_deg=rot_x,
                        rot_y_deg=yaw,
                        rot_z_deg=rot_z,
                        scale_x=rand_s,
                        scale_y=rand_s,
                        scale_z=rand_s,
                        biome=biome_s.dominant_biome.value,
                        id=f"inst_{placed_count}",
                    )
                    instances.append(parent_inst)

                    # ---------------------------------------------------------
                    # Natural Clustering (Clumps / Groves of child instances)
                    # ---------------------------------------------------------
                    if rule.cluster_probability > 0:
                        is_clustered = (((hsh >> 22) % 1000) / 1000.0) < rule.cluster_probability
                        if is_clustered:
                            c_min, c_max = rule.cluster_count_range
                            c_count = c_min + ((hsh >> 18) % (c_max - c_min + 1))
                            for c_idx in range(c_count):
                                cl_hsh = self._deterministic_hash(rule.prototype_id, r, c, "child", c_idx)
                                cl_ang = ((cl_hsh % 3600) / 3600.0) * 2 * math.pi
                                cl_dist = (0.2 + 0.8 * (((cl_hsh >> 6) % 1000) / 1000.0)) * rule.cluster_radius_m

                                ch_x = px + math.cos(cl_ang) * cl_dist
                                ch_z = pz + math.sin(cl_ang) * cl_dist

                                if not (min_x <= ch_x <= max_x and min_y <= ch_z <= max_y):
                                    continue

                                ch_pt = Point(ch_x, ch_z)
                                if roads_union and roads_union.distance(ch_pt) < rule.road_buffer_m[0]:
                                    continue
                                if buildings_union and buildings_union.distance(ch_pt) < rule.building_buffer_m[0]:
                                    continue
                                if water_union and water_union.distance(ch_pt) < rule.water_buffer_m[0]:
                                    continue

                                ch_elev, ch_slope, _ch_nx, _ch_ny, _ch_nz = get_ground_sample(ch_x, ch_z)
                                if ch_slope > rule.max_slope_deg:
                                    continue

                                # Child instances are slightly smaller
                                ch_scale = rand_s * (0.65 + 0.35 * (((cl_hsh >> 12) % 1000) / 1000.0))
                                ch_radius = rule.collision_radius_m * ch_scale

                                if not spatial_grid.can_place(ch_x, ch_z, ch_radius):
                                    continue

                                spatial_grid.insert(ch_x, ch_z, ch_radius)
                                placed_count += 1
                                ch_yaw = ((cl_hsh >> 4) % 3600) / 10.0
                                instances.append(InstanceTransform(
                                    prototype_id=rule.prototype_id,
                                    category=rule.category,
                                    subcategory=rule.subcategory,
                                    x=ch_x,
                                    y=ch_elev,
                                    z=ch_z,
                                    rot_y_deg=ch_yaw,
                                    scale_x=ch_scale,
                                    scale_y=ch_scale,
                                    scale_z=ch_scale,
                                    biome=biome_s.dominant_biome.value,
                                    id=f"inst_{placed_count}",
                                ))

        return instances
