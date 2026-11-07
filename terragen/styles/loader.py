"""Visual Style Preset Loader & Registry.

Discovers, validates, and manages style presets stored as decoupled JSON files.
Preserves determinism, file-backed versioning, and non-destructive layering.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from .models import StylePreset

PRESETS_DIR = Path(__file__).resolve().parent / "presets"

_CACHE: dict[str, StylePreset] = {}


def get_presets_dir() -> Path:
    PRESETS_DIR.mkdir(parents=True, exist_ok=True)
    return PRESETS_DIR


def load_preset_file(path: Path | str) -> StylePreset:
    """Reads, parses, and validates a single preset JSON file."""
    f = Path(path)
    if not f.exists():
        raise FileNotFoundError(f"Preset file not found: {f}")
    data = json.loads(f.read_text(encoding="utf-8"))
    return StylePreset.from_dict(data)


def load_all_presets(force_reload: bool = False) -> dict[str, StylePreset]:
    """Scans PRESETS_DIR and populates the in-memory preset cache."""
    global _CACHE
    if _CACHE and not force_reload:
        return _CACHE

    presets: dict[str, StylePreset] = {}
    p_dir = get_presets_dir()

    for p_file in sorted(p_dir.glob("*.json")):
        try:
            preset = load_preset_file(p_file)
            presets[preset.name.lower()] = preset
        except Exception as e:
            # Skip corrupted files but log
            print(f"[Avertisment] Eroare la încărcarea presetului {p_file.name}: {e}")

    # Fallbacks if presets dir was empty or missing key presets
    if not presets:
        _ensure_builtin_presets_written()
        return load_all_presets(force_reload=True)

    _CACHE = presets
    return _CACHE


def get_style_preset(name: str | None = None, overrides: dict[str, Any] | None = None) -> StylePreset:
    """Retrieves a style preset by key, applying optional user runtime overrides."""
    presets = load_all_presets()
    key = str(name).lower() if name is not None else "stylized_realistic"

    if key not in presets:
        # Check alias
        if key == "default":
            key = "stylized_realistic" if "stylized_realistic" in presets else "stylized_european_town"
        elif key == "stylized_realistic" and "stylized_realistic" not in presets:
            _ensure_builtin_presets_written()
            presets = load_all_presets(force_reload=True)
            key = "stylized_realistic"

        if key not in presets:
            raise KeyError(f"Style preset '{name}' not found. Available: {list(presets.keys())}")

    base = presets[key]
    preset = copy.deepcopy(base)

    if overrides and isinstance(overrides, dict):
        if "name" in overrides:
            preset.name = str(overrides["name"])
        if "label" in overrides:
            preset.label = str(overrides["label"])
        if "description" in overrides:
            preset.description = str(overrides["description"])

        b = overrides.get("building", {})
        if "simplification_tol" in b:
            preset.building.simplification_tol = float(b["simplification_tol"])
        if "height_scale" in b:
            preset.building.height_scale = float(b["height_scale"])
        if b.get("roof_palette"):
            preset.building.roof_palette = [tuple(c) for c in b["roof_palette"]]
        if b.get("facade_palette"):
            preset.building.facade_palette = [tuple(c) for c in b["facade_palette"]]

        v = overrides.get("vegetation", {})
        if "enabled" in v:
            preset.vegetation.enabled = bool(v["enabled"])
        if "density_per_ha" in v:
            preset.vegetation.density_per_ha = float(v["density_per_ha"])

        t = overrides.get("terrain", {})
        if "elevation_scale" in t:
            preset.terrain.elevation_scale = float(t["elevation_scale"])
        if "low_color" in t:
            preset.terrain.low_color = tuple(t["low_color"])
        if "rock_color" in t:
            preset.terrain.rock_color = tuple(t["rock_color"])

    return preset


def list_style_presets() -> list[dict[str, Any]]:
    """Returns a list of all available style presets with descriptive metadata."""
    presets = load_all_presets()
    out = []
    for p in presets.values():
        out.append({
            "name": p.name,
            "label": p.label,
            "description": p.description,
            "version": p.version,
            "elevation_scale": p.terrain.elevation_scale,
            "building_height_scale": p.building.height_scale,
            "vegetation_enabled": p.vegetation.enabled,
            "vegetation_density": p.vegetation.density_per_ha,
            "roof_styles": p.building.roof_styles,
            "facade_colors": len(p.building.facade_palette),
            "roof_colors": len(p.building.roof_palette),
            "sun_azimuth_deg": p.lighting.sun_azimuth_deg,
            "sun_elevation_deg": p.lighting.sun_elevation_deg,
        })
    return out


def _ensure_builtin_presets_written() -> None:
    """Writes baseline JSON presets to presets directory if not present."""
    p_dir = get_presets_dir()
    for name, data in _BUILTIN_PRESET_DICTS.items():
        dest = p_dir / f"{name}.json"
        if not dest.exists():
            dest.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


_BUILTIN_PRESET_DICTS: dict[str, dict[str, Any]] = {
    "stylized_realistic": {
        "name": "stylized_realistic",
        "label": "Stylized Realistic",
        "description": "Art-directed physically plausible world with lush balanced tones, subtle relief exaggeration, and golden hour lighting.",
        "version": "1.0.0",
        "building": {
            "simplification_tol": 0.6,
            "min_area": 12.0,
            "height_scale": 1.05,
            "height_variation": 0.30,
            "roof_overhang": 0.45,
            "roof_ridge_scale": 1.0,
            "ground_floor_darkening": 0.85,
            "add_chimneys": True,
            "roof_styles": ["gable", "flat", "hip"],
            "facade_palette": [
                [210, 192, 172],
                [225, 212, 190],
                [185, 178, 165],
                [230, 222, 208],
                [195, 170, 150]
            ],
            "roof_palette": [
                [172, 82, 60],
                [82, 80, 88],
                [110, 138, 132],
                [145, 78, 62]
            ],
            "roughness": 0.82,
            "metallic": 0.02
        },
        "vegetation": {
            "enabled": True,
            "density_per_ha": 25.0,
            "forest_density_per_ha": 160.0,
            "tree_types": ["pine", "deciduous"],
            "foliage_palette": [
                [68, 148, 62],
                [82, 165, 74],
                [95, 178, 82]
            ],
            "trunk_palette": [
                [95, 68, 45],
                [80, 58, 40]
            ],
            "scale_min": 0.80,
            "scale_max": 1.40,
            "max_slope_deg": 35.0,
            "exclude_roads_buffer_m": 3.0,
            "exclude_buildings_buffer_m": 2.5
        },
        "terrain": {
            "elevation_scale": 1.15,
            "elevation_offset_m": 0.0,
            "low_color": [115, 168, 88],
            "mid_color": [152, 175, 108],
            "high_color": [170, 164, 142],
            "rock_color": [118, 122, 128],
            "peak_color": [242, 245, 250],
            "slope_threshold_deg": 20.0,
            "slope_blend_range_deg": 16.0,
            "roughness": 0.85,
            "metallic": 0.0
        },
        "roads": {
            "asphalt_color": [62, 66, 74],
            "sidewalk_color": [172, 168, 160],
            "track_color": [140, 125, 105],
            "width_scale": 1.0,
            "roughness": 0.70,
            "metallic": 0.04
        },
        "water": {
            "color": [52, 135, 178],
            "opacity": 0.85,
            "surface_offset_m": -0.32,
            "roughness": 0.12,
            "metallic": 0.05
        },
        "lighting": {
            "sun_azimuth_deg": 55.0,
            "sun_elevation_deg": 48.0,
            "sun_color": [255, 246, 230],
            "sun_intensity": 4.0,
            "ambient_color": [175, 205, 235],
            "sky_horizon_color": [215, 230, 245],
            "sky_zenith_color": [115, 155, 215],
            "fog_enabled": True,
            "fog_density": 0.0008,
            "fog_color": [210, 225, 240]
        },
        "material": {
            "roughness": 0.80,
            "metallic": 0.04
        }
    },
    "stylized_european_town": {
        "name": "stylized_european_town",
        "label": "Stylized European Town",
        "description": "Vibrant historic town aesthetic with warm terracotta roofs, pastel facades, and lush greenery.",
        "version": "1.0.0",
        "building": {
            "simplification_tol": 0.8,
            "min_area": 12.0,
            "height_scale": 1.0,
            "height_variation": 0.35,
            "roof_overhang": 0.50,
            "roof_ridge_scale": 1.1,
            "ground_floor_darkening": 0.84,
            "add_chimneys": True,
            "roof_styles": ["gable", "hip", "flat"],
            "facade_palette": [
                [236, 218, 188],
                [218, 192, 160],
                [242, 232, 214],
                [202, 168, 142],
                [228, 208, 192],
                [184, 198, 178],
                [212, 186, 174]
            ],
            "roof_palette": [
                [188, 82, 58],
                [168, 72, 52],
                [204, 96, 68],
                [78, 82, 94]
            ],
            "roughness": 0.80,
            "metallic": 0.02
        },
        "vegetation": {
            "enabled": True,
            "density_per_ha": 18.0,
            "forest_density_per_ha": 130.0,
            "tree_types": ["deciduous", "pine"],
            "foliage_palette": [
                [62, 156, 78],
                [78, 172, 88],
                [96, 186, 98]
            ],
            "trunk_palette": [
                [95, 68, 45],
                [80, 58, 40]
            ],
            "scale_min": 0.70,
            "scale_max": 1.30,
            "max_slope_deg": 32.0,
            "exclude_roads_buffer_m": 3.0,
            "exclude_buildings_buffer_m": 2.0
        },
        "terrain": {
            "elevation_scale": 1.0,
            "elevation_offset_m": 0.0,
            "low_color": [132, 178, 98],
            "mid_color": [162, 190, 118],
            "high_color": [178, 172, 142],
            "rock_color": [122, 120, 116],
            "peak_color": None,
            "slope_threshold_deg": 19.0,
            "slope_blend_range_deg": 18.0,
            "roughness": 0.85,
            "metallic": 0.0
        },
        "roads": {
            "asphalt_color": [74, 78, 86],
            "sidewalk_color": [168, 164, 155],
            "track_color": [145, 130, 110],
            "width_scale": 1.0,
            "roughness": 0.70,
            "metallic": 0.04
        },
        "water": {
            "color": [58, 142, 188],
            "opacity": 0.85,
            "surface_offset_m": -0.30,
            "roughness": 0.15,
            "metallic": 0.05
        },
        "lighting": {
            "sun_azimuth_deg": 45.0,
            "sun_elevation_deg": 52.0,
            "sun_color": [255, 248, 235],
            "sun_intensity": 3.8,
            "ambient_color": [185, 205, 230],
            "sky_horizon_color": [210, 225, 245],
            "sky_zenith_color": [120, 160, 215],
            "fog_enabled": False,
            "fog_density": 0.001,
            "fog_color": [200, 215, 230]
        },
        "material": {
            "roughness": 0.80,
            "metallic": 0.04
        }
    },
    "stylized_rural": {
        "name": "stylized_rural",
        "label": "Stylized Rural",
        "description": "Quiet countryside landscape with rustic farmhouses, hay-colored roofs, and winding dirt paths.",
        "version": "1.0.0",
        "building": {
            "simplification_tol": 1.2,
            "min_area": 16.0,
            "height_scale": 0.90,
            "height_variation": 0.25,
            "roof_overhang": 0.60,
            "roof_ridge_scale": 1.2,
            "ground_floor_darkening": 0.80,
            "add_chimneys": True,
            "roof_styles": ["gable"],
            "facade_palette": [
                [220, 205, 175],
                [195, 178, 150],
                [168, 142, 118],
                [210, 190, 165]
            ],
            "roof_palette": [
                [185, 125, 68],
                [160, 105, 55],
                [150, 75, 50]
            ],
            "roughness": 0.88,
            "metallic": 0.01
        },
        "vegetation": {
            "enabled": True,
            "density_per_ha": 35.0,
            "forest_density_per_ha": 180.0,
            "tree_types": ["deciduous", "pine"],
            "foliage_palette": [
                [78, 155, 62],
                [96, 172, 74],
                [118, 185, 85]
            ],
            "trunk_palette": [
                [95, 68, 45]
            ],
            "scale_min": 0.80,
            "scale_max": 1.45,
            "max_slope_deg": 35.0,
            "exclude_roads_buffer_m": 2.5,
            "exclude_buildings_buffer_m": 2.0
        },
        "terrain": {
            "elevation_scale": 1.0,
            "elevation_offset_m": 0.0,
            "low_color": [140, 185, 95],
            "mid_color": [170, 198, 110],
            "high_color": [182, 175, 135],
            "rock_color": [128, 122, 112],
            "peak_color": None,
            "slope_threshold_deg": 22.0,
            "slope_blend_range_deg": 16.0,
            "roughness": 0.90,
            "metallic": 0.0
        },
        "roads": {
            "asphalt_color": [90, 85, 78],
            "sidewalk_color": [155, 145, 130],
            "track_color": [140, 125, 102],
            "width_scale": 0.92,
            "roughness": 0.85,
            "metallic": 0.02
        },
        "water": {
            "color": [68, 150, 168],
            "opacity": 0.80,
            "surface_offset_m": -0.28,
            "roughness": 0.18,
            "metallic": 0.02
        },
        "lighting": {
            "sun_azimuth_deg": 65.0,
            "sun_elevation_deg": 38.0,
            "sun_color": [255, 240, 218],
            "sun_intensity": 3.6,
            "ambient_color": [190, 210, 225],
            "sky_horizon_color": [225, 235, 245],
            "sky_zenith_color": [125, 168, 220],
            "fog_enabled": True,
            "fog_density": 0.0012,
            "fog_color": [215, 228, 238]
        },
        "material": {
            "roughness": 0.85,
            "metallic": 0.02
        }
    },
    "stylized_forest": {
        "name": "stylized_forest",
        "label": "Stylized Forest",
        "description": "Deep wilderness canopy with emerald moss tones, dense conifers, and dark slate riverbeds.",
        "version": "1.0.0",
        "building": {
            "simplification_tol": 1.0,
            "min_area": 14.0,
            "height_scale": 0.95,
            "height_variation": 0.30,
            "roof_overhang": 0.55,
            "roof_ridge_scale": 1.15,
            "ground_floor_darkening": 0.78,
            "add_chimneys": True,
            "roof_styles": ["gable"],
            "facade_palette": [
                [175, 152, 128],
                [150, 130, 108],
                [190, 172, 145]
            ],
            "roof_palette": [
                [95, 115, 90],
                [72, 85, 70],
                [128, 88, 62]
            ],
            "roughness": 0.85,
            "metallic": 0.02
        },
        "vegetation": {
            "enabled": True,
            "density_per_ha": 65.0,
            "forest_density_per_ha": 260.0,
            "tree_types": ["pine", "deciduous"],
            "foliage_palette": [
                [42, 122, 58],
                [52, 138, 68],
                [35, 105, 50],
                [68, 150, 75]
            ],
            "trunk_palette": [
                [75, 52, 35],
                [90, 62, 42]
            ],
            "scale_min": 0.85,
            "scale_max": 1.55,
            "max_slope_deg": 38.0,
            "exclude_roads_buffer_m": 2.0,
            "exclude_buildings_buffer_m": 2.0
        },
        "terrain": {
            "elevation_scale": 1.05,
            "elevation_offset_m": 0.0,
            "low_color": [95, 148, 75],
            "mid_color": [122, 162, 88],
            "high_color": [145, 150, 115],
            "rock_color": [108, 110, 105],
            "peak_color": None,
            "slope_threshold_deg": 18.0,
            "slope_blend_range_deg": 14.0,
            "roughness": 0.88,
            "metallic": 0.0
        },
        "roads": {
            "asphalt_color": [70, 72, 75],
            "sidewalk_color": [140, 138, 130],
            "track_color": [120, 105, 88],
            "width_scale": 0.95,
            "roughness": 0.80,
            "metallic": 0.03
        },
        "water": {
            "color": [45, 115, 135],
            "opacity": 0.88,
            "surface_offset_m": -0.32,
            "roughness": 0.14,
            "metallic": 0.03
        },
        "lighting": {
            "sun_azimuth_deg": 70.0,
            "sun_elevation_deg": 44.0,
            "sun_color": [245, 245, 230],
            "sun_intensity": 3.4,
            "ambient_color": [160, 195, 205],
            "sky_horizon_color": [195, 218, 225],
            "sky_zenith_color": [100, 145, 195],
            "fog_enabled": True,
            "fog_density": 0.002,
            "fog_color": [180, 205, 215]
        },
        "material": {
            "roughness": 0.85,
            "metallic": 0.03
        }
    },
    "stylized_mountains": {
        "name": "stylized_mountains",
        "label": "Stylized Mountains",
        "description": "Dramatic high alpine terrain with exposed granite ridges, subalpine pines, and permanent snow peaks.",
        "version": "1.0.0",
        "building": {
            "simplification_tol": 0.8,
            "min_area": 12.0,
            "height_scale": 0.95,
            "height_variation": 0.28,
            "roof_overhang": 0.55,
            "roof_ridge_scale": 1.25,
            "ground_floor_darkening": 0.82,
            "add_chimneys": True,
            "roof_styles": ["gable"],
            "facade_palette": [
                [208, 192, 170],
                [182, 162, 140],
                [155, 145, 135]
            ],
            "roof_palette": [
                [142, 68, 52],
                [82, 75, 78],
                [105, 102, 108]
            ],
            "roughness": 0.84,
            "metallic": 0.02
        },
        "vegetation": {
            "enabled": True,
            "density_per_ha": 28.0,
            "forest_density_per_ha": 140.0,
            "tree_types": ["pine"],
            "foliage_palette": [
                [48, 115, 62],
                [62, 132, 74],
                [78, 145, 86]
            ],
            "trunk_palette": [
                [85, 60, 42]
            ],
            "scale_min": 0.65,
            "scale_max": 1.25,
            "max_slope_deg": 36.0,
            "exclude_roads_buffer_m": 2.5,
            "exclude_buildings_buffer_m": 2.0
        },
        "terrain": {
            "elevation_scale": 1.25,
            "elevation_offset_m": 0.0,
            "low_color": [112, 160, 85],
            "mid_color": [145, 168, 105],
            "high_color": [165, 158, 135],
            "rock_color": [125, 126, 130],
            "peak_color": [245, 248, 252],
            "slope_threshold_deg": 16.0,
            "slope_blend_range_deg": 12.0,
            "roughness": 0.88,
            "metallic": 0.0
        },
        "roads": {
            "asphalt_color": [65, 68, 74],
            "sidewalk_color": [152, 150, 145],
            "track_color": [135, 122, 105],
            "width_scale": 0.95,
            "roughness": 0.75,
            "metallic": 0.03
        },
        "water": {
            "color": [42, 130, 172],
            "opacity": 0.90,
            "surface_offset_m": -0.35,
            "roughness": 0.10,
            "metallic": 0.06
        },
        "lighting": {
            "sun_azimuth_deg": 35.0,
            "sun_elevation_deg": 55.0,
            "sun_color": [255, 250, 240],
            "sun_intensity": 4.2,
            "ambient_color": [170, 200, 240],
            "sky_horizon_color": [205, 225, 250],
            "sky_zenith_color": [95, 145, 225],
            "fog_enabled": True,
            "fog_density": 0.0006,
            "fog_color": [215, 230, 248]
        },
        "material": {
            "roughness": 0.82,
            "metallic": 0.04
        }
    },
    "realistic": {
        "name": "realistic",
        "label": "Realistic (Standard)",
        "description": "Standard photorealistic baseline matching raw geographic OSM classification and aerial tones.",
        "version": "1.0.0",
        "building": {
            "simplification_tol": 0.0,
            "min_area": 8.0,
            "height_scale": 1.0,
            "height_variation": 0.0,
            "roof_overhang": 0.35,
            "roof_ridge_scale": 1.0,
            "ground_floor_darkening": 0.88,
            "add_chimneys": True,
            "roof_styles": ["gable", "flat"],
            "facade_palette": [],
            "roof_palette": [],
            "roughness": 0.80,
            "metallic": 0.02
        },
        "vegetation": {
            "enabled": False,
            "density_per_ha": 0.0,
            "forest_density_per_ha": 0.0,
            "tree_types": ["pine", "deciduous"],
            "foliage_palette": [],
            "trunk_palette": [[102, 72, 48]],
            "scale_min": 0.75,
            "scale_max": 1.35,
            "max_slope_deg": 34.0,
            "exclude_roads_buffer_m": 3.0,
            "exclude_buildings_buffer_m": 2.0
        },
        "terrain": {
            "elevation_scale": 1.0,
            "elevation_offset_m": 0.0,
            "low_color": [146, 154, 136],
            "mid_color": [160, 162, 145],
            "high_color": [170, 162, 138],
            "rock_color": [122, 120, 116],
            "peak_color": None,
            "slope_threshold_deg": 18.0,
            "slope_blend_range_deg": 16.0,
            "roughness": 0.85,
            "metallic": 0.0
        },
        "roads": {
            "asphalt_color": [72, 76, 84],
            "sidewalk_color": [163, 160, 152],
            "track_color": [145, 130, 110],
            "width_scale": 1.0,
            "roughness": 0.70,
            "metallic": 0.04
        },
        "water": {
            "color": [68, 128, 165],
            "opacity": 0.85,
            "surface_offset_m": -0.30,
            "roughness": 0.12,
            "metallic": 0.05
        },
        "lighting": {
            "sun_azimuth_deg": 45.0,
            "sun_elevation_deg": 50.0,
            "sun_color": [255, 252, 242],
            "sun_intensity": 3.5,
            "ambient_color": [180, 200, 220],
            "sky_horizon_color": [210, 220, 235],
            "sky_zenith_color": [120, 160, 210],
            "fog_enabled": False,
            "fog_density": 0.001,
            "fog_color": [200, 215, 230]
        },
        "material": {
            "roughness": 0.80,
            "metallic": 0.04
        }
    }
}
