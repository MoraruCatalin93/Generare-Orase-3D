"""Unit tests for Region persistence, ProjectManager integration, and lock integrity."""

from __future__ import annotations

import pytest

from terragen.projects import ProjectManager
from terragen.projects.model import Region, RegionType


def test_add_region_and_reload(tmp_path):
    mgr = ProjectManager(tmp_path)
    proj = mgr.create_project("Region Persist Test", center=(46.5446, 24.5612), size=1200.0)

    poly = [
        [24.558, 46.542],
        [24.565, 46.542],
        [24.565, 46.548],
        [24.558, 46.548],
        [24.558, 46.542],
    ]
    hole = [
        [24.560, 46.544],
        [24.563, 46.544],
        [24.563, 46.546],
        [24.560, 46.546],
        [24.560, 46.544],
    ]
    new_reg = mgr.add_region(
        proj.id,
        {
            "id": "reg_center_plaza",
            "name": "Center Plaza",
            "region_type": RegionType.URBAN.value,
            "polygon": poly,
            "holes": [hole],
            "style_preset": "medieval_town",
            "seed": 1024,
            "generation": {"detail_level": "high", "roofs": True, "road_width_scale": 1.2},
            "tags": {"historic": True, "priority": 1},
        },
    )

    assert new_reg.id == "reg_center_plaza"
    assert new_reg.geographic["area_m2"] > 0
    assert len(new_reg.holes) == 1

    # Reopen project from disk
    reopened = mgr.open_project(proj.id)
    assert len(reopened.regions) == 1
    r = reopened.regions[0]
    assert r.id == "reg_center_plaza"
    assert r.name == "Center Plaza"
    assert r.region_type == RegionType.URBAN.value
    assert r.style_preset == "medieval_town"
    assert r.seed == 1024
    assert r.generation["detail_level"] == "high"
    assert r.generation["road_width_scale"] == 1.2
    assert r.tags["historic"] is True
    assert len(r.holes) == 1
    assert r.holes[0][0] == [24.560, 46.544]
    assert r.polygon[0] == [24.558, 46.542]


def test_update_region_and_lock_protection(tmp_path):
    mgr = ProjectManager(tmp_path)
    proj = mgr.create_project("Region Lock Test", center=(46.5446, 24.5612), size=1200.0)

    poly = [
        [24.558, 46.542],
        [24.565, 46.542],
        [24.565, 46.548],
        [24.558, 46.548],
        [24.558, 46.542],
    ]
    reg = mgr.add_region(proj.id, {"name": "Protected Zone", "polygon": poly, "locked": True})
    assert reg.locked is True

    # Attempting to change geometry of locked region must raise ValueError
    new_poly = [
        [24.550, 46.540],
        [24.560, 46.540],
        [24.560, 46.550],
        [24.550, 46.550],
        [24.550, 46.540],
    ]
    with pytest.raises(ValueError, match="is locked"):
        mgr.update_region(proj.id, reg.id, {"polygon": new_poly})

    # Non-geometric property updates (e.g. name, color, seed) are permitted
    updated = mgr.update_region(proj.id, reg.id, {"name": "Renamed Protected Zone", "seed": 99})
    assert updated.name == "Renamed Protected Zone"
    assert updated.seed == 99

    # Unlocking enables geometry modification
    mgr.update_region(proj.id, reg.id, {"locked": False})
    unlocked_update = mgr.update_region(proj.id, reg.id, {"polygon": new_poly})
    assert unlocked_update.polygon == new_poly


def test_split_and_merge_in_project_manager(tmp_path):
    mgr = ProjectManager(tmp_path)
    proj = mgr.create_project("Region Split Merge Test", center=(46.5446, 24.5612), size=1200.0)

    poly = [
        [24.550, 46.540],
        [24.570, 46.540],
        [24.570, 46.560],
        [24.550, 46.560],
        [24.550, 46.540],
    ]
    reg = mgr.add_region(proj.id, {"name": "Large Sector", "polygon": poly})

    # Split sector
    line = [[24.560, 46.535], [24.560, 46.565]]
    parts = mgr.split_region(proj.id, reg.id, line)
    assert len(parts) == 2

    # Verify project has the 2 sub-regions
    reopened = mgr.open_project(proj.id)
    assert len(reopened.regions) == 2
    r_ids = [r.id for r in reopened.regions]

    # Merge them back
    merged = mgr.merge_regions(proj.id, r_ids, merged_name="Reunited Sector")
    assert merged.name == "Reunited Sector"

    final_proj = mgr.open_project(proj.id)
    assert len(final_proj.regions) == 1
    assert final_proj.regions[0].id == merged.id


def test_delete_region(tmp_path):
    mgr = ProjectManager(tmp_path)
    proj = mgr.create_project("Region Delete Test", center=(46.5446, 24.5612), size=1200.0)

    poly = [[24.55, 46.54], [24.56, 46.54], [24.56, 46.55], [24.55, 46.55], [24.55, 46.54]]
    r = mgr.add_region(proj.id, {"name": "To Delete", "polygon": poly})

    assert len(mgr.get_regions(proj.id)) == 1
    mgr.delete_region(proj.id, r.id)
    assert len(mgr.get_regions(proj.id)) == 0

    with pytest.raises(KeyError):
        mgr.delete_region(proj.id, "non_existent_id")

