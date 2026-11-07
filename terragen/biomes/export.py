"""Blender and Game Engine Export Utilities for Biome Maps and Splatmaps."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from .types import BIOME_COLORS, BIOME_INDEX, BiomeGridResult, BiomeType


def generate_splatmap_textures(
    grid_result: BiomeGridResult,
    output_dir: Path | str,
    stem: str = "biome",
) -> dict[str, Any]:
    """Exports 8-bit RGBA Splatmap PNGs and metadata manifest for Blender shader networks.

    Standard game engine terrain shaders (Blender, Unity, Unreal) use RGBA splatmaps
    where each channel represents the blend weight of a specific ground material.
    """
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    # 1. Rank biomes by global presence in this grid
    coverage = grid_result.get_coverage_percentages()
    ranked_biomes = sorted(
        BiomeType.all_biomes(),
        key=lambda b: coverage.get(b.value, 0.0),
        reverse=True,
    )

    # Primary 4 biomes map to R, G, B, A of Splatmap 0
    primary_4 = ranked_biomes[:4]
    h, w = grid_result.shape
    splat0 = np.zeros((h, w, 4), dtype=np.uint8)

    channel_mapping: list[dict[str, Any]] = []

    for c_idx, b in enumerate(primary_4):
        b_idx = BIOME_INDEX[b]
        w_channel = grid_result.weights[b_idx]
        splat0[:, :, c_idx] = np.clip(w_channel * 255.0, 0, 255).astype(np.uint8)
        channel_mapping.append({
            "splatmap": f"{stem}_splatmap_0.png",
            "channel": ["R", "G", "B", "A"][c_idx],
            "biome": b.value,
            "coverage_pct": coverage.get(b.value, 0.0),
            "color_rgb": list(BIOME_COLORS[b]),
        })

    splat0_file = out_path / f"{stem}_splatmap_0.png"
    Image.fromarray(splat0).save(splat0_file)

    # 2. Dominant Biome Visual Color Map (RGB Preview PNG)
    color_map = grid_result.get_dominant_colors_rgb()
    colormap_file = out_path / f"{stem}_colormap.png"
    Image.fromarray(color_map).save(colormap_file)

    # 3. Biome Distribution Metadata Manifest
    manifest_data = {
        "generator": "Terragen Game World Generator - Biome Engine",
        "bounds_m": list(grid_result.bounds_m),
        "resolution_m": grid_result.resolution_m,
        "grid_shape": list(grid_result.shape),
        "coverage_percentages": coverage,
        "splatmaps": [str(splat0_file.name)],
        "colormap": str(colormap_file.name),
        "channels": channel_mapping,
        "legend": {
            b.value: {
                "color_rgb": list(BIOME_COLORS[b]),
                "coverage_pct": coverage.get(b.value, 0.0),
            }
            for b in BiomeType.all_biomes()
        },
    }

    manifest_file = out_path / f"{stem}_manifest.json"
    manifest_file.write_text(json.dumps(manifest_data, indent=2), encoding="utf-8")

    return {
        "splatmap_file": str(splat0_file),
        "colormap_file": str(colormap_file),
        "manifest_file": str(manifest_file),
        "channels": channel_mapping,
        "coverage": coverage,
    }


def sample_mesh_vertex_colors(
    grid_result: BiomeGridResult,
    vertices: np.ndarray,
) -> np.ndarray:
    """Calculates smoothly blended RGBA vertex colors for a 3D terrain mesh.

    Parameters:
        grid_result: Computed BiomeGridResult covering the mesh bounds.
        vertices: (N, 3) array where X=East, Y=Altitude, Z=North.

    Returns:
        (N, 4) uint8 array of RGBA vertex colors.
    """
    if len(vertices) == 0:
        return np.empty((0, 4), dtype=np.uint8)

    min_x, min_y, max_x, max_y = grid_result.bounds_m
    h, w = grid_result.shape

    # Vertex X is East (x), Vertex Z is North (y)
    vx = vertices[:, 0]
    vy = vertices[:, 2]

    # Normalized UVs in grid space
    u = np.clip((vx - min_x) / max(1e-6, max_x - min_x) * (w - 1), 0, w - 1)
    v = np.clip((vy - min_y) / max(1e-6, max_y - min_y) * (h - 1), 0, h - 1)

    c0 = np.floor(u).astype(np.int32)
    c1 = np.clip(c0 + 1, 0, w - 1)
    r0 = np.floor(v).astype(np.int32)
    r1 = np.clip(r0 + 1, 0, h - 1)

    fu = (u - c0)[:, None]
    fv = (v - r0)[:, None]

    # Bilinear interpolation of biome weights per vertex
    num_biomes = len(BiomeType.all_biomes())
    v_weights = np.zeros((len(vertices), num_biomes), dtype=np.float32)

    for b in BiomeType.all_biomes():
        idx = BIOME_INDEX[b]
        w_grid = grid_result.weights[idx]
        w_interp = (
            w_grid[r0, c0] * (1.0 - fu.ravel()) * (1.0 - fv.ravel())
            + w_grid[r0, c1] * fu.ravel() * (1.0 - fv.ravel())
            + w_grid[r1, c0] * (1.0 - fu.ravel()) * fv.ravel()
            + w_grid[r1, c1] * fu.ravel() * fv.ravel()
        )
        v_weights[:, idx] = w_interp

    # Blend colors
    out_rgb = np.zeros((len(vertices), 3), dtype=np.float32)
    for b in BiomeType.all_biomes():
        idx = BIOME_INDEX[b]
        b_col = np.array(BIOME_COLORS[b], dtype=np.float32)[None, :]
        out_rgb += v_weights[:, idx : idx + 1] * b_col

    rgba = np.full((len(vertices), 4), 255, dtype=np.uint8)
    rgba[:, :3] = np.clip(out_rgb, 0, 255).astype(np.uint8)
    return rgba

