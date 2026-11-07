"""Manual Biome Overrides and Spatial Modification Stack.

Allows game designers to author custom polygon zones overriding procedural biomes
with continuous boundary feathering/blending.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from shapely.geometry import Polygon
from shapely.prepared import prep

from .types import BIOME_INDEX, BiomeType


@dataclass
class BiomeOverride:
    """A manual spatial polygon override for biome assignment."""

    biome: BiomeType
    polygon_xy: list[tuple[float, float]]  # [(x0, y0), (x1, y1), ...] in metric space
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    blend_distance_m: float = 40.0         # Feathering distance in meters
    weight: float = 1.0                    # Strength of override (0.0 to 1.0)
    enabled: bool = True
    label: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "biome": self.biome.value,
            "polygon_xy": [list(pt) for pt in self.polygon_xy],
            "blend_distance_m": self.blend_distance_m,
            "weight": self.weight,
            "enabled": self.enabled,
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BiomeOverride:
        b_str = data.get("biome", "Grassland")
        biome = BiomeType(b_str) if b_str in [b.value for b in BiomeType] else BiomeType.GRASSLAND
        pts = [tuple(p) for p in data.get("polygon_xy", [])]
        return cls(
            id=str(data.get("id", uuid.uuid4().hex[:8])),
            biome=biome,
            polygon_xy=pts,  # type: ignore
            blend_distance_m=float(data.get("blend_distance_m", 40.0)),
            weight=float(data.get("weight", 1.0)),
            enabled=bool(data.get("enabled", True)),
            label=str(data.get("label", "")),
        )


class BiomeOverrideManager:
    """Manages an ordered, reversible stack of manual biome overrides."""

    def __init__(self, overrides: list[BiomeOverride] | None = None):
        self._overrides: list[BiomeOverride] = list(overrides or [])

    @property
    def items(self) -> list[BiomeOverride]:
        return list(self._overrides)

    def add_override(self, override: BiomeOverride) -> None:
        self._overrides.append(override)

    def remove_override(self, override_id: str) -> bool:
        initial_len = len(self._overrides)
        self._overrides = [ov for ov in self._overrides if ov.id != override_id]
        return len(self._overrides) < initial_len

    def clear(self) -> None:
        self._overrides.clear()

    def apply(
        self,
        xs: np.ndarray,
        ys: np.ndarray,
        weights: np.ndarray,
    ) -> np.ndarray:
        """Applies enabled overrides onto a 3D biome weights array (10, H, W).

        Transitions are feathered smoothly using blend_distance_m.
        Returns modified weights array with shape (10, H, W).
        """
        if not self._overrides:
            return weights

        X, Y = np.meshgrid(xs, ys)
        _h, _w = X.shape
        out_weights = weights.copy()

        for ov in self._overrides:
            if not ov.enabled or len(ov.polygon_xy) < 3:
                continue

            poly = Polygon(ov.polygon_xy)
            if not poly.is_valid:
                poly = poly.buffer(0)
            if poly.is_empty:
                continue

            min_poly_x, min_poly_y, max_poly_x, max_poly_y = poly.bounds
            feather = max(1.0, ov.blend_distance_m)

            # Bounding box cull for fast execution
            in_bbox = (
                (min_poly_x - feather <= X)
                & (max_poly_x + feather >= X)
                & (min_poly_y - feather <= Y)
                & (max_poly_y + feather >= Y)
            )

            if not np.any(in_bbox):
                continue

            target_idx = BIOME_INDEX[ov.biome]
            prep_poly = prep(poly)

            # Compute blend mask for cells in bounding box
            r_indices, c_indices = np.nonzero(in_bbox)
            for r_idx, c_idx in zip(r_indices, c_indices, strict=False):
                from shapely.geometry import Point
                pt = Point(float(X[r_idx, c_idx]), float(Y[r_idx, c_idx]))

                if prep_poly.contains(pt):
                    # Inside polygon: full override factor
                    factor = float(ov.weight)
                else:
                    dist = poly.distance(pt)
                    if dist <= feather:
                        # Smoothstep feathering
                        t = (feather - dist) / feather
                        factor = float(ov.weight) * (t * t * (3.0 - 2.0 * t))
                    else:
                        factor = 0.0

                if factor > 0.0:
                    # Blend towards target biome
                    for b_i in range(out_weights.shape[0]):
                        if b_i == target_idx:
                            out_weights[b_i, r_idx, c_idx] = (
                                out_weights[b_i, r_idx, c_idx] * (1.0 - factor) + 1.0 * factor
                            )
                        else:
                            out_weights[b_i, r_idx, c_idx] *= (1.0 - factor)

        return out_weights

    def to_dict_list(self) -> list[dict[str, Any]]:
        return [ov.to_dict() for ov in self._overrides]

    @classmethod
    def from_dict_list(cls, data: list[dict[str, Any]]) -> BiomeOverrideManager:
        return cls([BiomeOverride.from_dict(d) for d in data])
