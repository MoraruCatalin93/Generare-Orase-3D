from terragen import server
from terragen.osmdata import Way


def test_buildings_endpoint_with_local_pbf(monkeypatch, tmp_path):
    from terragen import osmdata
    pbf = tmp_path / "romania.osm.pbf"
    pbf.write_bytes(b"fake")
    monkeypatch.setattr(server, "detect_pbf", lambda: str(pbf))
    server._MAP_BUILDINGS_CACHE.clear()
    ring = [(24.5, 46.5), (24.51, 46.5), (24.51, 46.51), (24.5, 46.5)]
    monkeypatch.setattr(osmdata, "read_osm", lambda path, bbox: [
        Way(id=1, tags={"building": "yes", "height": "12"}, coords=ring),
        Way(id=2, tags={"highway": "residential"}, coords=ring),
        Way(id=3, tags={"building": "yes"}, coords=ring[:-1]),
    ])
    response = server.app.test_client().get("/api/map-buildings?lat=46.5&lon=24.5")
    assert response.status_code == 200
    assert len(response.json["features"]) == 1
    assert response.json["features"][0]["properties"]["height"] == 12


def test_buildings_endpoint_validates_coordinates():
    response = server.app.test_client().get("/api/map-buildings?lat=nan&lon=24")
    assert response.status_code == 400


def test_buildings_endpoint_without_pbf(monkeypatch):
    monkeypatch.setattr(server, "detect_pbf", lambda: None)
    response = server.app.test_client().get("/api/map-buildings?lat=46.5&lon=24.5")
    assert response.status_code == 404
