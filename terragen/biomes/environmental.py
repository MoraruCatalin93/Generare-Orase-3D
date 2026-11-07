"""Environmental Analysis Engine for Terragen Game Worlds.

Analyzes topography, hydrology, climate, and human infrastructure:
- Altitude (DEM heights)
- Slope (gradient magnitude in degrees)
- Aspect (compass orientation of terrain slope in degrees)
- Water Proximity (Euclidean distance to water bodies, rivers, coastlines)
- Moisture Estimation (Hydrological proximity + Topographic Wetness / Drainage index)
- Temperature / Climate (Lapse rate model: T = T_base - 0.0065 * Z)
- Urban Infrastructure Mask (OSM buildings, highways, and residential buffers)
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from scipy.ndimage import distance_transform_edt
from shapely.geometry import box
from shapely.prepared import prep

from ..osmdata import Way
from ..project import LocalProjection


class EnvironmentalAnalyzer:
    """Performs vectorized spatial and topographical analysis across 2D metric grids."""

    def __init__(
        self,
        base_temperature_c: float = 18.0,
        base_moisture: float = 0.50,
        lapse_rate_per_1000m: float = 6.5,
    ):
        self.base_temperature_c = float(base_temperature_c)
        self.base_moisture = float(base_moisture)
        self.lapse_rate_per_1000m = float(lapse_rate_per_1000m)

    def analyze(
        self,
        bounds_m: tuple[float, float, float, float],
        resolution_m: float,
        dem: Any,
        proj: LocalProjection,
        ways: list[Way] | None = None,
        climate_override: dict[str, Any] | None = None,
    ) -> dict[str, np.ndarray]:
        """Runs the complete environmental analysis pipeline for the given bounding box.

        Returns a dictionary containing 2D arrays (H, W):
        - elevation: float64
        - slope: float64 (degrees)
        - aspect: float64 (degrees [0, 360))
        - water_distance: float64 (meters)
        - moisture: float64 ([0.0, 1.0])
        - temperature: float64 (Celsius)
        - urban_mask: bool
        """
        min_x, min_y, max_x, max_y = bounds_m
        width_m = max_x - min_x
        height_m = max_y - min_y

        step = max(1.0, float(resolution_m))
        cols = max(2, round(width_m / step) + 1)
        rows = max(2, round(height_m / step) + 1)

        dx = (max_x - min_x) / (cols - 1)
        dy = (max_y - min_y) / (rows - 1)

        # 1. Altitude with 1-cell guard band for seamless central difference boundary gradients
        xs_ext = np.linspace(min_x - dx, max_x + dx, cols + 2, dtype=np.float64)
        ys_ext = np.linspace(min_y - dy, max_y + dy, rows + 2, dtype=np.float64)
        X_ext, Y_ext = np.meshgrid(xs_ext, ys_ext)
        lons_ext, lats_ext = proj.to_lonlat(X_ext, Y_ext)
        elevation_ext = np.asarray(dem.heights(lons_ext, lats_ext), dtype=np.float64)

        # 2. Slope and Aspect from Central Difference Spatial Gradients
        gy_ext, gx_ext = np.gradient(elevation_ext, dy, dx)

        # Slice to interior grid (matching exactly min_x..max_x, min_y..max_y)
        elevation = elevation_ext[1:-1, 1:-1]
        gx = gx_ext[1:-1, 1:-1]
        gy = gy_ext[1:-1, 1:-1]
        xs = xs_ext[1:-1]
        ys = ys_ext[1:-1]

        slope_rad = np.arctan(np.hypot(gx, gy))
        slope_deg = np.degrees(slope_rad)

        # Aspect: Compass direction facing downslope (0=North, 90=East, 180=South, 270=West)
        aspect_rad = np.arctan2(-gx, gy)
        aspect_deg = (np.degrees(aspect_rad) + 360.0) % 360.0

        # 3. Water Proximity Analysis
        water_mask = np.zeros((rows, cols), dtype=bool)
        urban_mask = np.zeros((rows, cols), dtype=bool)

        query_box = box(min_x - 50.0, min_y - 50.0, max_x + 50.0, max_y + 50.0)

        if ways:
            for w in ways:
                tags = getattr(w, "tags", {})
                is_water = (
                    tags.get("natural") in ("water", "wetland", "coastline", "bay")
                    or "water" in tags
                    or "waterway" in tags
                )
                is_urban = (
                    "building" in tags
                    or "building:part" in tags
                    or tags.get("landuse") in ("residential", "commercial", "industrial", "retail")
                    or tags.get("highway") in ("primary", "secondary", "tertiary", "residential", "trunk", "motorway")
                )

                if not (is_water or is_urban):
                    continue

                coords = getattr(w, "coords", [])
                if len(coords) < 2:
                    continue

                # Project way coordinates to local metric XY
                c_arr = np.asarray(coords, dtype=np.float64)
                w_x, w_y = proj.to_xy(c_arr[:, 0], c_arr[:, 1])
                w_pts = np.column_stack([w_x, w_y])

                # Quick bounding box cull
                w_min_x, w_min_y = float(w_pts[:, 0].min()), float(w_pts[:, 1].min())
                w_max_x, w_max_y = float(w_pts[:, 0].max()), float(w_pts[:, 1].max())

                if (
                    w_max_x < min_x
                    or w_min_x > max_x
                    or w_max_y < min_y
                    or w_min_y > max_y
                ):
                    continue

                # Convert to raster coordinates
                c_cols = np.clip(np.round((w_pts[:, 0] - min_x) / dx).astype(int), 0, cols - 1)
                c_rows = np.clip(np.round((w_pts[:, 1] - min_y) / dy).astype(int), 0, rows - 1)

                if is_water:
                    water_mask[c_rows, c_cols] = True
                    # Fill rings if polygon
                    rings = getattr(w, "rings", None)
                    if rings and len(rings) > 0 and len(w_pts) >= 3:
                        from shapely.geometry import Polygon
                        try:
                            poly = Polygon(w_pts)
                            if poly.is_valid and poly.intersects(query_box):
                                min_r = max(0, int(np.floor((w_min_y - min_y) / dy)))
                                max_r = min(rows - 1, int(np.ceil((w_max_y - min_y) / dy)))
                                min_c = max(0, int(np.floor((w_min_x - min_x) / dx)))
                                max_c = min(cols - 1, int(np.ceil((w_max_x - min_x) / dx)))

                                prep_poly = prep(poly)
                                for r_idx in range(min_r, max_r + 1):
                                    for c_idx in range(min_c, max_c + 1):
                                        from shapely.geometry import Point
                                        if prep_poly.contains(Point(xs[c_idx], ys[r_idx])):
                                            water_mask[r_idx, c_idx] = True
                        except Exception:
                            pass

                if is_urban:
                    urban_mask[c_rows, c_cols] = True
                    # If building polygon, mark interior
                    if "building" in tags and len(w_pts) >= 3:
                        min_r = max(0, int(np.floor((w_min_y - min_y) / dy)))
                        max_r = min(rows - 1, int(np.ceil((w_max_y - min_y) / dy)))
                        min_c = max(0, int(np.floor((w_min_x - min_x) / dx)))
                        max_c = min(cols - 1, int(np.ceil((w_max_x - min_x) / dx)))
                        urban_mask[min_r : max_r + 1, min_c : max_c + 1] = True

        # Euclidean Distance Transform for Water
        if np.any(water_mask):
            # distance_transform_edt computes distance to nearest zero cell.
            # We invert the water mask (~water_mask) so non-water cells measure distance to water.
            dist_cells = distance_transform_edt(~water_mask)
            cell_size = math.sqrt(dx * dy)
            water_distance = dist_cells * cell_size
        else:
            # Default: no direct water body nearby (default 1000m buffer)
            water_distance = np.full((rows, cols), 1000.0, dtype=np.float64)

        # 4. Temperature Estimation (Lapse Rate Model)
        base_temp = (
            float(climate_override.get("base_temperature_c", self.base_temperature_c))
            if climate_override
            else self.base_temperature_c
        )
        lapse = self.lapse_rate_per_1000m / 1000.0
        # Reference elevation (sea level = 0.0m) ensures identical temperature everywhere across chunks
        ref_elevation = (
            float(climate_override.get("reference_elevation_m", 0.0))
            if climate_override
            else 0.0
        )
        temperature = base_temp - (elevation - ref_elevation) * lapse

        # 5. Moisture Estimation
        # Combines base moisture, proximity to water, and slope drainage
        base_m = (
            float(climate_override.get("base_moisture", self.base_moisture))
            if climate_override
            else self.base_moisture
        )

        # Proximity to water: decays exponentially with distance
        # Within 50m: +0.45 moisture; 200m: +0.22 moisture; >600m: negligible
        water_moisture_boost = np.exp(-water_distance / 250.0) * 0.50

        # Topographic slope drainage effect: steep slopes drain rapidly, valleys retain water
        drainage_factor = np.exp(-slope_deg / 15.0)

        # Aspect sun exposure effect: South-facing slopes (135° to 225°) are drier in Northern hemisphere
        # North-facing slopes retain more moisture (less evaporation)
        aspect_dryness = np.cos(np.radians(aspect_deg - 180.0))  # 1.0 at South (180°), -1.0 at North (0°)
        aspect_factor = 1.0 - 0.12 * np.clip(aspect_dryness, 0.0, 1.0)

        moisture_raw = (base_m + water_moisture_boost) * (0.65 + 0.35 * drainage_factor) * aspect_factor
        moisture = np.clip(moisture_raw, 0.0, 1.0)

        return {
            "elevation": elevation,
            "slope": slope_deg,
            "aspect": aspect_deg,
            "water_distance": water_distance,
            "moisture": moisture,
            "temperature": temperature,
            "urban_mask": urban_mask,
        }
