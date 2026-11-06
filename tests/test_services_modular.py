import io
import json
import zipfile
from pathlib import Path
import pytest

from terragen.services.elevation_service import ElevationService
from terragen.services.map_preview_service import MapPreviewService
from terragen.services.pbf_service import PbfDownloadService, detect_pbf, osm_reader_name
from terragen.services.site_service import SiteService, safe_site_name


def test_site_service_operations(tmp_path):
    sites_dir = tmp_path / "sites"
    sites_dir.mkdir()
    svc = SiteService(sites_dir=sites_dir)

    # Safe site name
    assert safe_site_name("Târgu Mureș 2026") == "târgu-mureș-2026"
    assert safe_site_name("../evil/path") == "..evilpath"

    # Site path protection rejects path traversal and root reference
    assert svc.site_path("oras-nou") == sites_dir / "oras-nou"
    with pytest.raises(ValueError):
        svc.site_path(".")
    with pytest.raises(ValueError):
        svc.site_path("..")

    # Create dummy site
    site = sites_dir / "test-city"
    site.mkdir()
    (site / "manifest.json").write_text(
        json.dumps({"stats": {"buildings": 10}, "layers_triangles": {"buildings": 100}, "area": {"size_m": 1200}}),
        encoding="utf-8",
    )
    (site / "mesh.txt").write_text("dummy mesh data", encoding="utf-8")

    # List sites
    listed = svc.list_sites()
    assert len(listed) == 1
    assert listed[0]["name"] == "test-city"
    assert listed[0]["stats"]["buildings"] == 10

    # Create zip
    zpath = svc.create_zip("test-city")
    assert zpath.exists()
    with zipfile.ZipFile(zpath, "r") as z:
        assert "test-city/mesh.txt" in z.namelist()

    # Delete site
    assert svc.delete_site("test-city") is True
    assert not site.exists()
    assert svc.delete_site("non-existent") is False


def test_elevation_service(monkeypatch):
    from conftest import FakeDem
    import terragen.services.elevation_service
    monkeypatch.setattr(terragen.services.elevation_service, "Dem", lambda *a, **k: FakeDem())

    svc = ElevationService()
    assert svc.get_elevations([]) == []

    elevs = svc.get_elevations([[46.5446, 24.5612], [46.5450, 24.5620]])
    assert len(elevs) == 2
    assert all(isinstance(v, (int, float)) for v in elevs)


def test_pbf_service_status():
    svc = PbfDownloadService()
    st = svc.get_status()
    assert st["status"] == "idle"
    assert st["done_mb"] == 0.0
    assert osm_reader_name() in ("pyosmium", "osmium-tool", "pbf-intern (Python pur)")


def test_map_preview_service(tmp_path, monkeypatch):
    import terragen.services.map_preview_service
    from terragen.osmdata import Way

    svc = MapPreviewService()
    pbf_file = tmp_path / "mock.osm.pbf"
    pbf_file.write_bytes(b"data")

    ring = [(24.50, 46.50), (24.51, 46.50), (24.51, 46.51), (24.50, 46.50)]
    monkeypatch.setattr(
        terragen.services.map_preview_service,
        "read_osm",
        lambda path, bbox: [
            Way(id=1, tags={"building": "residential", "height": "15"}, coords=ring)
        ],
    )

    data, err, status = svc.get_building_features(pbf_file, 46.50, 24.50)
    assert status == 200
    assert err is None
    assert len(data["features"]) == 1
    assert data["features"][0]["properties"]["height"] == 15.0
