"""Unit and Integration Tests for Terragen Visual Style Engine (Etapa 2).

Verifies:
1. Preset discovery and loading from JSON files.
2. Preset validation (valid configurations, out-of-bounds error handling).
3. Non-destructive geographic elevation exaggeration.
4. Pilot preset ('stylized_realistic') structure and parameters.
5. Deterministic visual output generation.
6. Blender export integration (lighting, atmosphere, and PBR materials).
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from terragen.blender_export import BlenderExportOptions
from terragen.build import CityBuilder
from terragen.osmdata import Way
from terragen.project import LocalProjection
from terragen.projects.manager import ProjectManager
from terragen.styles import (
    STYLE_PRESETS,
    StylePreset,
    get_style_preset,
    list_style_presets,
    validate_style_preset,
)
from terragen.styles.validator import StyleValidationError


class SyntheticDem:
    """Deterministic synthetic DEM for testing."""
    def __init__(self, base_elev: float = 400.0, slope_factor: float = 0.05):
        self.base_elev = base_elev
        self.slope_factor = slope_factor

    def heights(self, lons, lats):
        lons_arr = np.asarray(lons, dtype=np.float64)
        lats_arr = np.asarray(lats, dtype=np.float64)
        return self.base_elev + (lons_arr - 25.0) * 1000.0 * self.slope_factor + (lats_arr - 45.0) * 1000.0 * self.slope_factor


# -----------------------------------------------------------------------------
# 1. Preset Discovery & Loading
# -----------------------------------------------------------------------------

def test_preset_discovery_and_loader():
    """All JSON presets in terragen/styles/presets/ are discovered and loaded."""
    presets = list_style_presets()
    assert len(presets) >= 6
    names = {p["name"] for p in presets}
    assert "stylized_realistic" in names
    assert "stylized_european_town" in names
    assert "stylized_mountains" in names

    # Loading via get_style_preset
    preset = get_style_preset("stylized_realistic")
    assert isinstance(preset, StylePreset)
    assert preset.name == "stylized_realistic"
    assert preset.label == "Stylized Realistic"
    assert preset.version == "1.0.0"

    # Backward compatibility via STYLE_PRESETS proxy
    assert "stylized_realistic" in STYLE_PRESETS
    assert isinstance(STYLE_PRESETS["stylized_realistic"], StylePreset)


def test_all_six_target_styles_available():
    """All 6 core visual styles specified in Etapa 2 are loaded and validated."""
    required_styles = [
        "low_poly",
        "hand_painted",
        "stylized_realistic",
        "cozy_cartoon",
        "dark_fantasy",
        "post_apocalyptic",
    ]
    for style_id in required_styles:
        preset = get_style_preset(style_id)
        assert isinstance(preset, StylePreset)
        assert preset.name == style_id
        assert len(preset.label) > 0
        assert preset.terrain.elevation_scale >= 1.0
        assert preset.lighting.sun_intensity > 0
        assert len(preset.building.facade_palette) >= 3
        assert len(preset.building.roof_palette) >= 3
        assert len(preset.vegetation.foliage_palette) >= 2


# -----------------------------------------------------------------------------
# 2. Pilot Style: 'stylized_realistic' Deep Audit
# -----------------------------------------------------------------------------

def test_pilot_style_stylized_realistic():
    """Pilot style adheres to game engine PBR specifications."""
    p = get_style_preset("stylized_realistic")

    # Geometry & Elevation
    assert p.terrain.elevation_scale == pytest.approx(1.15)
    assert p.terrain.elevation_offset_m == pytest.approx(0.0)
    assert p.building.simplification_tol == pytest.approx(0.6)
    assert p.building.height_scale == pytest.approx(1.05)

    # PBR Material roughness & metallic
    assert p.terrain.roughness == pytest.approx(0.85)
    assert p.terrain.metallic == pytest.approx(0.0)
    assert p.building.roughness == pytest.approx(0.82)
    assert p.roads.roughness == pytest.approx(0.70)
    assert p.roads.metallic == pytest.approx(0.04)
    assert p.water.roughness == pytest.approx(0.12)
    assert p.water.metallic == pytest.approx(0.05)

    # Lighting & Atmosphere
    assert p.lighting.sun_intensity == pytest.approx(4.0)
    assert p.lighting.sun_azimuth_deg == pytest.approx(55.0)
    assert p.lighting.sun_elevation_deg == pytest.approx(48.0)
    assert p.lighting.fog_enabled is True
    assert p.lighting.fog_density == pytest.approx(0.0008)

    # Vegetation
    assert p.vegetation.enabled is True
    assert p.vegetation.density_per_ha == pytest.approx(25.0)
    assert "pine" in p.vegetation.tree_types
    assert "deciduous" in p.vegetation.tree_types


# -----------------------------------------------------------------------------
# 3. Validation Schema & Robustness
# -----------------------------------------------------------------------------

def test_preset_validator_success():
    """Valid preset passes schema validation without raising errors."""
    valid_doc = {
        "name": "custom_test_style",
        "label": "Custom Test Style",
        "description": "Validation test preset",
        "version": "1.0.0",
        "terrain": {
            "elevation_scale": 1.2,
            "elevation_offset_m": 0.0,
            "low_color": [100, 150, 80],
            "mid_color": [140, 170, 110],
            "high_color": [170, 160, 140],
            "rock_color": [120, 120, 120],
            "slope_threshold_deg": 22.0,
            "slope_blend_range_deg": 15.0,
            "roughness": 0.85,
            "metallic": 0.0,
        },
        "building": {
            "simplification_tol": 0.2,
            "min_area": 8.0,
            "height_scale": 1.0,
            "height_variation": 0.1,
            "roof_overhang": 0.4,
            "roof_ridge_scale": 1.1,
            "add_chimneys": True,
            "roof_styles": ["gable", "hipped"],
            "facade_palette": [[220, 210, 195]],
            "roof_palette": [[160, 70, 55]],
            "roughness": 0.82,
            "metallic": 0.0,
        },
        "roads": {
            "asphalt_color": [70, 74, 80],
            "sidewalk_color": [170, 165, 155],
            "track_color": [140, 125, 105],
            "width_scale": 1.0,
            "roughness": 0.80,
            "metallic": 0.05,
        },
        "water": {
            "color": [55, 120, 160],
            "opacity": 0.85,
            "surface_offset_m": -0.3,
            "roughness": 0.12,
            "metallic": 0.05,
        },
        "vegetation": {
            "enabled": True,
            "density_per_ha": 20.0,
            "forest_density_per_ha": 150.0,
            "tree_types": ["pine"],
            "foliage_palette": [[65, 145, 60]],
            "trunk_palette": [[90, 65, 40]],
            "scale_min": 0.8,
            "scale_max": 1.3,
            "max_slope_deg": 35.0,
            "exclude_roads_buffer_m": 3.0,
            "exclude_buildings_buffer_m": 2.0,
        },
        "lighting": {
            "sun_azimuth_deg": 50.0,
            "sun_elevation_deg": 45.0,
            "sun_color": [255, 245, 230],
            "sun_intensity": 4.0,
            "ambient_color": [175, 200, 230],
            "sky_horizon_color": [205, 220, 240],
            "sky_zenith_color": [120, 160, 220],
            "fog_enabled": False,
            "fog_density": 0.001,
            "fog_color": [200, 215, 230],
        },
    }
    preset = validate_style_preset(valid_doc)
    assert preset.name == "custom_test_style"


def test_preset_validator_rejects_out_of_bounds():
    """Validator catches invalid RGB channels and negative scales."""
    bad_rgb = {
        "name": "bad_rgb_style",
        "terrain": {"low_color": [300, 150, 80]},  # 300 > 255
    }
    with pytest.raises(StyleValidationError, match=r"\[0, 255\]"):
        validate_style_preset(bad_rgb)

    bad_slope = {
        "name": "bad_slope_style",
        "terrain": {"slope_threshold_deg": 120.0},  # > 90 deg
    }
    with pytest.raises(StyleValidationError, match="slope_threshold_deg"):
        validate_style_preset(bad_slope)

    bad_density = {
        "name": "bad_density_style",
        "vegetation": {"density_per_ha": -5.0},
    }
    with pytest.raises(StyleValidationError, match="density_per_ha"):
        validate_style_preset(bad_density)


# -----------------------------------------------------------------------------
# 4. Non-Destructive DEM Elevation Exaggeration
# -----------------------------------------------------------------------------

def test_non_destructive_elevation_scaling():
    """Applying elevation scale exaggerates mesh relief while preserving base DEM."""
    dem = SyntheticDem(base_elev=300.0, slope_factor=0.1)
    proj = LocalProjection(45.0, 25.0)
    ways: list[Way] = []

    # 1. Unscaled baseline
    b_base = CityBuilder(ways, proj, dem, size_m=500.0, terrain_res=32, seed=42, style_preset=None)
    m_base = b_base.build_terrain()
    z_base = m_base.v[:, 1]
    relief_base = float(z_base.max() - z_base.min())

    # 2. Scaled pilot preset (elevation_scale = 1.15)
    b_pilot = CityBuilder(ways, proj, dem, size_m=500.0, terrain_res=32, seed=42, style_preset="stylized_realistic")
    m_pilot = b_pilot.build_terrain()
    z_pilot = m_pilot.v[:, 1]
    relief_pilot = float(z_pilot.max() - z_pilot.min())

    # Relief is exaggerated by ~15%
    assert relief_pilot == pytest.approx(relief_base * 1.15, rel=1e-4)

    # Base DEM values are identical (non-destructive)
    test_lons = np.array([25.0, 25.01])
    test_lats = np.array([45.0, 45.01])
    assert np.allclose(dem.heights(test_lons, test_lats), [300.0, 302.0], atol=1e-5)


def test_visual_style_determinism():
    """Generations with identical seed and preset produce bit-identical mesh geometry."""
    dem = SyntheticDem(base_elev=350.0)
    proj = LocalProjection(45.0, 25.0)

    b1 = CityBuilder([], proj, dem, size_m=300.0, terrain_res=16, seed=123, style_preset="stylized_realistic")
    m1 = b1.build_terrain()

    b2 = CityBuilder([], proj, dem, size_m=300.0, terrain_res=16, seed=123, style_preset="stylized_realistic")
    m2 = b2.build_terrain()

    assert np.array_equal(m1.v, m2.v)
    assert np.array_equal(m1.f, m2.f)
    assert np.array_equal(m1.c, m2.c)


# -----------------------------------------------------------------------------
# 5. Blender Export Integration (Lighting, Atmosphere, PBR)
# -----------------------------------------------------------------------------

def test_blender_export_manifest_style_and_lighting(tmp_path):
    """Blender manifest embeds style lighting, atmosphere, and PBR roughness parameters."""
    pm = ProjectManager(tmp_path / "projects")
    proj = pm.create_project(
        name="Style_Test_City",
        center=(45.0, 25.0),
        size=1000.0,
    )
    proj.generation.style_preset = "stylized_realistic"
    pm.save_project(proj)

    opts = BlenderExportOptions(
        mode="layer",
        formats=("glb",),
        include_heightmap=False,
        include_regions_geojson=False,
        style_preset="stylized_realistic",
    )
    out_dir = tmp_path / "blender_out"
    dem = SyntheticDem()
    pm.export_blender(proj.id, options=opts, out_dir=out_dir, base_dem=dem, ways=[])

    manifest_file = out_dir / "blender_manifest.json"
    assert manifest_file.exists()
    doc = json.loads(manifest_file.read_text(encoding="utf-8"))

    # Style block verification
    assert "style" in doc
    style_info = doc["style"]
    assert style_info["preset_id"] == "stylized_realistic"
    assert style_info["name"] == "stylized_realistic"

    # Lighting block verification
    lighting = style_info["lighting"]
    assert lighting["sun_intensity"] == 4.0
    assert lighting["sun_azimuth_deg"] == 55.0
    assert lighting["sun_elevation_deg"] == 48.0
    assert lighting["fog_enabled"] is True

    # PBR Materials block verification
    materials = style_info["materials"]
    assert materials["terrain"]["roughness"] == 0.85
    assert materials["building"]["roughness"] == 0.82
    assert materials["roads"]["roughness"] == 0.70

    # Blender importer script contains lighting setup
    script_file = out_dir / "import_to_blender.py"
    assert script_file.exists()
    script_code = script_file.read_text(encoding="utf-8")
    assert "setup_environment_and_lighting" in script_code
    assert "Sun_Terragen" in script_code
    assert "Environment_Lighting" in script_code
