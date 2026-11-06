from pathlib import Path
import json
import pytest

from conftest import LAT0, LON0, FakeDem

from terragen.cli import generate
from terragen.export import write_glb, write_obj, write_heightmap, build_network_graph
from terragen.projects import ProjectManager
from terragen.services.generation_service import GenerationService, Job
from terragen.services.site_service import SiteService


@pytest.fixture
def test_env(tmp_path):
    projects_dir = tmp_path / "projects"
    sites_dir = tmp_path / "sites"
    projects_dir.mkdir()
    sites_dir.mkdir()
    p_mgr = ProjectManager(projects_dir=projects_dir)
    s_srv = SiteService(sites_dir=sites_dir)
    g_srv = GenerationService(site_service=s_srv, project_manager=p_mgr)
    return {"projects": p_mgr, "sites": s_srv, "generation": g_srv, "tmp": tmp_path}


def test_project_generation_separates_data_from_assets(test_env, ways, fake_dem, monkeypatch):
    """Verify project specification is decoupled from generated 3D assets."""
    p_mgr = test_env["projects"]
    g_srv = test_env["generation"]
    s_srv = test_env["sites"]

    proj = p_mgr.create_project(
        name="Timisoara Test Project",
        center=(LAT0, LON0),
        size=500.0,
        seed=123,
    )

    # Monkeypatch Dem and generate so generation is completely offline and instant
    import terragen.cli
    import terragen.services.generation_service
    monkeypatch.setattr(terragen.cli, "Dem", lambda *a, **k: fake_dem)

    orig_generate = terragen.cli.generate
    def mock_generate(**kwargs):
        kwargs["ways"] = ways
        return orig_generate(**kwargs)

    monkeypatch.setattr(terragen.cli, "generate", mock_generate)
    monkeypatch.setattr(terragen.services.generation_service, "generate", mock_generate)

    job_params = {
        "lat": proj.geographic.center_lat,
        "lon": proj.geographic.center_lon,
        "size": proj.dimensions.size_x_m,
        "name": proj.slug,
        "pbf": None,
        "overpass": False,
        "terrain_res": 64,
        "dem_zoom": 14,
        "formats": ["glb", "obj"],
        "roofs": True,
        "seed": proj.seed,
        "project_id": proj.id,
    }
    job = Job(job_params)

    manifest = g_srv._generate_staged(job, job_params)
    assert manifest is not None

    # 1. Verify project file remained clean JSON and was NOT polluted by mesh blobs
    reloaded_proj = p_mgr.open_project(proj.id)
    proj_file = p_mgr._resolve_project_dir(proj.id) / "project.json"
    assert proj_file.stat().st_size < 15000  # Project metadata is lightweight

    # 2. Verify assets are placed cleanly in the site directory
    site_dir = s_srv.site_path(proj.slug)
    assert (site_dir / "manifest.json").exists()
    assert (site_dir / "glb" / "terrain.glb").exists()
    assert (site_dir / "glb" / "buildings.glb").exists()
    assert (site_dir / "obj" / "buildings.obj").exists()
    assert (site_dir / "data" / "roads_network.json").exists()

    # 3. Verify project records export history and asset linkage
    assert reloaded_proj.assets.build_dir == str(site_dir)
    assert reloaded_proj.assets.manifest_path == str(site_dir / "manifest.json")
    assert len(reloaded_proj.export_history) == 1
    exp = reloaded_proj.export_history[0]
    assert exp.status == "success"
    assert "glb" in exp.format


def test_legacy_direct_generation_remains_functional(tmp_path, ways, fake_dem, monkeypatch):
    """Verify that original cli.generate without projects works identically."""
    import terragen.cli
    monkeypatch.setattr(terragen.cli, "Dem", lambda *a, **k: fake_dem)

    out_dir = tmp_path / "legacy_output"
    manifest = generate(
        center=(LAT0, LON0),
        size=400.0,
        out=str(out_dir),
        terrain_res=64,
        formats=("glb", "obj"),
        ways=ways,
    )

    assert manifest["area"]["center_lat"] == LAT0
    assert (out_dir / "manifest.json").exists()
    assert (out_dir / "glb" / "buildings.glb").exists()
    assert (out_dir / "obj" / "buildings.obj").exists()
    assert (out_dir / "data" / "roads_network.json").exists()
    assert (out_dir / "terrain" / "terrain_heightmap_65.r16").exists()
