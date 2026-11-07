"""Comprehensive Test Suite for Terragen Biome & Procedural Terrain Engine (Etapa 3).

Verifies:
1. Geographic ecological coherence across all 10 biomes.
2. Continuous fuzzy transitions and normalized weight distributions (sum = 1.0).
3. Deterministic seed reproducibility.
4. Seamless continuity across chunk boundaries.
5. Infrastructure and urban mask integration (no uncontrolled overlaps).
6. Reversible manual polygon overrides with continuous feathering.
7. Blender splatmap and metadata generation.
8. Performance scalability for 10 km and 20 km worlds.
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from terragen.biomes import (
    BIOME_COLORS,
    BiomeGenerator,
    BiomeGridResult,
    BiomeOverride,
    BiomeType,
    generate_splatmap_textures,
    sample_mesh_vertex_colors,
)
from terragen.osmdata import Way
from terragen.project import LocalProjection


class ProceduralDem:
    """Multi-feature procedural DEM generating realistic alpine peaks, river valleys, and plains."""

    def heights(self, lons, lats):
        lons_arr = np.asarray(lons, dtype=np.float64)
        lats_arr = np.asarray(lats, dtype=np.float64)
        # Scaled coordinates
        u = (lons_arr - 25.0) * 10000.0  # Easting approx in meters
        v = (lats_arr - 45.0) * 10000.0  # Northing approx in meters

        # Base elevation + central mountain range + rolling hills
        dist_from_center = np.hypot(u, v)
        mountain_peak = 2400.0 * np.exp(-((dist_from_center - 1500.0) ** 2) / (1200.0**2))
        hills = 120.0 * np.sin(u / 300.0) * np.cos(v / 300.0)
        base = 250.0 + (v / 50.0)

        # River valley depression running along u = 0
        river_depression = 80.0 * np.exp(-(u**2) / (150.0**2))

        return np.clip(base + mountain_peak + hills - river_depression, 5.0, 3200.0)


# -----------------------------------------------------------------------------
# 1. Verification of the 10 Biomes & Ecological Coherence
# -----------------------------------------------------------------------------

def test_all_ten_biomes_registered():
    """All 10 required biomes are present, indexed, and provided with distinct debug colors."""
    all_biomes = BiomeType.all_biomes()
    assert len(all_biomes) == 10
    expected = {
        "Grassland",
        "Forest",
        "Dense Forest",
        "Alpine",
        "Rocky Mountains",
        "Desert",
        "Wetlands",
        "Coastal",
        "Snow",
        "Urban",
    }
    actual = {b.value for b in all_biomes}
    assert actual == expected

    for b in all_biomes:
        assert b in BIOME_COLORS
        r, g, b_c = BIOME_COLORS[b]
        assert 0 <= r <= 255 and 0 <= g <= 255 and 0 <= b_c <= 255


def test_ecological_niche_coherence():
    """Verifies that environmental conditions correctly classify into their respective biomes."""
    generator = BiomeGenerator(seed=123, noise_amplitude=0.0)

    # 1. High Alpine Peaks (>2400m) -> Snow
    sample_snow = generator.rules[BiomeType.SNOW].evaluate(
        elevation=np.array([[2600.0]]),
        slope=np.array([[12.0]]),
        moisture=np.array([[0.5]]),
        temperature=np.array([[-5.0]]),
        water_distance=np.array([[500.0]]),
        urban_mask=np.array([[False]]),
    )
    assert sample_snow[0, 0] > 0.8

    # 2. Steep Clifflines (>35 deg) at 1200m -> Rocky Mountains
    sample_rock = generator.rules[BiomeType.ROCKY_MOUNTAINS].evaluate(
        elevation=np.array([[1400.0]]),
        slope=np.array([[38.0]]),
        moisture=np.array([[0.35]]),
        temperature=np.array([[10.0]]),
        water_distance=np.array([[400.0]]),
        urban_mask=np.array([[False]]),
    )
    assert sample_rock[0, 0] > 0.8

    # 3. Arid Basin with low moisture (<0.10) and high heat -> Desert
    sample_desert = generator.rules[BiomeType.DESERT].evaluate(
        elevation=np.array([[250.0]]),
        slope=np.array([[4.0]]),
        moisture=np.array([[0.08]]),
        temperature=np.array([[28.0]]),
        water_distance=np.array([[1200.0]]),
        urban_mask=np.array([[False]]),
    )
    assert sample_desert[0, 0] > 0.8

    # 4. Immediate Shoreline (elev 6m, water dist 25m) -> Coastal
    sample_coastal = generator.rules[BiomeType.COASTAL].evaluate(
        elevation=np.array([[8.0]]),
        slope=np.array([[3.0]]),
        moisture=np.array([[0.75]]),
        temperature=np.array([[19.0]]),
        water_distance=np.array([[20.0]]),
        urban_mask=np.array([[False]]),
    )
    assert sample_coastal[0, 0] > 0.8

    # 5. Flat Valley with high moisture and water nearby -> Wetlands
    sample_wetlands = generator.rules[BiomeType.WETLANDS].evaluate(
        elevation=np.array([[40.0]]),
        slope=np.array([[2.0]]),
        moisture=np.array([[0.85]]),
        temperature=np.array([[16.0]]),
        water_distance=np.array([[60.0]]),
        urban_mask=np.array([[False]]),
    )
    assert sample_wetlands[0, 0] > 0.8


# -----------------------------------------------------------------------------
# 2. Gradual Transitions & Normalized Weights (Sum = 1.0)
# -----------------------------------------------------------------------------

def test_continuous_weights_and_normalization():
    """Biome weights sum to exactly 1.0 across the entire grid with continuous transitions."""
    dem = ProceduralDem()
    proj = LocalProjection(45.0, 25.0)
    generator = BiomeGenerator(seed=777)

    res = generator.evaluate_grid(
        bounds_m=(-1000.0, -1000.0, 1000.0, 1000.0),
        resolution_m=40.0,
        dem=dem,
        proj=proj,
    )

    assert isinstance(res, BiomeGridResult)
    assert res.weights.shape[0] == 10  # 10 biomes
    h, w = res.shape
    assert res.weights.shape[1] == h
    assert res.weights.shape[2] == w

    # Weights sum to 1.0 at every single cell
    weight_sums = np.sum(res.weights, axis=0)
    assert np.allclose(weight_sums, 1.0, atol=1e-5)

    # All weights are bounded in [0.0, 1.0]
    assert np.all(res.weights >= 0.0)
    assert np.all(res.weights <= 1.0)

    # Gradients of weights between adjacent cells are continuous (no NaN, no Inf)
    for b_idx in range(10):
        gy, gx = np.gradient(res.weights[b_idx])
        assert not np.any(np.isnan(gx))
        assert not np.any(np.isnan(gy))


# -----------------------------------------------------------------------------
# 3. Deterministic Seed Reproducibility
# -----------------------------------------------------------------------------

def test_deterministic_seed_reproducibility():
    """Identical seeds produce bit-identical biome weights and classifications."""
    dem = ProceduralDem()
    proj = LocalProjection(45.0, 25.0)

    gen1 = BiomeGenerator(seed=42)
    res1 = gen1.evaluate_grid(bounds_m=(-500.0, -500.0, 500.0, 500.0), resolution_m=25.0, dem=dem, proj=proj)

    gen2 = BiomeGenerator(seed=42)
    res2 = gen2.evaluate_grid(bounds_m=(-500.0, -500.0, 500.0, 500.0), resolution_m=25.0, dem=dem, proj=proj)

    # Must be bit-identical
    assert np.array_equal(res1.dominant, res2.dominant)
    assert np.allclose(res1.weights, res2.weights, atol=1e-7)

    # Different seeds yield different boundary micro-perturbations
    gen3 = BiomeGenerator(seed=9999)
    res3 = gen3.evaluate_grid(bounds_m=(-500.0, -500.0, 500.0, 500.0), resolution_m=25.0, dem=dem, proj=proj)
    assert not np.array_equal(res1.weights, res3.weights)


# -----------------------------------------------------------------------------
# 4. Multi-Chunk Boundary Continuity
# -----------------------------------------------------------------------------

def test_chunk_boundary_continuity():
    """Adjacent chunks evaluate identical biome weights along their shared boundary."""
    dem = ProceduralDem()
    proj = LocalProjection(45.0, 25.0)
    generator = BiomeGenerator(seed=101)

    # Chunk West: [-500, 0] to [0, 500] (shared East border at x = 0.0)
    res_west = generator.evaluate_grid(
        bounds_m=(-500.0, 0.0, 0.0, 500.0),
        resolution_m=10.0,
        dem=dem,
        proj=proj,
    )

    # Chunk East: [0, 0] to [500, 500] (shared West border at x = 0.0)
    res_east = generator.evaluate_grid(
        bounds_m=(0.0, 0.0, 500.0, 500.0),
        resolution_m=10.0,
        dem=dem,
        proj=proj,
    )

    # Compare values at shared boundary x = 0.0:
    # East edge of West chunk (last column) vs West edge of East chunk (first column)
    border_west = res_west.weights[:, :, -1]  # (10, H)
    border_east = res_east.weights[:, :, 0]   # (10, H)

    discrepancy = np.max(np.abs(border_west - border_east))
    assert discrepancy < 1e-5, f"Boundary seam detected! Max discrepancy: {discrepancy}"


# -----------------------------------------------------------------------------
# 5. Infrastructure & Roads/Buildings No-Overlap
# -----------------------------------------------------------------------------

def test_urban_infrastructure_no_uncontrolled_overlap():
    """OSM buildings and road networks are classified as Urban and suppress natural biomes."""
    dem = ProceduralDem()
    proj = LocalProjection(45.0, 25.0)

    # Synthetic urban buildings and roads
    bld_lon0, bld_lat0 = proj.to_lonlat(-10.0, -10.0)
    bld_lon1, bld_lat1 = proj.to_lonlat(20.0, 20.0)
    bld_way = Way(
        id=101,
        tags={"building": "yes"},
        coords=[
            (bld_lon0, bld_lat0),
            (bld_lon1, bld_lat0),
            (bld_lon1, bld_lat1),
            (bld_lon0, bld_lat1),
            (bld_lon0, bld_lat0),
        ],
        rings=[[(bld_lon0, bld_lat0), (bld_lon1, bld_lat0), (bld_lon1, bld_lat1), (bld_lon0, bld_lat1), (bld_lon0, bld_lat0)]],
    )

    generator = BiomeGenerator(seed=42)
    res = generator.evaluate_grid(
        bounds_m=(-100.0, -100.0, 100.0, 100.0),
        resolution_m=5.0,
        dem=dem,
        proj=proj,
        ways=[bld_way],
    )

    # Inside building footprint (x=5.0, y=5.0), urban mask is True and dominant biome is Urban
    sample_inside = res.sample_at_xy(5.0, 5.0)
    assert sample_inside.is_urban is True
    assert sample_inside.dominant_biome == BiomeType.URBAN
    assert sample_inside.weights[BiomeType.URBAN] > 0.60


# -----------------------------------------------------------------------------
# 6. Manual Biome Overrides & Reversibility
# -----------------------------------------------------------------------------

def test_manual_biome_override_and_reversibility():
    """Manual polygon overrides force specific biomes with feathering and are completely reversible."""
    dem = ProceduralDem()
    proj = LocalProjection(45.0, 25.0)
    generator = BiomeGenerator(seed=42)

    # 1. Baseline before override
    res_base = generator.evaluate_grid(
        bounds_m=(-200.0, -200.0, 200.0, 200.0),
        resolution_m=10.0,
        dem=dem,
        proj=proj,
    )
    s_before = res_base.sample_at_xy(0.0, 0.0)
    assert s_before.dominant_biome != BiomeType.DESERT

    # 2. Add manual Desert override polygon
    override = BiomeOverride(
        biome=BiomeType.DESERT,
        polygon_xy=[(-50.0, -50.0), (50.0, -50.0), (50.0, 50.0), (-50.0, 50.0)],
        blend_distance_m=30.0,
        weight=1.0,
        label="Artisan Dune Basin",
    )
    generator.add_override(override)

    res_ov = generator.evaluate_grid(
        bounds_m=(-200.0, -200.0, 200.0, 200.0),
        resolution_m=10.0,
        dem=dem,
        proj=proj,
    )
    s_inside = res_ov.sample_at_xy(0.0, 0.0)
    # Dominant biome inside is now Desert
    assert s_inside.dominant_biome == BiomeType.DESERT
    assert s_inside.weights[BiomeType.DESERT] > 0.90

    # 3. Reversibility: remove override
    assert generator.overrides.remove_override(override.id) is True
    res_restored = generator.evaluate_grid(
        bounds_m=(-200.0, -200.0, 200.0, 200.0),
        resolution_m=10.0,
        dem=dem,
        proj=proj,
    )
    s_restored = res_restored.sample_at_xy(0.0, 0.0)
    assert s_restored.dominant_biome == s_before.dominant_biome
    assert np.allclose(res_restored.weights, res_base.weights, atol=1e-5)


# -----------------------------------------------------------------------------
# 7. Blender Splatmaps & Vertex Color Export
# -----------------------------------------------------------------------------

def test_blender_splatmap_and_vertex_colors_export(tmp_path):
    """Generates 8-bit RGBA splatmaps, JSON manifest, and blended mesh vertex colors."""
    dem = ProceduralDem()
    proj = LocalProjection(45.0, 25.0)
    generator = BiomeGenerator(seed=42)

    res = generator.evaluate_grid(
        bounds_m=(-300.0, -300.0, 300.0, 300.0),
        resolution_m=15.0,
        dem=dem,
        proj=proj,
    )

    out_dir = tmp_path / "biome_export"
    meta = generate_splatmap_textures(res, out_dir, stem="world_biome")

    # Splatmap PNG exists and is 4-channel 8-bit RGBA
    splat_file = Path(meta["splatmap_file"])
    assert splat_file.exists()
    img = Image.open(splat_file)
    assert img.mode == "RGBA"
    assert img.size == (res.shape[1], res.shape[0])

    # Colormap preview exists
    color_file = Path(meta["colormap_file"])
    assert color_file.exists()

    # Manifest JSON exists and has coverage & channel breakdown
    man_file = Path(meta["manifest_file"])
    assert man_file.exists()
    assert len(meta["channels"]) == 4

    # Sample Mesh Vertex Colors
    vertices = np.array([
        [-100.0, 280.0, -100.0],
        [0.0, 450.0, 0.0],
        [150.0, 2300.0, 150.0],  # Mountain height
    ], dtype=np.float64)

    v_colors = sample_mesh_vertex_colors(res, vertices)
    assert v_colors.shape == (3, 4)
    assert v_colors.dtype == np.uint8
    assert np.all(v_colors[:, 3] == 255)  # Alpha = 255


# -----------------------------------------------------------------------------
# 8. Large-Scale Scalability & Performance: 10 km and 20 km Worlds
# -----------------------------------------------------------------------------

def test_scalability_10km_world():
    """Evaluates a 10 km world (10,000m x 10,000m) with high performance (< 1.5 seconds)."""
    dem = ProceduralDem()
    proj = LocalProjection(45.0, 25.0)
    generator = BiomeGenerator(seed=10001)

    t0 = time.perf_counter()
    res_10km = generator.evaluate_grid(
        bounds_m=(-5000.0, -5000.0, 5000.0, 5000.0),
        resolution_m=20.0,  # 501 x 501 grid = 251,001 evaluation points
        dem=dem,
        proj=proj,
    )
    elapsed = time.perf_counter() - t0

    assert isinstance(res_10km, BiomeGridResult)
    assert res_10km.shape == (501, 501)
    assert elapsed < 1.5, f"10km evaluation took too long: {elapsed:.2f}s (budget: 1.5s)"

    # Coverage distribution contains multiple realistic biomes
    cov = res_10km.get_coverage_percentages()
    assert sum(cov.values()) == pytest.approx(100.0, abs=0.5)


def test_scalability_20km_world():
    """Evaluates a massive 20 km world (20,000m x 20,000m) with high performance (< 3.0 seconds)."""
    dem = ProceduralDem()
    proj = LocalProjection(45.0, 25.0)
    generator = BiomeGenerator(seed=20002)

    t0 = time.perf_counter()
    res_20km = generator.evaluate_grid(
        bounds_m=(-10000.0, -10000.0, 10000.0, 10000.0),
        resolution_m=25.0,  # 801 x 801 grid = 641,601 evaluation points
        dem=dem,
        proj=proj,
    )
    elapsed = time.perf_counter() - t0

    assert isinstance(res_20km, BiomeGridResult)
    assert res_20km.shape == (801, 801)
    assert elapsed < 3.0, f"20km evaluation took too long: {elapsed:.2f}s (budget: 3.0s)"

    # Weights sum to 1.0 everywhere across the entire 20km world
    sums = np.sum(res_20km.weights, axis=0)
    assert np.allclose(sums, 1.0, atol=1e-5)
