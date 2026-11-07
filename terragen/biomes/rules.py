"""Configurable Biome Rules and Continuous Fuzzy Membership Scoring."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from .types import BiomeType


def smooth_trapezoid(
    val: np.ndarray,
    min_val: float | None,
    max_val: float | None,
    falloff: float,
) -> np.ndarray:
    """Calculates continuous C1-smooth membership score in [0.0, 1.0].

    Uses smooth cosine S-curves over the transition falloff zones.
    """
    score = np.ones_like(val, dtype=np.float64)
    falloff = max(1e-5, float(falloff))

    if min_val is not None:
        left_edge = min_val - falloff
        # Cells below left_edge get 0.0
        score = np.where(val < left_edge, 0.0, score)
        # Transition zone [left_edge, min_val]
        in_left_trans = (val >= left_edge) & (val < min_val)
        if np.any(in_left_trans):
            t = (val[in_left_trans] - left_edge) / falloff
            # Smoothstep 3t^2 - 2t^3
            score[in_left_trans] *= t * t * (3.0 - 2.0 * t)

    if max_val is not None:
        right_edge = max_val + falloff
        # Cells above right_edge get 0.0
        score = np.where(val > right_edge, 0.0, score)
        # Transition zone [max_val, right_edge]
        in_right_trans = (val > max_val) & (val <= right_edge)
        if np.any(in_right_trans):
            t = (right_edge - val[in_right_trans]) / falloff
            score[in_right_trans] *= t * t * (3.0 - 2.0 * t)

    return score


@dataclass
class BiomeRule:
    """Configurable environmental rules defining a single biome's ecological niche."""

    biome: BiomeType
    elevation_range: tuple[float | None, float | None] = (None, None)
    elevation_falloff: float = 60.0
    slope_range: tuple[float | None, float | None] = (None, None)
    slope_falloff: float = 4.0
    moisture_range: tuple[float | None, float | None] = (None, None)
    moisture_falloff: float = 0.08
    temperature_range: tuple[float | None, float | None] = (None, None)
    temperature_falloff: float = 2.5
    water_distance_max_m: float | None = None
    water_distance_falloff_m: float = 40.0
    requires_urban: bool = False
    base_weight: float = 1.0

    def evaluate(
        self,
        elevation: np.ndarray,
        slope: np.ndarray,
        moisture: np.ndarray,
        temperature: np.ndarray,
        water_distance: np.ndarray,
        urban_mask: np.ndarray,
    ) -> np.ndarray:
        """Evaluates spatial membership weights for this biome across all environmental inputs.

        Returns a 2D float64 array of scores >= 0.0.
        """
        # 1. Urban check
        if self.requires_urban:
            return np.where(urban_mask, float(self.base_weight) * 2.5, 0.0)

        # Non-urban biomes are suppressed where urban infrastructure exists
        score = np.where(urban_mask, 0.05, float(self.base_weight))

        # 2. Elevation score
        if self.elevation_range != (None, None):
            s_elev = smooth_trapezoid(
                elevation,
                self.elevation_range[0],
                self.elevation_range[1],
                self.elevation_falloff,
            )
            score *= s_elev

        # 3. Slope score
        if self.slope_range != (None, None):
            s_slope = smooth_trapezoid(
                slope,
                self.slope_range[0],
                self.slope_range[1],
                self.slope_falloff,
            )
            score *= s_slope

        # 4. Moisture score
        if self.moisture_range != (None, None):
            s_moist = smooth_trapezoid(
                moisture,
                self.moisture_range[0],
                self.moisture_range[1],
                self.moisture_falloff,
            )
            score *= s_moist

        # 5. Temperature score
        if self.temperature_range != (None, None):
            s_temp = smooth_trapezoid(
                temperature,
                self.temperature_range[0],
                self.temperature_range[1],
                self.temperature_falloff,
            )
            score *= s_temp

        # 6. Water distance score
        if self.water_distance_max_m is not None:
            s_water = smooth_trapezoid(
                water_distance,
                min_val=None,
                max_val=self.water_distance_max_m,
                falloff=self.water_distance_falloff_m,
            )
            score *= s_water

        return score

    def to_dict(self) -> dict[str, Any]:
        return {
            "biome": self.biome.value,
            "elevation_range": list(self.elevation_range),
            "elevation_falloff": self.elevation_falloff,
            "slope_range": list(self.slope_range),
            "slope_falloff": self.slope_falloff,
            "moisture_range": list(self.moisture_range),
            "moisture_falloff": self.moisture_falloff,
            "temperature_range": list(self.temperature_range),
            "temperature_falloff": self.temperature_falloff,
            "water_distance_max_m": self.water_distance_max_m,
            "water_distance_falloff_m": self.water_distance_falloff_m,
            "requires_urban": self.requires_urban,
            "base_weight": self.base_weight,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BiomeRule:
        b_val = data.get("biome", "Grassland")
        biome_type = BiomeType(b_val) if b_val in [b.value for b in BiomeType] else BiomeType.GRASSLAND
        return cls(
            biome=biome_type,
            elevation_range=tuple(data.get("elevation_range", (None, None))),  # type: ignore
            elevation_falloff=float(data.get("elevation_falloff", 60.0)),
            slope_range=tuple(data.get("slope_range", (None, None))),          # type: ignore
            slope_falloff=float(data.get("slope_falloff", 4.0)),
            moisture_range=tuple(data.get("moisture_range", (None, None))),    # type: ignore
            moisture_falloff=float(data.get("moisture_falloff", 0.08)),
            temperature_range=tuple(data.get("temperature_range", (None, None))), # type: ignore
            temperature_falloff=float(data.get("temperature_falloff", 2.5)),
            water_distance_max_m=data.get("water_distance_max_m"),
            water_distance_falloff_m=float(data.get("water_distance_falloff_m", 40.0)),
            requires_urban=bool(data.get("requires_urban", False)),
            base_weight=float(data.get("base_weight", 1.0)),
        )


def create_default_biome_rules() -> dict[BiomeType, BiomeRule]:
    """Factory creating balanced, geographically coherent rules for all 10 biomes."""
    return {
        # 1. Snow / Glacier: High altitude (>2200m) or sub-zero temperature
        BiomeType.SNOW: BiomeRule(
            biome=BiomeType.SNOW,
            elevation_range=(2100.0, None),
            elevation_falloff=100.0,
            temperature_range=(None, 0.0),
            temperature_falloff=3.0,
            base_weight=1.5,
        ),
        # 2. Rocky Mountains: Steep crags and scree slopes (>28 deg)
        BiomeType.ROCKY_MOUNTAINS: BiomeRule(
            biome=BiomeType.ROCKY_MOUNTAINS,
            elevation_range=(600.0, 2600.0),
            elevation_falloff=100.0,
            slope_range=(26.0, 90.0),
            slope_falloff=4.0,
            base_weight=1.4,
        ),
        # 3. Alpine: High elevation above timberline (1600m - 2400m), moderate slopes
        BiomeType.ALPINE: BiomeRule(
            biome=BiomeType.ALPINE,
            elevation_range=(1550.0, 2300.0),
            elevation_falloff=80.0,
            slope_range=(0.0, 32.0),
            slope_falloff=5.0,
            temperature_range=(-2.0, 10.0),
            temperature_falloff=3.0,
            base_weight=1.2,
        ),
        # 4. Dense Forest: High moisture (>0.58), temperate climate, sheltered valleys
        BiomeType.DENSE_FOREST: BiomeRule(
            biome=BiomeType.DENSE_FOREST,
            elevation_range=(100.0, 1500.0),
            elevation_falloff=80.0,
            slope_range=(0.0, 30.0),
            slope_falloff=4.0,
            moisture_range=(0.58, 1.0),
            moisture_falloff=0.08,
            temperature_range=(6.0, 22.0),
            temperature_falloff=2.5,
            base_weight=1.25,
        ),
        # 5. Forest: Standard woodland, moderate moisture (0.40 - 0.68)
        BiomeType.FOREST: BiomeRule(
            biome=BiomeType.FOREST,
            elevation_range=(40.0, 1700.0),
            elevation_falloff=70.0,
            slope_range=(0.0, 34.0),
            slope_falloff=5.0,
            moisture_range=(0.40, 0.70),
            moisture_falloff=0.08,
            temperature_range=(4.0, 24.0),
            temperature_falloff=3.0,
            base_weight=1.1,
        ),
        # 6. Wetlands: Lowlands near water, flat terrain (<7 deg), high moisture
        BiomeType.WETLANDS: BiomeRule(
            biome=BiomeType.WETLANDS,
            elevation_range=(0.0, 350.0),
            elevation_falloff=40.0,
            slope_range=(0.0, 7.0),
            slope_falloff=2.0,
            moisture_range=(0.65, 1.0),
            moisture_falloff=0.07,
            water_distance_max_m=140.0,
            water_distance_falloff_m=50.0,
            base_weight=1.35,
        ),
        # 7. Coastal: Low elevation shoreline (<20m), immediate water proximity (<80m)
        BiomeType.COASTAL: BiomeRule(
            biome=BiomeType.COASTAL,
            elevation_range=(0.0, 22.0),
            elevation_falloff=8.0,
            slope_range=(0.0, 16.0),
            slope_falloff=3.0,
            water_distance_max_m=85.0,
            water_distance_falloff_m=35.0,
            base_weight=1.4,
        ),
        # 8. Desert: Arid conditions, low moisture (<0.22), warm temperatures
        BiomeType.DESERT: BiomeRule(
            biome=BiomeType.DESERT,
            elevation_range=(0.0, 1400.0),
            elevation_falloff=100.0,
            slope_range=(0.0, 24.0),
            slope_falloff=4.0,
            moisture_range=(0.0, 0.22),
            moisture_falloff=0.06,
            temperature_range=(16.0, 48.0),
            temperature_falloff=3.0,
            base_weight=1.3,
        ),
        # 9. Grassland: Broad temperate plains and rolling foothills
        BiomeType.GRASSLAND: BiomeRule(
            biome=BiomeType.GRASSLAND,
            elevation_range=(0.0, 1400.0),
            elevation_falloff=100.0,
            slope_range=(0.0, 22.0),
            slope_falloff=4.0,
            moisture_range=(0.20, 0.55),
            moisture_falloff=0.08,
            temperature_range=(8.0, 30.0),
            temperature_falloff=3.0,
            base_weight=1.0,
        ),
        # 10. Urban: Settlement footprint and road network
        BiomeType.URBAN: BiomeRule(
            biome=BiomeType.URBAN,
            requires_urban=True,
            base_weight=2.0,
        ),
    }

