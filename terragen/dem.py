"""Model digital al terenului din tile-uri Terrarium (AWS elevation-tiles-prod).

Fiecare pixel PNG codifică altitudinea:  h = (R * 256 + G + B / 256) - 32768
"""

from __future__ import annotations

import io
import math
import os
from pathlib import Path

import numpy as np
import requests
from PIL import Image

TILE_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
CACHE = Path(os.environ.get("TERRAGEN_CACHE", Path.home() / ".cache" / "terragen" / "dem"))


def _tile_x(lon, z: int):
    return (np.asarray(lon, dtype=np.float64) + 180.0) / 360.0 * (2**z)


def _tile_y(lat, z: int):
    r = np.radians(np.asarray(lat, dtype=np.float64))
    return (1.0 - np.log(np.tan(r) + 1.0 / np.cos(r)) / np.pi) / 2.0 * (2**z)


def _lon_of_tile_x(x: float, z: int) -> float:
    return x / (2**z) * 360.0 - 180.0


def _lat_of_tile_y(y: float, z: int) -> float:
    n = math.pi - 2.0 * math.pi * y / (2**z)
    return math.degrees(math.atan(math.sinh(n)))


class Dem:
    """Mozaic de tile-uri DEM + eșantionare biliniară în (lon, lat)."""

    def __init__(self, bbox, zoom: int = 15, pad_tiles: int = 1):
        s, w, n, e = bbox
        self.zoom = zoom
        x0 = math.floor(_tile_x(w, zoom)) - pad_tiles
        x1 = math.floor(_tile_x(e, zoom)) + pad_tiles
        y0 = math.floor(_tile_y(n, zoom)) - pad_tiles
        y1 = math.floor(_tile_y(s, zoom)) + pad_tiles

        self.px0, self.py0 = x0 * 256, y0 * 256
        rows, cols = (y1 - y0 + 1) * 256, (x1 - x0 + 1) * 256
        mosaic = np.zeros((rows, cols), dtype=np.float32)
        filled = np.zeros((rows, cols), dtype=bool)

        CACHE.mkdir(parents=True, exist_ok=True)
        session = requests.Session()
        session.headers["User-Agent"] = "terragen/1.0 (city mesh generator)"
        got = 0
        for ty in range(y0, y1 + 1):
            for tx in range(x0, x1 + 1):
                arr = self._get_tile(session, zoom, tx, ty)
                if arr is None:
                    continue
                oy, ox = (ty - y0) * 256, (tx - x0) * 256
                mosaic[oy : oy + 256, ox : ox + 256] = arr
                filled[oy : oy + 256, ox : ox + 256] = True
                got += 1
        if got == 0:
            raise RuntimeError("Nu am putut descărca niciun tile DEM.")
        if not filled.all():
            # zone lipsă -> completează cu vecinul cel mai apropiat (fallback simplu)
            mosaic[~filled] = np.nan
            self._fill_nan(mosaic)
        self.mosaic = mosaic
        self.bbox = bbox

    @staticmethod
    def _fill_nan(a: np.ndarray):
        """Completează golurile cu cel mai apropiat pixel valid (nu cu mediana — altfel
        apar trepte vizibile la marginea mozaicului)."""
        holes = np.isnan(a)
        if not holes.any():
            return
        idx = np.where(~holes)
        if not len(idx[0]):
            a[holes] = 0.0
            return
        from scipy.spatial import cKDTree

        tree = cKDTree(np.column_stack(idx))
        hy, hx = np.where(holes)
        _, near = tree.query(np.column_stack([hy, hx]), k=1)
        a[hy, hx] = a[idx[0][near], idx[1][near]]

    @staticmethod
    def _get_tile(session, z, x, y):
        f = CACHE / f"{z}_{x}_{y}.png"
        if f.exists():
            raw = f.read_bytes()
        else:
            url = TILE_URL.format(z=z, x=x, y=y)
            try:
                r = session.get(url, timeout=30)
                if r.status_code != 200:
                    return None
                raw = r.content
                tmp = f.with_suffix(".part")  # scriere atomică: două job-uri nu corup cache-ul
                tmp.write_bytes(raw)
                tmp.replace(f)
            except Exception:
                return None
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        r_, g_, b_ = (np.asarray(img, dtype=np.float32)[:, :, i] for i in range(3))
        return (r_ * 256.0 + g_ + b_ / 256.0) - 32768.0

    # ---------- eșantionare ----------
    def _px(self, lon, lat):
        z = self.zoom
        return (
            float(_tile_x(lon, z)) * 256.0 - self.px0,
            float(_tile_y(lat, z)) * 256.0 - self.py0,
        )

    def height(self, lon, lat) -> float:
        x, y = self._px(lon, lat)
        m = self.mosaic
        h, w = m.shape
        x = min(max(x, 0.0), w - 1.001)
        y = min(max(y, 0.0), h - 1.001)
        x0, y0 = int(x), int(y)
        fx, fy = x - x0, y - y0
        return float(
            m[y0, x0] * (1 - fx) * (1 - fy)
            + m[y0, x0 + 1] * fx * (1 - fy)
            + m[y0 + 1, x0] * (1 - fx) * fy
            + m[y0 + 1, x0 + 1] * fx * fy
        )

    def heights(self, lons, lats) -> np.ndarray:
        lons = np.asarray(lons, dtype=np.float64)
        lats = np.asarray(lats, dtype=np.float64)
        out = np.empty(lons.shape, dtype=np.float64)
        flat_lon, flat_lat = lons.ravel(), lats.ravel()
        z = self.zoom
        xs = np.asarray(_tile_x(flat_lon, z), dtype=np.float64) * 256.0 - self.px0
        ys = np.asarray(_tile_y(flat_lat, z), dtype=np.float64) * 256.0 - self.py0
        m = self.mosaic
        h, w = m.shape
        xs = np.clip(xs, 0, w - 1.001)
        ys = np.clip(ys, 0, h - 1.001)
        x0 = xs.astype(np.int32)
        y0 = ys.astype(np.int32)
        fx, fy = xs - x0, ys - y0
        vals = (
            m[y0, x0] * (1 - fx) * (1 - fy)
            + m[y0, x0 + 1] * fx * (1 - fy)
            + m[y0 + 1, x0] * (1 - fx) * fy
            + m[y0 + 1, x0 + 1] * fx * fy
        )
        out[:] = vals.reshape(lons.shape)
        return out

    def meters_per_pixel(self, lat: float) -> float:
        return 156543.03392 * math.cos(math.radians(lat)) / (2**self.zoom)
