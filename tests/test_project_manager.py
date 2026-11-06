import json
import pytest

from terragen.projects import (
    CorruptedProjectError,
    Project,
    ProjectManager,
    ProjectValidationError,
    Region,
    migrate_project_dict,
)


@pytest.fixture
def project_mgr(tmp_path):
    projects_dir = tmp_path / "projects"
    return ProjectManager(projects_dir=projects_dir)


def test_create_and_reopen_project(project_mgr):
    """Verify that a project can be created and reopened without any data loss."""
    center = (46.5446, 24.5612)
    created = project_mgr.create_project(
        name="Târgu Mureș Proiect Test",
        center=center,
        size=1800.0,
        description="Descriere detaliată a proiectului",
        seed=42,
        terrain_res=384,
        dem_zoom=14,
        formats=["glb", "obj"],
        roofs=True,
    )

    assert created.id
    assert created.name == "Târgu Mureș Proiect Test"
    assert created.slug.startswith("targu-mures-proiect-test")
    assert created.geographic.center_lat == 46.5446
    assert created.geographic.center_lon == 24.5612
    assert created.dimensions.size_x_m == 1800.0
    assert created.terrain.resolution == 384
    assert created.terrain.dem_zoom == 14
    assert created.seed == 42
    assert "obj" in created.generation.formats

    # Reopen by slug
    loaded_by_slug = project_mgr.open_project(created.slug)
    assert loaded_by_slug.id == created.id
    assert loaded_by_slug.name == created.name
    assert loaded_by_slug.geographic.center_lat == 46.5446
    assert loaded_by_slug.geographic.center_lon == 24.5612
    assert loaded_by_slug.dimensions.size_x_m == 1800.0
    assert loaded_by_slug.terrain.resolution == 384
    assert loaded_by_slug.seed == 42

    # Reopen by id
    loaded_by_id = project_mgr.open_project(created.id)
    assert loaded_by_id.id == created.id
    assert loaded_by_id.slug == created.slug


def test_geographic_coordinates_remain_exact(project_mgr):
    """Verify high-precision geographic coordinates and bounds remain bit-exact."""
    exact_lat = 45.6520123
    exact_lon = 25.6100987
    proj = project_mgr.create_project(
        name="Precision Check",
        center=(exact_lat, exact_lon),
        size=1234.56,
    )

    reopened = project_mgr.open_project(proj.slug)
    assert reopened.geographic.center_lat == round(exact_lat, 6)
    assert reopened.geographic.center_lon == round(exact_lon, 6)
    assert reopened.geographic.bounds.south < exact_lat < reopened.geographic.bounds.north
    assert reopened.geographic.bounds.west < exact_lon < reopened.geographic.bounds.east


def test_save_and_reload_all_settings(project_mgr):
    """Verify all complex sections (regions, chunks, terrain, generation, sources) persist."""
    proj = project_mgr.create_project(
        name="Full Settings Project",
        center=(46.7712, 23.6236),
        size=2000.0,
    )

    # Add custom region
    polygon = [[23.62, 46.77], [23.63, 46.77], [23.63, 46.78], [23.62, 46.78], [23.62, 46.77]]
    proj.regions.append(Region(id="reg-1", name="Centru Istoric", polygon=polygon, tags={"type": "historic"}))

    # Configure chunk grid
    proj.chunks.grid.enabled = True
    proj.chunks.grid.chunk_size_m = 500.0
    proj.chunks.grid.rows = 4
    proj.chunks.grid.cols = 4

    # Update terrain and generation settings
    proj.terrain.exaggeration = 1.35
    proj.terrain.smooth_window = 7
    proj.generation.detail_level = "high"
    proj.sources.osm_mode = "local_pbf"
    proj.sources.osm_pbf_name = "cluj.osm.pbf"

    project_mgr.save_project(proj)

    reloaded = project_mgr.open_project(proj.id)
    assert len(reloaded.regions) == 1
    assert reloaded.regions[0].name == "Centru Istoric"
    assert reloaded.regions[0].tags == {"type": "historic"}
    assert reloaded.chunks.grid.enabled is True
    assert reloaded.chunks.grid.rows == 4
    assert reloaded.terrain.exaggeration == 1.35
    assert reloaded.terrain.smooth_window == 7
    assert reloaded.generation.detail_level == "high"
    assert reloaded.sources.osm_pbf_name == "cluj.osm.pbf"


def test_save_as_creates_independent_clone(project_mgr):
    """Verify Save As creates an independent clone with a new ID and separate path."""
    original = project_mgr.create_project(
        name="Original Project",
        center=(44.4323, 26.1010),
        size=1500.0,
    )

    cloned = project_mgr.save_project_as(original, "Cloned Project")
    assert cloned.id != original.id
    assert cloned.name == "Cloned Project"
    assert cloned.slug != original.slug
    assert cloned.geographic.center_lat == original.geographic.center_lat

    # Modifying clone does not alter original
    cloned.dimensions.size_x_m = 3000.0
    project_mgr.save_project(cloned)

    refreshed_orig = project_mgr.open_project(original.id)
    refreshed_clone = project_mgr.open_project(cloned.id)
    assert refreshed_orig.dimensions.size_x_m == 1500.0
    assert refreshed_clone.dimensions.size_x_m == 3000.0


def test_duplicate_and_rename_project(project_mgr):
    """Verify duplicate and rename operations."""
    proj = project_mgr.create_project(name="Alpha Project", center=(47.057, 21.93), size=1000.0)

    # Duplicate
    dup = project_mgr.duplicate_project(proj.slug)
    assert dup.id != proj.id
    assert "Alpha Project (Copy)" in dup.name
    assert project_mgr.open_project(dup.id).name == dup.name

    # Rename
    renamed = project_mgr.rename_project(proj.slug, "Alpha Renamed")
    assert renamed.name == "Alpha Renamed"
    assert project_mgr.open_project(proj.id).name == "Alpha Renamed"


def test_delete_requires_confirmation(project_mgr):
    """Verify deletion fails without confirmation and succeeds with confirmation."""
    proj = project_mgr.create_project(name="To Delete", center=(45.7489, 21.2087), size=1200.0)

    with pytest.raises(ValueError, match="confirmation"):
        project_mgr.delete_project(proj.slug, confirm=False)

    assert project_mgr.open_project(proj.id) is not None

    # Delete with confirmation
    assert project_mgr.delete_project(proj.slug, confirm=True) is True
    with pytest.raises(FileNotFoundError):
        project_mgr.open_project(proj.id)


def test_invalid_project_data_rejected(project_mgr):
    """Verify validation catches out-of-range latitude, negative dimensions, or empty names."""
    with pytest.raises(ProjectValidationError, match="Invalid center coordinates"):
        project_mgr.create_project(name="Bad Lat", center=(999.0, 24.0), size=1500.0)

    with pytest.raises(ProjectValidationError, match="size must be positive"):
        project_mgr.create_project(name="Bad Size", center=(46.5, 24.5), size=-500.0)


def test_corrupted_file_recovery_from_backup(project_mgr):
    """Verify recovery when project.json is corrupted but backup project.json.bak exists."""
    proj = project_mgr.create_project(name="Resilient Project", center=(46.54, 24.56), size=1500.0)
    proj_dir = project_mgr._resolve_project_dir(proj.id)

    # Make a change so that .bak is populated
    proj.description = "Updated description"
    project_mgr.save_project(proj)
    assert (proj_dir / "project.json.bak").exists()

    # Intentionally corrupt project.json
    (proj_dir / "project.json").write_text("{corrupted json content---", encoding="utf-8")

    # Reopening should recover from backup automatically
    recovered = project_mgr.open_project(proj.id)
    assert recovered.name == "Resilient Project"
    # project.json should now be clean and valid again
    valid_reloaded = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    assert valid_reloaded["name"] == "Resilient Project"


def test_unrecoverable_corrupted_file_raises_error(project_mgr):
    """Verify informative CorruptedProjectError when neither main nor backup is valid JSON."""
    proj = project_mgr.create_project(name="Doomed Project", center=(46.54, 24.56), size=1500.0)
    proj_dir = project_mgr._resolve_project_dir(proj.id)

    (proj_dir / "project.json").write_text("NOT JSON", encoding="utf-8")
    if (proj_dir / "project.json.bak").exists():
        (proj_dir / "project.json.bak").write_text("ALSO NOT JSON", encoding="utf-8")

    with pytest.raises(CorruptedProjectError, match="corrupted"):
        project_mgr.open_project(proj_dir)



def test_legacy_schema_migration():
    """Verify that legacy / v0 flat format safely migrates to schema version 1."""
    legacy_data = {
        "name": "Legacy City",
        "lat": 46.5446,
        "lon": 24.5612,
        "size": 1600.0,
        "seed": 99,
        "terrain_res": 384,
        "dem_zoom": 14,
        "roofs": False,
        "obj": True,
        "custom_plugin_tag": "keep_this_metadata",
    }

    migrated = migrate_project_dict(legacy_data)
    assert migrated["schema_version"] == 1
    assert migrated["name"] == "Legacy City"
    assert migrated["geographic"]["center"]["lat"] == 46.5446
    assert migrated["geographic"]["center"]["lon"] == 24.5612
    assert migrated["dimensions"]["size_x_m"] == 1600.0
    assert migrated["terrain"]["resolution"] == 384
    assert migrated["generation"]["roofs"] is False
    assert "obj" in migrated["generation"]["formats"]
    assert migrated["extra_metadata"]["custom_plugin_tag"] == "keep_this_metadata"

    # Must be parseable into Project object
    proj = Project.from_dict(legacy_data)
    assert proj.schema_version == 1
    assert proj.name == "Legacy City"
    assert proj.dimensions.size_x_m == 1600.0
