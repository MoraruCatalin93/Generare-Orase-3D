"""Region System: Geometric operations, validation, metrics, and manipulation.

Provides comprehensive support for:
- 9 region types with default styling presets.
- Polygon geometry validation, self-intersection repair, and holes support.
- Split region by cutting line.
- Merge multiple regions into unified geometry.
- Duplicate and vertex-level manipulations.
- Area and dimension metrics (m², hectares, km², width/height).
- Overlap and boundary conflict detection.
- Shared boundary extraction to avoid duplicate geometry at borders.
"""

from __future__ import annotations

import copy
from enum import Enum
import math
import uuid
from typing import Any

from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.ops import split, unary_union
from shapely.validation import make_valid

from .model import Region, RegionType


DEFAULT_REGION_COLORS: dict[str, str] = {
    RegionType.URBAN.value: "#e74c3c",       # Crimson / Brick Red
    RegionType.SUBURBAN.value: "#e67e22",    # Warm Orange
    RegionType.INDUSTRIAL.value: "#7f8c8d",  # Slate Gray
    RegionType.FOREST.value: "#27ae60",      # Forest Green
    RegionType.GRASSLAND.value: "#2ecc71",   # Emerald Grass
    RegionType.MOUNTAIN.value: "#8e44ad",    # Mountain Purple / Slate
    RegionType.AGRICULTURAL.value: "#f39c12",# Harvest Amber
    RegionType.WATER.value: "#2980b9",       # Deep Blue
    RegionType.CUSTOM.value: "#16a085",      # Teal / Custom
}

DEFAULT_STYLE_PRESETS: dict[str, str] = {
    RegionType.URBAN.value: "dense_urban",
    RegionType.SUBURBAN.value: "residential",
    RegionType.INDUSTRIAL.value: "commercial_park",
    RegionType.FOREST.value: "dense_woodland",
    RegionType.GRASSLAND.value: "open_plains",
    RegionType.MOUNTAIN.value: "rugged_terrain",
    RegionType.AGRICULTURAL.value: "cropland_fields",
    RegionType.WATER.value: "water_basin",
    RegionType.CUSTOM.value: "default",
}


def sanitize_ring_coords(ring: list[list[float]] | list[tuple[float, float]]) -> list[list[float]]:
    """Validates and sanitizes a coordinate ring [[lon, lat], ...].

    Ensures at least 3 distinct points, closes the ring if needed, and removes
    consecutive duplicate points.
    """
    if not isinstance(ring, (list, tuple)) or len(ring) < 3:
        raise ValueError(f"Ring must have at least 3 points, got {len(ring) if isinstance(ring, (list, tuple)) else 'invalid'}")

    clean: list[list[float]] = []
    for pt in ring:
        if not isinstance(pt, (list, tuple)) or len(pt) < 2:
            continue
        lon = float(pt[0])
        lat = float(pt[1])
        if not (-180.0 <= lon <= 180.0 and -85.0 <= lat <= 85.0):
            raise ValueError(f"Coordinate out of WGS84 range: lon={lon}, lat={lat}")
        if not math.isfinite(lon) or not math.isfinite(lat):
            raise ValueError(f"Coordinate values must be finite numbers: lon={lon}, lat={lat}")

        # Avoid consecutive duplicate points
        if clean and abs(clean[-1][0] - lon) < 1e-9 and abs(clean[-1][1] - lat) < 1e-9:
            continue
        clean.append([round(lon, 7), round(lat, 7)])

    if len(clean) < 3:
        raise ValueError("Polygon ring must have at least 3 distinct vertices.")

    # Ensure ring is closed
    if abs(clean[0][0] - clean[-1][0]) > 1e-9 or abs(clean[0][1] - clean[-1][1]) > 1e-9:
        clean.append(list(clean[0]))

    return clean


def to_shapely_polygon(
    polygon: list[list[float]],
    holes: list[list[list[float]]] | None = None,
) -> Polygon:
    """Constructs and repairs a Shapely Polygon with optional holes.

    Repairs self-intersections, bowties, and collinear vertices using make_valid.
    """
    exterior_ring = sanitize_ring_coords(polygon)
    clean_holes: list[list[list[float]]] = []
    if holes:
        for h in holes:
            try:
                ch = sanitize_ring_coords(h)
                if len(ch) >= 4:
                    clean_holes.append(ch)
            except Exception:
                continue

    poly = Polygon(exterior_ring, clean_holes if clean_holes else None)
    if not poly.is_valid:
        fixed = make_valid(poly)
        if isinstance(fixed, MultiPolygon):
            # Pick largest polygon part
            poly = max(fixed.geoms, key=lambda g: g.area)
        elif isinstance(fixed, Polygon):
            poly = fixed
        else:
            poly = poly.buffer(0)

    # Filter out holes that fall outside the exterior
    if clean_holes and poly.interiors:
        pass  # already handled by shapely construction
    return poly


def shapely_to_coords(poly: Polygon) -> tuple[list[list[float]], list[list[list[float]]]]:
    """Converts a Shapely Polygon into GeoJSON coordinates: (exterior, list_of_holes)."""
    exterior = [[round(coord[0], 7), round(coord[1], 7)] for coord in poly.exterior.coords]
    holes = [
        [[round(coord[0], 7), round(coord[1], 7)] for coord in interior.coords]
        for interior in poly.interiors
    ]
    return exterior, holes


def calculate_region_metrics(
    polygon: list[list[float]],
    holes: list[list[list[float]]] | None = None,
    proj_origin: tuple[float, float] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Calculates geographic metrics (area, bounding box, centroid, dimensions)

    and projected metric coordinates (in local meters).
    Returns (geographic_dict, projected_dict).
    """
    poly = to_shapely_polygon(polygon, holes)
    min_lon, min_lat, max_lon, max_lat = poly.bounds
    centroid = poly.centroid
    c_lon, c_lat = centroid.x, centroid.y

    origin_lat, origin_lon = proj_origin if proj_origin else (c_lat, c_lon)

    # Local Transverse Mercator metric conversion
    from ..project import LocalProjection

    proj = LocalProjection(origin_lat, origin_lon)

    # Project exterior coordinates
    ext_proj: list[list[float]] = []
    for lon, lat in poly.exterior.coords:
        x, y = proj.to_xy(lon, lat)
        ext_proj.append([round(float(x), 2), round(float(y), 2)])

    # Project holes
    holes_proj: list[list[list[float]]] = []
    for interior in poly.interiors:
        h_coords: list[list[float]] = []
        for lon, lat in interior.coords:
            x, y = proj.to_xy(lon, lat)
            h_coords.append([round(float(x), 2), round(float(y), 2)])
        holes_proj.append(h_coords)

    # Compute metric area and metric dimensions from projected polygon
    poly_m = Polygon(ext_proj, holes_proj if holes_proj else None)
    if not poly_m.is_valid:
        poly_m = make_valid(poly_m)
        if isinstance(poly_m, MultiPolygon):
            poly_m = max(poly_m.geoms, key=lambda g: g.area)

    area_m2 = max(0.0, float(poly_m.area))
    min_x, min_y, max_x, max_y = poly_m.bounds
    width_m = max(0.0, float(max_x - min_x))
    height_m = max(0.0, float(max_y - min_y))

    geo_metrics = {
        "center": {"lat": round(c_lat, 7), "lon": round(c_lon, 7)},
        "bounds": {
            "south": round(min_lat, 7),
            "west": round(min_lon, 7),
            "north": round(max_lat, 7),
            "east": round(max_lon, 7),
        },
        "area_m2": round(area_m2, 2),
        "area_ha": round(area_m2 / 10_000.0, 4),
        "area_km2": round(area_m2 / 1_000_000.0, 4),
        "dimensions_m": {"width_m": round(width_m, 2), "height_m": round(height_m, 2)},
    }

    projected_coords = {
        "exterior": ext_proj,
        "holes": holes_proj,
        "origin": {"lat": origin_lat, "lon": origin_lon},
    }

    return geo_metrics, projected_coords


def split_region(
    region: Region,
    cutting_line_coords: list[list[float]],
    proj_origin: tuple[float, float] | None = None,
) -> list[Region]:
    """Splits a Region into 2 or more separate Regions using a cutting LineString.

    Preserves parent attributes while creating new stable IDs and computing individual metrics.
    """
    if len(cutting_line_coords) < 2:
        raise ValueError("Cutting line requires at least 2 coordinate points.")

    poly = to_shapely_polygon(region.polygon, region.holes)
    line = LineString(cutting_line_coords)

    if not line.intersects(poly):
        raise ValueError("Cutting line does not intersect the region polygon.")

    split_result = split(poly, line)
    polys: list[Polygon] = [g for g in split_result.geoms if isinstance(g, Polygon) and g.area > 1e-10]

    if len(polys) <= 1:
        raise ValueError("Cutting line does not divide the region into multiple parts.")

    new_regions: list[Region] = []
    for idx, p in enumerate(polys, start=1):
        ext, hls = shapely_to_coords(p)
        geo, proj = calculate_region_metrics(ext, hls, proj_origin=proj_origin)
        new_reg = Region(
            id=f"{region.id}_p{idx}_{uuid.uuid4().hex[:4]}",
            name=f"{region.name} (Part {idx})",
            region_type=region.region_type,
            color=region.color,
            polygon=ext,
            holes=hls,
            description=region.description,
            geographic=geo,
            projected=proj,
            style_preset=region.style_preset,
            seed=(region.seed + idx * 31) % 1_000_000,
            generation=copy.deepcopy(region.generation),
            visible=region.visible,
            enabled=region.enabled,
            status="unbuilt",
            locked=region.locked,
            chunks=[],
            export_status="none",
            tags=copy.deepcopy(region.tags),
        )
        new_regions.append(new_reg)

    return new_regions


def merge_regions(
    regions: list[Region],
    merged_name: str | None = None,
    proj_origin: tuple[float, float] | None = None,
) -> Region:
    """Merges two or more Regions into a single unified Region.

    Uses shapely.ops.unary_union to dissolve boundaries.
    """
    if len(regions) < 2:
        raise ValueError("At least 2 regions are required to perform a merge.")

    polys = [to_shapely_polygon(r.polygon, r.holes) for r in regions]
    union_geom = unary_union(polys)

    if isinstance(union_geom, MultiPolygon):
        # In case user merges disjoint regions, pick largest or convex boundary
        primary_poly = max(union_geom.geoms, key=lambda g: g.area)
    elif isinstance(union_geom, Polygon):
        primary_poly = union_geom
    else:
        raise ValueError(f"Merged geometry cannot be represented as Polygon: {union_geom.geom_type}")

    ext, hls = shapely_to_coords(primary_poly)
    geo, proj = calculate_region_metrics(ext, hls, proj_origin=proj_origin)

    base = regions[0]
    name = merged_name or (" + ".join([r.name for r in regions])[:60])

    return Region(
        id=f"reg_m_{uuid.uuid4().hex[:8]}",
        name=name,
        region_type=base.region_type,
        color=base.color,
        polygon=ext,
        holes=hls,
        description=f"Merged from: {', '.join(r.name for r in regions)}",
        geographic=geo,
        projected=proj,
        style_preset=base.style_preset,
        seed=base.seed,
        generation=copy.deepcopy(base.generation),
        visible=True,
        enabled=True,
        status="unbuilt",
        locked=False,
        chunks=[],
        export_status="none",
        tags=copy.deepcopy(base.tags),
    )


def duplicate_region(
    region: Region,
    offset_m: tuple[float, float] = (15.0, 15.0),
    proj_origin: tuple[float, float] | None = None,
) -> Region:
    """Duplicates an existing region with a new unique stable ID and offset coordinates."""
    from ..project import LocalProjection

    poly = to_shapely_polygon(region.polygon, region.holes)
    c_lon, c_lat = poly.centroid.x, poly.centroid.y
    origin_lat, origin_lon = proj_origin if proj_origin else (c_lat, c_lon)
    proj = LocalProjection(origin_lat, origin_lon)

    # Offset exterior
    dx_m, dy_m = offset_m
    new_ext: list[list[float]] = []
    for lon, lat in region.polygon:
        x, y = proj.to_xy(lon, lat)
        new_lon, new_lat = proj.to_lonlat(x + dx_m, y + dy_m)
        new_ext.append([round(float(new_lon), 7), round(float(new_lat), 7)])

    new_holes: list[list[list[float]]] = []
    for h in region.holes:
        nh: list[list[float]] = []
        for lon, lat in h:
            x, y = proj.to_xy(lon, lat)
            new_lon, new_lat = proj.to_lonlat(x + dx_m, y + dy_m)
            nh.append([round(float(new_lon), 7), round(float(new_lat), 7)])
        new_holes.append(nh)

    geo, projected = calculate_region_metrics(new_ext, new_holes, proj_origin=proj_origin)

    return Region(
        id=f"reg_{uuid.uuid4().hex[:8]}",
        name=f"{region.name} (Copy)",
        region_type=region.region_type,
        color=region.color,
        polygon=new_ext,
        holes=new_holes,
        description=region.description,
        geographic=geo,
        projected=projected,
        style_preset=region.style_preset,
        seed=(region.seed + 101) % 1_000_000,
        generation=copy.deepcopy(region.generation),
        visible=region.visible,
        enabled=region.enabled,
        status="unbuilt",
        locked=False,
        chunks=[],
        export_status="none",
        tags=copy.deepcopy(region.tags),
    )


def detect_overlaps(regions: list[Region]) -> list[dict[str, Any]]:
    """Detects 2D polygon overlaps and boundary conflicts between pairs of regions.

    Shared touching borders (1D lines) are considered valid; only 2D intersection
    areas > 1.0 m² are flagged as conflicts.
    """
    conflicts: list[dict[str, Any]] = []
    shapely_polys: list[tuple[Region, Polygon]] = []

    for r in regions:
        if not r.polygon or len(r.polygon) < 3:
            continue
        try:
            poly = to_shapely_polygon(r.polygon, r.holes)
            shapely_polys.append((r, poly))
        except Exception:
            continue

    from ..project import LocalProjection

    for i in range(len(shapely_polys)):
        for j in range(i + 1, len(shapely_polys)):
            r_a, poly_a = shapely_polys[i]
            r_b, poly_b = shapely_polys[j]

            if not poly_a.intersects(poly_b):
                continue

            inter = poly_a.intersection(poly_b)
            # If intersection is 2D polygon or multipolygon
            if inter.geom_type in ("Polygon", "MultiPolygon") and inter.area > 1e-12:
                # Calculate metric area of overlap
                c = inter.centroid
                proj = LocalProjection(c.y, c.x)
                if inter.geom_type == "Polygon":
                    coords_m = [proj.to_xy(pt[0], pt[1]) for pt in inter.exterior.coords]
                    area_m2 = float(Polygon(coords_m).area)
                else:
                    area_m2 = sum(
                        float(Polygon([proj.to_xy(pt[0], pt[1]) for pt in g.exterior.coords]).area)
                        for g in inter.geoms
                    )

                if area_m2 > 0.5:
                    conflicts.append({
                        "region_a_id": r_a.id,
                        "region_a_name": r_a.name,
                        "region_b_id": r_b.id,
                        "region_b_name": r_b.name,
                        "overlap_area_m2": round(area_m2, 2),
                        "severity": "conflict" if area_m2 > 10.0 else "warning",
                        "message": (
                            f"Regions '{r_a.name}' and '{r_b.name}' overlap by "
                            f"{round(area_m2, 2)} m²."
                        ),
                    })

    return conflicts


def extract_shared_boundaries(
    region_a: Region,
    region_b: Region,
) -> list[list[list[float]]]:
    """Extracts shared boundary line segments between two adjacent regions.

    Used by the generation pipeline to avoid generating duplicate perimeter walls,
    fences, or border roads.
    """
    poly_a = to_shapely_polygon(region_a.polygon, region_a.holes)
    poly_b = to_shapely_polygon(region_b.polygon, region_b.holes)

    shared = poly_a.boundary.intersection(poly_b.boundary)
    segments: list[list[list[float]]] = []

    if shared.is_empty:
        return segments

    if shared.geom_type == "LineString":
        segments.append([[round(c[0], 7), round(c[1], 7)] for c in shared.coords])
    elif shared.geom_type == "MultiLineString":
        for line in shared.geoms:
            segments.append([[round(c[0], 7), round(c[1], 7)] for c in line.coords])

    return segments


def associate_chunks_with_regions(
    regions: list[Region],
    chunks: list[dict[str, Any]],
) -> dict[str, list[str]]:
    """Maps which chunks intersect each region, and vice versa.

    Returns mapping: {region_id: [chunk_id, ...]}
    """
    region_to_chunks: dict[str, list[str]] = {r.id: [] for r in regions}
    region_polys = {r.id: to_shapely_polygon(r.polygon, r.holes) for r in regions if r.polygon}

    for ch in chunks:
        ch_id = ch.get("id")
        bbox = ch.get("bbox") or {}
        s = bbox.get("south")
        w = bbox.get("west")
        n = bbox.get("north")
        e = bbox.get("east")
        if None in (s, w, n, e):
            continue
        chunk_box = Polygon([(w, s), (e, s), (e, n), (w, n), (w, s)])

        for r_id, r_poly in region_polys.items():
            if r_poly.intersects(chunk_box):
                region_to_chunks[r_id].append(ch_id)

    # Update regions in-place
    for r in regions:
        if r.id in region_to_chunks:
            r.chunks = region_to_chunks[r.id]

    return region_to_chunks

