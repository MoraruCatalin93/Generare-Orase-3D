"""Core Biome Generator Engine for Large-Scale Game Worlds.

Coordinates environmental analysis, fuzzy rule membership, seedable boundary perturbation,
manual overrides, and seamless multi-chunk evaluation.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from ..osmdata import Way
from ..project import LocalProjection
from .environmental import EnvironmentalAnalyzer
from .noise import coherent_noise_2d
from .overrides import BiomeOverride, BiomeOverrideManager
from .rules import BiomeRule, create_default_biome_rules
from .types import BIOME_INDEX, BiomeGridResult, BiomeSample, BiomeType


class BiomeGenerator:
    """Procedural Terrain & Biome Generator for large worlds."""

    def __init__(
        self,
        seed: int = 42,
        rules: dict[BiomeType, BiomeRule] | None = None,
        overrides: BiomeOverrideManager | None = None,
        noise_scale_m: float = 240.0,
        noise_amplitude: float = 0.12,
        base_temperature_c: float = 18.0,
        base_moisture: float = 0.50,
    ):
        self.seed = int(seed)
        self.rules = dict(rules or create_default_biome_rules())
        self.overrides = overrides or BiomeOverrideManager()
        self.noise_scale_m = float(noise_scale_m)
        self.noise_amplitude = float(noise_amplitude)

        self.analyzer = EnvironmentalAnalyzer(
            base_temperature_c=base_temperature_c,
            base_moisture=base_moisture,
        )

    def add_override(self, override: BiomeOverride) -> None:
        """Adds a manual biome polygon override."""
        self.overrides.add_override(override)

    def evaluate_grid(
        self,
        bounds_m: tuple[float, float, float, float],
        resolution_m: float,
        dem: Any,
        proj: LocalProjection,
        ways: list[Way] | None = None,
        climate_override: dict[str, Any] | None = None,
    ) -> BiomeGridResult:
        """Evaluates procedural biomes across a 2D bounding box (min_x, min_y, max_x, max_y).

        Returns a BiomeGridResult with continuous normalized weights for all 10 biomes.
        """
        min_x, min_y, max_x, max_y = bounds_m
        step = max(1.0, float(resolution_m))
        cols = max(2, round((max_x - min_x) / step) + 1)
        rows = max(2, round((max_y - min_y) / step) + 1)

        xs = np.linspace(min_x, max_x, cols, dtype=np.float64)
        ys = np.linspace(min_y, max_y, rows, dtype=np.float64)
        X, Y = np.meshgrid(xs, ys)

        # 1. Environmental Analysis
        env = self.analyzer.analyze(
            bounds_m=bounds_m,
            resolution_m=resolution_m,
            dem=dem,
            proj=proj,
            ways=ways,
            climate_override=climate_override,
        )

        elevation = env["elevation"]
        slope = env["slope"]
        aspect = env["aspect"]
        moisture = env["moisture"]
        temperature = env["temperature"]
        water_dist = env["water_distance"]
        urban_mask = env["urban_mask"]

        # 2. Coherent Micro-Noise Perturbation for Natural Meandering Transitions
        if self.noise_amplitude > 0:
            c_noise = coherent_noise_2d(
                X,
                Y,
                scale=self.noise_scale_m,
                seed=self.seed,
                octaves=3,
            )
            # Modulate moisture and elevation slightly at boundary evaluation
            moist_perturbed = np.clip(moisture + c_noise * self.noise_amplitude, 0.0, 1.0)
            elev_perturbed = elevation + c_noise * (self.noise_amplitude * 40.0)
        else:
            moist_perturbed = moisture
            elev_perturbed = elevation

        # 3. Rule Evaluation per Biome
        all_biomes = BiomeType.all_biomes()
        num_biomes = len(all_biomes)
        raw_weights = np.zeros((num_biomes, rows, cols), dtype=np.float64)

        for b in all_biomes:
            rule = self.rules.get(b)
            if not rule:
                continue
            idx = BIOME_INDEX[b]
            raw_weights[idx] = rule.evaluate(
                elevation=elev_perturbed,
                slope=slope,
                moisture=moist_perturbed,
                temperature=temperature,
                water_distance=water_dist,
                urban_mask=urban_mask,
            )

        # Ensure base baseline so no cell has sum 0
        raw_weights[BIOME_INDEX[BiomeType.GRASSLAND]] += 0.05

        # 4. Apply Manual Overrides
        overridden_weights = self.overrides.apply(xs, ys, raw_weights)

        # 5. Normalization (Weights sum to 1.0 at every grid cell)
        total_w = np.sum(overridden_weights, axis=0, keepdims=True)
        total_w[total_w == 0] = 1.0
        normalized_weights = np.clip(overridden_weights / total_w, 0.0, 1.0).astype(np.float32)

        # 6. Dominant Biome Index
        dominant_indices = np.argmax(normalized_weights, axis=0)

        return BiomeGridResult(
            bounds_m=bounds_m,
            resolution_m=resolution_m,
            shape=(rows, cols),
            dominant=dominant_indices,
            weights=normalized_weights,
            elevation=elevation,
            slope=slope,
            aspect=aspect,
            moisture=moisture,
            temperature=temperature,
            water_distance=water_dist,
            urban_mask=urban_mask,
        )

    def sample_point(
        self,
        x: float,
        y: float,
        dem: Any,
        proj: LocalProjection,
        ways: list[Way] | None = None,
    ) -> BiomeSample:
        """Evaluates exact biome parameters at an arbitrary continuous coordinate (x, y)."""
        res = self.evaluate_grid(
            bounds_m=(x - 5.0, y - 5.0, x + 5.0, y + 5.0),
            resolution_m=5.0,
            dem=dem,
            proj=proj,
            ways=ways,
        )
        return res.sample_at_xy(x, y)
