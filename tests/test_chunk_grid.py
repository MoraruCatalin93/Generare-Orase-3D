"""Tests for ChunkGrid layout, 20km x 20km world partitioning, and spatial indexing."""

from __future__ import annotations

import pytest

from terragen.projects import (
    ChunkEngine,
    Project,
    ProjectManager,
    compute_grid_dimensions,
    derive_chunk_seed,
)
from terragen.projects.model import GeographicLocation, Region


def test_20km_world_with_512m_chunks_layout(tmp_path):
    mgr = ProjectManager(tmp_path)
    # Create 20 km x 20 km project
    proj = mgr.create_project(
        "Large World 20km",
        center=(46.5446, 24.5612),
        size=20000.0,
    )
    engine = ChunkEngine(proj, project_dir=tmp_path / proj.slug)

    # Setup 512m chunks
    settings = engine.init_grid(chunk_size_m=512.0, world_size_x_m=20000.0, world_size_y_m=20000.0)
    grid = settings.grid

    # 20000 / 512 = 39.0625 -> ceil = 40 rows and 40 cols
    assert grid.cols == 40
    assert grid.rows == 40
    assert len(settings.items) == 1600

    # Verify boundary coordinates: world spans [-10000, +10000]
    first_chunk = engine.get_chunk("chk_r00_c00")
    assert first_chunk is not None
    assert first_chunk.row == 0
    assert first_chunk.col == 0
    assert first_chunk.bounds_projected["min_x"] == -10000.0
    assert first_chunk.bounds_projected["max_y"] == 10000.0

    last_chunk = engine.get_chunk("chk_r39_c39")
    assert last_chunk is not None
    assert last_chunk.row == 39
    assert last_chunk.col == 39
    assert last_chunk.bounds_projected["max_x"] == 10480.0 or last_chunk.bounds_projected["max_x"] >= 10000.0


def test_chunk_neighbors_graph(tmp_path):
    mgr = ProjectManager(tmp_path)
    proj = mgr.create_project("Neighbors Test", center=(46.5446, 24.5612), size=5000.0)
    engine = ChunkEngine(proj, project_dir=tmp_path / proj.slug)
    engine.init_grid(chunk_size_m=500.0, world_size_x_m=5000.0, world_size_y_m=5000.0)

    # Interior chunk (r05_c05)
    c55 = engine.get_chunk("chk_r05_c05")
    assert c55 is not None
    assert c55.neighbors["north"] == "chk_r04_c05"
    assert c55.neighbors["south"] == "chk_r06_c05"
    assert c55.neighbors["west"] == "chk_r05_c04"
    assert c55.neighbors["east"] == "chk_r05_c06"
    assert c55.neighbors["nw"] == "chk_r04_c04"
    assert c55.neighbors["se"] == "chk_r06_c06"

    # Top-left corner chunk (r00_c00) has None for North, West, NW
    c00 = engine.get_chunk("chk_r00_c00")
    assert c00 is not None
    assert c00.neighbors["north"] is None
    assert c00.neighbors["west"] is None
    assert c00.neighbors["nw"] is None
    assert c00.neighbors["south"] == "chk_r01_c00"
    assert c00.neighbors["east"] == "chk_r00_c01"


def test_spatial_query_and_region_mapping(tmp_path):
    mgr = ProjectManager(tmp_path)
    proj = mgr.create_project("Spatial Query Test", center=(46.5446, 24.5612), size=3000.0)
    engine = ChunkEngine(proj, project_dir=tmp_path / proj.slug)
    engine.init_grid(chunk_size_m=500.0, world_size_x_m=3000.0, world_size_y_m=3000.0)

    # Query chunk at center (0, 0)
    center_chunk = engine.get_chunk_at(0.0, 0.0)
    assert center_chunk is not None
    assert center_chunk.bounds_projected["min_x"] <= 0.0 <= center_chunk.bounds_projected["max_x"]
    assert center_chunk.bounds_projected["min_y"] <= 0.0 <= center_chunk.bounds_projected["max_y"]

    # Query chunk by lat/lon
    c_lat, c_lon = proj.geographic.center_lat, proj.geographic.center_lon
    geo_chunk = engine.get_chunk_at_lonlat(c_lon, c_lat)
    assert geo_chunk is not None
    assert geo_chunk.id == center_chunk.id

    # Region-Chunk intersection mapping
    reg = Region(
        id="reg_test_zone",
        name="Test Zone",
        polygon=[
            [c_lon - 0.002, c_lat - 0.002],
            [c_lon + 0.002, c_lat - 0.002],
            [c_lon + 0.002, c_lat + 0.002],
            [c_lon - 0.002, c_lat + 0.002],
            [c_lon - 0.002, c_lat - 0.002],
        ],
    )
    proj.regions.append(reg)
    engine._map_regions_to_chunks(proj.chunks.items)

    matching_chunks = [ch for ch in proj.chunks.items if "reg_test_zone" in ch.regions]
    assert len(matching_chunks) >= 1
    assert center_chunk.id in [ch.id for ch in matching_chunks]


def test_configurable_dimensions():
    # 10 km world with 1024 m chunks
    r, c = compute_grid_dimensions(10000.0, 10000.0, chunk_size_m=1024.0)
    assert r == 10
    assert c == 10

    # 1500 m world with 256 m chunks
    r2, c2 = compute_grid_dimensions(1500.0, 1500.0, chunk_size_m=256.0)
    assert r2 == 6
    assert c2 == 6

