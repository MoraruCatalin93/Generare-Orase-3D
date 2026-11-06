"""Boundary continuity tests: terrain height continuity, road continuity, and owner-chunk deduplication."""

from __future__ import annotations

import numpy as np
import pytest
from shapely.geometry import LineString, Polygon

from terragen.projects import ChunkEngine, ProjectManager


class SyntheticDem:
    """Deterministic synthetic elevation model for testing boundary continuity."""

    def heights(self, lons, lats) -> np.ndarray:
        lons = np.asarray(lons, dtype=np.float64)
        lats = np.asarray(lats, dtype=np.float64)
        # Smooth continuous 3D surface
        return 320.0 + 45.0 * np.sin(lons * 200.0) + 30.0 * np.cos(lats * 200.0)


def test_terrain_height_continuity_at_shared_boundaries(tmp_path):
    mgr = ProjectManager(tmp_path)
    proj = mgr.create_project("Continuity Test", center=(46.5446, 24.5612), size=2048.0)
    engine = ChunkEngine(proj, project_dir=tmp_path / proj.slug)
    engine.init_grid(chunk_size_m=512.0, world_size_x_m=2048.0, world_size_y_m=2048.0)

    dem = SyntheticDem()

    # 1. Test vertical boundary (East-West adjacent chunks: r01_c01 and r01_c02)
    val_ew = engine.validate_boundary_heights("chk_r01_c01", "chk_r01_c02", dem=dem, num_samples=64)
    assert val_ew["continuous"] is True
    assert val_ew["max_elevation_gap_m"] == 0.0

    # 2. Test horizontal boundary (North-South adjacent chunks: r01_c01 and r02_c01)
    val_ns = engine.validate_boundary_heights("chk_r01_c01", "chk_r02_c01", dem=dem, num_samples=64)
    assert val_ns["continuous"] is True
    assert val_ns["max_elevation_gap_m"] == 0.0


def test_no_duplicate_buildings_across_chunk_boundaries(tmp_path):
    mgr = ProjectManager(tmp_path)
    proj = mgr.create_project("Deduplication Test", center=(46.5446, 24.5612), size=2048.0)
    engine = ChunkEngine(proj, project_dir=tmp_path / proj.slug)
    engine.init_grid(chunk_size_m=512.0, world_size_x_m=2048.0, world_size_y_m=2048.0)

    # Chunks: chk_r01_c01 (X in [-512, 0]) and chk_r01_c02 (X in [0, 512])
    # Border is at X = 0.0
    # Create a building that straddles the border X = 0.0: X from -15 to +10 (centroid at -2.5 -> in c01)
    building_coords = np.array([
        [-15.0, 100.0],
        [10.0, 100.0],
        [10.0, 120.0],
        [-15.0, 120.0],
        [-15.0, 100.0],
    ])

    owner = engine.get_feature_owner_chunk(building_coords)
    assert owner == "chk_r01_c01"

    # Verify that Chunk c02 does NOT own this building
    c02_owner = (owner == "chk_r01_c02")
    assert c02_owner is False


def test_road_continuity_across_chunk_boundary(tmp_path):
    mgr = ProjectManager(tmp_path)
    proj = mgr.create_project("Road Continuity Test", center=(46.5446, 24.5612), size=2048.0)
    engine = ChunkEngine(proj, project_dir=tmp_path / proj.slug)
    engine.init_grid(chunk_size_m=512.0, world_size_x_m=2048.0, world_size_y_m=2048.0)

    dem = SyntheticDem()

    # Border at X = 0.0 between c01 ([-512, 0]) and c02 ([0, 512])
    # A road LineString runs horizontally from X = -100 to X = +100 across the border at Y = 50.0
    road_line = LineString([[-100.0, 50.0], [100.0, 50.0]])

    ch1 = engine.get_chunk("chk_r01_c01")
    ch2 = engine.get_chunk("chk_r01_c02")
    bp1 = ch1.bounds_projected
    bp2 = ch2.bounds_projected

    box1 = Polygon([(bp1["min_x"], bp1["min_y"]), (bp1["max_x"], bp1["min_y"]),
                    (bp1["max_x"], bp1["max_y"]), (bp1["min_x"], bp1["max_y"])])
    box2 = Polygon([(bp2["min_x"], bp2["min_y"]), (bp2["max_x"], bp2["min_y"]),
                    (bp2["max_x"], bp2["max_y"]), (bp2["min_x"], bp2["max_y"])])

    clip1 = road_line.intersection(box1)
    clip2 = road_line.intersection(box2)

    # Both clips touch the border at X = 0.0, Y = 50.0
    pt1_end = clip1.coords[-1]
    pt2_start = clip2.coords[0]

    assert abs(pt1_end[0] - 0.0) < 1e-6
    assert abs(pt2_start[0] - 0.0) < 1e-6
    assert abs(pt1_end[1] - pt2_start[1]) < 1e-6

    # Verify matching elevations at that shared boundary point
    lon1, lat1 = engine.proj.to_lonlat(pt1_end[0], pt1_end[1])
    lon2, lat2 = engine.proj.to_lonlat(pt2_start[0], pt2_start[1])

    z1 = float(dem.heights(lon1, lat1))
    z2 = float(dem.heights(lon2, lat2))

    assert abs(z1 - z2) < 1e-6

