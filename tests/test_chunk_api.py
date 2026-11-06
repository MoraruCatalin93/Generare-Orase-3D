"""REST API integration tests for Chunk endpoints."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from terragen.server import app, project_manager


class SyntheticDem:
    def heights(self, lons, lats) -> np.ndarray:
        lons = np.asarray(lons, dtype=np.float64)
        lats = np.asarray(lats, dtype=np.float64)
        return 340.0 + 10.0 * np.sin(lons * 50.0)


@pytest.fixture
def client_with_chunk_project(tmp_path):
    orig_root = project_manager.root
    project_manager.root = tmp_path
    client = app.test_client()

    create_payload = {
        "name": "Chunk API City",
        "center": [46.5446, 24.5612],
        "size": 2048.0,
    }
    resp = client.post("/api/projects", json=create_payload)
    assert resp.status_code == 201
    proj_id = resp.get_json()["project"]["id"]

    yield client, proj_id

    project_manager.root = orig_root


def test_api_chunks_grid_setup_and_listing(client_with_chunk_project):
    client, pid = client_with_chunk_project

    # 1. Setup grid: 2048 m with 512 m chunks -> 4 x 4 = 16 chunks
    res = client.post(
        f"/api/projects/{pid}/chunks/grid",
        json={"chunk_size_m": 512.0, "world_size_x_m": 2048.0, "world_size_y_m": 2048.0},
    )
    assert res.status_code == 201
    data = res.get_json()
    assert data["ok"] is True
    assert data["count"] == 16
    assert data["grid"]["rows"] == 4
    assert data["grid"]["cols"] == 4

    # 2. Get chunks list and GeoJSON
    list_res = client.get(f"/api/projects/{pid}/chunks")
    assert list_res.status_code == 200
    ld = list_res.get_json()
    assert ld["count"] == 16
    assert ld["geojson"]["type"] == "FeatureCollection"
    assert len(ld["geojson"]["features"]) == 16

    # 3. Get single chunk details
    c_res = client.get(f"/api/projects/{pid}/chunks/chk_r01_c01")
    assert c_res.status_code == 200
    c_data = c_res.get_json()
    assert c_data["chunk"]["id"] == "chk_r01_c01"
    assert c_data["geojson"]["geometry"]["type"] == "Polygon"


def test_api_blender_export(client_with_chunk_project):
    client, pid = client_with_chunk_project

    # Setup grid
    client.post(f"/api/projects/{pid}/chunks/grid", json={"chunk_size_m": 512.0})

    # Export blender manifest
    exp_res = client.post(f"/api/projects/{pid}/export/blender")
    assert exp_res.status_code == 200
    ed = exp_res.get_json()
    assert ed["ok"] is True
    assert "blender_manifest.json" in ed["manifest_path"]
    assert "import_to_blender.py" in ed["script_path"]
    assert Path(ed["manifest_path"]).exists()
    assert Path(ed["script_path"]).exists()


def test_api_generate_single_chunk(client_with_chunk_project, monkeypatch):
    client, pid = client_with_chunk_project
    client.post(f"/api/projects/{pid}/chunks/grid", json={"chunk_size_m": 512.0})

    import terragen.dem as dem_module
    monkeypatch.setattr(dem_module, "Dem", lambda *args, **kwargs: SyntheticDem())

    gen_res = client.post(f"/api/projects/{pid}/chunks/chk_r00_c00/generate")
    assert gen_res.status_code == 200
    gd = gen_res.get_json()
    assert gd["ok"] is True
    assert gd["chunk_id"] == "chk_r00_c00"
    assert gd["manifest"]["stats"]["terrain_triangles"] > 0

