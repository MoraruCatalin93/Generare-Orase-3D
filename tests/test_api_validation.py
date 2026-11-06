import pytest

from terragen.server import app


@pytest.fixture
def client():
    return app.test_client()


@pytest.mark.parametrize("field,value", [
    ("terrain_res", "abc"), ("terrain_res", 100000), ("terrain_res", 2.5),
    ("dem_zoom", 30), ("dem_zoom", -1), ("seed", "bad"),
    ("seed", True), ("name", []), ("obj", "false"),
])
def test_generate_rejects_invalid_fields(client, field, value):
    data = {"lat": 46.54, "lon": 24.56, field: value}
    assert client.post("/api/generate", json=data).status_code == 400


@pytest.mark.parametrize("points", [
    [["x", 24]], [[float("nan"), 24]], [[90, 24]],
    [[46, 24], [47, 24]], [[46, 24]] * 9, "bad", [[46]],
])
def test_elevation_rejects_invalid_points(client, points):
    assert client.post("/api/elevation", json={"points": points}).status_code == 400


def test_empty_elevation(client):
    response = client.post("/api/elevation", json={"points": []})
    assert response.status_code == 200
    assert response.json == {"elevations": []}


def test_non_object_json(client):
    assert client.post("/api/generate", json=[1]).status_code == 400
    assert client.post("/api/elevation", json=[1]).status_code == 400
