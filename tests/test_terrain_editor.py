"""Tests for Terrain Editor: Non-destructive Sculpting, Modifiers, Masks, Undo/Redo, and Seam Continuity."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from shapely.geometry import Point, Polygon

from conftest import LAT0, LON0
from terragen.project import LocalProjection
from terragen.projects import ChunkEngine, ProjectManager
from terragen.server import app
from terragen.terrain_editor import (
    ModifiedDem,
    TerrainEditor,
    TerrainModifier,
    calculate_radial_falloff,
)


class SyntheticPlainsDem:
    """Deterministic planar DEM for testing height calculations."""

    def heights(self, lons, lats) -> np.ndarray:
        lons = np.asarray(lons, dtype=np.float64)
        lats = np.asarray(lats, dtype=np.float64)
        # Smooth gentle slope around 300m
        return 300.0 + (lons - LON0) * 100.0 + (lats - LAT0) * 100.0


# -----------------------------------------------------------------------------
# 1. Falloffs and Spatial Math
# -----------------------------------------------------------------------------


def test_radial_falloffs():
    radius = 50.0

    # At center (r=0), all falloffs must be 1.0
    for ftype in ("smooth", "linear", "gaussian", "flat"):
        w0 = calculate_radial_falloff(0.0, radius, ftype)
        assert np.isclose(w0, 1.0, atol=1e-5), f"{ftype} at r=0 should be 1.0"

    # At or beyond boundary (r>=radius), falloffs must be 0.0
    for ftype in ("smooth", "linear", "gaussian", "flat"):
        w_edge = calculate_radial_falloff(50.0, radius, ftype)
        w_far = calculate_radial_falloff(75.0, radius, ftype)
        assert np.isclose(w_edge, 0.0, atol=1e-5), f"{ftype} at r=R should be 0.0"
        assert np.isclose(w_far, 0.0, atol=1e-5), f"{ftype} at r>R should be 0.0"

    # Midpoint checks
    w_smooth = calculate_radial_falloff(25.0, radius, "smooth")
    w_linear = calculate_radial_falloff(25.0, radius, "linear")
    assert 0.0 < w_smooth < 1.0
    assert np.isclose(w_linear, 0.5, atol=1e-5)


# -----------------------------------------------------------------------------
# 2. Height Preservation & Non-destructive Base DEM
# -----------------------------------------------------------------------------


def test_height_preservation_original_dem():
    """Verify that original DEM is never modified and untouched coordinates preserve elevation."""
    base_dem = SyntheticPlainsDem()
    proj = LocalProjection(LAT0, LON0)
    editor = TerrainEditor(base_dem=base_dem, proj=proj)

    # Base elevation at (0, 0) and (200, 200)
    lon0, lat0 = proj.to_lonlat(0.0, 0.0)
    lon_far, lat_far = proj.to_lonlat(200.0, 200.0)
    orig_z0 = float(base_dem.heights(lon0, lat0))
    orig_z_far = float(base_dem.heights(lon_far, lat_far))

    # Apply raise at (0, 0) with radius 30m
    editor.raise_terrain((0.0, 0.0), radius_m=30.0, amount_m=15.0)

    # Wrap in ModifiedDem
    mod_dem = ModifiedDem(base_dem=base_dem, editor=editor, proj=proj)

    # 1. Original DEM is completely untouched
    assert np.isclose(float(base_dem.heights(lon0, lat0)), orig_z0, atol=1e-7)
    assert np.isclose(float(base_dem.heights(lon_far, lat_far)), orig_z_far, atol=1e-7)

    # 2. ModifiedDem reflects change at center
    new_z0 = float(mod_dem.heights(lon0, lat0))
    assert np.isclose(new_z0, orig_z0 + 15.0, atol=1e-4)

    # 3. Far point outside brush radius preserves original elevation
    new_z_far = float(mod_dem.heights(lon_far, lat_far))
    assert np.isclose(new_z_far, orig_z_far, atol=1e-7)


# -----------------------------------------------------------------------------
# 3. Sculpting Operations: Raise, Lower, Flatten, Smooth, Hills, Valleys
# -----------------------------------------------------------------------------


def test_raise_and_lower_operations():
    editor = TerrainEditor()
    xs = np.array([0.0, 20.0, 100.0])
    ys = np.array([0.0, 0.0, 0.0])
    base_z = np.array([300.0, 300.0, 300.0])

    # Raise
    editor.raise_terrain((0.0, 0.0), radius_m=50.0, amount_m=10.0, falloff="linear")
    z_raised = editor.evaluate_heights(xs, ys, base_z)
    assert np.isclose(z_raised[0], 310.0, atol=1e-4)  # Center: +10
    assert 300.0 < z_raised[1] < 310.0               # Midpoint: linear falloff
    assert np.isclose(z_raised[2], 300.0, atol=1e-4)  # Outside: 300

    # Lower
    editor.lower_terrain((0.0, 0.0), radius_m=50.0, amount_m=4.0, falloff="flat")
    z_both = editor.evaluate_heights(xs, ys, base_z)
    assert np.isclose(z_both[0], 306.0, atol=1e-4)   # 310 - 4 = 306


def test_flatten_operation():
    editor = TerrainEditor()
    X, Y = np.meshgrid(np.linspace(-40, 40, 9), np.linspace(-40, 40, 9))
    base_Z = 250.0 + 0.5 * X + 0.3 * Y  # Sloped surface

    # Flatten area towards target 280.0m with flat falloff
    editor.flatten_terrain((0.0, 0.0), radius_m=20.0, target_height_m=280.0, falloff="flat", strength=1.0)
    Z_flat = editor.evaluate_grid(X, Y, base_Z)

    # All points within radius 20 must be leveled to exactly 280.0
    r_grid = np.hypot(X, Y)
    inner_mask = r_grid < 18.0
    assert np.allclose(Z_flat[inner_mask], 280.0, atol=1e-3)


def test_smooth_operation():
    editor = TerrainEditor()
    X, Y = np.meshgrid(np.linspace(-30, 30, 15), np.linspace(-30, 30, 15))
    # Noisy/rough surface with a sharp peak at center
    base_Z = np.full_like(X, 300.0)
    base_Z[7, 7] = 360.0  # Sharp spike

    var_before = float(np.var(base_Z[5:10, 5:10]))
    editor.smooth_terrain((0.0, 0.0), radius_m=25.0, strength=0.9)
    Z_smoothed = editor.evaluate_grid(X, Y, base_Z)
    var_after = float(np.var(Z_smoothed[5:10, 5:10]))

    # Variance must decrease significantly (surface smoothed)
    assert var_after < var_before * 0.6
    # Spike at center is softened
    assert Z_smoothed[7, 7] < 340.0


def test_hill_and_valley_creation():
    editor = TerrainEditor()
    xs = np.array([10.0, -40.0])
    ys = np.array([10.0, -40.0])
    base_z = np.array([300.0, 300.0])

    # Hill at (10, 10)
    editor.create_hill((10.0, 10.0), radius_m=30.0, height_m=25.0)
    # Valley at (-40, -40)
    editor.create_valley((-40.0, -40.0), radius_m=30.0, depth_m=12.0)

    z_eval = editor.evaluate_heights(xs, ys, base_z)
    assert np.isclose(z_eval[0], 325.0, atol=1e-4)  # 300 + 25
    assert np.isclose(z_eval[1], 288.0, atol=1e-4)  # 300 - 12


def test_water_area_creation():
    editor = TerrainEditor()
    editor.create_water_area((0.0, 0.0), radius_m=40.0, depth_m=4.0, water_level_m=298.0)

    water_bodies = editor.get_water_bodies()
    assert len(water_bodies) == 1
    wb = water_bodies[0]
    assert wb["water_level_m"] == 298.0
    assert wb["depth_m"] == 4.0
    assert len(wb["polygon"]) == 16


# -----------------------------------------------------------------------------
# 4. Region Scoping & Terrain Exclusions
# -----------------------------------------------------------------------------


class FakeRegion:
    def __init__(self, rid: str, polygon: list[list[float]]):
        self.id = rid
        self.polygon = polygon


def test_region_scoped_modifications():
    # Define a rectangular region polygon from x=[0, 50], y=[0, 50]
    reg = FakeRegion("reg_urban", [[0.0, 0.0], [50.0, 0.0], [50.0, 50.0], [0.0, 50.0], [0.0, 0.0]])
    editor = TerrainEditor(regions=[reg])

    # Raise terrain with brush centered at (0, 0) and radius 40m, restricted to 'reg_urban'
    editor.raise_terrain((0.0, 0.0), radius_m=40.0, amount_m=10.0, region_id="reg_urban", falloff="flat")

    # Point A at (10, 10) is inside both brush and region
    # Point B at (-10, -10) is inside brush but OUTSIDE region
    xs = np.array([10.0, -10.0])
    ys = np.array([10.0, -10.0])
    base_z = np.array([300.0, 300.0])

    z_eval = editor.evaluate_heights(xs, ys, base_z)
    assert np.isclose(z_eval[0], 310.0, atol=1e-4)  # Inside region: raised
    assert np.isclose(z_eval[1], 300.0, atol=1e-4)  # Outside region: preserved!


def test_terrain_exclusion_zone():
    editor = TerrainEditor()
    # Define an exclusion zone covering [10, 10] to [40, 40]
    editor.define_exclusion(polygon=[[10.0, 10.0], [40.0, 10.0], [40.0, 40.0], [10.0, 40.0], [10.0, 10.0]])

    # Broad raise covering [ -50, 50 ]
    editor.raise_terrain((0.0, 0.0), radius_m=100.0, amount_m=20.0, falloff="flat")

    xs = np.array([-20.0, 25.0])
    ys = np.array([-20.0, 25.0])
    base_z = np.array([300.0, 300.0])

    z_eval = editor.evaluate_heights(xs, ys, base_z)
    assert np.isclose(z_eval[0], 320.0, atol=1e-4)  # Outside exclusion: raised
    assert np.isclose(z_eval[1], 300.0, atol=1e-4)  # Inside exclusion: protected from edits!


# -----------------------------------------------------------------------------
# 5. Undo and Redo Mechanics
# -----------------------------------------------------------------------------


def test_undo_and_redo_stack():
    editor = TerrainEditor()
    assert not editor.can_undo()
    assert not editor.can_redo()

    # Step 1: Add Op A
    m_a = editor.raise_terrain((0.0, 0.0), radius_m=30.0, amount_m=10.0)
    assert editor.can_undo()
    assert not editor.can_redo()
    assert len(editor.modifiers) == 1

    # Step 2: Add Op B
    m_b = editor.create_valley((50.0, 50.0), radius_m=20.0, depth_m=5.0)
    assert len(editor.modifiers) == 2

    # Step 3: Undo Op B
    undone = editor.undo()
    assert undone.id == m_b.id
    assert len(editor.modifiers) == 1
    assert editor.can_redo()

    # Step 4: Undo Op A
    undone_a = editor.undo()
    assert undone_a.id == m_a.id
    assert len(editor.modifiers) == 0
    assert not editor.can_undo()
    assert editor.can_redo()

    # Step 5: Redo Op A
    redone_a = editor.redo()
    assert redone_a.id == m_a.id
    assert len(editor.modifiers) == 1

    # Step 6: Any new action clears the redo stack
    editor.raise_terrain((-20.0, 0.0), radius_m=15.0, amount_m=3.0)
    assert not editor.can_redo()


# -----------------------------------------------------------------------------
# 6. Save & Reopen Persistence in Projects
# -----------------------------------------------------------------------------


def test_save_and_reopen_terrain_modifiers(tmp_path):
    mgr = ProjectManager(tmp_path)
    proj = mgr.create_project("Terrain Persistence Project", center=(LAT0, LON0), size=1200.0)

    editor = mgr.get_terrain_editor(proj.id)
    editor.raise_terrain((10.0, 20.0), radius_m=45.0, amount_m=12.5, falloff="gaussian")
    editor.flatten_terrain((-30.0, 15.0), radius_m=35.0, target_height_m=285.0)
    editor.create_water_area((50.0, -40.0), radius_m=25.0, depth_m=3.5, water_level_m=292.0)

    # Save to project
    mgr.save_terrain_editor(proj.id, editor)

    # Reopen project from scratch
    reopened = mgr.open_project(proj.id)
    assert len(reopened.terrain.modifiers) == 3

    # Load editor from reopened project
    reopened_editor = mgr.get_terrain_editor(proj.id)
    assert len(reopened_editor.modifiers) == 3
    ops = [m.op for m in reopened_editor.modifiers]
    assert ops == ["raise_lower", "flatten", "water_basin"]
    assert reopened_editor.modifiers[0].amount_m == 12.5
    assert reopened_editor.modifiers[1].target_height_m == 285.0


# -----------------------------------------------------------------------------
# 7. Chunk Boundary Continuity Across Shared Edges (Delta Z = 0.0)
# -----------------------------------------------------------------------------


def test_chunk_boundary_continuity_with_terrain_edits(tmp_path):
    """Terrain modifications crossing chunk boundaries must produce exact zero-gap edge heights."""
    mgr = ProjectManager(tmp_path)
    proj = mgr.create_project("Chunk Seam Test", center=(LAT0, LON0), size=1024.0)
    engine = ChunkEngine(proj, project_dir=tmp_path / proj.slug)
    engine.init_grid(chunk_size_m=512.0, world_size_x_m=1024.0, world_size_y_m=1024.0)

    base_dem = SyntheticPlainsDem()

    # Place a large hill right across the vertical boundary between chk_r00_c00 and chk_r00_c01
    # Boundary is at X = 0.0
    editor = mgr.get_terrain_editor(proj.id, base_dem=base_dem)
    editor.create_hill((0.0, 100.0), radius_m=80.0, height_m=30.0, falloff="smooth")
    mgr.save_terrain_editor(proj.id, editor)

    mod_dem = ModifiedDem(base_dem=base_dem, editor=editor, proj=engine.proj)

    # Validate heights along the seam line at x = 0.0
    ch_west = engine.get_chunk("chk_r00_c00")
    ch_east = engine.get_chunk("chk_r00_c01")
    assert ch_west and ch_east

    val = engine.validate_boundary_heights("chk_r00_c00", "chk_r00_c01", dem=mod_dem, num_samples=32)
    assert val["continuous"] is True
    assert val["max_elevation_gap_m"] < 1e-6  # Zero gap!


# -----------------------------------------------------------------------------
# 8. Heightmap & GLB Mesh Export Accuracy
# -----------------------------------------------------------------------------


def test_heightmap_and_mesh_export(tmp_path):
    base_dem = SyntheticPlainsDem()
    proj = LocalProjection(LAT0, LON0)
    editor = TerrainEditor(base_dem=base_dem, proj=proj)

    editor.raise_terrain((0.0, 0.0), radius_m=60.0, amount_m=18.0)
    editor.create_water_area((100.0, -100.0), radius_m=40.0, depth_m=4.0, water_level_m=296.0)

    export_dir = tmp_path / "exports"

    # 1. Heightmap export (16-bit PNG + JSON)
    hm_meta = editor.export_heightmap(export_dir, size_m=600.0, resolution=64, stem="sculpted_terrain")
    assert (export_dir / "sculpted_terrain_heightmap.png").exists()
    assert (export_dir / "sculpted_terrain_metadata.json").exists()
    assert hm_meta["bit_depth"] == 16
    assert hm_meta["min_altitude_m"] <= hm_meta["max_altitude_m"]
    assert hm_meta["modifiers_applied"] == 2
    assert len(hm_meta["water_bodies"]) == 1

    # 2. GLB mesh export
    mesh_files = editor.export_terrain_mesh(export_dir, size_m=600.0, resolution=32, stem="sculpted_mesh")
    assert "glb" in mesh_files
    glb_path = Path(mesh_files["glb"])
    assert glb_path.exists()
    assert glb_path.stat().st_size > 500


# -----------------------------------------------------------------------------
# 9. Server REST API Integration
# -----------------------------------------------------------------------------


def test_server_terrain_editor_api(tmp_path):
    # Setup test project
    mgr = ProjectManager(tmp_path)
    proj = mgr.create_project("API Terrain Project", center=(LAT0, LON0), size=1000.0)

    from terragen import server
    server.project_manager = mgr
    client = app.test_client()

    pid = proj.id

    # 1. Get modifiers (initially empty)
    resp = client.get(f"/api/projects/{pid}/terrain/modifiers")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["modifiers"]) == 0
    assert not data["can_undo"]

    # 2. Add raise modifier
    add_resp = client.post(
        f"/api/projects/{pid}/terrain/modifiers",
        json={
            "op": "raise_lower",
            "center_x": 0.0,
            "center_y": 0.0,
            "radius_m": 50.0,
            "amount_m": 12.0,
            "falloff": "smooth",
        },
    )
    assert add_resp.status_code == 200
    res_data = add_resp.get_json()
    assert res_data["ok"] is True
    mod_id = res_data["modifier"]["id"]
    assert res_data["state"]["can_undo"] is True

    # 3. Undo
    undo_resp = client.post(f"/api/projects/{pid}/terrain/undo")
    assert undo_resp.status_code == 200
    assert undo_resp.get_json()["undone"]["id"] == mod_id
    assert undo_resp.get_json()["state"]["can_redo"] is True

    # 4. Redo
    redo_resp = client.post(f"/api/projects/{pid}/terrain/redo")
    assert redo_resp.status_code == 200
    assert redo_resp.get_json()["redone"]["id"] == mod_id

    # 5. Export terrain
    exp_resp = client.post(f"/api/projects/{pid}/terrain/export")
    assert exp_resp.status_code == 200
    exp_data = exp_resp.get_json()
    assert exp_data["ok"] is True
    assert "heightmap_metadata" in exp_data

    # 6. Delete modifier
    del_resp = client.delete(f"/api/projects/{pid}/terrain/modifiers/{mod_id}")
    assert del_resp.status_code == 200
    assert len(del_resp.get_json()["state"]["modifiers"]) == 0
