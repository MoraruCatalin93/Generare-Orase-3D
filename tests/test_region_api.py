"""REST API integration tests for Region endpoints."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from terragen.server import app, project_manager


@pytest.fixture
def client_with_project(tmp_path):
    orig_root = project_manager.root
    project_manager.root = tmp_path
    client = app.test_client()

    # Create a project
    create_payload = {
        "name": "API Region Test City",
        "center": [46.5446, 24.5612],
        "size": 1500.0,
    }
    resp = client.post("/api/projects", json=create_payload)
    assert resp.status_code == 201
    proj_id = resp.get_json()["project"]["id"]

    yield client, proj_id

    project_manager.root = orig_root


def test_api_regions_crud_workflow(client_with_project):
    client, pid = client_with_project

    # 1. Initially empty
    resp = client.get(f"/api/projects/{pid}/regions")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["count"] == 0
    assert data["geojson"]["type"] == "FeatureCollection"

    # 2. Create region
    poly = [
        [24.555, 46.540],
        [24.565, 46.540],
        [24.565, 46.550],
        [24.555, 46.550],
        [24.555, 46.540],
    ]
    create_resp = client.post(
        f"/api/projects/{pid}/regions",
        json={
            "name": "Downtown",
            "region_type": "urban",
            "polygon": poly,
            "seed": 42,
            "style_preset": "dense_urban",
        },
    )
    assert create_resp.status_code == 201
    reg_id = create_resp.get_json()["region"]["id"]

    # 3. Get region details
    get_resp = client.get(f"/api/projects/{pid}/regions/{reg_id}")
    assert get_resp.status_code == 200
    r_detail = get_resp.get_json()["region"]
    assert r_detail["name"] == "Downtown"
    assert r_detail["geographic"]["area_m2"] > 0

    # 4. Update region (rename, change type to commercial/suburban, lock)
    put_resp = client.put(
        f"/api/projects/{pid}/regions/{reg_id}",
        json={"name": "Downtown Central", "region_type": "suburban", "locked": True},
    )
    assert put_resp.status_code == 200
    assert put_resp.get_json()["region"]["name"] == "Downtown Central"
    assert put_resp.get_json()["region"]["locked"] is True

    # 5. Duplicate region
    dup_resp = client.post(f"/api/projects/{pid}/regions/{reg_id}/duplicate", json={})
    assert dup_resp.status_code == 201
    dup_id = dup_resp.get_json()["region"]["id"]
    assert dup_id != reg_id

    # 6. Validate regions
    val_resp = client.get(f"/api/projects/{pid}/regions/validate")
    assert val_resp.status_code == 200
    val_data = val_resp.get_json()
    assert val_data["ok"] is True
    assert val_data["validation"]["region_count"] == 2

    # 7. Delete duplicated region
    del_resp = client.delete(f"/api/projects/{pid}/regions/{dup_id}")
    assert del_resp.status_code == 200

    # Verify count is 1
    list_resp = client.get(f"/api/projects/{pid}/regions")
    assert list_resp.get_json()["count"] == 1


def test_api_regions_split_and_merge(client_with_project):
    client, pid = client_with_project

    # Create a square polygon
    poly = [
        [24.550, 46.540],
        [24.570, 46.540],
        [24.570, 46.560],
        [24.550, 46.560],
        [24.550, 46.540],
    ]
    create_resp = client.post(
        f"/api/projects/{pid}/regions",
        json={"name": "Sector 1", "polygon": poly},
    )
    assert create_resp.status_code == 201
    reg_id = create_resp.get_json()["region"]["id"]

    # Split
    split_resp = client.post(
        f"/api/projects/{pid}/regions/{reg_id}/split",
        json={"line": [[24.560, 46.535], [24.560, 46.565]]},
    )
    assert split_resp.status_code == 200
    split_regions = split_resp.get_json()["regions"]
    assert len(split_regions) == 2
    r_ids = [r["id"] for r in split_regions]

    # Merge
    merge_resp = client.post(
        f"/api/projects/{pid}/regions/merge",
        json={"region_ids": r_ids, "name": "Sector 1 Reunited"},
    )
    assert merge_resp.status_code == 200
    merged = merge_resp.get_json()["region"]
    assert merged["name"] == "Sector 1 Reunited"


def test_api_region_generation_trigger(client_with_project, monkeypatch):
    client, pid = client_with_project
    import terragen.server as server

    # Add a region with independent seed and settings
    poly = [
        [24.550, 46.540],
        [24.560, 46.540],
        [24.560, 46.550],
        [24.550, 46.550],
        [24.550, 46.540],
    ]
    create_resp = client.post(
        f"/api/projects/{pid}/regions",
        json={
            "name": "District Gamma",
            "region_type": "industrial",
            "polygon": poly,
            "seed": 999,
            "generation": {"roofs": False},
        },
    )
    reg_id = create_resp.get_json()["region"]["id"]

    captured = {}

    def mock_start(params):
        captured.update(params)
        job = server.Job(params)
        server.JOBS[job.id] = job
        return job

    monkeypatch.setattr(server, "_start_job", mock_start)

    gen_resp = client.post(f"/api/projects/{pid}/regions/{reg_id}/generate")
    assert gen_resp.status_code == 200
    assert gen_resp.get_json()["region_id"] == reg_id
    assert captured["region_id"] == reg_id
    assert captured["seed"] == 999
    assert captured["roofs"] is False

    # Check status transitioned to generating
    r_check = client.get(f"/api/projects/{pid}/regions/{reg_id}")
    assert r_check.get_json()["region"]["status"] == "generating"
