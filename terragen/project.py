"""Proiecție locală: WGS84 (lon/lat) -> metri (E, N) față de un punct de origine."""

from __future__ import annotations

import math

from pyproj import Transformer


class LocalProjection:
    """Proiecție Transverse Mercator centrată pe oraș -> coordonate metrice locale.

    x = Est (m), y = Nord (m), origine în (lat0, lon0).
    """

    def __init__(self, lat0: float, lon0: float):
        self.lat0, self.lon0 = lat0, lon0
        proj = (
            f"+proj=tmerc +lat_0={lat0} +lon_0={lon0} +k=1 +x_0=0 +y_0=0 "
            f"+ellps=WGS84 +units=m +no_defs"
        )
        self._t = Transformer.from_crs("EPSG:4326", proj, always_xy=True)
        self._inv = Transformer.from_crs(proj, "EPSG:4326", always_xy=True)

    def to_xy(self, lon, lat):
        """Scalar sau vectorizat (liste/array) -> (x, y) în metri."""
        return self._t.transform(lon, lat)

    def to_lonlat(self, x, y):
        """Invers: metri locali -> (lon, lat)."""
        return self._inv.transform(x, y)

    def bbox_of_square(self, size_m: float):
        """Bounding box (s, w, n, e) pentru un pătrat de `size_m` metri centrat pe origine."""
        half = size_m / 2.0
        dlat = half / 111_320.0
        dlon = half / (111_320.0 * math.cos(math.radians(self.lat0)))
        return (self.lat0 - dlat, self.lon0 - dlon, self.lat0 + dlat, self.lon0 + dlon)
