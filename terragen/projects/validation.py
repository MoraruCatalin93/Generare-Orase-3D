"""Validation and recovery mechanisms for Terragen project files."""

from __future__ import annotations

import copy
import math
import uuid
from typing import Any


class ProjectValidationError(ValueError):
    """Raised when project data fails structural or geographic validation."""


class CorruptedProjectError(IOError):
    """Raised when project file is corrupted and cannot be repaired."""


def validate_project_dict(data: dict[str, Any]) -> list[str]:
    """Validates project dictionary; returns list of validation errors (empty if valid)."""
    errors: list[str] = []

    if not isinstance(data, dict):
        return ["Project data must be a JSON object / dictionary"]

    # Basic metadata
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        errors.append("Project 'name' must be a non-empty string")

    # Geographic center
    geo = data.get("geographic")
    if not isinstance(geo, dict):
        errors.append("Project 'geographic' configuration is missing or not a dictionary")
    else:
        center = geo.get("center")
        if not isinstance(center, dict):
            errors.append("Project 'geographic.center' is missing or not a dictionary")
        else:
            lat = center.get("lat")
            lon = center.get("lon")
            if not isinstance(lat, (int, float)) or not (-85.0 <= lat <= 85.0):
                errors.append(f"Geographic latitude {lat} is out of valid bounds [-85, 85]")
            if not isinstance(lon, (int, float)) or not (-180.0 <= lon <= 180.0):
                errors.append(f"Geographic longitude {lon} is out of valid bounds [-180, 180]")

        bounds = geo.get("bounds")
        if isinstance(bounds, dict):
            s = bounds.get("south")
            n = bounds.get("north")
            w = bounds.get("west")
            e = bounds.get("east")
            if any(v is None or not isinstance(v, (int, float)) for v in (s, n, w, e)):
                errors.append("Geographic bounds contains missing or non-numeric values")
            elif s > n:
                errors.append(f"Geographic bounds south ({s}) cannot be greater than north ({n})")

    # Dimensions
    dims = data.get("dimensions")
    if isinstance(dims, dict):
        size_x = dims.get("size_x_m")
        size_y = dims.get("size_y_m")
        if size_x is not None and (not isinstance(size_x, (int, float)) or size_x <= 0):
            errors.append(f"Invalid size_x_m: {size_x} (must be positive number)")
        if size_y is not None and (not isinstance(size_y, (int, float)) or size_y <= 0):
            errors.append(f"Invalid size_y_m: {size_y} (must be positive number)")

    # Terrain
    terrain = data.get("terrain")
    if isinstance(terrain, dict):
        res = terrain.get("resolution")
        if res is not None and (not isinstance(res, int) or res < 16 or res > 4096):
            errors.append(f"Invalid terrain resolution: {res} (must be between 16 and 4096)")

    # Regions
    regions = data.get("regions")
    if regions is not None:
        if not isinstance(regions, list):
            errors.append("Project 'regions' must be a list")
        else:
            for idx, r in enumerate(regions):
                if not isinstance(r, dict):
                    errors.append(f"Region at index {idx} must be a dictionary")
                elif "polygon" in r and not isinstance(r["polygon"], list):
                    errors.append(f"Region '{r.get('name', idx)}' polygon must be a coordinate list")

    return errors


def repair_project_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Repairs incomplete or corrupted project dictionary with safe defaults."""
    if not isinstance(data, dict):
        data = {}
    d = copy.deepcopy(data)

    # 1. ID & basic metadata
    if not d.get("id") or not isinstance(d["id"], str):
        d["id"] = uuid.uuid4().hex
    if not d.get("name") or not isinstance(d["name"], str) or not d["name"].strip():
        d["name"] = "Recovered Project"

    # 2. Geographic & Center
    geo = d.get("geographic")
    if not isinstance(geo, dict):
        geo = {}
        d["geographic"] = geo

    center = geo.get("center")
    if not isinstance(center, dict):
        center = {}
        geo["center"] = center

    lat = center.get("lat")
    if not isinstance(lat, (int, float)) or math.isnan(lat) or not (-85.0 <= lat <= 85.0):
        lat = 46.5446
    center["lat"] = float(lat)

    lon = center.get("lon")
    if not isinstance(lon, (int, float)) or math.isnan(lon) or not (-180.0 <= lon <= 180.0):
        lon = 24.5612
    center["lon"] = float(lon)

    # 3. Dimensions
    dims = d.get("dimensions")
    if not isinstance(dims, dict):
        dims = {}
        d["dimensions"] = dims
    size_x = dims.get("size_x_m")
    if not isinstance(size_x, (int, float)) or size_x <= 0 or math.isnan(size_x):
        size_x = 1500.0
    dims["size_x_m"] = float(size_x)

    size_y = dims.get("size_y_m")
    if not isinstance(size_y, (int, float)) or size_y <= 0 or math.isnan(size_y):
        size_y = float(size_x)
    dims["size_y_m"] = float(size_y)

    # 4. Bounds recovery if missing or inverted
    bounds = geo.get("bounds")
    half_x = float(size_x) / 2.0
    half_y = float(size_y) / 2.0
    dlat = half_y / 111320.0
    dlon = half_x / (111320.0 * max(0.01, math.cos(math.radians(float(lat)))))
    calc_bounds = {
        "south": float(lat) - dlat,
        "west": float(lon) - dlon,
        "north": float(lat) + dlat,
        "east": float(lon) + dlon,
    }

    if not isinstance(bounds, dict):
        geo["bounds"] = calc_bounds
    else:
        for k in ("south", "west", "north", "east"):
            val = bounds.get(k)
            if not isinstance(val, (int, float)) or math.isnan(val):
                bounds[k] = calc_bounds[k]
        if bounds["south"] > bounds["north"]:
            bounds["south"], bounds["north"] = bounds["north"], bounds["south"]
        if bounds["west"] > bounds["east"]:
            bounds["west"], bounds["east"] = bounds["east"], bounds["west"]

    # 5. Coordinate system
    cs = d.get("coordinate_system")
    if not isinstance(cs, dict):
        cs = {}
        d["coordinate_system"] = cs
    if not cs.get("proj_string"):
        cs["proj_string"] = (
            f"+proj=tmerc +lat_0={lat} +lon_0={lon} +k=1 +x_0=0 +y_0=0 +ellps=WGS84 +units=m +no_defs"
        )
    if not cs.get("world_origin"):
        cs["world_origin"] = {"lat": float(lat), "lon": float(lon), "altitude": 0.0}

    # 6. Terrain
    terrain = d.get("terrain")
    if not isinstance(terrain, dict):
        terrain = {}
        d["terrain"] = terrain
    if not isinstance(terrain.get("resolution"), int) or terrain["resolution"] < 16:
        terrain["resolution"] = 256
    if not isinstance(terrain.get("dem_zoom"), int) or terrain["dem_zoom"] < 5:
        terrain["dem_zoom"] = 15

    # 7. Seed
    seed = d.get("seed")
    if not isinstance(seed, int) or isinstance(seed, bool):
        d["seed"] = 7

    # 8. Lists & collections
    if not isinstance(d.get("regions"), list):
        d["regions"] = []
    if not isinstance(d.get("chunks"), dict):
        d["chunks"] = {
            "grid": {"enabled": False, "chunk_size_m": 512.0, "rows": 1, "cols": 1},
            "items": [],
        }
    if not isinstance(d.get("generation"), dict):
        d["generation"] = {"roofs": True, "formats": ["glb"], "building_palette_seed": 7}
    if not isinstance(d.get("export_history"), list):
        d["export_history"] = []
    if not isinstance(d.get("assets"), dict):
        d["assets"] = {}

    return d

