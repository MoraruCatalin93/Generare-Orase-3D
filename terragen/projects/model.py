"""Data models and serialization for Terragen projects."""

from __future__ import annotations

import copy
import dataclasses
import datetime
import math
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from terragen import __version__


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


@dataclass
class GeographicBounds:
    south: float
    west: float
    north: float
    east: float

    def to_dict(self) -> dict[str, float]:
        return {
            "south": round(float(self.south), 6),
            "west": round(float(self.west), 6),
            "north": round(float(self.north), 6),
            "east": round(float(self.east), 6),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GeographicBounds:
        return cls(
            south=float(data.get("south", 0.0)),
            west=float(data.get("west", 0.0)),
            north=float(data.get("north", 0.0)),
            east=float(data.get("east", 0.0)),
        )


@dataclass
class GeographicLocation:
    center_lat: float
    center_lon: float
    bounds: GeographicBounds
    elevation_ref: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "center": {
                "lat": round(float(self.center_lat), 6),
                "lon": round(float(self.center_lon), 6),
            },
            "bounds": self.bounds.to_dict(),
            "elevation_ref": round(float(self.elevation_ref), 2),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GeographicLocation:
        center = data.get("center", {})
        lat = float(center.get("lat", data.get("lat", 0.0)))
        lon = float(center.get("lon", data.get("lon", 0.0)))
        bounds_dict = data.get("bounds", {})
        bounds = GeographicBounds.from_dict(bounds_dict)
        elevation_ref = float(data.get("elevation_ref", 0.0))
        return cls(center_lat=lat, center_lon=lon, bounds=bounds, elevation_ref=elevation_ref)


@dataclass
class CoordinateSystem:
    projection: str = "tmerc"
    datum: str = "WGS84"
    units: str = "meters"
    world_origin_lat: float = 0.0
    world_origin_lon: float = 0.0
    world_origin_altitude: float = 0.0
    proj_string: str = ""
    axis_engine: str = "X=East, Y=Up, Z=North"
    axis_glb: str = "X=East, Y=Up, Z=-North"
    axis_obj: str = "X=East, Y=Up, Z=North"
    axis_blender_target: str = "X=East, Y=North, Z=Up"

    def to_dict(self) -> dict[str, Any]:
        return {
            "projection": self.projection,
            "datum": self.datum,
            "units": self.units,
            "world_origin": {
                "lat": round(float(self.world_origin_lat), 6),
                "lon": round(float(self.world_origin_lon), 6),
                "altitude": round(float(self.world_origin_altitude), 2),
            },
            "proj_string": self.proj_string,
            "axis_convention": {
                "engine": self.axis_engine,
                "glb": self.axis_glb,
                "obj": self.axis_obj,
                "blender_target": self.axis_blender_target,
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CoordinateSystem:
        origin = data.get("world_origin", {})
        axes = data.get("axis_convention", {})
        return cls(
            projection=str(data.get("projection", "tmerc")),
            datum=str(data.get("datum", "WGS84")),
            units=str(data.get("units", "meters")),
            world_origin_lat=float(origin.get("lat", 0.0)),
            world_origin_lon=float(origin.get("lon", 0.0)),
            world_origin_altitude=float(origin.get("altitude", 0.0)),
            proj_string=str(data.get("proj_string", "")),
            axis_engine=str(axes.get("engine", "X=East, Y=Up, Z=North")),
            axis_glb=str(axes.get("glb", "X=East, Y=Up, Z=-North")),
            axis_obj=str(axes.get("obj", "X=East, Y=Up, Z=North")),
            axis_blender_target=str(axes.get("blender_target", "X=East, Y=North, Z=Up")),
        )


@dataclass
class WorldDimensions:
    size_x_m: float = 1500.0
    size_y_m: float = 1500.0
    height_min_m: float = 0.0
    height_max_m: float = 2000.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "size_x_m": round(float(self.size_x_m), 2),
            "size_y_m": round(float(self.size_y_m), 2),
            "height_range_m": {
                "min": round(float(self.height_min_m), 2),
                "max": round(float(self.height_max_m), 2),
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorldDimensions:
        hr = data.get("height_range_m", {})
        return cls(
            size_x_m=float(data.get("size_x_m", data.get("size", 1500.0))),
            size_y_m=float(data.get("size_y_m", data.get("size", 1500.0))),
            height_min_m=float(hr.get("min", 0.0)),
            height_max_m=float(hr.get("max", 2000.0)),
        )


@dataclass
class TerrainSettings:
    resolution: int = 256
    dem_zoom: int = 15
    step_m: float = 5.86
    exaggeration: float = 1.0
    smooth_window: int = 5
    modifiers: list[dict[str, Any]] = field(default_factory=list)
    undo_stack: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "resolution": int(self.resolution),
            "dem_zoom": int(self.dem_zoom),
            "step_m": round(float(self.step_m), 4),
            "exaggeration": round(float(self.exaggeration), 2),
            "smooth_window": int(self.smooth_window),
            "modifiers": copy.deepcopy(self.modifiers),
            "undo_stack": copy.deepcopy(self.undo_stack),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TerrainSettings:
        return cls(
            resolution=int(data.get("resolution", data.get("terrain_res", 256))),
            dem_zoom=int(data.get("dem_zoom", 15)),
            step_m=float(data.get("step_m", 5.86)),
            exaggeration=float(data.get("exaggeration", 1.0)),
            smooth_window=int(data.get("smooth_window", 5)),
            modifiers=list(data.get("modifiers", []) or []),
            undo_stack=list(data.get("undo_stack", []) or []),
        )


@dataclass
class SourcesMetadata:
    osm_mode: str = "auto"  # auto | local_pbf | overpass
    osm_pbf_path: str | None = None
    osm_pbf_name: str | None = None
    osm_pbf_size_bytes: int | None = None
    osm_pbf_mtime: float | None = None
    overpass_endpoint: str | None = None
    dem_provider: str = "AWS Terrarium"
    dem_url_template: str = (
        "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
    )
    dem_zoom: int = 15
    dem_cache_dir: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "osm": {
                "mode": self.osm_mode,
                "pbf_path": self.osm_pbf_path,
                "pbf_name": self.osm_pbf_name,
                "pbf_size_bytes": self.osm_pbf_size_bytes,
                "pbf_mtime": self.osm_pbf_mtime,
                "overpass_endpoint": self.overpass_endpoint,
            },
            "dem": {
                "provider": self.dem_provider,
                "url_template": self.dem_url_template,
                "zoom": self.dem_zoom,
                "cache_dir": self.dem_cache_dir,
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SourcesMetadata:
        osm = data.get("osm", {})
        dem = data.get("dem", {})
        return cls(
            osm_mode=str(osm.get("mode", "auto")),
            osm_pbf_path=osm.get("pbf_path"),
            osm_pbf_name=osm.get("pbf_name"),
            osm_pbf_size_bytes=osm.get("pbf_size_bytes"),
            osm_pbf_mtime=osm.get("pbf_mtime"),
            overpass_endpoint=osm.get("overpass_endpoint"),
            dem_provider=str(dem.get("provider", "AWS Terrarium")),
            dem_url_template=str(
                dem.get(
                    "url_template",
                    "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png",
                )
            ),
            dem_zoom=int(dem.get("zoom", 15)),
            dem_cache_dir=dem.get("cache_dir"),
        )


class RegionType(str, Enum):
    URBAN = "urban"
    SUBURBAN = "suburban"
    INDUSTRIAL = "industrial"
    FOREST = "forest"
    GRASSLAND = "grassland"
    MOUNTAIN = "mountain"
    AGRICULTURAL = "agricultural"
    WATER = "water"
    CUSTOM = "custom"


@dataclass
class Region:
    id: str
    name: str
    polygon: list[list[float]]  # GeoJSON ring coordinates [[lon, lat], ...]
    holes: list[list[list[float]]] = field(default_factory=list)
    region_type: str = "urban"
    color: str = "#e74c3c"
    description: str = ""
    geographic: dict[str, Any] = field(default_factory=dict)
    projected: dict[str, Any] = field(default_factory=dict)
    style_preset: str = "default"
    seed: int = 7
    generation: dict[str, Any] = field(default_factory=dict)
    visible: bool = True
    enabled: bool = True
    status: str = "unbuilt"
    locked: bool = False
    chunks: list[str] = field(default_factory=list)
    export_status: str = "none"
    tags: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "region_type": self.region_type,
            "color": self.color,
            "polygon": copy.deepcopy(self.polygon),
            "holes": copy.deepcopy(self.holes),
            "description": self.description,
            "geographic": copy.deepcopy(self.geographic),
            "projected": copy.deepcopy(self.projected),
            "style_preset": self.style_preset,
            "seed": int(self.seed),
            "generation": copy.deepcopy(self.generation),
            "visible": bool(self.visible),
            "enabled": bool(self.enabled),
            "status": str(self.status),
            "locked": bool(self.locked),
            "chunks": list(self.chunks),
            "export_status": str(self.export_status),
            "tags": copy.deepcopy(self.tags),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def to_geojson_feature(self) -> dict[str, Any]:
        coords: list[Any] = [self.polygon] + [h for h in self.holes if h]
        return {
            "type": "Feature",
            "id": self.id,
            "properties": {
                "id": self.id,
                "name": self.name,
                "region_type": self.region_type,
                "color": self.color,
                "description": self.description,
                "style_preset": self.style_preset,
                "seed": self.seed,
                "visible": self.visible,
                "enabled": self.enabled,
                "status": self.status,
                "locked": self.locked,
                "chunks": self.chunks,
                "export_status": self.export_status,
                "area_m2": self.geographic.get("area_m2", 0.0),
                "area_ha": self.geographic.get("area_ha", 0.0),
                "area_km2": self.geographic.get("area_km2", 0.0),
                "dimensions_m": self.geographic.get("dimensions_m", {}),
                "generation": self.generation,
                "tags": self.tags,
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": coords,
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Region:
        raw_poly = data.get("polygon", []) or []
        raw_holes = data.get("holes", []) or []
        reg_type = str(data.get("region_type", "urban")).lower()
        return cls(
            id=str(data.get("id", uuid.uuid4().hex[:8])),
            name=str(data.get("name", "Region")),
            region_type=reg_type,
            color=str(data.get("color", "#e74c3c")),
            polygon=raw_poly,
            holes=raw_holes,
            description=str(data.get("description", "")),
            geographic=data.get("geographic", {}) or {},
            projected=data.get("projected", {}) or {},
            style_preset=str(data.get("style_preset", "default")),
            seed=int(data.get("seed", 7)),
            generation=data.get("generation", {}) or {},
            visible=bool(data.get("visible", True)),
            enabled=bool(data.get("enabled", True)),
            status=str(data.get("status", "unbuilt")),
            locked=bool(data.get("locked", False)),
            chunks=list(data.get("chunks", []) or []),
            export_status=str(data.get("export_status", "none")),
            tags=data.get("tags", {}) or {},
            created_at=str(data.get("created_at", _now_iso())),
            updated_at=str(data.get("updated_at", data.get("created_at", _now_iso()))),
        )


@dataclass
class ChunkGrid:
    enabled: bool = False
    chunk_size_m: float = 512.0
    world_size_x_m: float = 20000.0
    world_size_y_m: float = 20000.0
    rows: int = 1
    cols: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": bool(self.enabled),
            "chunk_size_m": round(float(self.chunk_size_m), 2),
            "world_size_x_m": round(float(self.world_size_x_m), 2),
            "world_size_y_m": round(float(self.world_size_y_m), 2),
            "rows": int(self.rows),
            "cols": int(self.cols),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChunkGrid:
        return cls(
            enabled=bool(data.get("enabled", False)),
            chunk_size_m=float(data.get("chunk_size_m", 512.0)),
            world_size_x_m=float(data.get("world_size_x_m", 20000.0)),
            world_size_y_m=float(data.get("world_size_y_m", 20000.0)),
            rows=int(data.get("rows", 1)),
            cols=int(data.get("cols", 1)),
        )


@dataclass
class ChunkItem:
    id: str
    row: int
    col: int
    center_lat: float
    center_lon: float
    bounds: GeographicBounds
    bounds_projected: dict[str, float] = field(default_factory=dict)
    size_m: float = 512.0
    seed: int = 0
    status: str = "unbuilt"  # unbuilt | queued | generating | built | stale | error
    neighbors: dict[str, str | None] = field(default_factory=dict)
    regions: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    export_status: str = "none"  # none | pending | exported | stale
    last_built: str | None = None
    stats: dict[str, Any] = field(default_factory=dict)
    content_hash: str | None = None
    visible: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "row": self.row,
            "col": self.col,
            "center": {
                "lat": round(float(self.center_lat), 6),
                "lon": round(float(self.center_lon), 6),
            },
            "bounds": self.bounds.to_dict(),
            "bounds_projected": copy.deepcopy(self.bounds_projected),
            "size_m": round(float(self.size_m), 2),
            "seed": int(self.seed),
            "status": self.status,
            "neighbors": copy.deepcopy(self.neighbors),
            "regions": list(self.regions),
            "dependencies": list(self.dependencies),
            "export_status": self.export_status,
            "last_built": self.last_built,
            "stats": copy.deepcopy(self.stats),
            "content_hash": self.content_hash,
            "visible": bool(self.visible),
        }

    def to_geojson_feature(self) -> dict[str, Any]:
        s, w = self.bounds.south, self.bounds.west
        n, e = self.bounds.north, self.bounds.east
        return {
            "type": "Feature",
            "id": self.id,
            "properties": {
                "id": self.id,
                "row": self.row,
                "col": self.col,
                "status": self.status,
                "seed": self.seed,
                "regions": self.regions,
                "visible": self.visible,
                "size_m": self.size_m,
                "export_status": self.export_status,
                "stats": self.stats,
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]],
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChunkItem:
        c = data.get("center", {})
        b = data.get("bounds", {})
        return cls(
            id=str(data.get("id", "")),
            row=int(data.get("row", 0)),
            col=int(data.get("col", 0)),
            center_lat=float(c.get("lat", 0.0)),
            center_lon=float(c.get("lon", 0.0)),
            bounds=(
                GeographicBounds.from_dict(b)
                if isinstance(b, dict)
                else GeographicBounds(0.0, 0.0, 0.0, 0.0)
            ),
            bounds_projected=data.get("bounds_projected", {}) or {},
            size_m=float(data.get("size_m", 512.0)),
            seed=int(data.get("seed", 0)),
            status=str(data.get("status", "unbuilt")),
            neighbors=data.get("neighbors", {}) or {},
            regions=list(data.get("regions", []) or []),
            dependencies=list(data.get("dependencies", []) or []),
            export_status=str(data.get("export_status", "none")),
            last_built=data.get("last_built"),
            stats=data.get("stats", {}) or {},
            content_hash=data.get("content_hash"),
            visible=bool(data.get("visible", True)),
        )


@dataclass
class ChunksSettings:
    grid: ChunkGrid = field(default_factory=ChunkGrid)
    items: list[ChunkItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "grid": self.grid.to_dict(),
            "items": [it.to_dict() for it in self.items],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChunksSettings:
        grid_data = data.get("grid", {})
        items_data = data.get("items", [])
        return cls(
            grid=ChunkGrid.from_dict(grid_data),
            items=[ChunkItem.from_dict(it) for it in items_data if isinstance(it, dict)],
        )


@dataclass
class GenerationSettings:
    roofs: bool = True
    formats: list[str] = field(default_factory=lambda: ["glb"])
    building_palette_seed: int = 7
    road_width_scale: float = 1.0
    detail_level: str = "medium"  # low | medium | high

    def to_dict(self) -> dict[str, Any]:
        return {
            "roofs": bool(self.roofs),
            "formats": list(self.formats),
            "building_palette_seed": int(self.building_palette_seed),
            "road_width_scale": round(float(self.road_width_scale), 2),
            "detail_level": self.detail_level,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GenerationSettings:
        fmts = data.get("formats", ["glb"])
        if isinstance(fmts, (tuple, list)):
            fmts = list(fmts)
        else:
            fmts = ["glb"]
        return cls(
            roofs=bool(data.get("roofs", True)),
            formats=fmts,
            building_palette_seed=int(data.get("building_palette_seed", data.get("seed", 7))),
            road_width_scale=float(data.get("road_width_scale", 1.0)),
            detail_level=str(data.get("detail_level", "medium")),
        )


@dataclass
class ExportRecord:
    id: str
    timestamp: str
    format: str
    output_path: str
    stats: dict[str, Any] = field(default_factory=dict)
    status: str = "success"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "format": self.format,
            "output_path": self.output_path,
            "stats": copy.deepcopy(self.stats),
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExportRecord:
        return cls(
            id=str(data.get("id", uuid.uuid4().hex[:8])),
            timestamp=str(data.get("timestamp", _now_iso())),
            format=str(data.get("format", "glb")),
            output_path=str(data.get("output_path", "")),
            stats=data.get("stats", {}) or {},
            status=str(data.get("status", "success")),
        )


@dataclass
class AssetsReference:
    build_dir: str | None = None
    manifest_path: str | None = None
    last_generated_at: str | None = None
    last_build_stats: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "build_dir": self.build_dir,
            "manifest_path": self.manifest_path,
            "last_generated_at": self.last_generated_at,
            "last_build_stats": (
                copy.deepcopy(self.last_build_stats) if self.last_build_stats else None
            ),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AssetsReference:
        return cls(
            build_dir=data.get("build_dir"),
            manifest_path=data.get("manifest_path"),
            last_generated_at=data.get("last_generated_at"),
            last_build_stats=data.get("last_build_stats"),
        )


@dataclass
class Project:
    """Terragen persistent project model."""

    name: str
    geographic: GeographicLocation
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    slug: str = ""
    description: str = ""
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    schema_version: int = 1
    app_version: str = __version__
    seed: int = 7
    coordinate_system: CoordinateSystem = field(default_factory=CoordinateSystem)
    dimensions: WorldDimensions = field(default_factory=WorldDimensions)
    terrain: TerrainSettings = field(default_factory=TerrainSettings)
    sources: SourcesMetadata = field(default_factory=SourcesMetadata)
    regions: list[Region] = field(default_factory=list)
    chunks: ChunksSettings = field(default_factory=ChunksSettings)
    generation: GenerationSettings = field(default_factory=GenerationSettings)
    export_history: list[ExportRecord] = field(default_factory=list)
    assets: AssetsReference = field(default_factory=AssetsReference)
    extra_metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.slug:
            import re

            clean = re.sub(r"[^\w\-. ]+", "", self.name).strip().replace(" ", "-").lower()
            self.slug = clean[:48] or "project"
        # If coordinate_system proj_string is missing, configure standard tmerc
        if not self.coordinate_system.proj_string:
            lat = self.geographic.center_lat
            lon = self.geographic.center_lon
            self.coordinate_system.world_origin_lat = lat
            self.coordinate_system.world_origin_lon = lon
            self.coordinate_system.proj_string = (
                f"+proj=tmerc +lat_0={lat} +lon_0={lon} +k=1 +x_0=0 +y_0=0 +ellps=WGS84 +units=m +no_defs"
            )

    def touch(self):
        self.updated_at = _now_iso()

    def to_dict(self) -> dict[str, Any]:
        data = {
            "schema_version": self.schema_version,
            "app_version": self.app_version,
            "id": self.id,
            "name": self.name,
            "slug": self.slug,
            "description": self.description,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "seed": self.seed,
            "geographic": self.geographic.to_dict(),
            "coordinate_system": self.coordinate_system.to_dict(),
            "dimensions": self.dimensions.to_dict(),
            "terrain": self.terrain.to_dict(),
            "sources": self.sources.to_dict(),
            "regions": [r.to_dict() for r in self.regions],
            "chunks": self.chunks.to_dict(),
            "generation": self.generation.to_dict(),
            "export_history": [e.to_dict() for e in self.export_history],
            "assets": self.assets.to_dict(),
        }
        if self.extra_metadata:
            data["extra_metadata"] = copy.deepcopy(self.extra_metadata)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Project:
        from .migration import migrate_project_dict
        from .validation import repair_project_dict

        # Apply schema migrations and repairs
        migrated = migrate_project_dict(data)
        repaired = repair_project_dict(migrated)

        geo = GeographicLocation.from_dict(repaired.get("geographic", {}))
        cs = CoordinateSystem.from_dict(repaired.get("coordinate_system", {}))
        dims = WorldDimensions.from_dict(repaired.get("dimensions", {}))
        terrain = TerrainSettings.from_dict(repaired.get("terrain", {}))
        sources = SourcesMetadata.from_dict(repaired.get("sources", {}))
        regions = [
            Region.from_dict(r) for r in repaired.get("regions", []) if isinstance(r, dict)
        ]
        chunks = ChunksSettings.from_dict(repaired.get("chunks", {}))
        gen = GenerationSettings.from_dict(repaired.get("generation", {}))
        exports = [
            ExportRecord.from_dict(e)
            for e in repaired.get("export_history", [])
            if isinstance(e, dict)
        ]
        assets = AssetsReference.from_dict(repaired.get("assets", {}))

        return cls(
            id=str(repaired.get("id", uuid.uuid4().hex)),
            name=str(repaired.get("name", "Project")),
            slug=str(repaired.get("slug", "")),
            description=str(repaired.get("description", "")),
            created_at=str(repaired.get("created_at", _now_iso())),
            updated_at=str(repaired.get("updated_at", _now_iso())),
            schema_version=int(repaired.get("schema_version", 1)),
            app_version=str(repaired.get("app_version", __version__)),
            seed=int(repaired.get("seed", 7)),
            geographic=geo,
            coordinate_system=cs,
            dimensions=dims,
            terrain=terrain,
            sources=sources,
            regions=regions,
            chunks=chunks,
            generation=gen,
            export_history=exports,
            assets=assets,
            extra_metadata=repaired.get("extra_metadata", {}),
        )

