"""Schema versioning and safe migrations for Terragen projects."""

from __future__ import annotations

import copy
import math
import uuid
from typing import Any

from terragen import __version__

CURRENT_SCHEMA_VERSION = 1


def migrate_project_dict(raw: dict[str, Any]) -> dict[str, Any]:
    """Migrates a project dictionary from older versions to CURRENT_SCHEMA_VERSION."""
    if not isinstance(raw, dict):
        raw = {}
    data = copy.deepcopy(raw)

    schema_version = data.get("schema_version")
    if schema_version is None:
        schema_version = 0

    if schema_version < 1:
        data = _migrate_v0_to_v1(data)

    data["schema_version"] = CURRENT_SCHEMA_VERSION
    if "app_version" not in data:
        data["app_version"] = __version__
    return data


def _migrate_v0_to_v1(data: dict[str, Any]) -> dict[str, Any]:
    """Upgrades an unversioned / v0 project dictionary to schema version 1."""
    out: dict[str, Any] = {}

    # ID & metadata
    out["id"] = data.get("id") or uuid.uuid4().hex
    out["name"] = str(data.get("name") or "Unnamed Project")
    out["slug"] = str(data.get("slug") or "")
    out["description"] = str(data.get("description") or "")
    out["created_at"] = data.get("created_at") or data.get("timestamp") or ""
    out["updated_at"] = data.get("updated_at") or out["created_at"]
    out["seed"] = int(data.get("seed", 7))

    # Geographic location
    lat = float(data.get("lat", data.get("center_lat", 46.5446)))
    lon = float(data.get("lon", data.get("center_lon", 24.5612)))
    size = float(data.get("size", data.get("size_m", 1500.0)))
    half = size / 2.0
    dlat = half / 111320.0
    dlon = half / (111320.0 * math.cos(math.radians(lat)))

    geo = data.get("geographic")
    if isinstance(geo, dict):
        out["geographic"] = geo
    else:
        out["geographic"] = {
            "center": {"lat": lat, "lon": lon},
            "bounds": {
                "south": lat - dlat,
                "west": lon - dlon,
                "north": lat + dlat,
                "east": lon + dlon,
            },
            "elevation_ref": float(data.get("elevation", 0.0)),
        }

    # Coordinate system
    cs = data.get("coordinate_system")
    if isinstance(cs, dict):
        out["coordinate_system"] = cs
    else:
        out["coordinate_system"] = {
            "projection": "tmerc",
            "datum": "WGS84",
            "units": "meters",
            "world_origin": {"lat": lat, "lon": lon, "altitude": 0.0},
            "proj_string": (
                f"+proj=tmerc +lat_0={lat} +lon_0={lon} +k=1 +x_0=0 +y_0=0 "
                f"+ellps=WGS84 +units=m +no_defs"
            ),
            "axis_convention": {
                "engine": "X=East, Y=Up, Z=North",
                "glb": "X=East, Y=Up, Z=-North",
                "obj": "X=East, Y=Up, Z=North",
                "blender_target": "X=East, Y=North, Z=Up",
            },
        }

    # Dimensions
    dims = data.get("dimensions")
    if isinstance(dims, dict):
        out["dimensions"] = dims
    else:
        out["dimensions"] = {
            "size_x_m": size,
            "size_y_m": size,
            "height_range_m": {"min": 0.0, "max": 2000.0},
        }

    # Terrain
    terrain = data.get("terrain")
    if isinstance(terrain, dict):
        out["terrain"] = terrain
    else:
        res = int(data.get("terrain_res", 256))
        out["terrain"] = {
            "resolution": res,
            "dem_zoom": int(data.get("dem_zoom", 15)),
            "step_m": size / max(1, res),
            "exaggeration": 1.0,
            "smooth_window": 5,
        }

    # Sources
    sources = data.get("sources")
    if isinstance(sources, dict):
        out["sources"] = sources
    else:
        out["sources"] = {
            "osm": {
                "mode": "auto" if not data.get("overpass") else "overpass",
                "pbf_path": data.get("pbf"),
                "pbf_name": None,
                "pbf_size_bytes": None,
                "pbf_mtime": None,
                "overpass_endpoint": None,
            },
            "dem": {
                "provider": "AWS Terrarium",
                "url_template": (
                    "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
                ),
                "zoom": int(data.get("dem_zoom", 15)),
                "cache_dir": None,
            },
        }

    # Regions & Chunks
    out["regions"] = data.get("regions", []) if isinstance(data.get("regions"), list) else []
    chunks = data.get("chunks")
    if isinstance(chunks, dict):
        out["chunks"] = chunks
    else:
        out["chunks"] = {
            "grid": {"enabled": False, "chunk_size_m": 500.0, "rows": 1, "cols": 1},
            "items": [],
        }

    # Generation & Exports
    gen = data.get("generation")
    if isinstance(gen, dict):
        out["generation"] = gen
    else:
        formats = data.get("formats", ["glb"])
        if data.get("obj") and "obj" not in formats:
            formats.append("obj")
        out["generation"] = {
            "roofs": bool(data.get("roofs", True)),
            "formats": formats,
            "building_palette_seed": int(data.get("seed", 7)),
            "road_width_scale": 1.0,
            "detail_level": "medium",
        }

    out["export_history"] = (
        data.get("export_history", []) if isinstance(data.get("export_history"), list) else []
    )
    out["assets"] = (
        data.get("assets", {})
        if isinstance(data.get("assets"), dict)
        else {
            "build_dir": data.get("out") or data.get("site_dir"),
            "manifest_path": None,
            "last_generated_at": None,
            "last_build_stats": None,
        }
    )

    # Preserve any unknown or custom fields
    known_keys = {
        "schema_version",
        "app_version",
        "id",
        "name",
        "slug",
        "description",
        "created_at",
        "updated_at",
        "seed",
        "geographic",
        "coordinate_system",
        "dimensions",
        "terrain",
        "sources",
        "regions",
        "chunks",
        "generation",
        "export_history",
        "assets",
        "extra_metadata",
    }
    extra = {k: v for k, v in data.items() if k not in known_keys}
    if extra:
        out["extra_metadata"] = extra

    return out

