"""Terragen Biome & Procedural Terrain Engine (Etapa 3).

Provides:
- 10 Foundational Game World Biomes (Grassland, Forest, Dense Forest, Alpine,
  Rocky Mountains, Desert, Wetlands, Coastal, Snow, Urban).
- Environmental Analyzer (Altitude, Slope, Aspect, Water Proximity, Moisture, Temperature, Infrastructure).
- Fuzzy Membership Rules with continuous transitions.
- Deterministic Seedable Noise.
- Reversible Manual Polygon Overrides with feathering.
- Seamless Multi-Chunk Generation & Chunk Boundary Continuity.
- Blender Splatmap & Metadata Exporter.
"""

from __future__ import annotations

from .environmental import EnvironmentalAnalyzer
from .export import generate_splatmap_textures, sample_mesh_vertex_colors
from .generator import BiomeGenerator
from .noise import coherent_noise_2d
from .overrides import BiomeOverride, BiomeOverrideManager
from .rules import BiomeRule, create_default_biome_rules, smooth_trapezoid
from .types import (
    BIOME_COLORS,
    BIOME_INDEX,
    INDEX_TO_BIOME,
    BiomeGridResult,
    BiomeSample,
    BiomeType,
)

__all__ = [
    "BIOME_COLORS",
    "BIOME_INDEX",
    "INDEX_TO_BIOME",
    "BiomeGenerator",
    "BiomeGridResult",
    "BiomeOverride",
    "BiomeOverrideManager",
    "BiomeRule",
    "BiomeSample",
    "BiomeType",
    "EnvironmentalAnalyzer",
    "coherent_noise_2d",
    "create_default_biome_rules",
    "generate_splatmap_textures",
    "sample_mesh_vertex_colors",
    "smooth_trapezoid",
]
