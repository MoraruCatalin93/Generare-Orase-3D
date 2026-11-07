"""Spatial Hash Grid for Fast O(1) Collision & Overlap Prevention."""

from __future__ import annotations

import math


class SpatialHashGrid:
    """2D spatial bucket grid for collision footprint queries."""

    def __init__(self, cell_size_m: float = 4.0):
        self.cell_size = max(1.0, float(cell_size_m))
        self.inv_cell = 1.0 / self.cell_size
        self._grid: dict[tuple[int, int], list[tuple[float, float, float]]] = {}
        self.count = 0

    def insert(self, x: float, z: float, radius: float) -> None:
        """Inserts an obstacle footprint into the spatial grid."""
        cx = math.floor(x * self.inv_cell)
        cz = math.floor(z * self.inv_cell)
        key = (cx, cz)
        if key not in self._grid:
            self._grid[key] = []
        self._grid[key].append((x, z, radius))
        self.count += 1

    def can_place(self, x: float, z: float, radius: float) -> bool:
        """Checks if placing an object at (x, z) with radius collides with any existing footprint."""
        cx = math.floor(x * self.inv_cell)
        cz = math.floor(z * self.inv_cell)

        # Check neighborhood cells (typically 3x3)
        check_range = math.ceil(radius * self.inv_cell) + 1

        for dx in range(-check_range, check_range + 1):
            for dz in range(-check_range, check_range + 1):
                bucket = self._grid.get((cx + dx, cz + dz))
                if not bucket:
                    continue
                for ox, oz, orad in bucket:
                    min_dist = radius + orad
                    # Squared distance comparison
                    dist_sq = (x - ox) ** 2 + (z - oz) ** 2
                    if dist_sq < (min_dist * min_dist):
                        return False
        return True

    def clear(self) -> None:
        self._grid.clear()
        self.count = 0
