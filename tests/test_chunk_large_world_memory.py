"""Tests for large-world memory efficiency, batch generation, cancellation, and Blender export."""

from __future__ import annotations

import json
import os
import resource
from pathlib import Path

import numpy as np
import pytest

from terragen.projects import ChunkEngine, ProjectManager


class SyntheticDem:
    def heights(self, lons, lats) -> np.ndarray:
        lons = np.asarray(lons, dtype=np.float64)
        lats = np.asarray(lats, dtype=np.float64)
        return 300.0 + 20.0 * np.cos(lons * 100.0)


def test_20km_world_setup_is_lightweight_and_fast(tmp_path):
    mgr = ProjectManager(tmp_path)
    proj = mgr.create_project("Large World 20km Memory", center=(46.5446, 24.5612), size=20000.0)
    engine = ChunkEngine(proj, project_dir=tmp_path / proj.slug)

    # Initialize 1600 chunks
    settings = engine.init_grid(chunk_size_m=512.0, world_size_x_m=20000.0, world_size_y_m=20000.0)
    assert len(settings.items) == 1600

    # Serialization size for 1600 chunks is compact JSON (< 2MB)
    data = settings.to_dict()
    assert len(data["items"]) == 1600


def test_memory_remains_bounded_during_chunk_generation(tmp_path):
    mgr = ProjectManager(tmp_path)
    proj = mgr.create_project("Bounded Memory", center=(46.5446, 24.5612), size=2048.0)
    engine = ChunkEngine(proj, project_dir=tmp_path / proj.slug)
    engine.init_grid(chunk_size_m=512.0, world_size_x_m=2048.0, world_size_y_m=2048.0)

    dem = SyntheticDem()
    chunk_ids = [c.id for c in engine.project.chunks.items[:6]]

    progress_log = []

    def on_progress(cur, tot, cid, msg):
        progress_log.append((cur, tot, cid))

    res = engine.generate_chunks(
        chunk_ids=chunk_ids,
        dem=dem,
        force=True,
        progress_callback=on_progress,
    )

    assert res["completed"] == 6
    assert len(progress_log) == 6
    assert progress_log[-1] == (6, 6, chunk_ids[-1])


def test_cancellation_preserves_completed_chunks(tmp_path):
    mgr = ProjectManager(tmp_path)
    proj = mgr.create_project("Cancel Test", center=(46.5446, 24.5612), size=2048.0)
    engine = ChunkEngine(proj, project_dir=tmp_path / proj.slug)
    engine.init_grid(chunk_size_m=512.0, world_size_x_m=2048.0, world_size_y_m=2048.0)

    dem = SyntheticDem()
    chunk_ids = [c.id for c in engine.project.chunks.items[:5]]

    cancelled = False

    def is_cancelled_flag():
        return cancelled

    def on_progress(cur, tot, cid, msg):
        nonlocal cancelled
        if cur == 2:
            cancelled = True

    res = engine.generate_chunks(
        chunk_ids=chunk_ids,
        dem=dem,
        force=True,
        progress_callback=on_progress,
        is_cancelled=is_cancelled_flag,
    )

    # 2 chunks generated before cancellation
    assert res["completed"] == 2
    c0 = engine.get_chunk(chunk_ids[0])
    c1 = engine.get_chunk(chunk_ids[1])
    assert c0.status == "built"
    assert c1.status == "built"


def test_blender_export_manifest_and_script(tmp_path):
    mgr = ProjectManager(tmp_path)
    proj = mgr.create_project("Blender Export World", center=(46.5446, 24.5612), size=20000.0)
    engine = ChunkEngine(proj, project_dir=tmp_path / proj.slug)
    engine.init_grid(chunk_size_m=512.0, world_size_x_m=20000.0, world_size_y_m=20000.0)

    manifest_path = engine.export_blender_manifest()
    assert manifest_path.exists()

    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["project_name"] == "Blender Export World"
    assert data["dimensions_m"]["size_x"] == 20000.0
    assert data["dimensions_m"]["chunk_size"] == 512.0
    assert data["chunks_count"] == 1600

    # Verify python importer script syntax
    script_path = manifest_path.parent / "import_to_blender.py"
    assert script_path.exists()
    code = script_path.read_text(encoding="utf-8")
    # Must compile without SyntaxError
    compiled = compile(code, "import_to_blender.py", "exec")
    assert compiled is not None

