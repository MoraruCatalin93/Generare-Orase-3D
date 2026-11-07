"""Integration tests for Game World Editor API endpoints added in Etapa 5."""

from pathlib import Path
import pytest
from terragen.server import app, project_manager


@pytest.fixture
def client_and_pid(tmp_path):
    app.config["TESTING"] = True
    orig_root = project_manager.root
    project_manager.root = tmp_path
    client = app.test_client()

    create_payload = {
        "name": "Editor Test Project",
        "center": [46.5446, 24.5612],
        "size": 2000.0,
    }
    resp = client.post("/api/projects", json=create_payload)
    assert resp.status_code == 201
    pid = resp.get_json()["project"]["id"]

    yield client, pid
    project_manager.root = orig_root


def test_game_world_editor_endpoints(client_and_pid):
    client, pid = client_and_pid

    # 1. Test GET /api/projects/<pid>/world-state
    state_resp = client.get(f"/api/projects/{pid}/world-state")
    assert state_resp.status_code == 200
    state = state_resp.get_json()
    assert state["project_id"] == pid
    assert "style_preset" in state
    assert state["style_preset"] == "stylized_realistic"
    assert "chunks" in state
    assert state["chunks_count"] > 0
    assert "dimensions" in state

    # 2. Test POST /api/projects/{pid}/world-settings
    settings_resp = client.post(
        f"/api/projects/{pid}/world-settings",
        json={
            "style_preset": "dark_fantasy",
            "dressing_density": 1.75,
        },
    )
    assert settings_resp.status_code == 200
    updated = settings_resp.get_json()
    assert updated["ok"] is True
    assert updated["style_preset"] == "dark_fantasy"
    assert updated["dressing_density"] == 1.75

    # Verify persistence via GET
    state_resp2 = client.get(f"/api/projects/{pid}/world-state")
    assert state_resp2.get_json()["style_preset"] == "dark_fantasy"
    assert state_resp2.get_json()["dressing_density"] == 1.75

    # 3. Test safe file serving GET /api/projects/{pid}/files/<path:f>
    project_dir = project_manager._resolve_project_dir(pid)
    sample_file = project_dir / "test_asset.txt"
    sample_file.write_text("Hello Game World Editor!", encoding="utf-8")

    file_resp = client.get(f"/api/projects/{pid}/files/test_asset.txt")
    assert file_resp.status_code == 200
    assert file_resp.data == b"Hello Game World Editor!"

    # Path traversal safety check
    traversal_resp = client.get(f"/api/projects/{pid}/files/../../etc/passwd")
    assert traversal_resp.status_code in (400, 404)
