"""Asset Prototype Catalog and Builtin 3D Prototype Generators."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from ..export import write_glb
from ..geom import Mesh
from .models import AssetCategory, AssetPrototype, AssetSubcategory

# -----------------------------------------------------------------------------
# Lightweight Procedural Mesh Generators for Prototypes
# -----------------------------------------------------------------------------

def _create_cylinder(radius: float, height: float, segments: int, color: tuple[int, int, int]) -> Mesh:
    """Generates a closed cylinder centered at base (y=0 to y=height)."""
    angles = np.linspace(0, 2 * math.pi, segments, endpoint=False)
    x = radius * np.cos(angles)
    z = radius * np.sin(angles)

    verts_bottom = np.column_stack([x, np.zeros(segments), z])
    verts_top = np.column_stack([x, np.full(segments, height), z])
    center_bot = np.array([[0.0, 0.0, 0.0]])
    center_top = np.array([[0.0, height, 0.0]])

    verts = np.vstack([verts_bottom, verts_top, center_bot, center_top])
    idx_bot_c = 2 * segments
    idx_top_c = 2 * segments + 1

    faces = []
    # Side quads (2 triangles each)
    for i in range(segments):
        nxt = (i + 1) % segments
        b0, b1 = i, nxt
        t0, t1 = i + segments, nxt + segments
        faces.append([b0, t0, t1])
        faces.append([b0, t1, b1])
        # Bottom cap (pointing down)
        faces.append([idx_bot_c, nxt, i])
        # Top cap (pointing up)
        faces.append([idx_top_c, t0, t1])

    colors = np.full((len(verts), 4), [*list(color), 255], dtype=np.uint8)
    m = Mesh()
    m.add(verts, np.array(faces, dtype=np.int32), colors)
    return m


def _create_cone(radius: float, height: float, y_base: float, segments: int, color: tuple[int, int, int]) -> Mesh:
    """Generates a cone with base at y=y_base and apex at y=y_base + height."""
    angles = np.linspace(0, 2 * math.pi, segments, endpoint=False)
    x = radius * np.cos(angles)
    z = radius * np.sin(angles)

    verts_base = np.column_stack([x, np.full(segments, y_base), z])
    apex = np.array([[0.0, y_base + height, 0.0]])
    center_base = np.array([[0.0, y_base, 0.0]])

    verts = np.vstack([verts_base, apex, center_base])
    idx_apex = segments
    idx_center = segments + 1

    faces = []
    for i in range(segments):
        nxt = (i + 1) % segments
        faces.append([i, idx_apex, nxt])
        faces.append([idx_center, nxt, i])

    colors = np.full((len(verts), 4), [*list(color), 255], dtype=np.uint8)
    m = Mesh()
    m.add(verts, np.array(faces, dtype=np.int32), colors)
    return m


def _create_box(width: float, height: float, depth: float, y_base: float, color: tuple[int, int, int]) -> Mesh:
    """Generates a box sitting at y=y_base."""
    hw, hh, hd = width / 2.0, height, depth / 2.0
    v = np.array([
        [-hw, y_base, -hd],
        [hw, y_base, -hd],
        [hw, y_base, hd],
        [-hw, y_base, hd],
        [-hw, y_base + hh, -hd],
        [hw, y_base + hh, -hd],
        [hw, y_base + hh, hd],
        [-hw, y_base + hh, hd],
    ], dtype=np.float64)

    f = np.array([
        [0, 1, 5], [0, 5, 4],  # Front
        [1, 2, 6], [1, 6, 5],  # Right
        [2, 3, 7], [2, 7, 6],  # Back
        [3, 0, 4], [3, 4, 7],  # Left
        [4, 5, 6], [4, 6, 7],  # Top
        [0, 3, 2], [0, 2, 1],  # Bottom
    ], dtype=np.int32)

    c = np.full((len(v), 4), [*list(color), 255], dtype=np.uint8)
    m = Mesh()
    m.add(v, f, c)
    return m


# -----------------------------------------------------------------------------
# Asset Prototype Builders
# -----------------------------------------------------------------------------

def build_tree_prototype(tree_type: str = "pine") -> Mesh:
    """Generates a lightweight low-poly tree prototype."""
    m = Mesh()
    if tree_type == "pine":
        # Trunk
        trunk = _create_cylinder(radius=0.25, height=1.6, segments=5, color=(92, 64, 42))
        m.add(trunk.v, trunk.f, trunk.c)
        # Foliage cones
        c1 = _create_cone(radius=1.8, height=2.4, y_base=1.4, segments=6, color=(45, 110, 52))
        c2 = _create_cone(radius=1.4, height=2.2, y_base=2.6, segments=6, color=(55, 130, 62))
        c3 = _create_cone(radius=0.9, height=2.0, y_base=3.8, segments=5, color=(65, 145, 72))
        m.add(c1.v, c1.f, c1.c)
        m.add(c2.v, c2.f, c2.c)
        m.add(c3.v, c3.f, c3.c)
    else:
        # Deciduous tree
        trunk = _create_cylinder(radius=0.35, height=2.0, segments=6, color=(105, 78, 55))
        m.add(trunk.v, trunk.f, trunk.c)
        # Crown box / dodecahedron proxy
        crown = _create_box(width=2.8, height=3.2, depth=2.8, y_base=1.8, color=(72, 148, 65))
        m.add(crown.v, crown.f, crown.c)
    return m


def build_boulder_prototype(size: float = 2.0) -> Mesh:
    """Generates an irregular low-poly boulder prototype."""
    m = _create_box(width=size, height=size * 0.75, depth=size * 1.1, y_base=0.0, color=(128, 130, 136))
    return m


def build_bush_prototype() -> Mesh:
    """Generates a rounded bush prototype."""
    m = _create_box(width=1.4, height=1.1, depth=1.3, y_base=0.0, color=(82, 155, 75))
    return m


def build_lamp_post_prototype() -> Mesh:
    """Generates an urban street lamp post prototype."""
    m = Mesh()
    pole = _create_cylinder(radius=0.08, height=4.5, segments=5, color=(48, 52, 58))
    head = _create_box(width=0.45, height=0.35, depth=0.45, y_base=4.4, color=(245, 235, 190))
    m.add(pole.v, pole.f, pole.c)
    m.add(head.v, head.f, head.c)
    return m


def build_bench_prototype() -> Mesh:
    """Generates a park bench prototype."""
    m = Mesh()
    seat = _create_box(width=1.8, height=0.15, depth=0.55, y_base=0.45, color=(135, 92, 58))
    back = _create_box(width=1.8, height=0.50, depth=0.10, y_base=0.60, color=(135, 92, 58))
    leg1 = _create_box(width=0.15, height=0.45, depth=0.50, y_base=0.0, color=(45, 48, 52))
    leg2 = _create_box(width=0.15, height=0.45, depth=0.50, y_base=0.0, color=(45, 48, 52))
    # Offset legs
    leg1.v[:, 0] -= 0.7
    leg2.v[:, 0] += 0.7
    back.v[:, 2] -= 0.22
    m.add(seat.v, seat.f, seat.c)
    m.add(back.v, back.f, back.c)
    m.add(leg1.v, leg1.f, leg1.c)
    m.add(leg2.v, leg2.f, leg2.c)
    return m


def build_ruin_prototype() -> Mesh:
    """Generates an ancient stone ruin pillar / wall prototype."""
    m = Mesh()
    col1 = _create_box(width=1.0, height=3.5, depth=1.0, y_base=0.0, color=(145, 142, 138))
    col2 = _create_box(width=1.0, height=2.2, depth=1.0, y_base=0.0, color=(135, 132, 128))
    lintel = _create_box(width=3.2, height=0.6, depth=1.1, y_base=3.5, color=(150, 148, 142))
    col2.v[:, 0] += 2.0
    lintel.v[:, 0] += 1.0
    m.add(col1.v, col1.f, col1.c)
    m.add(col2.v, col2.f, col2.c)
    m.add(lintel.v, lintel.f, lintel.c)
    return m


# -----------------------------------------------------------------------------
# Asset Catalog Registry
# -----------------------------------------------------------------------------

class AssetCatalog:
    """Registry managing available asset prototypes with lazy prototype mesh loading."""

    def __init__(self):
        self._prototypes: dict[str, AssetPrototype] = {}
        self._register_default_prototypes()

    def register(self, prototype: AssetPrototype) -> None:
        self._prototypes[prototype.id] = prototype

    def get(self, prototype_id: str) -> AssetPrototype | None:
        return self._prototypes.get(prototype_id)

    def list_all(self) -> list[AssetPrototype]:
        return list(self._prototypes.values())

    def list_by_category(self, category: AssetCategory) -> list[AssetPrototype]:
        return [p for p in self._prototypes.values() if p.category == category]

    def list_by_subcategory(self, subcategory: AssetSubcategory) -> list[AssetPrototype]:
        return [p for p in self._prototypes.values() if p.subcategory == subcategory]

    def _register_default_prototypes(self) -> None:
        """Populates the catalog with the foundational game world dressing prototypes."""
        # 1. Nature: Trees
        self.register(AssetPrototype(
            id="tree_pine_01",
            name="Conifer Pine Tree",
            category=AssetCategory.NATURE,
            subcategory=AssetSubcategory.TREE,
            collision_radius_m=1.6,
            height_m=5.8,
            mesh=build_tree_prototype("pine"),
            tags=["conifer", "alpine", "forest"],
        ))
        self.register(AssetPrototype(
            id="tree_deciduous_01",
            name="Broadleaf Deciduous Tree",
            category=AssetCategory.NATURE,
            subcategory=AssetSubcategory.TREE,
            collision_radius_m=2.2,
            height_m=6.4,
            mesh=build_tree_prototype("deciduous"),
            tags=["broadleaf", "forest", "grassland"],
        ))

        # 2. Nature: Bushes & Rocks
        self.register(AssetPrototype(
            id="bush_temperate_01",
            name="Temperate Bush",
            category=AssetCategory.NATURE,
            subcategory=AssetSubcategory.BUSH,
            collision_radius_m=0.8,
            height_m=1.2,
            mesh=build_bush_prototype(),
            tags=["shrub", "foliage"],
        ))
        self.register(AssetPrototype(
            id="rock_granite_boulder_01",
            name="Granite Boulder",
            category=AssetCategory.NATURE,
            subcategory=AssetSubcategory.BOULDER,
            collision_radius_m=1.4,
            height_m=1.5,
            mesh=build_boulder_prototype(size=2.2),
            tags=["rock", "alpine", "mountain"],
        ))
        self.register(AssetPrototype(
            id="stone_pebble_cluster_01",
            name="Pebble Cluster",
            category=AssetCategory.NATURE,
            subcategory=AssetSubcategory.STONE,
            collision_radius_m=0.5,
            height_m=0.4,
            mesh=build_boulder_prototype(size=0.6),
            tags=["stone", "river", "shore"],
        ))

        # 3. Infrastructure: Street Lighting & Furniture
        self.register(AssetPrototype(
            id="lamp_post_urban_01",
            name="Urban Street Lamp Post",
            category=AssetCategory.INFRASTRUCTURE,
            subcategory=AssetSubcategory.LAMP_POST,
            collision_radius_m=0.4,
            height_m=4.8,
            mesh=build_lamp_post_prototype(),
            tags=["urban", "lighting", "roadside"],
        ))
        self.register(AssetPrototype(
            id="bench_park_01",
            name="Park Bench",
            category=AssetCategory.INFRASTRUCTURE,
            subcategory=AssetSubcategory.BENCH,
            collision_radius_m=0.9,
            height_m=0.9,
            mesh=build_bench_prototype(),
            tags=["urban", "sidewalk", "park"],
        ))

        # 4. Environment Props: Ruins & POIs
        self.register(AssetPrototype(
            id="ruin_stone_colonnade_01",
            name="Ancient Stone Colonnade",
            category=AssetCategory.ENVIRONMENT_PROPS,
            subcategory=AssetSubcategory.RUIN,
            collision_radius_m=2.8,
            height_m=4.2,
            mesh=build_ruin_prototype(),
            tags=["ruin", "landmark", "poi"],
        ))

    def export_prototype_glbs(self, output_dir: Path | str) -> dict[str, str]:
        """Saves all catalog prototypes as GLB models in output_dir."""
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        files = {}
        for proto in self._prototypes.values():
            if proto.mesh and len(proto.mesh.f) > 0:
                glb_file = out_path / f"{proto.id}.glb"
                write_glb(proto.mesh, glb_file, name=proto.id)
                files[proto.id] = str(glb_file)
        return files
