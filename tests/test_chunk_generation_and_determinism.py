"""Tests for deterministic chunk generation, seed derivation, and selective regeneration."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from terragen.osmdata import Way
from terragen.projects import ChunkEngine, ProjectManager, derive_chunk_seed


class SyntheticDem:
    def heights(self, lons, lats) -> np.ndarray:
        lons = np.asarray(lons, dtype=np.float64)
        lats = np.asarray(lats, dtype=np.float64)
        return 350.0 + 30.0 * np.sin(lons * 150.0)


def test_seed_derivation_determinism():
    world_seed = 7
    s1 = derive_chunk_seed(world_seed, "chk_r01_c01")
    s2 = derive_chunk_seed(world_seed, "chk_r01_c01")
    s_other = derive_chunk_seed(world_seed, "chk_r01_c02")

    # Identical inputs yield identical seed
    assert s1 == s2
    # Different chunks yield different seeds
    assert s1 != s_other


def test_generate_adjacent_chunks_and_verify_outputs(tmp_path):
    mgr = ProjectManager(tmp_path)
    proj = mgr.create_project("Chunk Gen Test", center=(46.5446, 24.5612), size=1024.0)
    engine = ChunkEngine(proj, project_dir=tmp_path / proj.slug)
    engine.init_grid(chunk_size_m=512.0, world_size_x_m=1024.0, world_size_y_m=1024.0)

    dem = SyntheticDem()

    # Generate chunk chk_r00_c00
    m00 = engine.generate_chunk("chk_r00_c00", dem=dem, force=True, terrain_res=16)
    c00_dir = engine.chunks_dir / "chk_r00_c00"
    glb00 = c00_dir / "chunk.glb"
    manifest00 = c00_dir / "manifest.json"

    assert glb00.exists()
    assert glb00.stat().st_size > 500
    assert manifest00.exists()
    assert m00["stats"]["terrain_triangles"] > 0

    # Generate adjacent chunk chk_r00_c01
    m01 = engine.generate_chunk("chk_r00_c01", dem=dem, force=True, terrain_res=16)
    c01_dir = engine.chunks_dir / "chk_r00_c01"
    glb01 = c01_dir / "chunk.glb"

    assert glb01.exists()
    assert glb01.stat().st_size > 500


def test_regenerate_one_chunk_keeps_unaffected_chunks_unchanged(tmp_path):
    mgr = ProjectManager(tmp_path)
    proj = mgr.create_project("Regen Test", center=(46.5446, 24.5612), size=1024.0)
    engine = ChunkEngine(proj, project_dir=tmp_path / proj.slug)
    engine.init_grid(chunk_size_m=512.0, world_size_x_m=1024.0, world_size_y_m=1024.0)

    dem = SyntheticDem()

    # Build c00 and c01
    engine.generate_chunk("chk_r00_c00", dem=dem, force=True, terrain_res=16)
    engine.generate_chunk("chk_r00_c01", dem=dem, force=True, terrain_res=16)

    glb01 = engine.chunks_dir / "chk_r00_c01" / "chunk.glb"
    mtime_c01_before = glb01.stat().st_mtime_ns
    bytes_c01_before = glb01.read_bytes()

    # Regenerate only c00
    engine.generate_chunk("chk_r00_c00", dem=dem, force=True, terrain_res=16)

    # Verify c01 was NOT modified
    assert glb01.stat().st_mtime_ns == mtime_c01_before
    assert glb01.read_bytes() == bytes_c01_before


def test_deterministic_output_content_hash(tmp_path):
    mgr = ProjectManager(tmp_path)
    proj = mgr.create_project("Hash Test", center=(46.5446, 24.5612), size=1024.0)
    engine = ChunkEngine(proj, project_dir=tmp_path / proj.slug)
    engine.init_grid(chunk_size_m=512.0, world_size_x_m=1024.0, world_size_y_m=1024.0)

    dem = SyntheticDem()

    # First run
    m1 = engine.generate_chunk("chk_r00_c00", dem=dem, force=True, terrain_res=16)
    glb_bytes1 = (engine.chunks_dir / "chk_r00_c00" / "chunk.glb").read_bytes()

    # Second run with force=True
    m2 = engine.generate_chunk("chk_r00_c00", dem=dem, force=True, terrain_res=16)
    glb_bytes2 = (engine.chunks_dir / "chk_r00_c00" / "chunk.glb").read_bytes()

    assert m1["content_hash"] == m2["content_hash"]
    assert glb_bytes1 == glb_bytes2

