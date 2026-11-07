"""Visual Style Engine Validation Module.

Validates style preset schemas, ensuring types, ranges, color RGB bounds,
and required identifiers adhere strictly to the specification.
"""

from __future__ import annotations

from typing import Any


class StyleValidationError(ValueError):
    """Raised when a style preset schema or parameter value is invalid."""
    pass


def _validate_rgb(val: Any, field_name: str) -> tuple[int, int, int]:
    if not isinstance(val, (list, tuple)) or len(val) != 3:
        raise StyleValidationError(f"Field '{field_name}' must be an RGB 3-tuple, got {val}")
    for idx, channel in enumerate(val):
        if not isinstance(channel, (int, float)) or not (0 <= int(channel) <= 255):
            raise StyleValidationError(f"Channel {idx} of '{field_name}' must be in range [0, 255], got {channel}")
    return int(val[0]), int(val[1]), int(val[2])


def _validate_palette(val: Any, field_name: str) -> list[tuple[int, int, int]]:
    if not isinstance(val, (list, tuple)):
        raise StyleValidationError(f"Palette '{field_name}' must be a list of RGB colors, got {type(val)}")
    out = []
    for idx, c in enumerate(val):
        out.append(_validate_rgb(c, f"{field_name}[{idx}]"))
    return out


def validate_style_preset_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Validates raw dictionary representation of a style preset against schema rules."""
    if not isinstance(data, dict):
        raise TypeError(f"Preset data must be a dictionary, got {type(data)}")

    name = str(data.get("name", "")).strip()
    if not name:
        raise ValueError("Style preset missing required non-empty 'name'")

    validated: dict[str, Any] = {
        "name": name,
        "label": str(data.get("label", name.replace("_", " ").title())),
        "description": str(data.get("description", "")),
        "version": str(data.get("version", "1.0.0")),
    }

    # 1. Building validation
    b = data.get("building", {})
    if not isinstance(b, dict):
        raise TypeError("'building' section must be a dictionary")
    validated["building"] = {
        "simplification_tol": max(0.0, float(b.get("simplification_tol", 0.8))),
        "min_area": max(1.0, float(b.get("min_area", 12.0))),
        "height_scale": max(0.2, min(5.0, float(b.get("height_scale", 1.0)))),
        "height_variation": max(0.0, min(1.0, float(b.get("height_variation", 0.35)))),
        "roof_overhang": max(0.0, float(b.get("roof_overhang", 0.45))),
        "roof_ridge_scale": max(0.2, float(b.get("roof_ridge_scale", 1.0))),
        "ground_floor_darkening": max(0.1, min(1.0, float(b.get("ground_floor_darkening", 0.85)))),
        "add_chimneys": bool(b.get("add_chimneys", True)),
        "roof_styles": list(b.get("roof_styles", ["gable", "flat", "hip"])),
        "facade_palette": _validate_palette(b.get("facade_palette", []), "building.facade_palette"),
        "roof_palette": _validate_palette(b.get("roof_palette", []), "building.roof_palette"),
        "roughness": max(0.0, min(1.0, float(b.get("roughness", 0.80)))),
        "metallic": max(0.0, min(1.0, float(b.get("metallic", 0.02)))),
    }

    # 2. Vegetation validation
    v = data.get("vegetation", {})
    if not isinstance(v, dict):
        raise TypeError("'vegetation' section must be a dictionary")
    if "density_per_ha" in v and float(v["density_per_ha"]) < 0:
        raise StyleValidationError(f"density_per_ha must be non-negative, got {v['density_per_ha']}")
    validated["vegetation"] = {
        "enabled": bool(v.get("enabled", True)),
        "density_per_ha": max(0.0, float(v.get("density_per_ha", 15.0))),
        "forest_density_per_ha": max(0.0, float(v.get("forest_density_per_ha", 140.0))),
        "tree_types": list(v.get("tree_types", ["pine", "deciduous"])),
        "foliage_palette": _validate_palette(v.get("foliage_palette", []), "vegetation.foliage_palette"),
        "trunk_palette": _validate_palette(v.get("trunk_palette", [(102, 72, 48)]), "vegetation.trunk_palette"),
        "scale_min": max(0.2, float(v.get("scale_min", 0.75))),
        "scale_max": max(0.2, float(v.get("scale_max", 1.35))),
        "max_slope_deg": max(0.0, min(90.0, float(v.get("max_slope_deg", 34.0)))),
        "exclude_roads_buffer_m": max(0.0, float(v.get("exclude_roads_buffer_m", 3.0))),
        "exclude_buildings_buffer_m": max(0.0, float(v.get("exclude_buildings_buffer_m", 2.0))),
    }

    # 3. Terrain validation
    t = data.get("terrain", {})
    if not isinstance(t, dict):
        raise TypeError("'terrain' section must be a dictionary")
    if "slope_threshold_deg" in t:
        st = float(t["slope_threshold_deg"])
        if not (1.0 <= st <= 89.0):
            raise StyleValidationError(f"slope_threshold_deg must be between 1.0 and 89.0, got {st}")
    validated["terrain"] = {
        "elevation_scale": max(0.1, min(10.0, float(t.get("elevation_scale", 1.0)))),
        "elevation_offset_m": float(t.get("elevation_offset_m", 0.0)),
        "low_color": _validate_rgb(t.get("low_color", (135, 175, 105)), "terrain.low_color"),
        "mid_color": _validate_rgb(t.get("mid_color", (160, 185, 120)), "terrain.mid_color"),
        "high_color": _validate_rgb(t.get("high_color", (175, 170, 145)), "terrain.high_color"),
        "rock_color": _validate_rgb(t.get("rock_color", (120, 122, 128)), "terrain.rock_color"),
        "peak_color": _validate_rgb(t["peak_color"], "terrain.peak_color") if t.get("peak_color") else None,
        "slope_threshold_deg": max(1.0, min(89.0, float(t.get("slope_threshold_deg", 20.0)))),
        "slope_blend_range_deg": max(1.0, float(t.get("slope_blend_range_deg", 16.0))),
        "roughness": max(0.0, min(1.0, float(t.get("roughness", 0.85)))),
        "metallic": max(0.0, min(1.0, float(t.get("metallic", 0.0)))),
    }

    # 4. Roads validation
    r = data.get("roads", {})
    if not isinstance(r, dict):
        raise TypeError("'roads' section must be a dictionary")
    validated["roads"] = {
        "asphalt_color": _validate_rgb(r.get("asphalt_color", (68, 72, 80)), "roads.asphalt_color"),
        "sidewalk_color": _validate_rgb(r.get("sidewalk_color", (168, 164, 155)), "roads.sidewalk_color"),
        "track_color": _validate_rgb(r.get("track_color", (145, 130, 110)), "roads.track_color"),
        "width_scale": max(0.2, min(5.0, float(r.get("width_scale", 1.0)))),
        "roughness": max(0.0, min(1.0, float(r.get("roughness", 0.70)))),
        "metallic": max(0.0, min(1.0, float(r.get("metallic", 0.04)))),
    }

    # 5. Water validation
    w = data.get("water", {})
    if not isinstance(w, dict):
        raise TypeError("'water' section must be a dictionary")
    validated["water"] = {
        "color": _validate_rgb(w.get("color", (62, 138, 182)), "water.color"),
        "opacity": max(0.0, min(1.0, float(w.get("opacity", 0.85)))),
        "surface_offset_m": float(w.get("surface_offset_m", -0.30)),
        "roughness": max(0.0, min(1.0, float(w.get("roughness", 0.12)))),
        "metallic": max(0.0, min(1.0, float(w.get("metallic", 0.05)))),
    }

    # 6. Lighting & Atmosphere validation
    l_sec = data.get("lighting", {})
    if not isinstance(l_sec, dict):
        raise TypeError("'lighting' section must be a dictionary")
    validated["lighting"] = {
        "sun_azimuth_deg": float(l_sec.get("sun_azimuth_deg", 45.0)) % 360.0,
        "sun_elevation_deg": max(0.0, min(90.0, float(l_sec.get("sun_elevation_deg", 52.0)))),
        "sun_color": _validate_rgb(l_sec.get("sun_color", (255, 248, 235)), "lighting.sun_color"),
        "sun_intensity": max(0.0, float(l_sec.get("sun_intensity", 3.5))),
        "ambient_color": _validate_rgb(l_sec.get("ambient_color", (185, 205, 230)), "lighting.ambient_color"),
        "sky_horizon_color": _validate_rgb(l_sec.get("sky_horizon_color", (210, 225, 245)), "lighting.sky_horizon_color"),
        "sky_zenith_color": _validate_rgb(l_sec.get("sky_zenith_color", (120, 160, 215)), "lighting.sky_zenith_color"),
        "fog_enabled": bool(l_sec.get("fog_enabled", False)),
        "fog_density": max(0.0, float(l_sec.get("fog_density", 0.001))),
        "fog_color": _validate_rgb(l_sec.get("fog_color", (200, 215, 230)), "lighting.fog_color"),
    }

    # 7. Material validation
    m = data.get("material", {})
    if not isinstance(m, dict):
        raise TypeError("'material' section must be a dictionary")
    validated["material"] = {
        "roughness": max(0.0, min(1.0, float(m.get("roughness", 0.80)))),
        "metallic": max(0.0, min(1.0, float(m.get("metallic", 0.04)))),
    }

    return validated
