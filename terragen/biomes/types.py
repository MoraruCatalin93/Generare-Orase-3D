"""Domain Types and Data Structures for Terragen Biome Engine."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np


class BiomeType(str, Enum):
    """The 10 foundational biomes supported by Terragen Game World Generator."""
    GRASSLAND = "Grassland"
    FOREST = "Forest"
    DENSE_FOREST = "Dense Forest"
    ALPINE = "Alpine"
    ROCKY_MOUNTAINS = "Rocky Mountains"
    DESERT = "Desert"
    WETLANDS = "Wetlands"
    COASTAL = "Coastal"
    SNOW = "Snow"
    URBAN = "Urban"

    @classmethod
    def all_biomes(cls) -> list[BiomeType]:
        return list(cls)


# Standardized PBR/Debug visualization colors (RGB 0-255) for viewport and splatmap legends
BIOME_COLORS: dict[BiomeType, tuple[int, int, int]] = {
    BiomeType.GRASSLAND: (138, 182, 94),        # Lush temperate grassland
    BiomeType.FOREST: (64, 132, 68),            # Standard mixed woodland
    BiomeType.DENSE_FOREST: (28, 82, 44),       # Deep dark canopy forest
    BiomeType.ALPINE: (165, 180, 142),          # High alpine meadow / scrub
    BiomeType.ROCKY_MOUNTAINS: (124, 126, 132), # Steep granite crags & cliffs
    BiomeType.DESERT: (224, 192, 132),          # Arid dunes and dry steppes
    BiomeType.WETLANDS: (78, 130, 112),         # Marshes, swamps and bayous
    BiomeType.COASTAL: (212, 202, 156),         # Sandy beaches & coastal margins
    BiomeType.SNOW: (240, 245, 252),            # Permanent snowfields & glaciers
    BiomeType.URBAN: (160, 152, 146),           # Built environment & settlements
}

BIOME_INDEX: dict[BiomeType, int] = {b: i for i, b in enumerate(BiomeType.all_biomes())}
INDEX_TO_BIOME: dict[int, BiomeType] = dict(enumerate(BiomeType.all_biomes()))


@dataclass
class BiomeSample:
    """Evaluated biome parameters for a single spatial point."""
    dominant_biome: BiomeType
    weights: dict[BiomeType, float]
    elevation_m: float
    slope_deg: float
    aspect_deg: float
    moisture: float
    temperature_c: float
    water_distance_m: float
    is_urban: bool


@dataclass
class BiomeGridResult:
    """Complete 2D spatial biome evaluation result over a bounding box."""
    bounds_m: tuple[float, float, float, float]  # min_x, min_y, max_x, max_y
    resolution_m: float
    shape: tuple[int, int]                       # rows (Y), cols (X)
    dominant: np.ndarray                         # 2D array of BiomeType (or index)
    weights: np.ndarray                          # 3D array (10, H, W) of normalized float32 weights
    elevation: np.ndarray                        # 2D float64
    slope: np.ndarray                            # 2D float64 (degrees)
    aspect: np.ndarray                           # 2D float64 (degrees [0, 360))
    moisture: np.ndarray                         # 2D float64 ([0.0, 1.0])
    temperature: np.ndarray                      # 2D float64 (Celsius)
    water_distance: np.ndarray                   # 2D float64 (meters)
    urban_mask: np.ndarray                       # 2D bool

    def get_coverage_percentages(self) -> dict[str, float]:
        """Calculates area coverage percentage for each biome."""
        total_cells = float(self.dominant.size)
        if total_cells == 0:
            return {b.value: 0.0 for b in BiomeType.all_biomes()}

        counts: dict[str, float] = {}
        for b in BiomeType.all_biomes():
            idx = BIOME_INDEX[b]
            cnt = float(np.count_nonzero(self.dominant == idx))
            counts[b.value] = round((cnt / total_cells) * 100.0, 2)
        return counts

    def get_dominant_colors_rgb(self) -> np.ndarray:
        """Returns 2D RGB image array (H, W, 3) using blended biome weights."""
        h, w = self.shape
        rgb = np.zeros((h, w, 3), dtype=np.float32)
        for b in BiomeType.all_biomes():
            idx = BIOME_INDEX[b]
            w_layer = self.weights[idx][:, :, None]  # (H, W, 1)
            c = np.array(BIOME_COLORS[b], dtype=np.float32)[None, None, :]  # (1, 1, 3)
            rgb += w_layer * c
        return np.clip(rgb, 0, 255).astype(np.uint8)

    def sample_at_xy(self, x: float, y: float) -> BiomeSample:
        """Interpolates biome parameters at arbitrary continuous coordinates (x, y)."""
        min_x, min_y, max_x, max_y = self.bounds_m
        h, w = self.shape
        u = np.clip((x - min_x) / max(1e-6, max_x - min_x) * (w - 1), 0, w - 1)
        v = np.clip((y - min_y) / max(1e-6, max_y - min_y) * (h - 1), 0, h - 1)
        col = round(float(u))
        row = round(float(v))

        dom_idx = int(self.dominant[row, col])
        dom_biome = INDEX_TO_BIOME.get(dom_idx, BiomeType.GRASSLAND)

        weights_dict = {
            b: float(self.weights[BIOME_INDEX[b], row, col])
            for b in BiomeType.all_biomes()
        }

        return BiomeSample(
            dominant_biome=dom_biome,
            weights=weights_dict,
            elevation_m=float(self.elevation[row, col]),
            slope_deg=float(self.slope[row, col]),
            aspect_deg=float(self.aspect[row, col]),
            moisture=float(self.moisture[row, col]),
            temperature_c=float(self.temperature[row, col]),
            water_distance_m=float(self.water_distance[row, col]),
            is_urban=bool(self.urban_mask[row, col]),
        )
