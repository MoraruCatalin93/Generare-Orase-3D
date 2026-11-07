"""Deterministic 2D Coherent Noise Generator for Biome Transition Boundaries.

Provides vectorized, deterministic multi-octave gradient/value noise over NumPy arrays.
Guarantees identical floating-point values at identical spatial coordinates (chunk-boundary continuous).
"""

from __future__ import annotations

import numpy as np


def _fade(t: np.ndarray) -> np.ndarray:
    """Quintic hermite fade curve: 6t^5 - 15t^4 + 10t^3."""
    return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)


def _hash2d(x: np.ndarray, y: np.ndarray, seed: int) -> np.ndarray:
    """Deterministic integer hash function mapping 2D grid coordinates to values in [-1, 1]."""
    # 32-bit integer arithmetic
    h = (seed * 374761393 + x * 668265263 + y * 12741261) & 0xFFFFFFFF
    h = (h ^ (h >> 13)) * 12741261
    h = h ^ (h >> 16)
    # Map to [-1.0, 1.0]
    return ((h & 0xFFFF) / 32767.5) - 1.0


def coherent_noise_2d(
    x: np.ndarray,
    y: np.ndarray,
    scale: float = 200.0,
    seed: int = 42,
    octaves: int = 3,
    persistence: float = 0.5,
    lacunarity: float = 2.0,
) -> np.ndarray:
    """Evaluates 2D coherent noise over continuous coordinate arrays x and y.

    Returns an array of shape matching x and y with values approximately in [-1.0, 1.0].
    """
    total = np.zeros_like(x, dtype=np.float64)
    freq = 1.0 / max(1e-4, float(scale))
    amp = 1.0
    max_amp = 0.0

    cur_seed = seed

    for _ in range(octaves):
        nx = x * freq
        ny = y * freq

        ix = np.floor(nx).astype(np.int64)
        iy = np.floor(ny).astype(np.int64)

        fx = nx - ix
        fy = ny - iy

        wx = _fade(fx)
        wy = _fade(fy)

        # Hash corners
        v00 = _hash2d(ix, iy, cur_seed)
        v10 = _hash2d(ix + 1, iy, cur_seed)
        v01 = _hash2d(ix, iy + 1, cur_seed)
        v11 = _hash2d(ix + 1, iy + 1, cur_seed)

        # Bilinear interpolation with quintic weights
        bot = v00 * (1.0 - wx) + v10 * wx
        top = v01 * (1.0 - wx) + v11 * wx
        layer = bot * (1.0 - wy) + top * wy

        total += layer * amp
        max_amp += amp

        freq *= lacunarity
        amp *= persistence
        cur_seed = (cur_seed * 1664525 + 1013904223) & 0xFFFFFFFF

    if max_amp > 0:
        total /= max_amp

    return total

