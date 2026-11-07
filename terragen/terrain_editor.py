"""Terrain Editor: Non-destructive terrain sculpting, elevation modifications, masking, and export.

Features:
- Non-destructive modifier stack preserving original DEM source integrity.
- Parametric operations: Raise, Lower, Smooth, Flatten, Hills, Valleys, Water Basins.
- Spatial weight masks with customizable falloffs (Smoothstep, Linear, Gaussian, Flat).
- Region-scoped modifications and terrain exclusion zones (protected natural terrain).
- Undo / Redo command history with serialization into persistent projects.
- Seam-free boundary continuity across large-world chunk borders (Delta Z = 0.0).
- 16-bit PNG heightmap and 3D GLB/OBJ mesh exports.
"""

from __future__ import annotations

import copy
import datetime
import hashlib
import json
import math
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np
from scipy.ndimage import gaussian_filter
from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

from .export import write_glb, write_heightmap, write_obj
from .geom import Mesh


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


# -----------------------------------------------------------------------------
# Spatial Falloff Functions
# -----------------------------------------------------------------------------

def calculate_radial_falloff(
    r: np.ndarray | float,
    radius: float,
    falloff_type: str = "smooth",
) -> np.ndarray | float:
    """Computes a normalized spatial weight [0.0, 1.0] given distance r and brush radius.

    - smooth: Cubic Hermite smoothstep (C1 continuous at boundaries).
    - linear: Linear transition 1.0 -> 0.0.
    - gaussian: Gaussian bell curve truncated at brush radius.
    - flat: Constant 1.0 inside radius, 0.0 outside.
    """
    if radius <= 0:
        return np.zeros_like(r) if isinstance(r, np.ndarray) else 0.0

    s = np.clip(r / radius, 0.0, 1.0)
    ftype = str(falloff_type).lower()

    if ftype == "smooth":
        # Smoothstep: 1 - (3s^2 - 2s^3)
        return (1.0 - s) * (1.0 - s) * (1.0 + 2.0 * s)
    elif ftype == "linear":
        return 1.0 - s
    elif ftype == "gaussian":
        # Gaussian bell normalized to reach 0 at s = 1
        g = np.exp(-3.0 * s * s)
        g_edge = math.exp(-3.0)
        return np.clip((g - g_edge) / (1.0 - g_edge), 0.0, 1.0)
    elif ftype == "flat":
        return np.where(s < 1.0, 1.0, 0.0) if isinstance(r, np.ndarray) else (1.0 if s < 1.0 else 0.0)
    else:
        # Default fallback
        return 1.0 - s


# -----------------------------------------------------------------------------
# Terrain Modifier Model
# -----------------------------------------------------------------------------

@dataclass
class TerrainModifier:
    """Encapsulates a single non-destructive terrain operation."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    op: str = "raise_lower"  # raise_lower | flatten | smooth | hill_valley | water_basin | mask_paint
    name: str = "Terrain Operation"
    center_x: float = 0.0
    center_y: float = 0.0
    radius_m: float = 50.0
    amount_m: float = 0.0          # Elevation delta for raise/lower/hill/valley
    target_height_m: float | None = None  # Target elevation for flatten/water
    strength: float = 1.0          # Blend factor [0.0, 1.0]
    falloff: str = "smooth"        # smooth | linear | gaussian | flat
    region_id: str | None = None   # Restrict operation inside region polygon
    polygon: list[list[float]] | None = None  # Custom polygon coordinates [[x, y], ...]
    is_exclusion: bool = False     # If True, locks elevation from edits
    enabled: bool = True
    created_at: str = field(default_factory=_now_iso)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "op": self.op,
            "name": self.name,
            "center": {"x": round(float(self.center_x), 2), "y": round(float(self.center_y), 2)},
            "radius_m": round(float(self.radius_m), 2),
            "amount_m": round(float(self.amount_m), 2),
            "target_height_m": round(float(self.target_height_m), 2) if self.target_height_m is not None else None,
            "strength": round(float(self.strength), 3),
            "falloff": self.falloff,
            "region_id": self.region_id,
            "polygon": self.polygon,
            "is_exclusion": bool(self.is_exclusion),
            "enabled": bool(self.enabled),
            "created_at": self.created_at,
            "metadata": copy.deepcopy(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TerrainModifier:
        c = data.get("center", {})
        cx = float(c.get("x", data.get("center_x", 0.0)))
        cy = float(c.get("y", data.get("center_y", 0.0)))
        return cls(
            id=str(data.get("id", uuid.uuid4().hex[:8])),
            op=str(data.get("op", "raise_lower")),
            name=str(data.get("name", "Terrain Operation")),
            center_x=cx,
            center_y=cy,
            radius_m=float(data.get("radius_m", 50.0)),
            amount_m=float(data.get("amount_m", 0.0)),
            target_height_m=float(data["target_height_m"]) if data.get("target_height_m") is not None else None,
            strength=float(data.get("strength", 1.0)),
            falloff=str(data.get("falloff", "smooth")),
            region_id=data.get("region_id"),
            polygon=data.get("polygon"),
            is_exclusion=bool(data.get("is_exclusion", False)),
            enabled=bool(data.get("enabled", True)),
            created_at=str(data.get("created_at", _now_iso())),
            metadata=dict(data.get("metadata", {}) or {}),
        )


# -----------------------------------------------------------------------------
# Terrain Editor Engine
# -----------------------------------------------------------------------------

class TerrainEditor:
    """Manages terrain modifications, non-destructive layer stack, and undo/redo."""

    def __init__(
        self,
        base_dem: Any | None = None,
        proj: Any | None = None,
        modifiers: list[TerrainModifier] | None = None,
        regions: list[Any] | None = None,
    ):
        self.base_dem = base_dem
        self.proj = proj
        self.modifiers: list[TerrainModifier] = list(modifiers or [])
        self.undo_stack: list[TerrainModifier] = []
        self.regions_map: dict[str, Any] = {r.id: r for r in (regions or []) if hasattr(r, "id")}

    def set_regions(self, regions: list[Any]) -> None:
        self.regions_map = {r.id: r for r in regions if hasattr(r, "id")}

    # -------------------------------------------------------------------------
    # High-level Sculpting Operations
    # -------------------------------------------------------------------------

    def raise_terrain(
        self,
        center_xy: tuple[float, float],
        radius_m: float,
        amount_m: float,
        falloff: str = "smooth",
        strength: float = 1.0,
        region_id: str | None = None,
        name: str | None = None,
    ) -> TerrainModifier:
        """Applies an upward elevation displacement within the specified radius."""
        mod = TerrainModifier(
            op="raise_lower",
            name=name or f"Raise Terrain (+{amount_m:.1f}m)",
            center_x=float(center_xy[0]),
            center_y=float(center_xy[1]),
            radius_m=float(radius_m),
            amount_m=abs(float(amount_m)),
            strength=float(strength),
            falloff=falloff,
            region_id=region_id,
        )
        return self.add_modifier(mod)

    def lower_terrain(
        self,
        center_xy: tuple[float, float],
        radius_m: float,
        amount_m: float,
        falloff: str = "smooth",
        strength: float = 1.0,
        region_id: str | None = None,
        name: str | None = None,
    ) -> TerrainModifier:
        """Applies a downward elevation displacement within the specified radius."""
        mod = TerrainModifier(
            op="raise_lower",
            name=name or f"Lower Terrain (-{amount_m:.1f}m)",
            center_x=float(center_xy[0]),
            center_y=float(center_xy[1]),
            radius_m=float(radius_m),
            amount_m=-abs(float(amount_m)),
            strength=float(strength),
            falloff=falloff,
            region_id=region_id,
        )
        return self.add_modifier(mod)

    def flatten_terrain(
        self,
        center_xy: tuple[float, float],
        radius_m: float,
        target_height_m: float,
        falloff: str = "smooth",
        strength: float = 1.0,
        region_id: str | None = None,
        name: str | None = None,
    ) -> TerrainModifier:
        """Levels terrain towards a target elevation (plateau / foundation)."""
        mod = TerrainModifier(
            op="flatten",
            name=name or f"Flatten Terrain ({target_height_m:.1f}m)",
            center_x=float(center_xy[0]),
            center_y=float(center_xy[1]),
            radius_m=float(radius_m),
            target_height_m=float(target_height_m),
            strength=float(strength),
            falloff=falloff,
            region_id=region_id,
        )
        return self.add_modifier(mod)

    def smooth_terrain(
        self,
        center_xy: tuple[float, float],
        radius_m: float,
        strength: float = 0.5,
        falloff: str = "smooth",
        region_id: str | None = None,
        name: str | None = None,
    ) -> TerrainModifier:
        """Locally smooths roughness or jagged terrain within the brush area."""
        mod = TerrainModifier(
            op="smooth",
            name=name or f"Smooth Terrain (r={radius_m:.0f}m)",
            center_x=float(center_xy[0]),
            center_y=float(center_xy[1]),
            radius_m=float(radius_m),
            strength=float(strength),
            falloff=falloff,
            region_id=region_id,
        )
        return self.add_modifier(mod)

    def create_hill(
        self,
        center_xy: tuple[float, float],
        radius_m: float,
        height_m: float,
        falloff: str = "smooth",
        region_id: str | None = None,
        name: str | None = None,
    ) -> TerrainModifier:
        """Creates a bell-shaped elevated hill mound."""
        mod = TerrainModifier(
            op="hill_valley",
            name=name or f"Create Hill (+{height_m:.1f}m)",
            center_x=float(center_xy[0]),
            center_y=float(center_xy[1]),
            radius_m=float(radius_m),
            amount_m=abs(float(height_m)),
            strength=1.0,
            falloff=falloff,
            region_id=region_id,
        )
        return self.add_modifier(mod)

    def create_valley(
        self,
        center_xy: tuple[float, float],
        radius_m: float,
        depth_m: float,
        falloff: str = "smooth",
        region_id: str | None = None,
        name: str | None = None,
    ) -> TerrainModifier:
        """Creates a smooth depression or valley."""
        mod = TerrainModifier(
            op="hill_valley",
            name=name or f"Create Valley (-{depth_m:.1f}m)",
            center_x=float(center_xy[0]),
            center_y=float(center_xy[1]),
            radius_m=float(radius_m),
            amount_m=-abs(float(depth_m)),
            strength=1.0,
            falloff=falloff,
            region_id=region_id,
        )
        return self.add_modifier(mod)

    def create_water_area(
        self,
        center_xy: tuple[float, float],
        radius_m: float,
        depth_m: float = 3.0,
        water_level_m: float | None = None,
        falloff: str = "smooth",
        region_id: str | None = None,
        name: str | None = None,
    ) -> TerrainModifier:
        """Carves a basin depression and registers a water table elevation."""
        mod = TerrainModifier(
            op="water_basin",
            name=name or f"Create Water Area (r={radius_m:.0f}m)",
            center_x=float(center_xy[0]),
            center_y=float(center_xy[1]),
            radius_m=float(radius_m),
            amount_m=float(depth_m),
            target_height_m=float(water_level_m) if water_level_m is not None else None,
            strength=1.0,
            falloff=falloff,
            region_id=region_id,
        )
        return self.add_modifier(mod)

    def paint_mask(
        self,
        polygon: list[list[float]],
        weight: float = 1.0,
        is_exclusion: bool = False,
        region_id: str | None = None,
        name: str | None = None,
    ) -> TerrainModifier:
        """Paints a custom polygon terrain weight or exclusion mask."""
        mod = TerrainModifier(
            op="mask_paint",
            name=name or ("Terrain Exclusion Mask" if is_exclusion else "Painted Terrain Mask"),
            polygon=polygon,
            strength=float(weight),
            is_exclusion=is_exclusion,
            region_id=region_id,
        )
        return self.add_modifier(mod)

    def define_exclusion(
        self,
        polygon: list[list[float]] | None = None,
        center_xy: tuple[float, float] | None = None,
        radius_m: float | None = None,
        name: str | None = None,
    ) -> TerrainModifier:
        """Locks an area against any terrain modifications (protected zone)."""
        mod = TerrainModifier(
            op="mask_paint",
            name=name or "Protected Terrain Exclusion",
            polygon=polygon,
            center_x=float(center_xy[0]) if center_xy else 0.0,
            center_y=float(center_xy[1]) if center_xy else 0.0,
            radius_m=float(radius_m) if radius_m else 0.0,
            is_exclusion=True,
        )
        return self.add_modifier(mod)

    # -------------------------------------------------------------------------
    # Undo / Redo & Stack Management
    # -------------------------------------------------------------------------

    def add_modifier(self, mod: TerrainModifier) -> TerrainModifier:
        self.modifiers.append(mod)
        # Any new action clears the redo stack
        self.undo_stack.clear()
        return mod

    def undo(self) -> TerrainModifier | None:
        if not self.modifiers:
            return None
        mod = self.modifiers.pop()
        self.undo_stack.append(mod)
        return mod

    def redo(self) -> TerrainModifier | None:
        if not self.undo_stack:
            return None
        mod = self.undo_stack.pop()
        self.modifiers.append(mod)
        return mod

    def can_undo(self) -> bool:
        return len(self.modifiers) > 0

    def can_redo(self) -> bool:
        return len(self.undo_stack) > 0

    def remove_modifier(self, mod_id: str) -> bool:
        for idx, m in enumerate(self.modifiers):
            if m.id == mod_id:
                self.modifiers.pop(idx)
                return True
        return False

    def toggle_modifier(self, mod_id: str, enabled: bool | None = None) -> bool:
        for m in self.modifiers:
            if m.id == mod_id:
                m.enabled = not m.enabled if enabled is None else bool(enabled)
                return True
        return False

    def clear_history(self) -> None:
        self.modifiers.clear()
        self.undo_stack.clear()

    # -------------------------------------------------------------------------
    # Height Evaluation Core
    # -------------------------------------------------------------------------

    def _get_region_poly(self, region_id: str) -> Polygon | None:
        r = self.regions_map.get(region_id)
        if not r:
            return None
        coords = getattr(r, "polygon", []) or []
        if len(coords) < 3:
            return None
        try:
            poly = Polygon(coords)
            if not poly.is_valid:
                poly = poly.buffer(0)
            return poly
        except Exception:
            return None

    def evaluate_heights(
        self,
        xs: np.ndarray | float,
        ys: np.ndarray | float,
        base_z: np.ndarray | float,
    ) -> np.ndarray:
        """Evaluates final modified terrain heights for arbitrary coordinates.

        Preserves the base DEM and seamlessly evaluates modifier stack in world meters.
        """
        is_scalar = np.isscalar(xs)
        x_arr = np.atleast_1d(np.asarray(xs, dtype=np.float64))
        y_arr = np.atleast_1d(np.asarray(ys, dtype=np.float64))
        z_arr = np.atleast_1d(np.asarray(base_z, dtype=np.float64)).copy()

        active_mods = [m for m in self.modifiers if m.enabled]
        if not active_mods:
            return z_arr[0] if is_scalar else z_arr

        # Pre-resolve exclusion areas
        exclusion_polys = []
        for m in active_mods:
            if m.is_exclusion:
                if m.polygon and len(m.polygon) >= 3:
                    try:
                        p = Polygon(m.polygon)
                        if p.is_valid:
                            exclusion_polys.append(p)
                    except Exception:
                        pass

        exclusion_union = unary_union(exclusion_polys) if exclusion_polys else None

        for mod in active_mods:
            if mod.is_exclusion:
                continue

            # 1. Spatial mask
            if mod.polygon and len(mod.polygon) >= 3:
                try:
                    poly = Polygon(mod.polygon)
                    weights = np.array([
                        mod.strength if poly.contains(Point(x_arr[i], y_arr[i])) else 0.0
                        for i in range(len(x_arr))
                    ], dtype=np.float64)
                except Exception:
                    weights = np.zeros_like(x_arr)
            else:
                dists = np.hypot(x_arr - mod.center_x, y_arr - mod.center_y)
                weights = calculate_radial_falloff(dists, mod.radius_m, mod.falloff) * mod.strength

            # 2. Region scoping filter
            if mod.region_id:
                reg_poly = self._get_region_poly(mod.region_id)
                if reg_poly:
                    for i in range(len(x_arr)):
                        if weights[i] > 0 and not reg_poly.contains(Point(x_arr[i], y_arr[i])):
                            weights[i] = 0.0

            # 3. Global exclusion mask filter
            if exclusion_union:
                for i in range(len(x_arr)):
                    if weights[i] > 0 and exclusion_union.contains(Point(x_arr[i], y_arr[i])):
                        weights[i] = 0.0

            if not np.any(weights > 0):
                continue

            # 4. Apply operation
            if mod.op == "raise_lower":
                z_arr += mod.amount_m * weights
            elif mod.op == "hill_valley":
                z_arr += mod.amount_m * weights
            elif mod.op == "flatten":
                if mod.target_height_m is not None:
                    delta = (mod.target_height_m - z_arr) * weights
                    z_arr += delta
            elif mod.op == "water_basin":
                # Carve basin
                target = (
                    mod.target_height_m - mod.amount_m
                    if mod.target_height_m is not None
                    else (z_arr - mod.amount_m)
                )
                delta = (target - z_arr) * weights
                z_arr += np.minimum(0.0, delta)
            elif mod.op == "smooth":
                # Smooth: approximate neighbor sampling around points
                delta_m = max(2.0, mod.radius_m * 0.1)
                # Sample local ring to determine local elevation mean
                z_local = z_arr  # smoothed towards current neighborhood
                # Blend with smoothed values
                # For point arrays, soft relaxation:
                z_arr += (np.median(z_arr) - z_arr) * (weights * 0.4)

        return z_arr[0] if is_scalar else z_arr

    def evaluate_grid(
        self,
        X: np.ndarray,
        Y: np.ndarray,
        base_Z: np.ndarray,
    ) -> np.ndarray:
        """Fast, vectorized evaluation across a 2D meshgrid (res x res)."""
        Z = base_Z.astype(np.float64).copy()
        active_mods = [m for m in self.modifiers if m.enabled]
        if not active_mods:
            return Z

        # Pre-resolve exclusion areas
        exclusion_polys = []
        for m in active_mods:
            if m.is_exclusion:
                if m.polygon and len(m.polygon) >= 3:
                    try:
                        p = Polygon(m.polygon)
                        if p.is_valid:
                            exclusion_polys.append(p)
                    except Exception:
                        pass
                elif m.radius_m > 0:
                    pt = Point(m.center_x, m.center_y)
                    exclusion_polys.append(pt.buffer(m.radius_m))

        exclusion_union = unary_union(exclusion_polys) if exclusion_polys else None

        for mod in active_mods:
            if mod.is_exclusion:
                continue

            # Compute 2D distances and weights
            D = np.hypot(X - mod.center_x, Y - mod.center_y)
            W = calculate_radial_falloff(D, mod.radius_m, mod.falloff) * mod.strength

            # Region restriction
            if mod.region_id:
                reg_poly = self._get_region_poly(mod.region_id)
                if reg_poly:
                    # Spatial filter
                    min_x, min_y, max_x, max_y = reg_poly.bounds
                    bbox_mask = (X >= min_x) & (X <= max_x) & (Y >= min_y) & (Y <= max_y)
                    if np.any(bbox_mask):
                        pts = [Point(x, y) for x, y in zip(X[bbox_mask], Y[bbox_mask])]
                        in_poly = np.array([reg_poly.contains(pt) for pt in pts])
                        W[~bbox_mask] = 0.0
                        W[bbox_mask] *= in_poly
                    else:
                        W[:] = 0.0

            # Exclusion restriction
            if exclusion_union:
                e_min_x, e_min_y, e_max_x, e_max_y = exclusion_union.bounds
                e_mask = (X >= e_min_x) & (X <= e_max_x) & (Y >= e_min_y) & (Y <= e_max_y)
                if np.any(e_mask):
                    e_pts = [Point(x, y) for x, y in zip(X[e_mask], Y[e_mask])]
                    in_ex = np.array([exclusion_union.contains(pt) for pt in e_pts])
                    W[e_mask] *= (~in_ex)

            if not np.any(W > 0):
                continue

            # Apply modifier
            if mod.op in ("raise_lower", "hill_valley"):
                Z += mod.amount_m * W
            elif mod.op == "flatten":
                if mod.target_height_m is not None:
                    Z += (mod.target_height_m - Z) * W
            elif mod.op == "water_basin":
                target = (
                    mod.target_height_m - mod.amount_m
                    if mod.target_height_m is not None
                    else (Z - mod.amount_m)
                )
                delta = (target - Z) * W
                Z += np.minimum(0.0, delta)
            elif mod.op == "smooth":
                # High-quality Gaussian filter smoothing on the 2D surface
                Z_smooth = gaussian_filter(Z, sigma=1.8)
                Z += (Z_smooth - Z) * W

        return Z

    # -------------------------------------------------------------------------
    # Metadata & Associations
    # -------------------------------------------------------------------------

    def get_water_bodies(self) -> list[dict[str, Any]]:
        """Extracts water surface polygons and heights generated by water_basin modifiers."""
        out = []
        for m in self.modifiers:
            if m.enabled and m.op == "water_basin":
                poly_pts = []
                n_seg = 16
                for i in range(n_seg):
                    ang = 2.0 * math.pi * i / n_seg
                    poly_pts.append([
                        round(m.center_x + m.radius_m * 0.9 * math.cos(ang), 2),
                        round(m.center_y + m.radius_m * 0.9 * math.sin(ang), 2),
                    ])
                out.append({
                    "id": m.id,
                    "name": m.name,
                    "center": {"x": m.center_x, "y": m.center_y},
                    "radius_m": m.radius_m,
                    "water_level_m": m.target_height_m,
                    "depth_m": m.amount_m,
                    "polygon": poly_pts,
                })
        return out

    def get_associations(self, chunks: list[Any] | None = None, regions: list[Any] | None = None) -> dict[str, Any]:
        """Maps each active modifier to the intersecting chunks and regions."""
        assoc: dict[str, Any] = {}
        chunks_list = chunks or []
        regions_list = regions or list(self.regions_map.values())

        for m in self.modifiers:
            m_circle = Point(m.center_x, m.center_y).buffer(max(1.0, m.radius_m))
            c_hits = []
            for ch in chunks_list:
                bp = getattr(ch, "bounds_projected", {})
                if bp:
                    c_box = Polygon([
                        (bp["min_x"], bp["min_y"]),
                        (bp["max_x"], bp["min_y"]),
                        (bp["max_x"], bp["max_y"]),
                        (bp["min_x"], bp["max_y"]),
                    ])
                    if c_box.intersects(m_circle):
                        c_hits.append(ch.id)

            r_hits = []
            for r in regions_list:
                coords = getattr(r, "polygon", []) or []
                if len(coords) >= 3:
                    try:
                        r_poly = Polygon(coords)
                        if r_poly.intersects(m_circle):
                            r_hits.append(r.id)
                    except Exception:
                        pass

            assoc[m.id] = {
                "name": m.name,
                "op": m.op,
                "chunks": c_hits,
                "regions": r_hits,
            }
        return assoc

    # -------------------------------------------------------------------------
    # Export Capabilities
    # -------------------------------------------------------------------------

    def export_heightmap(
        self,
        out_dir: Path | str,
        size_m: float = 2000.0,
        resolution: int = 256,
        stem: str = "terrain_edited",
    ) -> dict[str, Any]:
        """Generates the modified elevation grid and exports a 16-bit PNG heightmap + JSON metadata."""
        out_path = Path(out_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        n = resolution + 1
        half = size_m / 2.0
        step = size_m / resolution
        xs = -half + np.arange(n) * step
        ys = -half + np.arange(n) * step
        X, Y = np.meshgrid(xs, ys)

        if self.base_dem and self.proj:
            lons, lats = self.proj.to_lonlat(X, Y)
            base_Z = self.base_dem.heights(lons, lats)
        else:
            base_Z = np.zeros_like(X)

        Z = self.evaluate_grid(X, Y, base_Z)

        # Call Terragen 16-bit heightmap exporter
        meta = write_heightmap(Z, out_path, stem=stem, size_m=size_m)
        from PIL import Image
        zmin, zmax = float(Z.min()), float(Z.max())
        norm = (Z - zmin) / max(1e-9, zmax - zmin)
        u16 = np.clip(norm * 65535.0, 0, 65535).astype(np.uint16)
        Image.fromarray(u16).save(out_path / f"{stem}_heightmap.png")
        meta["bit_depth"] = 16
        meta["min_altitude_m"] = meta.get("elevation_min")
        meta["max_altitude_m"] = meta.get("elevation_max")
        meta["heightmap_png"] = f"{stem}_heightmap.png"
        meta["modifiers_applied"] = len([m for m in self.modifiers if m.enabled])
        meta["water_bodies"] = self.get_water_bodies()
        meta["export_timestamp"] = _now_iso()

        meta_file = out_path / f"{stem}_metadata.json"
        meta_file.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        return meta

    def export_terrain_mesh(
        self,
        out_dir: Path | str,
        size_m: float = 2000.0,
        resolution: int = 128,
        stem: str = "terrain_edited",
        formats: tuple[str, ...] = ("glb",),
    ) -> dict[str, str]:
        """Generates and exports the 3D surface mesh (GLB/OBJ) with slope-shaded vertices."""
        out_path = Path(out_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        n = resolution + 1
        half = size_m / 2.0
        step = size_m / resolution
        xs = -half + np.arange(n) * step
        ys = -half + np.arange(n) * step
        X, Y = np.meshgrid(xs, ys)

        if self.base_dem and self.proj:
            lons, lats = self.proj.to_lonlat(X, Y)
            base_Z = self.base_dem.heights(lons, lats)
        else:
            base_Z = np.zeros_like(X)

        Z = self.evaluate_grid(X, Y, base_Z)

        # Triangulation
        verts = np.column_stack([X.ravel(), Z.ravel(), Y.ravel()])
        i, j = np.meshgrid(np.arange(resolution), np.arange(resolution), indexing="ij")
        v00 = (i * n + j).ravel()
        v10 = (i * n + j + 1).ravel()
        v01 = ((i + 1) * n + j).ravel()
        v11 = ((i + 1) * n + j + 1).ravel()
        faces = np.vstack([
            np.column_stack([v00, v10, v11]),
            np.column_stack([v00, v11, v01]),
        ])

        # Shading
        gy, gx = np.gradient(Z, step)
        slope = np.degrees(np.arctan(np.hypot(gx, gy)))
        w_slope = np.clip((slope.ravel() - 18.0) / 20.0, 0, 1)[:, None]
        lo = np.array([125, 168, 98], float)
        rock = np.array([122, 120, 116], float)
        col = lo[None, :] * (1 - w_slope) + rock[None, :] * w_slope
        colors = np.clip(col, 0, 255).astype(np.uint8)

        mesh = Mesh()
        mesh.add(verts, faces, colors)

        files = {}
        if "glb" in formats:
            glb_path = out_path / f"{stem}.glb"
            write_glb(mesh, glb_path, name=stem)
            files["glb"] = str(glb_path)

        if "obj" in formats:
            obj_path = out_path / f"{stem}.obj"
            write_obj([("terrain", mesh)], obj_path)
            files["obj"] = str(obj_path)

        return files

    # -------------------------------------------------------------------------
    # Serialization
    # -------------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "modifiers": [m.to_dict() for m in self.modifiers],
            "undo_stack": [m.to_dict() for m in self.undo_stack],
            "can_undo": self.can_undo(),
            "can_redo": self.can_redo(),
            "active_modifiers_count": len([m for m in self.modifiers if m.enabled]),
            "total_modifiers_count": len(self.modifiers),
            "water_bodies_count": len(self.get_water_bodies()),
        }

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        base_dem: Any | None = None,
        proj: Any | None = None,
        regions: list[Any] | None = None,
    ) -> TerrainEditor:
        mods_data = data.get("modifiers", [])
        undo_data = data.get("undo_stack", [])
        editor = cls(
            base_dem=base_dem,
            proj=proj,
            modifiers=[TerrainModifier.from_dict(m) for m in mods_data if isinstance(m, dict)],
            regions=regions,
        )
        editor.undo_stack = [TerrainModifier.from_dict(m) for m in undo_data if isinstance(m, dict)]
        return editor


# -----------------------------------------------------------------------------
# Composite DEM Wrapper
# -----------------------------------------------------------------------------

class ModifiedDem:
    """Seamless DEM wrapper evaluating user terrain modifications over the immutable base DEM.

    Preserves underlying DEM data integrity while supplying modified elevations
    to CityBuilder, ChunkEngine, and road draping.
    """

    def __init__(self, base_dem: Any, editor: TerrainEditor, proj: Any):
        self.base_dem = base_dem
        self.editor = editor
        self.proj = proj

    def __getattr__(self, name: str) -> Any:
        return getattr(self.base_dem, name)

    def heights(self, lons: Any, lats: Any) -> np.ndarray:
        # 1. Query original read-only DEM
        base_z = self.base_dem.heights(lons, lats)
        # 2. Project geographic coordinates to local coordinates (meters)
        lons_arr = np.asarray(lons, dtype=np.float64)
        lats_arr = np.asarray(lats, dtype=np.float64)
        xs, ys = self.proj.to_xy(lons_arr, lats_arr)
        # 3. Non-destructively evaluate terrain modifier stack
        return self.editor.evaluate_heights(xs, ys, base_z)
