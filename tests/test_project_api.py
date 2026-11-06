import pytest

from terragen import server
from terragen.projects import ProjectManager


@pytest.fixture
def client(tmp_path, monkeypatch):
    projects_dir = tmp_path / "projects"
    sites_dir = tmp_path / "sites"
    projects_dir.mkdir()
    sites_dir.mkdir()
    mgr = ProjectManager(projects_dir=projects_dir)
    monkeypatch.setattr(server, "PROJECTS_DIR", projects_dir)
    monkeypatch.setattr(server, "project_manager", mgr)
    monkeypatch.setattr(server, "PROJECT_MGR", mgr)
    monkeypatch.setattr(server, "SITES_DIR", sites_dir)
    return server.app.test_client()


def test_api_projects_crud_workflow(client):
    # 1. Initially empty
    res = client.get("/api/projects")
    assert res.status_code == 200
    assert res.json["projects"] == []

    # 2. Create project
    create_payload = {
        "name": "Brașov Urban Center",
        "lat": 45.6520,
        "lon": 25.6100,
        "size": 1500.0,
        "description": "Centrul vechi al Brasovului",
        "seed": 10,
        "terrain_res": 256,
        "dem_zoom": 15,
        "roofs": True,
        "formats": ["glb", "obj"],
    }
    res = client.post("/api/projects", json=create_payload)
    assert res.status_code == 201
    created = res.json["project"]
    pid = created["id"]
    slug = created["slug"]
    assert created["name"] == "Brașov Urban Center"
    assert created["geographic"]["center"]["lat"] == 45.652
    assert created["geographic"]["center"]["lon"] == 25.61

    # 3. List contains newly created project
    res = client.get("/api/projects")
    assert len(res.json["projects"]) == 1
    assert res.json["projects"][0]["id"] == pid

    # 4. Get by ID and by slug
    res_id = client.get(f"/api/projects/{pid}")
    assert res_id.status_code == 200
    assert res_id.json["project"]["id"] == pid

    res_slug = client.get(f"/api/projects/{slug}")
    assert res_slug.status_code == 200
    assert res_slug.json["project"]["id"] == pid

    # 5. Update settings
    update_payload = {
        "description": "Descriere actualizată",
        "seed": 88,
        "terrain": {"resolution": 384},
    }
    res = client.put(f"/api/projects/{pid}", json=update_payload)
    assert res.status_code == 200
    assert res.json["project"]["description"] == "Descriere actualizată"
    assert res.json["project"]["seed"] == 88
    assert res.json["project"]["terrain"]["resolution"] == 384

    # 6. Save As
    res = client.post(f"/api/projects/{pid}/save-as", json={"name": "Brașov Extended"})
    assert res.status_code == 201
    clone = res.json["project"]
    assert clone["id"] != pid
    assert clone["name"] == "Brașov Extended"

    # 7. Duplicate
    res = client.post(f"/api/projects/{pid}/duplicate", json={})
    assert res.status_code == 201
    dup = res.json["project"]
    assert "Copy" in dup["name"]

    # 8. Rename
    res = client.post(f"/api/projects/{pid}/rename", json={"name": "Brașov Downtown"})
    assert res.status_code == 200
    assert res.json["project"]["name"] == "Brașov Downtown"

    # 9. Delete without confirmation fails
    res = client.delete(f"/api/projects/{pid}", json={})
    assert res.status_code == 400
    assert "confirmare" in res.json["error"]

    # 10. Delete with confirmation succeeds
    res = client.delete(f"/api/projects/{pid}", json={"confirm": True})
    assert res.status_code == 200
    assert res.json["ok"] is True

    # Project is gone
    res = client.get(f"/api/projects/{pid}")
    assert res.status_code == 404


def test_api_projects_generate(client, monkeypatch):
    """Verify that triggering generation from a project submits a job with project parameters."""
    res = client.post(
        "/api/projects",
        json={"name": "Sibiu Test", "lat": 45.797, "lon": 24.152, "size": 1200.0, "seed": 77},
    )
    pid = res.json["project"]["id"]

    captured_params = {}

    def fake_start_job(params):
        captured_params.update(params)
        job = server.Job(params)
        server.JOBS[job.id] = job
        return job

    monkeypatch.setattr(server, "_start_job", fake_start_job)

    res = client.post(f"/api/projects/{pid}/generate")
    assert res.status_code == 200
    assert "job" in res.json
    assert captured_params["project_id"] == pid
    assert captured_params["lat"] == 45.797
    assert captured_params["lon"] == 24.152
    assert captured_params["size"] == 1200.0
    assert captured_params["seed"] == 77

