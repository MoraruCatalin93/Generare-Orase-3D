"""Unit tests for Region geometry engine: validation, metrics, split, merge, and conflicts."""

from __future__ import annotations

import pytest
from shapely.geometry import Polygon

from terragen.projects.model import Region, RegionType
from terragen.projects.regions import (
    DEFAULT_REGION_COLORS,
    calculate_region_metrics,
    detect_overlaps,
    duplicate_region,
    extract_shared_boundaries,
    merge_regions,
    sanitize_ring_coords,
    split_region,
    to_shapely_polygon,
)


def test_sanitize_ring_coords_validates_and_closes():
    # 3 points unclosed
    ring = [[24.56, 46.54], [24.57, 46.54], [24.57, 46.55]]
    clean = sanitize_ring_coords(ring)
    assert len(clean) == 4
    assert clean[0] == clean[-1]

    # Rejects < 3 points
    with pytest.raises(ValueError, match="at least 3"):
        sanitize_ring_coords([[24.56, 46.54], [24.57, 46.54]])

    # Rejects out-of-bounds coordinates
    with pytest.raises(ValueError, match="WGS84"):
        sanitize_ring_coords([[195.0, 46.54], [24.57, 46.54], [24.57, 46.55]])


def test_self_intersection_repair():
    # Self-intersecting bowtie polygon
    bowtie = [[0.0, 0.0], [1.0, 1.0], [0.0, 1.0], [1.0, 0.0], [0.0, 0.0]]
    poly = to_shapely_polygon(bowtie)
    assert poly.is_valid
    assert poly.area > 0


def test_polygon_with_holes():
    outer = [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0], [0.0, 0.0]]
    hole = [[2.0, 2.0], [4.0, 2.0], [4.0, 4.0], [2.0, 4.0], [2.0, 2.0]]
    poly = to_shapely_polygon(outer, [hole])
    assert poly.is_valid
    assert len(poly.interiors) == 1
    # 100 - 4 = 96
    assert abs(poly.area - 96.0) < 1e-4


def test_calculate_region_metrics():
    # Roughly a 1000m x 1000m square around Târgu Mureș (46.54, 24.56)
    # 1 deg lat ~= 111320m -> 0.009 deg ~= 1000m
    # 1 deg lon ~= 111320 * cos(46.54) ~= 76560m -> 0.013 deg ~= 1000m
    poly = [
        [24.56, 46.54],
        [24.573, 46.54],
        [24.573, 46.549],
        [24.56, 46.549],
        [24.56, 46.54],
    ]
    geo, proj = calculate_region_metrics(poly)
    assert geo["area_m2"] > 500_000
    assert geo["area_ha"] > 50.0
    assert geo["area_km2"] > 0.5
    assert geo["dimensions_m"]["width_m"] > 500.0
    assert geo["dimensions_m"]["height_m"] > 500.0
    assert len(proj["exterior"]) == 5


def test_split_region_by_line():
    poly = [
        [24.56, 46.54],
        [24.58, 46.54],
        [24.58, 46.56],
        [24.56, 46.56],
        [24.56, 46.54],
    ]
    region = Region(
        id="reg_test_split",
        name="District Alpha",
        region_type=RegionType.URBAN.value,
        color=DEFAULT_REGION_COLORS["urban"],
        polygon=poly,
    )

    # Cutting line across the middle from south to north (lon = 24.57)
    line = [[24.57, 46.53], [24.57, 46.57]]
    sub_regions = split_region(region, line)

    assert len(sub_regions) == 2
    assert "District Alpha (Part 1)" in [r.name for r in sub_regions]
    assert "District Alpha (Part 2)" in [r.name for r in sub_regions]
    for sr in sub_regions:
        assert sr.region_type == RegionType.URBAN.value
        assert sr.geographic["area_m2"] > 0
        assert sr.id != region.id


def test_split_region_rejects_non_intersecting_line():
    poly = [
        [24.56, 46.54],
        [24.58, 46.54],
        [24.58, 46.56],
        [24.56, 46.56],
        [24.56, 46.54],
    ]
    region = Region(id="reg_1", name="Alpha", polygon=poly)
    line = [[24.50, 46.50], [24.51, 46.50]]
    with pytest.raises(ValueError, match="does not intersect"):
        split_region(region, line)


def test_merge_regions():
    # Two adjacent polygons sharing edge at lon 24.57
    poly1 = [
        [24.56, 46.54],
        [24.57, 46.54],
        [24.57, 46.56],
        [24.56, 46.56],
        [24.56, 46.54],
    ]
    poly2 = [
        [24.57, 46.54],
        [24.58, 46.54],
        [24.58, 46.56],
        [24.57, 46.56],
        [24.57, 46.54],
    ]
    r1 = Region(id="r1", name="West", region_type="suburban", polygon=poly1)
    r2 = Region(id="r2", name="East", region_type="suburban", polygon=poly2)

    merged = merge_regions([r1, r2], merged_name="Combined Zone")
    assert merged.name == "Combined Zone"
    assert merged.geographic["area_m2"] > 0
    # Merged area should be roughly sum of r1 and r2
    g1, _ = calculate_region_metrics(poly1)
    g2, _ = calculate_region_metrics(poly2)
    assert abs(merged.geographic["area_m2"] - (g1["area_m2"] + g2["area_m2"])) < 50.0


def test_duplicate_region():
    poly = [
        [24.56, 46.54],
        [24.57, 46.54],
        [24.57, 46.55],
        [24.56, 46.55],
        [24.56, 46.54],
    ]
    orig = Region(id="r_orig", name="Industrial Hub", region_type="industrial", polygon=poly)
    clone = duplicate_region(orig, offset_m=(30.0, 30.0))

    assert clone.id != orig.id
    assert clone.name == "Industrial Hub (Copy)"
    assert clone.region_type == "industrial"
    assert clone.polygon != orig.polygon
    assert clone.geographic["area_m2"] > 0


def test_detect_overlaps_and_shared_borders():
    # r1 and r2 share border at lon 24.57 (touching only, no 2D overlap)
    poly1 = [
        [24.56, 46.54],
        [24.57, 46.54],
        [24.57, 46.56],
        [24.56, 46.56],
        [24.56, 46.54],
    ]
    poly2 = [
        [24.57, 46.54],
        [24.58, 46.54],
        [24.58, 46.56],
        [24.57, 46.56],
        [24.57, 46.54],
    ]
    # r3 overlaps r1 substantially
    poly3 = [
        [24.565, 46.545],
        [24.575, 46.545],
        [24.575, 46.555],
        [24.565, 46.555],
        [24.565, 46.545],
    ]

    r1 = Region(id="r1", name="Zone 1", polygon=poly1)
    r2 = Region(id="r2", name="Zone 2", polygon=poly2)
    r3 = Region(id="r3", name="Conflicting Zone", polygon=poly3)

    # Touching regions produce no 2D overlap conflicts
    conflicts_no_overlap = detect_overlaps([r1, r2])
    assert len(conflicts_no_overlap) == 0

    # Extract shared boundary between r1 and r2
    shared = extract_shared_boundaries(r1, r2)
    assert len(shared) == 1
    assert len(shared[0]) >= 2

    # Including r3 detects conflict with both r1 and r2
    conflicts_with_r3 = detect_overlaps([r1, r2, r3])
    assert len(conflicts_with_r3) == 2
    overlap_pairs = {(c["region_a_id"], c["region_b_id"]) for c in conflicts_with_r3}
    assert ("r1", "r3") in overlap_pairs
    assert ("r2", "r3") in overlap_pairs

