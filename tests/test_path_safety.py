import pytest
from werkzeug.exceptions import BadRequest

from terragen import server
from terragen.site import build_static


def test_site_path_rejects_root(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "SITES_DIR", tmp_path.resolve())
    with pytest.raises(BadRequest):
        server.site_path(".")
    assert server.site_path("oras") == tmp_path.resolve() / "oras"


def test_static_export_rejects_source_as_destination(tmp_path):
    source = tmp_path / "oras"
    source.mkdir()
    manifest = source / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="suprapună"):
        build_static(source, tmp_path)
    assert manifest.read_text(encoding="utf-8") == "{}"


def test_static_export_rejects_destination_inside_source(tmp_path):
    source = tmp_path / "oras"
    source.mkdir()
    manifest = source / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="suprapună"):
        build_static(source, source / "export")
    assert manifest.exists()
