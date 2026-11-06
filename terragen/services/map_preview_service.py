"""Service for extracting building outlines and height styles for map preview."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from terragen.map_style import map_building_style
from terragen.osmdata import read_osm


class MapPreviewService:
    """Manages cached GeoJSON feature collection previews from local OSM PBF extracts."""

    def __init__(self):
        self.lock = threading.Lock()
        self.cache: dict[tuple, dict[str, Any]] = {}

    def get_building_features(
        self, pbf_path: str | Path, center_lat: float, center_lon: float
    ) -> tuple[dict[str, Any] | None, str | None, int]:
        lat = round(float(center_lat), 2)
        lon = round(float(center_lon), 2)
        pbf_file = Path(pbf_path)
        try:
            st = pbf_file.stat()
        except OSError:
            return None, "Fișierul PBF nu poate fi citit", 404

        key = (str(pbf_file.resolve()), st.st_size, st.st_mtime_ns, lat, lon)
        if key in self.cache:
            return self.cache[key], None, 200

        if not self.lock.acquire(blocking=False):
            return None, "Citirea PBF este deja în curs", 429

        try:
            if key in self.cache:
                return self.cache[key], None, 200

            bbox = (lat - 0.04, lon - 0.06, lat + 0.04, lon + 0.06)
            ways = read_osm(str(pbf_file), bbox)
            features = []
            for way in ways:
                tags = way.tags
                if not (
                    (tags.get("building") and tags.get("building") != "no")
                    or (tags.get("building:part") and tags.get("building:part") != "no")
                ):
                    continue
                rings = way.rings if way.rings else [way.coords]
                if not rings or any(len(ring) < 4 or ring[0] != ring[-1] for ring in rings):
                    continue
                features.append(
                    {
                        "type": "Feature",
                        "properties": map_building_style(tags, way.id),
                        "geometry": {"type": "Polygon", "coordinates": rings},
                    }
                )
                if len(features) >= 60000:
                    break

            result = {"type": "FeatureCollection", "features": features}
            if len(self.cache) >= 8:
                self.cache.pop(next(iter(self.cache)))
            self.cache[key] = result
            return result, None, 200
        finally:
            self.lock.release()

