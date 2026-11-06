import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from terragen.osmdata import Way  # noqa: E402


class FakeDem:
    """DEM sintetic: relieve netedă, fără rețea."""

    def __init__(self, *a, **k):
        self.mosaic = np.zeros((16, 16))

    def heights(self, lons, lats):
        lons = np.asarray(lons, dtype=float)
        lats = np.asarray(lats, dtype=float)
        return 320.0 + 6.0 * np.sin(lons * 2500) * np.cos(lats * 2500)

    def height(self, lon, lat):
        return float(self.heights([lon], [lat])[0])

    def meters_per_pixel(self, lat):
        return 4.0


LAT0, LON0 = 46.5446, 24.5612


def _grid(lat0=LAT0, lon0=LON0, n=5, step=0.0009, length=90):
    ways = []
    for i in range(-n, n + 1):
        ways.append(
            Way(
                id=1000 + i,
                tags={"highway": "residential", "name": f"st{i}"},
                coords=[
                    (lon0 + i * step + j * 0.00009, lat0 - 0.0036 + j * 0.000008)
                    for j in range(length)
                ],
            )
        )
        ways.append(
            Way(
                id=2000 + i,
                tags={"highway": "primary", "lanes": "2"},
                coords=[
                    (lon0 - 0.0036 + j * 0.000008, lat0 + i * step + j * 0.00009)
                    for j in range(length)
                ],
            )
        )
        ways.append(
            Way(
                id=3000 + i,
                tags={"highway": "footway"},
                coords=[
                    (lon0 - 0.0036 + j * 0.000008, lat0 + i * step + 0.00022 + j * 0.00009)
                    for j in range(length)
                ],
            )
        )
    k = 0
    for a in range(-3, 4):
        for b in range(-3, 4):
            k += 1
            cx, cy = lon0 + a * step + 0.00045, lat0 + b * step + 0.00045
            d = 0.00009 * (0.7 + 0.05 * (k % 7))
            ways.append(
                Way(
                    id=5000 + k,
                    tags={"building": "apartments" if k % 4 == 0 else "house"},
                    coords=[
                        (cx - d, cy - d),
                        (cx + d, cy - d),
                        (cx + d, cy + d),
                        (cx - d, cy + d),
                        (cx - d, cy - d),
                    ],
                )
            )
    ways.append(
        Way(
            id=9001,
            tags={"leisure": "park"},
            coords=[
                (lon0 - 0.003, lat0 - 0.003),
                (lon0 - 0.001, lat0 - 0.003),
                (lon0 - 0.001, lat0 - 0.001),
                (lon0 - 0.003, lat0 - 0.001),
                (lon0 - 0.003, lat0 - 0.003),
            ],
        )
    )
    return ways


@pytest.fixture
def ways():
    return _grid()


@pytest.fixture
def fake_dem():
    return FakeDem()


@pytest.fixture
def built(tmp_path, ways, fake_dem, monkeypatch):
    """Generează un oraș mic, offline, și întoarce (out_dir, manifest)."""
    import terragen.cli as cli

    monkeypatch.setattr(cli, "Dem", lambda *a, **k: fake_dem)
    out = tmp_path / "oras"
    manifest = cli.generate(
        center=(LAT0, LON0),
        size=600,
        out=str(out),
        ways=ways,
        formats=("glb",),
        log=lambda *_: None,
    )
    return out, manifest
