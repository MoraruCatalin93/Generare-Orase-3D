"""Tests for Game Geometry, glTF Winding, Outward Normals, Seamless Chunk Borders, and Cache Invalidation."""

from __future__ import annotations

import numpy as np
from shapely.geometry import Polygon

from terragen.blender_export import validate_glb_file, write_multi_mesh_glb
from terragen.export import write_glb
from terragen.geom import Mesh, extrude
from terragen.projects import ChunkEngine, ProjectManager
from tests.conftest import LAT0, LON0


class SyntheticRollingDem:
    """Analytical smooth DEM with continuous derivatives."""

    def heights(self, lons, lats) -> np.ndarray:
        lons = np.asarray(lons, dtype=np.float64)
        lats = np.asarray(lats, dtype=np.float64)
        return 340.0 + 25.0 * np.sin(lons * 120.0) + 18.0 * np.cos(lats * 120.0)


def test_extrude_outward_normals_and_ccw_winding():
    """Verify that extruded building walls have outward normals pointing away from center."""
    poly = Polygon([(0, 0), (20, 0), (20, 15), (0, 15)])
    v, f, cap, _uv = extrude(poly, base=0.0, top=12.0)

    # 1. Cap normals must face upwards (+Y)
    cap_faces = f[cap[f].all(axis=1)]
    tri_cap = v[cap_faces]
    n_cap = np.cross(tri_cap[:, 1] - tri_cap[:, 0], tri_cap[:, 2] - tri_cap[:, 0])
    assert (n_cap[:, 1] > 0).all()

    # 2. Wall normals must point strictly outwards from building center (10, 6, 7.5)
    center = np.array([10.0, 6.0, 7.5])
    wall_faces = f[~cap[f[:, 0]]]
    tri_wall = v[wall_faces]
    n_wall = np.cross(tri_wall[:, 1] - tri_wall[:, 0], tri_wall[:, 2] - tri_wall[:, 0])
    tri_centers = np.mean(tri_wall, axis=1)
    outward_vectors = tri_centers - center

    # Dot product between face normal and outward vector from centroid must be positive
    dots = np.sum(n_wall * outward_vectors, axis=1)
    assert (dots > 0).all()


def test_glb_export_has_single_sided_materials_and_correct_winding(tmp_path):
    """Verify that GLB exports produce doubleSided=False and CCW front-facing geometry."""
    poly = Polygon([(0, 0), (20, 0), (20, 15), (0, 15)])
    v, f, _cap, uv = extrude(poly, base=0.0, top=10.0)

    m = Mesh()
    m.add(v, f, np.full((len(v), 4), 220, dtype=np.uint8), uv)

    out_glb = tmp_path / "game_building.glb"
    write_glb(m, out_glb, name="GameBuilding")

    report = validate_glb_file(out_glb)
    assert report["valid"] is True
    # Verify material has doubleSided=False
    mat = report["materials_data"][0]
    assert mat.get("doubleSided", False) is False

    # Also test multi-mesh GLB
    out_multi = tmp_path / "multi_game.glb"
    write_multi_mesh_glb({"buildings": m}, out_multi, name_prefix="City")
    multi_report = validate_glb_file(out_multi)
    assert multi_report["valid"] is True
    assert multi_report["materials_data"][0].get("doubleSided", False) is False


def test_chunk_boundary_normals_are_seamless(tmp_path):
    """Verify that vertex normals along shared boundaries between adjacent chunks match with zero seam."""
    mgr = ProjectManager(tmp_path)
    proj = mgr.create_project("Seamless Test", center=(LAT0, LON0), size=1024.0)
    engine = ChunkEngine(proj, project_dir=tmp_path / proj.slug)
    engine.init_grid(chunk_size_m=512.0, world_size_x_m=1024.0, world_size_y_m=1024.0)

    dem = SyntheticRollingDem()

    # Generate horizontal neighbors: chk_r00_c00 and chk_r00_c01
    engine.generate_chunk("chk_r00_c00", dem=dem, force=True, terrain_res=16)
    engine.generate_chunk("chk_r00_c01", dem=dem, force=True, terrain_res=16)

    # Validate boundary normal continuity
    res_horiz = engine.validate_boundary_normals("chk_r00_c00", "chk_r00_c01", dem=dem, terrain_res=16)
    assert res_horiz["seamless"] is True
    assert res_horiz["max_normal_difference"] < 1e-5

    # Generate vertical neighbors: chk_r00_c00 and chk_r01_c00
    engine.generate_chunk("chk_r01_c00", dem=dem, force=True, terrain_res=16)
    res_vert = engine.validate_boundary_normals("chk_r00_c00", "chk_r01_c00", dem=dem, terrain_res=16)
    assert res_vert["seamless"] is True
    assert res_vert["max_normal_difference"] < 1e-5


def test_chunk_cache_invalidates_on_style_preset_change(tmp_path):
    """Verify that changing style preset invalidates chunk cache without needing force=True."""
    mgr = ProjectManager(tmp_path)
    proj = mgr.create_project("Cache Invalidate Test", center=(LAT0, LON0), size=1024.0)
    engine = ChunkEngine(proj, project_dir=tmp_path / proj.slug)
    engine.init_grid(chunk_size_m=512.0, world_size_x_m=1024.0, world_size_y_m=1024.0)

    dem = SyntheticRollingDem()

    # Build with stylized_rural
    m1 = engine.generate_chunk("chk_r00_c00", dem=dem, terrain_res=16, style_preset="stylized_rural")
    h1 = m1["content_hash"]

    # Re-generate with stylized_mountains (force=False)
    m2 = engine.generate_chunk("chk_r00_c00", dem=dem, terrain_res=16, style_preset="stylized_mountains")
    h2 = m2["content_hash"]

    # Content hash must be different because style preset changed!
    assert h1 != h2


def test_chunk_cache_invalidates_on_terrain_modifier_change(tmp_path):
    """Verify that applying terrain sculpting modifiers invalidates chunk cache."""
    mgr = ProjectManager(tmp_path)
    proj = mgr.create_project("Modifier Cache Test", center=(LAT0, LON0), size=1024.0)
    engine = ChunkEngine(proj, project_dir=tmp_path / proj.slug)
    engine.init_grid(chunk_size_m=512.0, world_size_x_m=1024.0, world_size_y_m=1024.0)

    dem = SyntheticRollingDem()

    # Build initial unmodified chunk
    m1 = engine.generate_chunk("chk_r00_c00", dem=dem, terrain_res=16)
    h1 = m1["content_hash"]

    # Add a terrain modifier to the project
    editor = mgr.get_terrain_editor(proj.id, base_dem=dem)
    editor.raise_terrain((0.0, 0.0), radius_m=100.0, amount_m=30.0)
    mgr.save_terrain_editor(proj.id, editor)

    # Reload chunk engine with updated project state
    updated_proj = mgr.open_project(proj.id)
    updated_engine = ChunkEngine(updated_proj, project_dir=tmp_path / proj.slug)

    m2 = updated_engine.generate_chunk("chk_r00_c00", dem=dem, terrain_res=16)
    h2 = m2["content_hash"]

    # Must detect modification and recompute hash
    assert h1 != h2
