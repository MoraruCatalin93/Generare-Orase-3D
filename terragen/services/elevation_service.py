"""Service for querying real-world elevations under cursor or coordinates."""

from __future__ import annotations

from typing import Any

from terragen.dem import Dem


class ElevationService:
    """Caches DEM mosaic queries for real-time terrain altitude inspection."""

    def __init__(self):
        self._cache: dict[tuple[float, float, float, float], Dem] = {}

    def get_elevations(self, points: list[list[float]]) -> list[float]:
        if not points:
            return []
        lats = [float(p[0]) for p in points]
        lons = [float(p[1]) for p in points]

        s, w = min(lats) - 0.006, min(lons) - 0.008
        n, e = max(lats) + 0.006, max(lons) + 0.008
        key = (round(s, 3), round(w, 3), round(n, 3), round(e, 3))

        dem = self._cache.get(key)
        if dem is None:
            dem = Dem((s, w, n, e), zoom=14)
            self._cache.clear()
            self._cache[key] = dem

        return [round(dem.height(lo, la), 1) for lo, la in zip(lons, lats, strict=False)]

