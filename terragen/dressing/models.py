"""Domain Models for Procedural World Dressing & Object Placement."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..biomes.types import BiomeType
from ..geom import Mesh


class AssetCategory(str, Enum):
    """Broad categories for world dressing assets."""
    NATURE = "nature"
    INFRASTRUCTURE = "infrastructure"
    ENVIRONMENT_PROPS = "environment_props"


class AssetSubcategory(str, Enum):
    """Fine-grained classification of dressing objects."""
    # Nature
    TREE = "tree"
    BUSH = "bush"
    GRASS = "grass"
    FLOWER = "flower"
    STONE = "stone"
    BOULDER = "boulder"
    LOG = "log"

    # Infrastructure
    BRIDGE = "bridge"
    FENCE = "fence"
    LAMP_POST = "lamp_post"
    SIGN = "sign"
    BENCH = "bench"
    URBAN_PROP = "urban_prop"

    # Environment Props
    RUIN = "ruin"
    ABANDONED = "abandoned"
    DECORATIVE = "decorative"
    POI = "poi"


@dataclass
class AssetPrototype:
    """A reusable 3D asset definition used as prototype for instanced placement."""
    id: str
    name: str
    category: AssetCategory
    subcategory: AssetSubcategory
    collision_radius_m: float = 1.5
    height_m: float = 4.0
    mesh: Mesh | None = None
    file_path: str | None = None
    tags: list[str] = field(default_factory=list)
    lod_variants: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category.value,
            "subcategory": self.subcategory.value,
            "collision_radius_m": self.collision_radius_m,
            "height_m": self.height_m,
            "file_path": self.file_path,
            "tags": list(self.tags),
            "lod_variants": dict(self.lod_variants),
        }


@dataclass
class InstanceTransform:
    """Spatial transform of an instanced object placed in the world."""
    prototype_id: str
    category: AssetCategory
    subcategory: AssetSubcategory
    x: float
    y: float  # Up (Altitude)
    z: float  # North
    rot_x_deg: float = 0.0  # Pitch
    rot_y_deg: float = 0.0  # Yaw / Heading
    rot_z_deg: float = 0.0  # Roll
    scale_x: float = 1.0
    scale_y: float = 1.0
    scale_z: float = 1.0
    biome: str = "Grassland"
    id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "prototype_id": self.prototype_id,
            "category": self.category.value,
            "subcategory": self.subcategory.value,
            "position": [round(self.x, 3), round(self.y, 3), round(self.z, 3)],
            "rotation_euler_deg": [round(self.rot_x_deg, 2), round(self.rot_y_deg, 2), round(self.rot_z_deg, 2)],
            "scale": [round(self.scale_x, 3), round(self.scale_y, 3), round(self.scale_z, 3)],
            "biome": self.biome,
        }


@dataclass
class PlacementRule:
    """Ecological and spatial rules for placing a specific asset prototype."""
    prototype_id: str
    category: AssetCategory
    subcategory: AssetSubcategory
    # Biome affinity: dict mapping BiomeType to weight multiplier (0.0 = prohibited)
    biome_affinity: dict[BiomeType, float] = field(default_factory=dict)
    density_per_ha: float = 20.0  # Target density in ideal conditions
    elevation_range: tuple[float | None, float | None] = (None, None)
    min_slope_deg: float = 0.0
    max_slope_deg: float = 35.0
    # Spatial clearance buffers
    road_buffer_m: tuple[float, float | None] = (3.0, None)       # (min_dist, max_dist)
    building_buffer_m: tuple[float, float | None] = (2.5, None)   # (min_dist, max_dist)
    water_buffer_m: tuple[float, float | None] = (1.5, None)      # (min_dist, max_dist)
    collision_radius_m: float = 1.5
    # Transform variations
    scale_range: tuple[float, float] = (0.85, 1.25)
    random_yaw: bool = True
    align_to_normal: bool = False
    max_tilt_deg: float = 15.0
    # Clustering (e.g. for groves of trees, flower patches, rock fields)
    cluster_probability: float = 0.35
    cluster_radius_m: float = 12.0
    cluster_count_range: tuple[int, int] = (2, 5)

    def is_biome_allowed(self, biome: BiomeType) -> bool:
        if not self.biome_affinity:
            return True
        return self.biome_affinity.get(biome, 0.0) > 0.0

    def get_biome_weight(self, biome: BiomeType) -> float:
        if not self.biome_affinity:
            return 1.0
        return float(self.biome_affinity.get(biome, 0.0))
