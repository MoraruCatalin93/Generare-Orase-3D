"""Tests for Stylized World Generation: Style Presets, Procedural Vegetation, and Low-Poly Geometry."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from shapely.geometry import Point, Polygon

from conftest import LAT0, LON0
from terragen.build import CityBuilder
from terragen.cli import generate
from terragen.geom import Mesh
from terragen.project import LocalProjection
from terragen.projects import ChunkEngine, ProjectManager
from terragen.server import app
from terragen.styles import (
    STYLE_PRESETS,
    BuildingStyle,
    RoadStyle,
    StylePreset,
    TerrainStyle,
    VegetationStyle,
    WaterStyle,
    build_tree_mesh,
    generate_vegetation_layer,
    get_style_preset,
    list_style_presets,
)


class SyntheticAlpineDem:
    """DEM simulating a valley rising to a steep alpine summit."""

    def heights(self, lons, lats) -> np.ndarray:
        lons = np.asarray(lons, dtype=np.float64)
        lats = np.asarray(lats, dtype=np.float64)
        # Low valley at 300m rising to a 1200m peak
        r = np.hypot(lons - LON0, lats - LAT0) * 111000.0
        return np.clip(1200.0 - 0.7 * r, 250.0, 1200.0)


# -----------------------------------------------------------------------------
# 1. Preset System & Configuration
# -----------------------------------------------------------------------------


def test_preset_discovery_and_structures():
    presets = list_style_presets()
    names = [p["name"] for p in presets]
    assert "stylized_european_town" in names
    assert "stylized_rural" in names
    assert "stylized_forest" in names
    assert "stylized_mountains" in names
    assert "realistic" in names

    # Check details of each preset
    for name in names:
        preset = get_style_preset(name)
        assert isinstance(preset, StylePreset)
        assert preset.name == name
        assert preset.label
        assert preset.description
        assert isinstance(preset.building, BuildingStyle)
        assert isinstance(preset.vegetation, VegetationStyle)
        assert isinstance(preset.terrain, TerrainStyle)
        assert isinstance(preset.roads, RoadStyle)
        assert isinstance(preset.water, WaterStyle)

        data = preset.to_dict()
        assert data["name"] == name
        assert "building" in data
        assert "vegetation" in data
        assert "terrain" in data


def test_custom_preset_overrides():
    custom = get_style_preset(
        "stylized_rural",
        overrides={
            "name": "my_custom_style",
            "label": "My Custom Style",
            "building": {
                "simplification_tol": 1.5,
                "height_scale": 1.25,
            },
            "vegetation": {
                "density_per_ha": 45.0,
            },
            "terrain": {
                "low_color": [100, 200, 100],
            },
        },
    )
    assert custom.name == "my_custom_style"
    assert custom.label == "My Custom Style"
    assert custom.building.simplification_tol == 1.5
    assert custom.building.height_scale == 1.25
    assert custom.vegetation.density_per_ha == 45.0
    assert custom.terrain.low_color == (100, 200, 100)


# -----------------------------------------------------------------------------
# 2. Determinism & Seed Variation
# -----------------------------------------------------------------------------


def test_stylized_generation_determinism(ways, fake_dem):
    proj = LocalProjection(LAT0, LON0)
    preset = get_style_preset("stylized_european_town")

    # Run 1
    b1 = CityBuilder(ways, proj, fake_dem, size_m=600, terrain_res=64, seed=42, style_preset=preset)
    b1.build_terrain()
    roads1, walks1, _ = b1.build_roads()
    bld1 = b1.build_buildings()
    areas1 = b1.build_areas()
    veg1 = b1.build_vegetation()

    # Run 2 with identical seed & preset
    b2 = CityBuilder(ways, proj, fake_dem, size_m=600, terrain_res=64, seed=42, style_preset=preset)
    b2.build_terrain()
    roads2, walks2, _ = b2.build_roads()
    bld2 = b2.build_buildings()
    areas2 = b2.build_areas()
    veg2 = b2.build_vegetation()

    # Vertex counts, face counts, and vertex positions must match exactly
    assert len(bld1.v) == len(bld2.v)
    assert len(bld1.f) == len(bld2.f)
    assert np.allclose(bld1.v, bld2.v, atol=1e-6)
    assert (bld1.c == bld2.c).all()

    assert len(veg1.v) == len(veg2.v)
    assert len(veg1.f) == len(veg2.f)
    if len(veg1.v) > 0:
        assert np.allclose(veg1.v, veg2.v, atol=1e-6)
        assert (veg1.c == veg2.c).all()


def test_seed_produces_controlled_variation(ways, fake_dem):
    proj = LocalProjection(LAT0, LON0)
    preset = get_style_preset("stylized_european_town")

    b_seed1 = CityBuilder(ways, proj, fake_dem, size_m=600, terrain_res=64, seed=101, style_preset=preset)
    b_seed1.build_terrain()
    b_seed1.build_roads()
    bld1 = b_seed1.build_buildings()
    b_seed1.build_areas()
    veg1 = b_seed1.build_vegetation()

    b_seed2 = CityBuilder(ways, proj, fake_dem, size_m=600, terrain_res=64, seed=909, style_preset=preset)
    b_seed2.build_terrain()
    b_seed2.build_roads()
    bld2 = b_seed2.build_buildings()
    b_seed2.build_areas()
    veg2 = b_seed2.build_vegetation()

    # Both produce valid stylized environments
    assert len(bld1.f) > 0 and len(bld2.f) > 0
    assert len(veg1.f) > 0 and len(veg2.f) > 0

    # Colors or positions differ between distinct seeds
    different_colors = not np.array_equal(bld1.c, bld2.c)
    different_veg = not np.array_equal(veg1.v, veg2.v)
    assert different_colors or different_veg


# -----------------------------------------------------------------------------
# 3. Geographic Coordinates Invariance
# -----------------------------------------------------------------------------


def test_geographic_coordinates_invariance(ways, fake_dem):
    """Underlying geographic coordinates (WGS84 & projection center) remain unaltered."""
    proj = LocalProjection(LAT0, LON0)

    # Realistic builder
    b_real = CityBuilder(ways, proj, fake_dem, size_m=600, terrain_res=64, seed=7, style_preset=None)
    t_real = b_real.build_terrain()

    # Stylized builder
    b_style = CityBuilder(ways, proj, fake_dem, size_m=600, terrain_res=64, seed=7, style_preset="stylized_mountains")
    t_style = b_style.build_terrain()

    # X, Z coordinates of the terrain mesh are identical (only coloring is stylized)
    assert np.allclose(t_real.v[:, 0], t_style.v[:, 0], atol=1e-8)
    assert np.allclose(t_real.v[:, 2], t_style.v[:, 2], atol=1e-8)
    # Altitudes match DEM
    assert np.allclose(t_real.v[:, 1], t_style.v[:, 1], atol=1e-8)


# -----------------------------------------------------------------------------
# 4. Procedural Low-Poly Tree Mesh Generation
# -----------------------------------------------------------------------------


def test_procedural_tree_mesh_generation():
    # Pine tree (conifer): stacked cones + 5-sided trunk
    pine = build_tree_mesh(tree_type="pine", x=10.0, z=100.0, y=20.0, scale=1.0)
    assert isinstance(pine, Mesh)
    assert len(pine.v) > 0
    assert len(pine.f) > 0
    # Pine has 3 stacked cones + trunk = low poly (< 65 vertices)
    assert len(pine.v) <= 65
    assert len(pine.f) <= 75

    # Deciduous tree: icosahedron foliage + trunk
    deciduous = build_tree_mesh(tree_type="deciduous", x=-5.0, z=100.0, y=15.0, scale=1.2)
    assert isinstance(deciduous, Mesh)
    assert len(deciduous.v) > 0
    assert len(deciduous.f) > 0
    # Deciduous foliage has 20 triangular faces + 10 trunk faces = 30 faces
    assert len(deciduous.f) == 30

    # Trees stand on the given elevation (z=100)
    assert np.isclose(pine.v[:, 1].min(), 100.0, atol=1e-4)
    assert np.isclose(deciduous.v[:, 1].min(), 100.0, atol=1e-4)


# -----------------------------------------------------------------------------
# 5. Spatial Exclusion Rules for Vegetation
# -----------------------------------------------------------------------------


def test_vegetation_spatial_exclusion_rules(fake_dem):
    proj = LocalProjection(LAT0, LON0)
    style = VegetationStyle(
        enabled=True,
        density_per_ha=100.0,
        forest_density_per_ha=250.0,
        exclude_roads_buffer_m=5.0,
        exclude_buildings_buffer_m=5.0,
        max_slope_deg=30.0,
    )

    # Define exclusion zones
    water_box = Polygon([(-50, -50), (50, -50), (50, 50), (-50, 50)])
    road_box = Polygon([(-200, 100), (200, 100), (200, 115), (-200, 115)])
    bld_box = Polygon([(100, -100), (140, -100), (140, -60), (100, -60)])

    veg_mesh = generate_vegetation_layer(
        bbox_m=(-250, -250, 250, 250),
        dem=fake_dem,
        proj=proj,
        style=style,
        seed=123,
        water_polygons=[water_box],
        road_polygons=[road_box],
        building_polygons=[bld_box],
    )

    assert len(veg_mesh.f) > 0

    # Extract tree base positions (each tree has trunk base vertices)
    xs = veg_mesh.v[:, 0]
    ys = veg_mesh.v[:, 2]  # North is Z in 3D (v[:, 2])

    for i in range(0, len(xs), 20):
        pt = Point(xs[i], ys[i])
        # No tree inside water
        assert not water_box.contains(pt), f"Tree generated inside water at {pt}"
        # No tree inside road strip
        assert not road_box.contains(pt), f"Tree generated inside road at {pt}"
        # No tree inside building
        assert not bld_box.contains(pt), f"Tree generated inside building at {pt}"


# -----------------------------------------------------------------------------
# 6. Procedural Terrain Stylized Materials & Snow Peak
# -----------------------------------------------------------------------------


def test_terrain_stylized_elevation_and_snow(ways):
    proj = LocalProjection(LAT0, LON0)
    dem = SyntheticAlpineDem()

    # Stylized Mountains preset with peak snow
    b_mountains = CityBuilder(
        ways, proj, dem, size_m=1200, terrain_res=64, style_preset="stylized_mountains"
    )
    t_mesh = b_mountains.build_terrain()

    # Colors array: shape (N, 4) or (N, 3)
    cols = t_mesh.c[:, :3]
    # Snow peak color is near white: (238, 244, 250)
    has_snow = np.any((cols[:, 0] > 220) & (cols[:, 1] > 230) & (cols[:, 2] > 240))
    assert has_snow, "Stylized mountains should render snow-capped peaks"


# -----------------------------------------------------------------------------
# 7. Backward Compatibility (Realistic Baseline)
# -----------------------------------------------------------------------------


def test_realistic_baseline_compatibility(ways, fake_dem):
    proj = LocalProjection(LAT0, LON0)

    # When style_preset is None, CityBuilder behaves exactly as original realistic mode
    b_real = CityBuilder(ways, proj, fake_dem, size_m=600, terrain_res=64, style_preset=None)
    t_mesh = b_real.build_terrain()
    r_mesh, w_mesh, _ = b_real.build_roads()
    b_mesh = b_real.build_buildings()
    veg_mesh = b_real.build_vegetation()

    # Vegetation is not procedurally placed in realistic mode
    assert len(veg_mesh.f) == 0
    # Buildings have original roof colors and wall colors
    assert len(b_mesh.f) > 0
    assert len(r_mesh.f) > 0


# -----------------------------------------------------------------------------
# 8. Full Export Pipeline with Stylized Presets
# -----------------------------------------------------------------------------


def test_glb_export_with_stylized_preset(tmp_path, ways, fake_dem):
    out_dir = tmp_path / "stylized_city"
    manifest = generate(
        center=(LAT0, LON0),
        size=600.0,
        out=out_dir,
        ways=ways,
        terrain_res=64,
        formats=("glb",),
        seed=17,
        style_preset="stylized_forest",
    )

    assert (out_dir / "glb" / "terrain.glb").exists()
    assert (out_dir / "glb" / "buildings.glb").exists()
    assert (out_dir / "glb" / "vegetation.glb").exists()
    assert (out_dir / "data" / "summary.json").exists()

    summary = json.loads((out_dir / "data" / "summary.json").read_text(encoding="utf-8"))
    assert "vegetation" in summary["layers"]


# -----------------------------------------------------------------------------
# 9. Server API: Styles and Presets
# -----------------------------------------------------------------------------


def test_api_style_presets_endpoints():
    client = app.test_client()

    # 1. List presets
    resp = client.get("/api/styles/presets")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "presets" in data
    names = [p["name"] for p in data["presets"]]
    assert "stylized_european_town" in names
    assert "stylized_rural" in names
    assert "stylized_forest" in names
    assert "stylized_mountains" in names

    # 2. Get specific preset details
    resp_preset = client.get("/api/styles/presets/stylized_forest")
    assert resp_preset.status_code == 200
    p_data = resp_preset.get_json()
    assert p_data["name"] == "stylized_forest"
    assert p_data["vegetation"]["enabled"] is True
    assert "pine" in p_data["vegetation"]["tree_types"]

    # 3. Non-existent preset returns 404
    resp_404 = client.get("/api/styles/presets/unknown_fantasy_preset")
    assert resp_404.status_code == 404


# -----------------------------------------------------------------------------
# 10. Large World Chunk Engine Integration with Stylized Presets
# -----------------------------------------------------------------------------


def test_chunk_engine_stylized_generation(tmp_path):
    mgr = ProjectManager(tmp_path)
    proj = mgr.create_project("Stylized Large World", center=(LAT0, LON0), size=1024.0)
    engine = ChunkEngine(proj, project_dir=tmp_path / proj.slug)
    engine.init_grid(chunk_size_m=512.0, world_size_x_m=1024.0, world_size_y_m=1024.0)

    dem = SyntheticAlpineDem()

    # Generate chunk with stylized_european_town
    manifest = engine.generate_chunk(
        "chk_r00_c00",
        dem=dem,
        force=True,
        terrain_res=16,
        style_preset="stylized_european_town",
    )

    c_dir = engine.chunks_dir / "chk_r00_c00"
    glb = c_dir / "chunk.glb"
    assert glb.exists()
    assert glb.stat().st_size > 1000
    assert manifest["status"] == "built"

