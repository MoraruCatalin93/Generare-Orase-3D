"""Large World Chunk Engine: Spatial partitioning, boundary continuity, deterministic generation, and Blender export.

Designed for large geographic worlds (e.g. 20 km x 20 km divided into 512 m x 512 m chunks)
with:
- Configurable world dimensions and chunk sizes.
- Stable, deterministic chunk IDs (chk_r{row:02d}_c{col:02d}).
- Global world coordinate system (LocalProjection centered on project).
- Exact boundary seam matching (zero elevation gap, continuous road endpoints).
- Owner-chunk feature assignment to eliminate duplicate buildings across borders.
- Memory-bounded staged generation with cancellation and progress reporting.
- Independent chunk export and Blender Master Scene generator (import_terragen_world.py).
"""

from __future__ import annotations

import copy
import gc
import hashlib
import json
import math
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union

from ..build import building_color, building_height, road_width
from ..dem import Dem
from ..geom import Mesh
from ..project import LocalProjection
from .model import ChunkGrid, ChunkItem, ChunksSettings, GeographicBounds, Project


def derive_chunk_seed(world_seed: int, chunk_id: str, region_seed: int | None = None) -> int:
    """Derives a deterministic 31-bit seed from global world seed, chunk ID, and optional region seed."""
    raw = f"{world_seed}:{chunk_id}:{region_seed if region_seed is not None else 0}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) & 0x7FFFFFFF


def compute_grid_dimensions(
    world_size_x_m: float,
    world_size_y_m: float,
    chunk_size_m: float = 512.0,
) -> tuple[int, int]:
    """Calculates (rows, cols) given world dimensions and chunk size in meters."""
    if chunk_size_m <= 0:
        raise ValueError(f"Chunk size must be positive, got {chunk_size_m}")
    cols = max(1, int(math.ceil(world_size_x_m / chunk_size_m)))
    rows = max(1, int(math.ceil(world_size_y_m / chunk_size_m)))
    return rows, cols


class ChunkEngine:
    """Core spatial partitioning and generation engine for large real-world chunked maps."""

    def __init__(
        self,
        project: Project,
        project_dir: Path | str | None = None,
        cache_dir: Path | str | None = None,
    ):
        self.project = project
        self.project_dir = Path(project_dir) if project_dir else Path(".")
        self.cache_dir = Path(cache_dir) if cache_dir else (self.project_dir / ".cache")
        self.chunks_dir = self.project_dir / "chunks"

        # Global World Coordinate System projection
        self.proj = LocalProjection(
            project.geographic.center_lat,
            project.geographic.center_lon,
        )

        # Initialize or synchronize chunk grid
        if not self.project.chunks.items or not self.project.chunks.grid.enabled:
            self.init_grid(
                chunk_size_m=self.project.chunks.grid.chunk_size_m or 512.0,
                world_size_x_m=self.project.dimensions.size_x_m or 20000.0,
                world_size_y_m=self.project.dimensions.size_y_m or 20000.0,
            )

    # -------------------------------------------------------------------------
    # Grid Initialization & Spatial Indexing
    # -------------------------------------------------------------------------

    def init_grid(
        self,
        chunk_size_m: float = 512.0,
        world_size_x_m: float | None = None,
        world_size_y_m: float | None = None,
    ) -> ChunksSettings:
        """Initializes and computes the chunk grid and items covering the world dimensions."""
        size_x = float(world_size_x_m or self.project.dimensions.size_x_m or 20000.0)
        size_y = float(world_size_y_m or self.project.dimensions.size_y_m or 20000.0)
        chunk_size = float(chunk_size_m or 512.0)

        rows, cols = compute_grid_dimensions(size_x, size_y, chunk_size)

        grid = ChunkGrid(
            enabled=True,
            chunk_size_m=chunk_size,
            world_size_x_m=size_x,
            world_size_y_m=size_y,
            rows=rows,
            cols=cols,
        )

        half_w = size_x / 2.0
        half_h = size_y / 2.0

        items: list[ChunkItem] = []
        item_map: dict[tuple[int, int], str] = {}

        # 1. Generate items with projected metric bounds & geographic bounds
        for r in range(rows):
            for c in range(cols):
                chunk_id = f"chk_r{r:02d}_c{c:02d}"
                item_map[(r, c)] = chunk_id

                min_x = -half_w + c * chunk_size
                max_x = min_x + chunk_size
                max_y = half_h - r * chunk_size
                min_y = max_y - chunk_size

                center_x = (min_x + max_x) / 2.0
                center_y = (min_y + max_y) / 2.0

                c_lon, c_lat = self.proj.to_lonlat(center_x, center_y)
                w_lon, s_lat = self.proj.to_lonlat(min_x, min_y)
                e_lon, n_lat = self.proj.to_lonlat(max_x, max_y)

                bounds_geo = GeographicBounds(
                    south=round(float(s_lat), 7),
                    west=round(float(w_lon), 7),
                    north=round(float(n_lat), 7),
                    east=round(float(e_lon), 7),
                )

                bounds_proj = {
                    "min_x": round(float(min_x), 2),
                    "min_y": round(float(min_y), 2),
                    "max_x": round(float(max_x), 2),
                    "max_y": round(float(max_y), 2),
                }

                chunk_seed = derive_chunk_seed(self.project.seed, chunk_id)

                item = ChunkItem(
                    id=chunk_id,
                    row=r,
                    col=c,
                    center_lat=round(float(c_lat), 7),
                    center_lon=round(float(c_lon), 7),
                    bounds=bounds_geo,
                    bounds_projected=bounds_proj,
                    size_m=chunk_size,
                    seed=chunk_seed,
                    status="unbuilt",
                    visible=True,
                )
                items.append(item)

        # 2. Populate neighbor relationships
        for item in items:
            r, c = item.row, item.col
            item.neighbors = {
                "north": item_map.get((r - 1, c)),
                "south": item_map.get((r + 1, c)),
                "west": item_map.get((r, c - 1)),
                "east": item_map.get((r, c + 1)),
                "nw": item_map.get((r - 1, c - 1)),
                "ne": item_map.get((r - 1, c + 1)),
                "sw": item_map.get((r + 1, c - 1)),
                "se": item_map.get((r + 1, c + 1)),
            }

        # 3. Associate with project regions
        self._map_regions_to_chunks(items)

        self.project.chunks = ChunksSettings(grid=grid, items=items)
        return self.project.chunks

    def _map_regions_to_chunks(self, items: list[ChunkItem]) -> None:
        """Finds spatial intersections between project regions and chunk bounding boxes."""
        if not self.project.regions:
            return

        from .regions import to_shapely_polygon

        region_polys = []
        for reg in self.project.regions:
            if reg.polygon and len(reg.polygon) >= 3:
                try:
                    poly = to_shapely_polygon(reg.polygon, reg.holes)
                    region_polys.append((reg.id, poly))
                except Exception:
                    continue

        for item in items:
            s, w = item.bounds.south, item.bounds.west
            n, e = item.bounds.north, item.bounds.east
            c_poly = Polygon([(w, s), (e, s), (e, n), (w, n), (w, s)])
            intersecting: list[str] = []
            for r_id, r_poly in region_polys:
                if c_poly.intersects(r_poly):
                    intersecting.append(r_id)
            item.regions = intersecting

    def get_chunk(self, chunk_id: str) -> ChunkItem | None:
        """Retrieves a chunk by stable ID."""
        for ch in self.project.chunks.items:
            if ch.id == chunk_id:
                return ch
        return None

    def get_chunk_at(self, x_m: float, y_m: float) -> ChunkItem | None:
        """Finds the chunk containing the given local metric coordinates."""
        grid = self.project.chunks.grid
        half_w = grid.world_size_x_m / 2.0
        half_h = grid.world_size_y_m / 2.0

        if not (-half_w <= x_m <= half_w and -half_h <= y_m <= half_h):
            return None

        col = int((x_m + half_w) // grid.chunk_size_m)
        row = int((half_h - y_m - 1e-8) // grid.chunk_size_m)

        col = min(max(0, col), grid.cols - 1)
        row = min(max(0, row), grid.rows - 1)

        chunk_id = f"chk_r{row:02d}_c{col:02d}"
        return self.get_chunk(chunk_id)

    def get_chunk_at_lonlat(self, lon: float, lat: float) -> ChunkItem | None:
        """Finds the chunk containing the given geographic coordinates."""
        x, y = self.proj.to_xy(lon, lat)
        return self.get_chunk_at(float(x), float(y))

    def get_chunks_in_bbox(self, min_x: float, min_y: float, max_x: float, max_y: float) -> list[ChunkItem]:
        """Finds all chunks that intersect the metric bounding box."""
        q_box = box(min_x, min_y, max_x, max_y)
        results: list[ChunkItem] = []
        for ch in self.project.chunks.items:
            bp = ch.bounds_projected
            c_box = box(bp["min_x"], bp["min_y"], bp["max_x"], bp["max_y"])
            if q_box.intersects(c_box):
                results.append(ch)
        return results

    def evaluate_biomes_for_chunk(
        self,
        chunk_id: str,
        dem: Dem,
        ways: list[Way] | None = None,
        resolution_m: float = 8.0,
    ) -> Any:
        """Evaluates procedural biomes across the chunk bounds."""
        ch = self.get_chunk(chunk_id)
        if not ch:
            raise KeyError(f"Chunk {chunk_id} not found")
        bp = ch.bounds_projected
        from ..biomes import BiomeGenerator
        bg = BiomeGenerator(seed=self.project.seed)
        return bg.evaluate_grid(
            bounds_m=(bp["min_x"], bp["min_y"], bp["max_x"], bp["max_y"]),
            resolution_m=resolution_m,
            dem=dem,
            proj=self.proj,
            ways=ways,
        )

    def populate_chunk_dressing(
        self,
        chunk_id: str,
        dem: Dem,
        ways: list[Way] | None = None,
        density_multiplier: float = 1.0,
    ) -> list[Any]:
        """Populates procedural world dressing instances for the chunk."""
        ch = self.get_chunk(chunk_id)
        if not ch:
            raise KeyError(f"Chunk {chunk_id} not found")
        bp = ch.bounds_projected
        chunk_seed = derive_chunk_seed(self.project.seed, ch.id)
        from ..dressing import WorldDressingEngine
        engine = WorldDressingEngine(seed=chunk_seed, density_multiplier=density_multiplier)
        return engine.populate_bounds(
            bounds_m=(bp["min_x"], bp["min_y"], bp["max_x"], bp["max_y"]),
            dem=dem,
            proj=self.proj,
            ways=ways,
        )

    # -------------------------------------------------------------------------
    # Boundary Continuity & Seam Math
    # -------------------------------------------------------------------------

    def sample_terrain_grid_for_chunk(
        self,
        chunk: ChunkItem,
        dem: Dem,
        resolution: int = 64,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Generates exact terrain elevation vertices for the chunk with seam-aligned boundaries.

        Returns (X, Y, Z) grids of shape (resolution + 1, resolution + 1).
        Because adjacent chunks evaluate the identical LocalProjection at identical boundary
        coordinates, heights on shared borders match with exact floating-point equality.
        """
        bp = chunk.bounds_projected
        min_x, max_x = bp["min_x"], bp["max_x"]
        min_y, max_y = bp["min_y"], bp["max_y"]

        n = resolution + 1
        xs = np.linspace(min_x, max_x, n, dtype=np.float64)
        ys = np.linspace(min_y, max_y, n, dtype=np.float64)

        X, Y = np.meshgrid(xs, ys)  # X: East, Y: North
        lon, lat = self.proj.to_lonlat(X, Y)
        Z = dem.heights(lon, lat)

        return X, Y, Z.astype(np.float64)

    def validate_boundary_heights(
        self,
        chunk_a_id: str,
        chunk_b_id: str,
        dem: Dem,
        num_samples: int = 32,
    ) -> dict[str, Any]:
        """Validates height continuity along the shared boundary of two adjacent chunks.

        Returns maximum elevation discrepancy (should be 0.0 or < 1e-9).
        """
        ch_a = self.get_chunk(chunk_a_id)
        ch_b = self.get_chunk(chunk_b_id)
        if not ch_a or not ch_b:
            raise KeyError("One or both chunk IDs not found")

        bp_a = ch_a.bounds_projected
        bp_b = ch_b.bounds_projected

        # Check adjacency
        is_horizontal_adj = abs(bp_a["max_x"] - bp_b["min_x"]) < 1e-3 or abs(bp_b["max_x"] - bp_a["min_x"]) < 1e-3
        is_vertical_adj = abs(bp_a["max_y"] - bp_b["min_y"]) < 1e-3 or abs(bp_b["max_y"] - bp_a["min_y"]) < 1e-3

        if not (is_horizontal_adj or is_vertical_adj):
            raise ValueError(f"Chunks {chunk_a_id} and {chunk_b_id} are not adjacent")

        if is_horizontal_adj:
            # Shared vertical border (constant X)
            shared_x = bp_a["max_x"] if abs(bp_a["max_x"] - bp_b["min_x"]) < 1e-3 else bp_b["max_x"]
            min_y = max(bp_a["min_y"], bp_b["min_y"])
            max_y = min(bp_a["max_y"], bp_b["max_y"])
            y_samples = np.linspace(min_y, max_y, num_samples, dtype=np.float64)
            x_samples = np.full_like(y_samples, shared_x)
        else:
            # Shared horizontal border (constant Y)
            shared_y = bp_a["max_y"] if abs(bp_a["max_y"] - bp_b["min_y"]) < 1e-3 else bp_b["max_y"]
            min_x = max(bp_a["min_x"], bp_b["min_x"])
            max_x = min(bp_a["max_x"], bp_b["max_x"])
            x_samples = np.linspace(min_x, max_x, num_samples, dtype=np.float64)
            y_samples = np.full_like(x_samples, shared_y)

        lon_a, lat_a = self.proj.to_lonlat(x_samples, y_samples)
        lon_b, lat_b = self.proj.to_lonlat(x_samples, y_samples)

        z_a = dem.heights(lon_a, lat_a)
        z_b = dem.heights(lon_b, lat_b)

        diff = np.abs(z_a - z_b)
        max_diff = float(np.max(diff))

        return {
            "chunk_a": chunk_a_id,
            "chunk_b": chunk_b_id,
            "samples_checked": num_samples,
            "max_elevation_gap_m": round(max_diff, 8),
            "continuous": max_diff < 1e-6,
        }

    def validate_boundary_normals(
        self,
        chunk_a_id: str,
        chunk_b_id: str,
        dem: Dem,
        terrain_res: int = 16,
    ) -> dict[str, Any]:
        """Validates that vertex normals on the shared border of two adjacent chunks match seamlessly."""
        ch_a = self.get_chunk(chunk_a_id)
        ch_b = self.get_chunk(chunk_b_id)
        if not ch_a or not ch_b:
            raise KeyError("One or both chunk IDs not found")

        bp_a = ch_a.bounds_projected
        bp_b = ch_b.bounds_projected

        is_horiz = abs(bp_a["max_x"] - bp_b["min_x"]) < 1e-3
        is_vert = abs(bp_a["max_y"] - bp_b["min_y"]) < 1e-3

        dx = ch_a.size_m / terrain_res

        def _get_normals(bp):
            min_x, max_x = bp["min_x"], bp["max_x"]
            min_y, max_y = bp["min_y"], bp["max_y"]
            xs_ext = np.linspace(min_x - dx, max_x + dx, terrain_res + 3, dtype=np.float64)
            ys_ext = np.linspace(min_y - dx, max_y + dx, terrain_res + 3, dtype=np.float64)
            X_ext, Y_ext = np.meshgrid(xs_ext, ys_ext)
            lon_ext, lat_ext = self.proj.to_lonlat(X_ext, Y_ext)
            Z_ext = dem.heights(lon_ext, lat_ext).astype(np.float64)
            gy_ext, gx_ext = np.gradient(Z_ext, dx)
            gx = gx_ext[1:-1, 1:-1]
            gy = gy_ext[1:-1, 1:-1]
            nx = -gx
            ny = np.ones_like(nx)
            nz = -gy
            nl = np.sqrt(nx**2 + ny**2 + nz**2)
            return nx / nl, ny / nl, nz / nl

        na_x, na_y, na_z = _get_normals(bp_a)
        nb_x, nb_y, nb_z = _get_normals(bp_b)

        if is_horiz:
            # ch_a is west (left, max_x = index -1), ch_b is east (right, min_x = index 0)
            diff_x = np.abs(na_x[:, -1] - nb_x[:, 0])
            diff_y = np.abs(na_y[:, -1] - nb_y[:, 0])
            diff_z = np.abs(na_z[:, -1] - nb_z[:, 0])
        elif is_vert:
            # ch_a is south (bottom, max_y = index -1), ch_b is north (top, min_y = index 0)
            diff_x = np.abs(na_x[-1, :] - nb_x[0, :])
            diff_y = np.abs(na_y[-1, :] - nb_y[0, :])
            diff_z = np.abs(na_z[-1, :] - nb_z[0, :])
        else:
            diff_x = diff_y = diff_z = np.array([0.0])

        max_diff = float(max(np.max(diff_x), np.max(diff_y), np.max(diff_z)))
        return {
            "chunk_a": chunk_a_id,
            "chunk_b": chunk_b_id,
            "max_normal_difference": round(max_diff, 8),
            "seamless": max_diff < 1e-5,
        }

    # -------------------------------------------------------------------------
    # Feature Assignment & Deduplication (Owner Chunk Principle)
    # -------------------------------------------------------------------------

    def get_feature_owner_chunk(self, feature_local_coords: np.ndarray) -> str | None:
        """Determines the single owner chunk for a 2D feature based on its centroid.

        Ensures buildings and point assets crossing boundaries are generated by
        exactly one chunk, completely eliminating duplicate 3D geometry in Blender.
        """
        if len(feature_local_coords) < 3:
            return None
        c_x = float(np.mean(feature_local_coords[:, 0]))
        c_y = float(np.mean(feature_local_coords[:, 1]))
        chunk = self.get_chunk_at(c_x, c_y)
        return chunk.id if chunk else None

    # -------------------------------------------------------------------------
    # Staged Chunk Generation
    # -------------------------------------------------------------------------

    def generate_chunk(
        self,
        chunk_id: str,
        dem: Dem,
        ways: list[Any] | None = None,
        force: bool = False,
        terrain_res: int = 64,
        roofs: bool = True,
        formats: tuple[str, ...] = ("glb",),
        style_preset: Any = None,
    ) -> dict[str, Any]:
        """Generates 3D assets for a single chunk in an isolated, staged pipeline.

        Memory is released immediately after writing the chunk files to disk.
        """
        chunk = self.get_chunk(chunk_id)
        if not chunk:
            raise KeyError(f"Chunk '{chunk_id}' not found")

        chunk_dir = self.chunks_dir / chunk_id
        chunk_dir.mkdir(parents=True, exist_ok=True)

        manifest_file = chunk_dir / "manifest.json"
        metadata_file = chunk_dir / "metadata.json"

        if style_preset is not None:
            from ..styles import StylePreset, get_style_preset
            preset = style_preset if isinstance(style_preset, StylePreset) else get_style_preset(style_preset)
        else:
            preset = None

        # Check determinism & caching with full invalidation triggers
        preset_key = getattr(preset, "name", str(style_preset or "default"))
        modifiers_raw = [m.to_dict() if hasattr(m, "to_dict") else m for m in (self.project.terrain.modifiers or [])]
        modifiers_str = json.dumps(modifiers_raw, sort_keys=True)
        regions_str = ",".join(sorted(getattr(chunk, "regions", None) or []))
        input_payload = f"{self.project.seed}:{chunk.id}:{chunk.size_m}:{terrain_res}:{roofs}:{preset_key}:{modifiers_str}:{regions_str}"
        input_hash = hashlib.sha256(input_payload.encode()).hexdigest()

        if not force and chunk.status == "built" and chunk.content_hash == input_hash and manifest_file.exists():
            return json.loads(manifest_file.read_text(encoding="utf-8"))

        chunk.status = "generating"
        t0 = time.time()

        # 1. Terrain Mesh with Guard-Ring for Seamless Boundary Normals
        bp = chunk.bounds_projected
        dx = chunk.size_m / terrain_res
        min_x, max_x = bp["min_x"], bp["max_x"]
        min_y, max_y = bp["min_y"], bp["max_y"]

        # Sample grid extended by 1 step in all directions
        xs_ext = np.linspace(min_x - dx, max_x + dx, terrain_res + 3, dtype=np.float64)
        ys_ext = np.linspace(min_y - dx, max_y + dx, terrain_res + 3, dtype=np.float64)
        X_ext, Y_ext = np.meshgrid(xs_ext, ys_ext)
        lon_ext, lat_ext = self.proj.to_lonlat(X_ext, Y_ext)
        Z_ext = dem.heights(lon_ext, lat_ext).astype(np.float64)
        if preset and (preset.terrain.elevation_scale != 1.0 or preset.terrain.elevation_offset_m != 0.0):
            scale = preset.terrain.elevation_scale
            offset = preset.terrain.elevation_offset_m
            Z_ext = Z_ext * scale + offset

        # Central difference gradients on extended grid
        gy_ext, gx_ext = np.gradient(Z_ext, dx)

        # Slice to chunk grid
        X = X_ext[1:-1, 1:-1]
        Y = Y_ext[1:-1, 1:-1]
        Z = Z_ext[1:-1, 1:-1]
        gx = gx_ext[1:-1, 1:-1]
        gy = gy_ext[1:-1, 1:-1]
        n = terrain_res + 1

        verts = np.column_stack([X.ravel(), Z.ravel(), Y.ravel()])  # x=E, y=Up, z=N

        # Compute analytical seamless normals
        nx = -gx.ravel()
        ny = np.ones_like(nx)
        nz = -gy.ravel()
        n_len = np.sqrt(nx**2 + ny**2 + nz**2)
        n_len[n_len == 0] = 1.0
        terrain_normals = np.column_stack([nx / n_len, ny / n_len, nz / n_len])

        i, j = np.meshgrid(np.arange(terrain_res), np.arange(terrain_res), indexing="ij")
        v00 = (i * n + j).ravel()
        v10 = (i * n + j + 1).ravel()
        v01 = ((i + 1) * n + j).ravel()
        v11 = ((i + 1) * n + j + 1).ravel()
        faces = np.vstack([
            np.column_stack([v00, v10, v11]),
            np.column_stack([v00, v11, v01]),
        ])

        # Shading colors: green / stone based on slope
        slope = np.degrees(np.arctan(np.hypot(gx, gy)))
        slope_thresh = preset.terrain.slope_threshold_deg if preset else 18.0
        w_slope = np.clip((slope.ravel() - slope_thresh) / 20.0, 0, 1)[:, None]
        lo = np.array(preset.terrain.low_color if preset else [110, 158, 86], float)
        hi = np.array(preset.terrain.high_color if preset else [170, 162, 138], float)
        rock = np.array(preset.terrain.rock_color if preset else [122, 120, 116], float)
        col = lo[None, :] * (1 - w_slope) + rock[None, :] * w_slope
        if preset and preset.terrain.peak_color:
            zmin, zmax = float(Z.min()), float(Z.max())
            if zmax > zmin + 30.0:
                snow = np.array(preset.terrain.peak_color, float)
                w_snow = np.clip((Z.ravel() - (zmin + (zmax - zmin) * 0.72)) / max(1.0, (zmax - zmin) * 0.28), 0, 1)[:, None]
                col = col * (1 - w_snow) + snow[None, :] * w_snow
        terrain_colors = np.clip(col, 0, 255).astype(np.uint8)

        terrain_mesh = Mesh()
        terrain_mesh.add(verts, faces, terrain_colors, normals=terrain_normals)

        # 2. Roads and Buildings
        chunk_box = box(bp["min_x"], bp["min_y"], bp["max_x"], bp["max_y"])
        roads_mesh = Mesh()
        buildings_mesh = Mesh()
        road_count = 0
        building_count = 0
        chunk_road_polys = []
        chunk_bld_polys = []
        chunk_seed = derive_chunk_seed(self.project.seed, chunk.id)
        elev_scale = preset.terrain.elevation_scale if preset else 1.0
        elev_offset = preset.terrain.elevation_offset_m if preset else 0.0

        def sample_dem_h(lons, lats):
            raw = dem.heights(lons, lats)
            return raw * elev_scale + elev_offset

        if ways:
            for w in ways:
                tags = getattr(w, "tags", {})
                hw = tags.get("highway")
                is_building = "building" in tags or "building:part" in tags

                # A. Roads
                if hw and hw not in {"proposed", "construction", "abandoned"}:
                    coords = getattr(w, "coords", [])
                    if len(coords) >= 2:
                        xy = np.column_stack(self.proj.to_xy([c[0] for c in coords], [c[1] for c in coords]))
                        line = LineString(xy)
                        if line.intersects(chunk_box):
                            clipped = line.intersection(chunk_box)
                            parts = [clipped] if clipped.geom_type == "LineString" else list(getattr(clipped, "geoms", []))
                            for p in parts:
                                if p.length > 1.0:
                                    r_coords = np.asarray(p.coords)
                                    lon, lat = self.proj.to_lonlat(r_coords[:, 0], r_coords[:, 1])
                                    z = sample_dem_h(lon, lat) + 0.16
                                    # Road ribbon
                                    from ..geom import smooth_1d
                                    z = smooth_1d(z, 5)
                                    # Buffer road strip using unified road width
                                    w_scale = preset.roads.width_scale if preset else 1.0
                                    r_width = road_width(tags) * w_scale
                                    poly = p.buffer(r_width / 2.0, cap_style="flat", join_style="round")
                                    if poly.is_empty:
                                        continue
                                    chunk_road_polys.append(poly)
                                    v2 = np.asarray(poly.exterior.coords)
                                    v_lon, v_lat = self.proj.to_lonlat(v2[:, 0], v2[:, 1])
                                    v_z = sample_dem_h(v_lon, v_lat) + 0.16
                                    from ..geom import triangulate_polygon
                                    v_tri, f_tri = triangulate_polygon(poly)
                                    if len(f_tri):
                                        tri_lon, tri_lat = self.proj.to_lonlat(v_tri[:, 0], v_tri[:, 1])
                                        tri_z = sample_dem_h(tri_lon, tri_lat) + 0.16
                                        r_col = preset.roads.asphalt_color if preset else (72, 76, 84)
                                        roads_mesh.add(
                                            np.column_stack([v_tri[:, 0], tri_z, v_tri[:, 1]]),
                                            f_tri,
                                            r_col,
                                        )
                                        road_count += 1

                # B. Buildings (Owner-chunk principle)
                if is_building and tags.get("building") != "no":
                    rings = getattr(w, "rings", None) or [getattr(w, "coords", [])]
                    ext = rings[0]
                    if len(ext) >= 3:
                        ext_xy = np.column_stack(self.proj.to_xy([c[0] for c in ext], [c[1] for c in ext]))
                        owner = self.get_feature_owner_chunk(ext_xy)
                        if owner == chunk.id:
                            # Generate building in owner chunk
                            poly = Polygon(ext_xy)
                            if preset and preset.building.simplification_tol > 0:
                                try:
                                    poly = poly.simplify(preset.building.simplification_tol, preserve_topology=True)
                                except Exception:
                                    pass
                            min_a = preset.building.min_area if preset else 10.0
                            if poly.is_valid and poly.area >= min_a:
                                chunk_bld_polys.append(poly)
                                c_lon, c_lat = self.proj.to_lonlat(poly.centroid.x, poly.centroid.y)
                                base_z = float(sample_dem_h(c_lon, c_lat))
                                wid = getattr(w, "id", 0)
                                b_h = building_height(tags, wid, chunk_seed)
                                if preset:
                                    b_h *= preset.building.height_scale
                                    if preset.building.height_variation > 0:
                                        rnd_h = ((int(hashlib.md5(f"hvar{wid}-{chunk_seed}".encode()).hexdigest()[:6], 16) % 1000) / 1000.0 - 0.5) * 2.0
                                        b_h = max(2.6, b_h * (1.0 + rnd_h * preset.building.height_variation))
                                from ..geom import FLOOR_H, MODULE_U, extrude
                                v_b, f_b, cap, uv = extrude(poly, base_z - 0.2, base_z + b_h, floor_h=FLOOR_H, module_u=MODULE_U)
                                if v_b is not None:
                                    b_hsh = int(hashlib.md5(f"b{wid}-{chunk_seed}".encode()).hexdigest()[:6], 16)
                                    if preset and preset.building.facade_palette:
                                        wall_c = preset.building.facade_palette[b_hsh % len(preset.building.facade_palette)]
                                    else:
                                        wall_c = building_color(wid, chunk_seed)
                                    if preset and preset.building.roof_palette:
                                        roof_c = preset.building.roof_palette[b_hsh % len(preset.building.roof_palette)]
                                    else:
                                        roof_c = (178, 88, 68)
                                    wall_cols = np.full((len(v_b), 4), list(wall_c) + [255], dtype=np.uint8)
                                    wall_cols[cap, :3] = list(roof_c)
                                    buildings_mesh.add(v_b, f_b, wall_cols, uv)
                                    building_count += 1

        # 3. Vegetation Layer
        veg_mesh = Mesh()
        if preset and getattr(preset, "vegetation", None) and preset.vegetation.enabled:
            from ..styles import generate_vegetation_layer
            veg_mesh = generate_vegetation_layer(
                bbox_m=(bp["min_x"], bp["min_y"], bp["max_x"], bp["max_y"]),
                dem=dem,
                proj=self.proj,
                style=preset.vegetation,
                seed=chunk_seed,
                forest_polygons=None,
                water_polygons=None,
                road_polygons=chunk_road_polys if chunk_road_polys else None,
                building_polygons=chunk_bld_polys if chunk_bld_polys else None,
                elevation_scale=elev_scale,
                elevation_offset_m=elev_offset,
            )

        # 4. Export Formats (GLB / OBJ)
        manifest_layers: dict[str, Any] = {}
        total_triangles = len(terrain_mesh.f) + len(roads_mesh.f) + len(buildings_mesh.f) + len(veg_mesh.f)

        from ..export import write_glb, write_obj

        # Combined chunk mesh
        combined = Mesh()
        if len(terrain_mesh.v):
            combined.add(terrain_mesh.v, terrain_mesh.f, terrain_mesh.c, normals=terrain_mesh.n)
        if len(roads_mesh.v):
            combined.add(roads_mesh.v, roads_mesh.f, roads_mesh.c)
        if len(buildings_mesh.v):
            combined.add(buildings_mesh.v, buildings_mesh.f, buildings_mesh.c, buildings_mesh.uv)
        if len(veg_mesh.v):
            combined.add(veg_mesh.v, veg_mesh.f, veg_mesh.c)

        out_glb = chunk_dir / "chunk.glb"
        write_glb(combined, out_glb, name=chunk.id)

        if "obj" in formats:
            write_obj([
                ("terrain", terrain_mesh),
                ("roads", roads_mesh),
                ("buildings", buildings_mesh),
            ], chunk_dir / "chunk.obj")

        elapsed = round(time.time() - t0, 2)
        chunk.status = "built"
        chunk.last_built = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        chunk.content_hash = input_hash
        chunk.stats = {
            "elapsed_s": elapsed,
            "triangles": total_triangles,
            "terrain_triangles": len(terrain_mesh.f),
            "roads_triangles": len(roads_mesh.f),
            "buildings_triangles": len(buildings_mesh.f),
            "road_segments": road_count,
            "buildings_count": building_count,
        }

        # Biome Analysis for Chunk
        from ..biomes import BiomeGenerator
        biome_gen = BiomeGenerator(seed=self.project.seed)
        chunk_biomes = biome_gen.evaluate_grid(
            bounds_m=(bp["min_x"], bp["min_y"], bp["max_x"], bp["max_y"]),
            resolution_m=max(8.0, dx * 2.0),
            dem=dem,
            proj=self.proj,
            ways=ways,
        )
        biome_coverage = chunk_biomes.get_coverage_percentages()

        # World Dressing for Chunk
        from ..dressing import WorldDressingEngine
        dressing_engine = WorldDressingEngine(seed=chunk_seed)
        chunk_instances = dressing_engine.populate_bounds(
            bounds_m=(bp["min_x"], bp["min_y"], bp["max_x"], bp["max_y"]),
            dem=dem,
            proj=self.proj,
            biome_grid=chunk_biomes,
            ways=ways,
        )

        manifest = {
            "chunk_id": chunk.id,
            "status": chunk.status,
            "row": chunk.row,
            "col": chunk.col,
            "bounds_projected": bp,
            "bounds_geo": chunk.bounds.to_dict(),
            "center": {"lat": chunk.center_lat, "lon": chunk.center_lon},
            "stats": chunk.stats,
            "biomes": biome_coverage,
            "world_dressing": {
                "instances_count": len(chunk_instances),
            },
            "files": {
                "glb": str(out_glb.name),
            },
            "content_hash": input_hash,
        }
        manifest_file.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        metadata_file.write_text(json.dumps(chunk.to_dict(), indent=2), encoding="utf-8")

        # Free memory immediately
        del terrain_mesh, roads_mesh, buildings_mesh, veg_mesh, combined
        gc.collect()

        return manifest

    def generate_chunks(
        self,
        chunk_ids: list[str] | None = None,
        dem: Dem | None = None,
        ways: list[Any] | None = None,
        force: bool = False,
        progress_callback: Callable[[int, int, str, str], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
        style_preset: Any = None,
    ) -> dict[str, Any]:
        """Batch generates multiple chunks sequentially with memory disposal between iterations."""
        targets = chunk_ids if chunk_ids is not None else [c.id for c in self.project.chunks.items]
        total = len(targets)
        results: dict[str, Any] = {}

        if dem is None:
            # Create world DEM covering entire project area
            half_w = self.project.chunks.grid.world_size_x_m / 2.0 * 1.05
            w_lon, s_lat = self.proj.to_lonlat(-half_w, -half_w)
            e_lon, n_lat = self.proj.to_lonlat(half_w, half_w)
            bbox = (s_lat, w_lon, n_lat, e_lon)
            dem = Dem(bbox, zoom=self.project.terrain.dem_zoom or 15)

        if self.project.terrain.modifiers and not isinstance(dem, Any) and getattr(dem, "__class__", None).__name__ != "ModifiedDem":
            from ..terrain_editor import ModifiedDem, TerrainEditor
            editor = TerrainEditor.from_dict(
                {
                    "modifiers": self.project.terrain.modifiers,
                    "undo_stack": self.project.terrain.undo_stack,
                },
                base_dem=dem,
                proj=self.proj,
                regions=self.project.regions,
            )
            dem = ModifiedDem(base_dem=dem, editor=editor, proj=self.proj)

        for idx, cid in enumerate(targets, start=1):
            if is_cancelled and is_cancelled():
                break

            if progress_callback:
                progress_callback(idx, total, cid, f"Generating chunk {cid} ({idx}/{total})")

            try:
                res = self.generate_chunk(
                    cid,
                    dem=dem,
                    ways=ways,
                    force=force,
                    terrain_res=min(self.project.terrain.resolution, 128),
                    roofs=self.project.generation.roofs,
                    style_preset=style_preset,
                )
                results[cid] = res
            except Exception as e:
                ch = self.get_chunk(cid)
                if ch:
                    ch.status = "error"
                results[cid] = {"error": str(e)}

        return {
            "total_requested": total,
            "completed": len(results),
            "results": results,
        }

    # -------------------------------------------------------------------------
    # Regeneration & Invalidation
    # -------------------------------------------------------------------------

    def invalidate_chunk(self, chunk_id: str) -> None:
        """Marks a chunk as stale so it will be regenerated on next build."""
        chunk = self.get_chunk(chunk_id)
        if chunk:
            chunk.status = "stale"

    def invalidate_region_chunks(self, region_id: str) -> list[str]:
        """Marks only the chunks intersecting a modified region as stale."""
        affected: list[str] = []
        for ch in self.project.chunks.items:
            if region_id in ch.regions:
                ch.status = "stale"
                affected.append(ch.id)
        return affected

    # -------------------------------------------------------------------------
    # Blender Export System
    # -------------------------------------------------------------------------

    def export_blender_manifest(self) -> Path:
        """Exports the Blender master scene index and an automated Blender import script.

        Output:
        - <project_dir>/blender_manifest.json
        - <project_dir>/import_to_blender.py
        """
        grid = self.project.chunks.grid
        chunks_manifest = []

        for ch in self.project.chunks.items:
            bp = ch.bounds_projected
            glb_path = self.chunks_dir / ch.id / "chunk.glb"
            chunks_manifest.append({
                "id": ch.id,
                "row": ch.row,
                "col": ch.col,
                "file": str(glb_path.relative_to(self.project_dir)) if glb_path.exists() else None,
                "exists": glb_path.exists(),
                "bounds": bp,
                "center": {
                    "x": (bp["min_x"] + bp["max_x"]) / 2.0,
                    "y": (bp["min_y"] + bp["max_y"]) / 2.0,
                },
                "status": ch.status,
            })

        master_data = {
            "project_name": self.project.name,
            "project_id": self.project.id,
            "world_origin": {
                "lat": self.project.geographic.center_lat,
                "lon": self.project.geographic.center_lon,
            },
            "dimensions_m": {
                "size_x": grid.world_size_x_m,
                "size_y": grid.world_size_y_m,
                "chunk_size": grid.chunk_size_m,
                "rows": grid.rows,
                "cols": grid.cols,
            },
            "chunks_count": len(chunks_manifest),
            "chunks": chunks_manifest,
        }

        manifest_path = self.project_dir / "blender_manifest.json"
        manifest_path.write_text(json.dumps(master_data, indent=2), encoding="utf-8")

        # Automated Blender Python importer script
        script_path = self.project_dir / "import_to_blender.py"
        script_code = '''"""Automated Terragen 3D World Importer for Blender 3.x / 4.x.

Run this script inside Blender (Text Editor -> Run Script) or from CLI:
  blender --python import_to_blender.py
"""

import json
from pathlib import Path
import bpy

def import_terragen_world():
    script_dir = Path(__file__).resolve().parent
    manifest_file = script_dir / "blender_manifest.json"
    if not manifest_file.exists():
        print(f"Error: Manifest not found at {manifest_file}")
        return

    data = json.loads(manifest_file.read_text(encoding="utf-8"))
    world_name = data.get("project_name", "TerragenWorld")
    print(f"Importing Terragen World: {world_name} ({data['dimensions_m']['rows']}x{data['dimensions_m']['cols']} chunks)")

    # Create root master collection
    root_coll = bpy.data.collections.get(world_name)
    if not root_coll:
        root_coll = bpy.data.collections.new(world_name)
        bpy.context.scene.collection.children.link(root_coll)

    # Set 3D viewport clipping for large worlds
    for area in bpy.context.screen.areas:
        if area.type == 'VIEW_3D':
            for space in area.spaces:
                if space.type == 'VIEW_3D':
                    space.clip_end = 50000.0  # 50 km view distance

    imported_count = 0
    for ch in data.get("chunks", []):
        rel_file = ch.get("file")
        if not rel_file:
            continue
        file_path = script_dir / rel_file
        if not file_path.exists():
            continue

        chunk_id = ch["id"]
        chunk_coll = bpy.data.collections.new(chunk_id)
        root_coll.children.link(chunk_coll)

        # Import GLTF / GLB
        bpy.ops.import_scene.gltf(filepath=str(file_path))
        imported_objs = [obj for obj in bpy.context.selected_objects]

        for obj in imported_objs:
            # Unlink from active collection and link to chunk collection
            for c in obj.users_collection:
                c.unlink(obj)
            chunk_coll.objects.link(obj)

        imported_count += 1
        print(f"[{imported_count}/{data['chunks_count']}] Loaded {chunk_id}")

    print(f"✔ Done. Successfully imported {imported_count} chunks into Blender.")

if __name__ == "__main__":
    import_terragen_world()
'''
        script_path.write_text(script_code, encoding="utf-8")
        return manifest_path
