import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from terragen.blender_export import (
    BlenderExportOptions,
    BlenderExportPipeline,
    validate_glb_file,
    write_multi_mesh_glb,
)
from terragen.export import write_glb
from terragen.geom import Mesh
from terragen.projects.manager import ProjectManager
from tests.conftest import FakeDem, LAT0, LON0


@pytest.fixture
def blender_project(tmp_path):
    pm = ProjectManager(projects_dir=tmp_path / "projects")
    p = pm.create_project("Blender_Alpha_City", (LAT0, LON0), 500.0)
    # Add a region
    pm.add_region(
        p.id,
        {
            "name": "Historic Center",
            "region_type": "urban",
            "polygon": [
                [LON0 - 0.001, LAT0 - 0.001],
                [LON0 + 0.001, LAT0 - 0.001],
                [LON0 + 0.001, LAT0 + 0.001],
                [LON0 - 0.001, LAT0 + 0.001],
                [LON0 - 0.001, LAT0 - 0.001],
            ],
            "style_preset": "european_town",
            "color": "#e74c3c",
        },
    )
    return pm, p


def test_validate_glb_file_valid(tmp_path):
    m = Mesh()
    m.add(
        np.array([[0, 0, 0], [10, 0, 0], [0, 10, 0]], dtype=float),
        np.array([[0, 1, 2]], dtype=int),
        np.array([[200, 100, 50, 255]], dtype=np.uint8),
    )
    path = tmp_path / "sample.glb"
    write_glb(m, path, name="TestTriangle", material_name="mat_sample")

    report = validate_glb_file(path)
    assert report["valid"] is True
    assert report["version"] == 2
    assert len(report["nodes"]) >= 1
    assert report["meshes_count"] >= 1
    assert len(report["materials"]) >= 1


def test_validate_glb_file_corrupt(tmp_path):
    # Corrupt magic
    p1 = tmp_path / "corrupt_magic.glb"
    p1.write_bytes(b"BADF" + b"\x00" * 30)
    with pytest.raises(ValueError, match="Invalid GLB magic"):
        validate_glb_file(p1)

    # Truncated file
    p2 = tmp_path / "truncated.glb"
    p2.write_bytes(b"glTF\x02\x00\x00\x00")
    with pytest.raises(ValueError):
        validate_glb_file(p2)

    # Nonexistent file
    with pytest.raises(FileNotFoundError):
        validate_glb_file(tmp_path / "nonexistent.glb")


def test_write_multi_mesh_glb(tmp_path):
    m1 = Mesh()
    m1.add(
        np.array([[0, 0, 0], [5, 0, 0], [0, 5, 0]], dtype=float),
        np.array([[0, 1, 2]], dtype=int),
        np.array([[100, 150, 200, 255]], dtype=np.uint8),
    )
    m2 = Mesh()
    m2.add(
        np.array([[10, 0, 0], [15, 0, 0], [10, 5, 0]], dtype=float),
        np.array([[0, 1, 2]], dtype=int),
        np.array([[50, 100, 150, 255]], dtype=np.uint8),
    )

    layers = [
        ("terrain", m1, "mat_terrain"),
        ("roads", m2, "mat_roads"),
    ]
    out_glb = tmp_path / "multi.glb"
    count = write_multi_mesh_glb(layers, out_glb, name_prefix="TestMulti")
    assert count > 0
    assert out_glb.exists()

    report = validate_glb_file(out_glb)
    assert report["valid"] is True
    assert report["meshes_count"] == 2
    assert len(report["materials"]) == 2
    assert len(report["nodes"]) == 2


def test_blender_export_layer_mode(blender_project, tmp_path, monkeypatch):
    import terragen.cli as cli
    monkeypatch.setattr(cli, "Dem", lambda *a, **k: FakeDem())

    pm, p = blender_project
    out_dir = tmp_path / "blender_layer_out"

    opts = BlenderExportOptions(
        mode="layer",
        formats=("glb",),
        include_heightmap=True,
        include_regions_geojson=True,
    )

    res = pm.export_blender(p.id, options=opts, out_dir=out_dir)
    assert res["status"] == "success"
    assert res["pre_validation"]["valid"] is True
    assert res["post_validation"]["valid"] is True

    manifest_file = Path(res["manifest_file"])
    assert manifest_file.exists()
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))

    # Validate manifest structure
    assert manifest["project_name"] == "Blender_Alpha_City"
    assert manifest["export_mode"] == "layer"
    assert manifest["coordinates"]["world_origin"]["lat"] == LAT0
    assert manifest["coordinates"]["world_origin"]["lon"] == LON0
    assert "meters" in manifest["coordinates"]["units"]
    assert manifest["coordinates"]["blender_unit_scale"] == 1.0

    # Check exported files
    layers_dir = out_dir / "layers"
    assert layers_dir.exists()
    assert (layers_dir / "terrain.glb").exists()
    assert (layers_dir / "vegetation.glb").exists()

    # Heightmap check
    hm_dir = out_dir / "heightmap"
    assert (hm_dir / "terrain_heightmap_16bit.png").exists()
    assert (hm_dir / "terrain_heightmap.json").exists()
    meta = json.loads((hm_dir / "terrain_heightmap.json").read_text())
    assert meta["resolution"] > 0
    assert (hm_dir / meta["raw_file"]).exists()

    # GeoJSON check
    geojson_file = out_dir / "regions" / "regions.geojson"
    assert geojson_file.exists()
    geo_data = json.loads(geojson_file.read_text())
    assert geo_data["type"] == "FeatureCollection"
    assert len(geo_data["features"]) >= 1
    assert geo_data["features"][0]["properties"]["name"] == "Historic Center"

    # Importer script & Readme check
    assert (out_dir / "import_to_blender.py").exists()
    assert (out_dir / "README_BLENDER.md").exists()


def test_blender_export_chunk_mode(blender_project, tmp_path, monkeypatch):
    import terragen.cli as cli
    monkeypatch.setattr(cli, "Dem", lambda *a, **k: FakeDem())

    pm, p = blender_project
    pm.init_chunk_grid(p.id, chunk_size_m=250.0)

    out_dir = tmp_path / "blender_chunk_out"
    opts = BlenderExportOptions(
        mode="chunk",
        include_heightmap=False,
    )

    res = pm.export_blender(p.id, options=opts, out_dir=out_dir)
    assert res["status"] == "success"
    assert res["post_validation"]["valid"] is True

    manifest = json.loads(Path(res["manifest_file"]).read_text())
    assert manifest["export_mode"] == "chunk"
    assert len(manifest["chunks"]) == 4

    chunks_dir = out_dir / "chunks"
    assert chunks_dir.exists()
    for ch in manifest["chunks"]:
        ch_dir = chunks_dir / ch["id"]
        assert ch_dir.exists()
        assert (ch_dir / "chunk.glb").exists()


def test_blender_export_selective_filters(blender_project, tmp_path, monkeypatch):
    import terragen.cli as cli
    monkeypatch.setattr(cli, "Dem", lambda *a, **k: FakeDem())

    pm, p = blender_project
    pm.init_chunk_grid(p.id, chunk_size_m=250.0)

    out_dir = tmp_path / "blender_selective_out"
    opts = BlenderExportOptions(
        mode="both",
        selected_layers=["terrain"],
        selected_chunks=["chk_r00_c00"],
    )

    res = pm.export_blender(p.id, options=opts, out_dir=out_dir)
    assert res["status"] == "success"
    manifest = json.loads(Path(res["manifest_file"]).read_text())

    # Only terrain layer exported
    layer_names = [l["name"] for l in manifest.get("layers", [])]
    assert "terrain" in layer_names
    assert "vegetation" not in layer_names

    # Only chunk_0_0 exported
    assert len(manifest.get("chunks", [])) == 1
    assert manifest["chunks"][0]["id"] == "chk_r00_c00"


@pytest.fixture
def client_with_chunk_project(tmp_path):
    from terragen.server import app
    import terragen.server as srv
    pm = ProjectManager(projects_dir=tmp_path / "projects")
    srv.project_manager = pm
    p = pm.create_project("ChunkBlenderProject", (LAT0, LON0), 1024.0)
    with app.test_client() as client:
        yield client, p.id


def test_api_blender_export_endpoint(client_with_chunk_project):
    client, pid = client_with_chunk_project
    client.post(f"/api/projects/{pid}/chunks/grid", json={"chunk_size_m": 512.0})

    resp = client.post(
        f"/api/projects/{pid}/export/blender",
        json={
            "mode": "layer",
            "formats": ["glb"],
            "include_heightmap": True,
            "include_regions_geojson": True,
            "terrain_res": 64,
        },
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["status"] == "success"
    assert Path(data["manifest_path"]).exists()
    assert Path(data["script_path"]).exists()
    assert data["post_validation"]["valid"] is True


def test_blender_headless_importer_execution(blender_project, tmp_path, monkeypatch):
    """Executes the generated import script in actual background Blender instance."""
    blender_path = Path("/Applications/Blender.app/Contents/MacOS/Blender")
    if not blender_path.exists():
        pytest.skip("Blender binary not found at /Applications/Blender.app/Contents/MacOS/Blender")

    import terragen.cli as cli
    monkeypatch.setattr(cli, "Dem", lambda *a, **k: FakeDem())

    pm, p = blender_project
    out_dir = tmp_path / "blender_headless_test"
    opts = BlenderExportOptions(
        mode="layer",
        include_heightmap=True,
        include_regions_geojson=True,
    )

    res = pm.export_blender(p.id, options=opts, out_dir=out_dir)
    script_path = res["script_file"]

    # Run Blender headlessly with import_to_blender.py
    cmd = [
        str(blender_path),
        "--background",
        "--python",
        str(script_path),
        "--",
        "--mode",
        "layer",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0, f"Blender returned error:\n{proc.stderr}\n{proc.stdout}"
    assert "Terragen Import Complete" in proc.stdout
    assert "Terragen_Blender_Alpha_City" in proc.stdout
