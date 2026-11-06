from terragen import server


def test_map_buildings_reads_extended_area(monkeypatch, tmp_path):
    from terragen import osmdata
    pbf = tmp_path / "romania.osm.pbf"
    pbf.write_bytes(b"fake")
    monkeypatch.setattr(server, "detect_pbf", lambda: str(pbf))
    server._MAP_BUILDINGS_CACHE.clear()
    captured = []
    def fake_read(path, bbox):
        captured.append(bbox)
        return []
    monkeypatch.setattr(osmdata, "read_osm", fake_read)
    response = server.app.test_client().get("/api/map-buildings?lat=46.54&lon=24.56")
    assert response.status_code == 200
    south, west, north, east = captured[0]
    assert round(north - south, 2) == 0.08
    assert round(east - west, 2) == 0.12
