"""Terragen Procedural World Dressing Engine (Etapa 4).

Features:
- Asset Catalog (Nature, Infrastructure, Environment Props).
- Biome-Aware Placement Rules (Slope, Altitude, Clearance Buffers).
- Spatial Hash Collision Prevention (No Overlapping Footprints).
- Natural Vegetation Groves & Clump Clustering.
- Deterministic Randomization & Chunk-Continuous Seeding.
- Memory-Optimized Instancing for Blender (Linked Duplicates & Collections).
"""

from __future__ import annotations

from .blender_export import export_world_dressing_for_blender
from .catalog import (
    AssetCatalog,
    build_bench_prototype,
    build_boulder_prototype,
    build_bush_prototype,
    build_lamp_post_prototype,
    build_ruin_prototype,
    build_tree_prototype,
)
from .engine import WorldDressingEngine
from .models import (
    AssetCategory,
    AssetPrototype,
    AssetSubcategory,
    InstanceTransform,
    PlacementRule,
)
from .rules import create_default_placement_rules
from .spatial_index import SpatialHashGrid

__all__ = [
    "AssetCatalog",
    "AssetCategory",
    "AssetPrototype",
    "AssetSubcategory",
    "InstanceTransform",
    "PlacementRule",
    "SpatialHashGrid",
    "WorldDressingEngine",
    "build_bench_prototype",
    "build_boulder_prototype",
    "build_bush_prototype",
    "build_lamp_post_prototype",
    "build_ruin_prototype",
    "build_tree_prototype",
    "create_default_placement_rules",
    "export_world_dressing_for_blender",
]
