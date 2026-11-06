"""Terragen Project Management subsystem."""

from .chunks import (
    ChunkEngine,
    compute_grid_dimensions,
    derive_chunk_seed,
)
from .manager import ProjectManager, safe_slug
from .migration import CURRENT_SCHEMA_VERSION, migrate_project_dict
from .model import (
    AssetsReference,
    ChunkGrid,
    ChunkItem,
    ChunksSettings,
    CoordinateSystem,
    ExportRecord,
    GenerationSettings,
    GeographicBounds,
    GeographicLocation,
    Project,
    Region,
    RegionType,
    SourcesMetadata,
    TerrainSettings,
    WorldDimensions,
)
from .regions import (
    DEFAULT_REGION_COLORS,
    DEFAULT_STYLE_PRESETS,
    calculate_region_metrics,
    detect_overlaps,
    duplicate_region,
    extract_shared_boundaries,
    merge_regions,
    split_region,
    to_shapely_polygon,
)
from .validation import (
    CorruptedProjectError,
    ProjectValidationError,
    repair_project_dict,
    validate_project_dict,
)

__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "AssetsReference",
    "ChunkGrid",
    "ChunkItem",
    "ChunksSettings",
    "ChunkEngine",
    "compute_grid_dimensions",
    "derive_chunk_seed",
    "CoordinateSystem",
    "CorruptedProjectError",
    "ExportRecord",
    "GenerationSettings",
    "GeographicBounds",
    "GeographicLocation",
    "Project",
    "ProjectManager",
    "ProjectValidationError",
    "Region",
    "RegionType",
    "DEFAULT_REGION_COLORS",
    "DEFAULT_STYLE_PRESETS",
    "calculate_region_metrics",
    "detect_overlaps",
    "duplicate_region",
    "extract_shared_boundaries",
    "merge_regions",
    "split_region",
    "to_shapely_polygon",
    "SourcesMetadata",
    "TerrainSettings",
    "WorldDimensions",
    "migrate_project_dict",
    "repair_project_dict",
    "safe_slug",
    "validate_project_dict",
]

