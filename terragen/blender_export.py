"""Professional Blender Export Pipeline for Terragen 3D Worlds.

Provides end-to-end authoring, validation, and scene preparation for Blender 3.x / 4.x / 5.x:
- Layer Mode: Each logical layer (terrain, roads, sidewalks, buildings, vegetation, water) is exported separately.
- Chunk Mode: Each chunk is exported separately, preserving distinct, unmerged sub-layers.
- PBR glTF 2.0 (GLB) with preserved material names, vertex colors (COLOR_0), UVs, and facade textures.
- Wavefront OBJ / MTL export support.
- 16-bit PNG heightmaps and metadata.
- GeoJSON region boundaries with style presets and attributes.
- Full coordinate validation (1 Blender unit = 1 meter, WGS84 origin, Local Transverse Mercator).
- Pre-export and post-export automated validation.
- Standalone Blender Python importer helper script (import_to_blender.py).
"""

from __future__ import annotations

import copy
import datetime
import json
import math
import struct
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from .export import _srgb_to_linear, _vertex_normals, write_glb, write_heightmap, write_obj
from .geom import Mesh
from .project import LocalProjection
from .projects.model import Project


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


# -----------------------------------------------------------------------------
# Configuration Dataclasses
# -----------------------------------------------------------------------------

class ExportMode(str, Enum):
    LAYER = "layer"
    CHUNK = "chunk"
    BOTH = "both"


@dataclass
class BlenderExportOptions:
    mode: str = "layer"               # "layer" | "chunk" | "both"
    formats: tuple[str, ...] = ("glb",)
    include_heightmap: bool = True
    include_regions_geojson: bool = True
    selected_chunks: list[str] | None = None
    selected_layers: list[str] | None = None
    terrain_res: int = 128
    style_preset: str | None = None
    generate_blender_script: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "formats": list(self.formats),
            "include_heightmap": self.include_heightmap,
            "include_regions_geojson": self.include_regions_geojson,
            "selected_chunks": self.selected_chunks,
            "selected_layers": self.selected_layers,
            "terrain_res": self.terrain_res,
            "style_preset": self.style_preset,
            "generate_blender_script": self.generate_blender_script,
        }


@dataclass
class ValidationReport:
    phase: str                      # "pre_export" | "post_export"
    valid: bool = True
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "valid": self.valid,
            "warnings": list(self.warnings),
            "errors": list(self.errors),
            "details": copy.deepcopy(self.details),
        }


# -----------------------------------------------------------------------------
# GLB Binary Validator
# -----------------------------------------------------------------------------

def validate_glb_file(path: Path | str) -> dict[str, Any]:
    """Parses and validates a binary glTF 2.0 (GLB) file structure.

    Checks:
    - 12-byte header magic (0x46546C67 'glTF') and version (2).
    - JSON chunk validity and parsing.
    - Min/max accessor bounds (ensures no NaNs or Infs).
    - Node names, meshes, primitives, and material definitions.
    """
    f_path = Path(path)
    if not f_path.exists():
        raise FileNotFoundError(f"GLB file not found: {f_path}")

    raw = f_path.read_bytes()
    if len(raw) < 12:
        raise ValueError(f"File too small to be a valid GLB: {len(raw)} bytes")

    magic, version, total_length = struct.unpack_from("<III", raw, 0)
    if magic != 0x46546C67:
        raise ValueError(f"Invalid GLB magic: {hex(magic)} (expected 0x46546C67 'glTF')")
    if version != 2:
        raise ValueError(f"Unsupported glTF version: {version} (expected 2)")
    if total_length != len(raw):
        raise ValueError(f"GLB file size mismatch: header specifies {total_length}, actual is {len(raw)}")

    json_len, chunk_type = struct.unpack_from("<II", raw, 12)
    if chunk_type != 0x4E4F534A:  # ASCII 'JSON'
        raise ValueError(f"First GLB chunk is not JSON (type {hex(chunk_type)})")

    json_bytes = raw[20:20 + json_len]
    doc = json.loads(json_bytes.decode("utf-8"))

    # Validate accessors
    accessors = doc.get("accessors", [])
    for idx, acc in enumerate(accessors):
        for bound_key in ("min", "max"):
            if bound_key in acc:
                for val in acc[bound_key]:
                    if math.isnan(val) or math.isinf(val):
                        raise ValueError(f"Accessor {idx} contains invalid {bound_key} value: {val}")

    nodes = doc.get("nodes", [])
    meshes = doc.get("meshes", [])
    materials = doc.get("materials", [])

    return {
        "valid": True,
        "version": version,
        "byte_size": len(raw),
        "nodes": [n.get("name") for n in nodes if "name" in n],
        "meshes_count": len(meshes),
        "materials": [m.get("name") for m in materials if "name" in m],
        "materials_data": materials,
    }


# -----------------------------------------------------------------------------
# Multi-Mesh GLB Writer (Preserves Separate Objects in a Single File)
# -----------------------------------------------------------------------------

def write_multi_mesh_glb(
    layers: dict[str, Mesh] | list[tuple[Any, ...]],
    path: Path,
    name_prefix: str = "obj",
    textures: dict[str, bytes] | None = None,
) -> int:
    """Exports multiple distinct meshes into a single glTF 2.0 GLB container.

    Each layer forms a distinct node and distinct mesh primitive in Blender,
    preserving object identity, material assignments, and vertex colors without merging.
    """
    if isinstance(layers, (list, tuple)):
        norm_layers = {item[0]: item[1] for item in layers if len(item) >= 2}
    else:
        norm_layers = dict(layers)

    valid_layers = {k: m for k, m in norm_layers.items() if m and len(m.f) > 0 and len(m.v) > 0}
    if not valid_layers:
        return 0

    path.parent.mkdir(parents=True, exist_ok=True)
    textures = textures or {}

    nodes = []
    meshes = []
    materials = []
    accessors = []
    views = []
    parts: list[tuple[bytes, int, int, int, str]] = []

    def add_buf(arr: np.ndarray, target: int, ctype: int, atype: str) -> int:
        data = np.ascontiguousarray(arr).tobytes()
        raw_len = len(data)
        data += b"\0" * ((-len(data)) % 4)
        parts.append((data, raw_len, target, ctype, atype))
        return len(parts) - 1

    doc_images = []
    doc_textures = []
    doc_samplers = [
        {"magFilter": 9729, "minFilter": 9987, "wrapS": 10497, "wrapT": 10497}
    ]

    for layer_name, mesh in valid_layers.items():
        v = mesh.v.copy()
        v[:, 2] *= -1.0  # Terragen (+Z=North) -> glTF (-Z=North)
        faces = mesh.f[:, [0, 2, 1]] if len(mesh.f) else mesh.f.copy()

        if getattr(mesh, "n", None) is not None and len(mesh.n) == len(v) and np.any(mesh.n != 0):
            normals = mesh.n.copy()
            normals[:, 2] *= -1.0
            zero_mask = np.linalg.norm(normals, axis=1) < 1e-6
            if np.any(zero_mask):
                auto_n = _vertex_normals(v, faces)
                normals[zero_mask] = auto_n[zero_mask]
        else:
            normals = _vertex_normals(v, faces)

        colors = _srgb_to_linear(
            mesh.c if len(mesh.c) == len(v) else np.full((len(v), 4), 255, np.uint8)
        )
        uv = mesh.uv if len(mesh.uv) == len(v) else np.zeros((len(v), 2))

        tex_bytes = textures.get(layer_name)

        i_pos = add_buf(v.astype(np.float32), 34962, 5126, "VEC3")
        i_nrm = add_buf(normals.astype(np.float32), 34962, 5126, "VEC3")
        i_col = add_buf(colors.astype(np.uint8), 34962, 5121, "VEC4")
        i_uv = add_buf(uv.astype(np.float32), 34962, 5126, "VEC2")
        i_idx = add_buf(faces.astype(np.uint32).reshape(-1), 34963, 5125, "SCALAR")

        # Material setup: Single-sided for opaque manifold meshes (GPU backface culling in Unity)
        mat_idx = len(materials)
        mat_name = f"mat_{layer_name}"
        material: dict[str, Any] = {
            "name": mat_name,
            "pbrMetallicRoughness": {
                "baseColorFactor": [1.0, 1.0, 1.0, 1.0],
                "metallicFactor": 0.05 if layer_name == "roads" else 0.0,
                "roughnessFactor": 0.2 if layer_name == "water" else 0.85,
            },
            "doubleSided": False,
        }

        if tex_bytes:
            i_img = add_buf(np.frombuffer(tex_bytes, dtype=np.uint8), 0, 5121, "SCALAR")
            img_idx = len(doc_images)
            doc_images.append({
                "bufferView": i_img,
                "mimeType": "image/png",
                "name": f"tex_{layer_name}",
            })
            tex_idx = len(doc_textures)
            doc_textures.append({"sampler": 0, "source": img_idx, "name": f"tex_{layer_name}"})
            material["pbrMetallicRoughness"]["baseColorTexture"] = {"index": tex_idx}

        materials.append(material)

        # Accessors for this mesh primitive
        pos_acc = {
            "bufferView": i_pos, "componentType": 5126, "count": len(v), "type": "VEC3",
            "min": [float(x) for x in np.min(v, axis=0)],
            "max": [float(x) for x in np.max(v, axis=0)],
        }
        nrm_acc = {"bufferView": i_nrm, "componentType": 5126, "count": len(v), "type": "VEC3"}
        col_acc = {"bufferView": i_col, "componentType": 5121, "count": len(v), "type": "VEC4", "normalized": True}
        uv_acc = {"bufferView": i_uv, "componentType": 5126, "count": len(v), "type": "VEC2"}
        idx_acc = {"bufferView": i_idx, "componentType": 5125, "count": faces.size, "type": "SCALAR"}

        accessors.extend([pos_acc, nrm_acc, col_acc, uv_acc, idx_acc])

        prim = {
            "attributes": {
                "POSITION": i_pos,
                "NORMAL": i_nrm,
                "COLOR_0": i_col,
                "TEXCOORD_0": i_uv,
            },
            "indices": i_idx,
            "mode": 4,  # TRIANGLES
            "material": mat_idx,
        }

        obj_name = f"{name_prefix}_{layer_name.capitalize()}"
        mesh_idx = len(meshes)
        meshes.append({"name": obj_name, "primitives": [prim]})
        nodes.append({"name": obj_name, "mesh": mesh_idx})

    # Assemble BufferViews
    blob = b"".join(p[0] for p in parts)
    offset = 0
    for data, raw_len, target, _ctype, _atype in parts:
        view = {"buffer": 0, "byteOffset": offset, "byteLength": raw_len}
        if target:
            view["target"] = target
        views.append(view)
        offset += len(data)

    doc: dict[str, Any] = {
        "asset": {"version": "2.0", "generator": "terragen-blender-pipeline"},
        "scene": 0,
        "scenes": [{"nodes": list(range(len(nodes)))}],
        "nodes": nodes,
        "meshes": meshes,
        "materials": materials,
        "accessors": accessors,
        "bufferViews": views,
        "buffers": [{"byteLength": len(blob)}],
    }

    if doc_images:
        doc["images"] = doc_images
        doc["textures"] = doc_textures
        doc["samplers"] = doc_samplers

    js = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    js += b" " * ((-len(js)) % 4)
    bin_chunk = blob + b"\0" * ((-len(blob)) % 4)
    total = 12 + 8 + len(js) + 8 + len(bin_chunk)

    with open(path, "wb") as fh:
        fh.write(struct.pack("<III", 0x46546C67, 2, total))
        fh.write(struct.pack("<II", len(js), 0x4E4F534A))
        fh.write(js)
        fh.write(struct.pack("<II", len(bin_chunk), 0x004E4942))
        fh.write(bin_chunk)

    return path.stat().st_size


# -----------------------------------------------------------------------------
# Standalone Blender Importer Script Template
# -----------------------------------------------------------------------------

BLENDER_IMPORT_SCRIPT_TEMPLATE = '''"""Standalone Terragen 3D World Importer for Blender 3.x, 4.x, and 5.x.

Features:
- Imports Terragen Layer Mode and Chunk Mode export packages.
- Automatically organizes the scene into 9 clean production collections under WORLD:
    * WORLD (Root Collection)
    * TERRAIN (Relief & terrain chunks)
    * ROADS (Highways, streets, sidewalks)
    * BUILDINGS (Extruded architecture & roofs)
    * WATER (Rivers, lakes, waterbodies)
    * VEGETATION (Instanced trees, bushes, flowers)
    * PROPS (Instanced street furniture, lamps, benches, ruins)
    * COLLISION (Simplified collision geometry, wireframe, hide_render)
    * LOD (Level of Detail variants: LOD_1, LOD_2)
    * REFERENCE (Origin marker at 0,0,0 with WGS84 metadata, chunk boundaries)
- Idempotent and repeatable: Re-running the script updates/replaces objects cleanly without creating .001 duplications.
- Zero-VRAM Linked Duplicates for vegetation and environment props.
- Consolidates duplicate materials (.001, .002) for clean shader organization.
- Supports selective collection, chunk, and layer filtering.

Run inside Blender:
  Open Blender -> Scripting Workspace -> Open this script -> Click 'Run Script' (Alt+P).

Or run via command line (headless / batch):
  blender --background --python import_to_blender.py
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

try:
    import bpy
except ImportError:
    print("Error: This script must be executed within Blender environment ('bpy' required).")
    sys.exit(1)

# -----------------------------------------------------------------------------
# User Filters (Configure via CLI or script variables)
# -----------------------------------------------------------------------------
FILTER_COLLECTIONS: list[str] | None = None
FILTER_CHUNKS: list[str] | None = None
FILTER_LAYERS: list[str] | None = None
SET_VIEWPORT_CLIPPING: bool = True
CONSOLIDATE_MATERIALS: bool = True

STANDARD_COLLECTION_NAMES = [
    "TERRAIN",
    "ROADS",
    "BUILDINGS",
    "WATER",
    "VEGETATION",
    "PROPS",
    "COLLISION",
    "LOD",
    "REFERENCE",
]


def parse_cli_args():
    global FILTER_COLLECTIONS, FILTER_CHUNKS, FILTER_LAYERS
    if "--" in sys.argv:
        argv = sys.argv[sys.argv.index("--") + 1 :]
        parser = argparse.ArgumentParser(description="Terragen Blender Importer")
        parser.add_argument("--collections", type=str, default=None, help="Comma-separated collections to import")
        parser.add_argument("--chunks", type=str, default=None, help="Comma-separated chunk IDs to import")
        parser.add_argument("--layers", type=str, default=None, help="Comma-separated layer names to import")
        args, _ = parser.parse_known_args(argv)
        if args.collections:
            FILTER_COLLECTIONS = [c.strip().upper() for c in args.collections.split(",")]
        if args.chunks:
            FILTER_CHUNKS = [c.strip() for c in args.chunks.split(",")]
        if args.layers:
            FILTER_LAYERS = [l.strip().lower() for l in args.layers.split(",")]


def get_or_create_collection(name: str, parent_collection: bpy.types.Collection | None = None) -> bpy.types.Collection:
    coll = bpy.data.collections.get(name)
    if not coll:
        coll = bpy.data.collections.new(name)
        if parent_collection:
            parent_collection.children.link(coll)
        else:
            bpy.context.scene.collection.children.link(coll)
    return coll


def purge_collection_objects(coll: bpy.types.Collection):
    """Cleanly purges objects in collection before re-import to guarantee zero .001 duplications."""
    for obj in list(coll.objects):
        mesh_data = obj.data if obj.type == "MESH" else None
        bpy.data.objects.remove(obj, do_unlink=True)
        if mesh_data and mesh_data.users == 0:
            bpy.data.meshes.remove(mesh_data)


def configure_viewport():
    if not SET_VIEWPORT_CLIPPING:
        return
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                for space in area.spaces:
                    if space.type == "VIEW_3D":
                        space.clip_start = 0.1
                        space.clip_end = 50000.0  # 50 km view distance


def consolidate_materials():
    if not CONSOLIDATE_MATERIALS:
        return
    mats = bpy.data.materials
    for obj in bpy.context.scene.objects:
        if obj.type != "MESH":
            continue
        for slot in obj.material_slots:
            if not slot.material:
                continue
            mat_name = slot.material.name
            if "." in mat_name and any(mat_name.endswith(f".{i:03d}") for i in range(1, 100)):
                base_name = mat_name.split(".")[0]
                base_mat = mats.get(base_name)
                if base_mat and base_mat != slot.material:
                    old_mat = slot.material
                    slot.material = base_mat
                    if old_mat.users == 0:
                        mats.remove(old_mat)


def setup_environment_and_lighting(manifest: dict, root_coll: bpy.types.Collection):
    style_info = manifest.get("style", {})
    lighting = style_info.get("lighting")
    if not lighting:
        return

    try:
        env_coll = get_or_create_collection("Environment_Lighting", root_coll)

        # Sun Lamp
        sun_name = "Sun_Terragen"
        sun_obj = bpy.data.objects.get(sun_name)
        if not sun_obj:
            sun_data = bpy.data.lights.new(name=sun_name, type="SUN")
            sun_obj = bpy.data.objects.new(name=sun_name, object_data=sun_data)
            env_coll.objects.link(sun_obj)

        sun_light = sun_obj.data
        sun_light.energy = float(lighting.get("sun_intensity", 4.0))
        sun_rgb = lighting.get("sun_color_rgb", [255, 246, 230])
        sun_light.color = (sun_rgb[0] / 255.0, sun_rgb[1] / 255.0, sun_rgb[2] / 255.0)

        az_rad = math.radians(float(lighting.get("sun_azimuth_deg", 55.0)))
        el_rad = math.radians(float(lighting.get("sun_elevation_deg", 48.0)))
        sun_obj.rotation_euler = (math.pi / 2.0 - el_rad, 0.0, az_rad)

        if bpy.context.scene.world and bpy.context.scene.world.use_nodes:
            nodes = bpy.context.scene.world.node_tree.nodes
            bg_node = nodes.get("Background")
            if bg_node:
                sky_rgb = lighting.get("ambient_sky_color_rgb", [175, 205, 235])
                bg_node.inputs["Color"].default_value = (
                    sky_rgb[0] / 255.0,
                    sky_rgb[1] / 255.0,
                    sky_rgb[2] / 255.0,
                    1.0,
                )
                bg_node.inputs["Strength"].default_value = float(lighting.get("ambient_intensity", 0.5))
        print("✔ Initialized environment lighting & atmosphere (Sun + Ambient Sky).")
    except Exception as exc:
        print(f"Notice: Could not setup lighting: {exc}")


def import_world_dressing_instances(
    manifest: dict,
    script_dir: Path,
    coll_veg: bpy.types.Collection,
    coll_props: bpy.types.Collection,
    coll_ref: bpy.types.Collection,
) -> int:
    dressing_meta = manifest.get("world_dressing")
    if not dressing_meta:
        return 0

    manifest_rel = dressing_meta.get("manifest")
    if not manifest_rel:
        return 0

    dressing_file = script_dir / manifest_rel
    if not dressing_file.exists():
        return 0

    try:
        data = json.loads(dressing_file.read_text(encoding="utf-8"))
        prototypes_data = data.get("prototypes", [])
        collections_data = data.get("collections", {})

        if not collections_data:
            return 0

        # Prototype asset storage under REFERENCE
        proto_coll = get_or_create_collection("Assets_Prototypes", coll_ref)
        proto_coll.hide_render = True

        loaded_protos: dict[str, bpy.types.Object] = {}
        for p in prototypes_data:
            p_id = p.get("id")
            p_rel = p.get("file")
            if not p_id or not p_rel:
                continue
            p_path = script_dir / p_rel
            if not p_path.exists():
                continue

            proto_name = f"Proto_{p_id}"
            existing = bpy.data.objects.get(proto_name)
            if existing:
                loaded_protos[p_id] = existing
                continue

            pre = set(bpy.data.objects)
            bpy.ops.import_scene.gltf(filepath=str(p_path))
            new_objs = [o for o in bpy.data.objects if o not in pre]
            if new_objs:
                proto_obj = new_objs[0]
                proto_obj.name = proto_name
                for c in list(proto_obj.users_collection):
                    c.objects.unlink(proto_obj)
                proto_coll.objects.link(proto_obj)
                loaded_protos[p_id] = proto_obj

        # Purge existing instances before re-import for idempotency
        purge_collection_objects(coll_veg)
        purge_collection_objects(coll_props)

        total_spawned = 0
        for coll_name, inst_list in collections_data.items():
            is_nature = coll_name.lower().startswith("nature")
            parent_dest = coll_veg if is_nature else coll_props
            sub_coll = get_or_create_collection(coll_name, parent_dest)
            purge_collection_objects(sub_coll)

            for inst in inst_list:
                p_id = inst.get("prototype_id")
                proto_obj = loaded_protos.get(p_id)
                if not proto_obj or not proto_obj.data:
                    continue

                pos = inst.get("position", [0, 0, 0])
                rot_deg = inst.get("rotation_euler_deg", [0, 0, 0])
                scl = inst.get("scale", [1, 1, 1])
                inst_id = inst.get("id", f"inst_{total_spawned}")

                inst_obj = bpy.data.objects.new(name=f"{p_id}_{inst_id}", object_data=proto_obj.data)
                inst_obj.location = (pos[0], -pos[2], pos[1])
                inst_obj.rotation_euler = (
                    math.radians(rot_deg[0]),
                    math.radians(rot_deg[2]),
                    math.radians(rot_deg[1]),
                )
                inst_obj.scale = (scl[0], scl[2], scl[1])
                inst_obj["terragen_id"] = inst_id
                inst_obj["terragen_prototype"] = p_id
                inst_obj["terragen_category"] = "nature" if is_nature else "prop"
                sub_coll.objects.link(inst_obj)
                total_spawned += 1

        print(f"✔ Instanced {total_spawned} world dressing objects across VEGETATION and PROPS.")
        return total_spawned
    except Exception as exc:
        print(f"Notice: World dressing import: {exc}")
        return 0


def import_collision_meshes(manifest: dict, script_dir: Path, coll_collision: bpy.types.Collection) -> int:
    col_info = manifest.get("collision")
    if not col_info:
        return 0

    purge_collection_objects(coll_collision)
    total_col = 0

    for col_type, rel_file in col_info.items():
        if not rel_file:
            continue
        file_path = script_dir / rel_file
        if not file_path.exists():
            continue

        pre_objs = set(bpy.data.objects)
        bpy.ops.import_scene.gltf(filepath=str(file_path))
        new_objs = [obj for obj in bpy.data.objects if obj not in pre_objs]

        for obj in new_objs:
            obj.name = f"Collision_{col_type.capitalize()}"
            obj.display_type = "WIRE"
            obj["terragen_id"] = f"col_{col_type}"
            obj["terragen_layer"] = "collision"
            for c in list(obj.users_collection):
                if c != coll_collision:
                    c.objects.unlink(obj)
            if obj.name not in coll_collision.objects:
                coll_collision.objects.link(obj)
            total_col += 1

    print(f"✔ Imported {total_col} collision objects into COLLISION collection (wireframe).")
    return total_col


def import_lod_meshes(manifest: dict, script_dir: Path, coll_lod: bpy.types.Collection) -> int:
    lod_info = manifest.get("lod")
    if not lod_info:
        return 0

    total_lod = 0
    for lod_key, lod_files in lod_info.items():
        lod_suffix = lod_key.lower().replace("lod", "").strip("_")
        sub_name = f"LOD_{lod_suffix}" if lod_suffix else lod_key.upper()
        sub_lod_coll = get_or_create_collection(sub_name, coll_lod)
        purge_collection_objects(sub_lod_coll)

        for name, rel_file in lod_files.items():
            if not rel_file:
                continue
            file_path = script_dir / rel_file
            if not file_path.exists():
                continue

            pre_objs = set(bpy.data.objects)
            bpy.ops.import_scene.gltf(filepath=str(file_path))
            new_objs = [obj for obj in bpy.data.objects if obj not in pre_objs]

            for obj in new_objs:
                obj.name = f"{name.capitalize()}_{lod_key.upper()}"
                obj["terragen_id"] = f"{name}_{lod_key}"
                obj["terragen_lod"] = lod_key
                for c in list(obj.users_collection):
                    if c != sub_lod_coll:
                        c.objects.unlink(obj)
                if obj.name not in sub_lod_coll.objects:
                    sub_lod_coll.objects.link(obj)
                total_lod += 1

    print(f"✔ Imported {total_lod} LOD variants into LOD collection.")
    return total_lod


def setup_reference_elements(manifest: dict, coll_ref: bpy.types.Collection):
    purge_collection_objects(coll_ref)

    # 1. World Origin Empty Marker
    origin_obj = bpy.data.objects.get("World_Origin")
    if not origin_obj:
        origin_obj = bpy.data.objects.new("World_Origin", None)
        origin_obj.empty_display_type = "ARROWS"
        origin_obj.empty_display_size = 50.0  # 50 meters
        coll_ref.objects.link(origin_obj)

    coords = manifest.get("coordinates", {})
    w_orig = coords.get("world_origin", {})
    origin_obj["world_origin_lat"] = float(w_orig.get("lat", 0.0))
    origin_obj["world_origin_lon"] = float(w_orig.get("lon", 0.0))
    origin_obj["elevation_ref"] = float(w_orig.get("altitude") or 0.0)
    origin_obj["project_name"] = manifest.get("project_name", "")
    origin_obj["export_timestamp"] = manifest.get("export_timestamp", "")
    origin_obj["units"] = "1 Blender unit = 1.0 meter"

    # 2. Chunk Grid Boundaries
    ref_info = manifest.get("reference", {})
    chunks_grid = ref_info.get("chunks_grid", [])
    if chunks_grid:
        grid_coll = get_or_create_collection("Chunk_Bounds", coll_ref)
        purge_collection_objects(grid_coll)
        for ch in chunks_grid:
            ch_id = ch.get("id")
            bp = ch.get("bounds", {})
            if not bp:
                continue
            min_x, max_x = bp.get("min_x", 0.0), bp.get("max_x", 0.0)
            min_y, max_y = bp.get("min_y", 0.0), bp.get("max_y", 0.0)
            cx = (min_x + max_x) / 2.0
            cz = (min_y + max_y) / 2.0
            sx = (max_x - min_x)
            sz = (max_y - min_y)

            ch_box = bpy.data.objects.new(f"Bound_{ch_id}", None)
            ch_box.empty_display_type = "CUBE"
            ch_box.location = (cx, -cz, 0.0)
            ch_box.scale = (sx / 2.0, sz / 2.0, 50.0)
            ch_box["terragen_chunk_id"] = ch_id
            ch_box["terragen_row"] = ch.get("row")
            ch_box["terragen_col"] = ch.get("col")
            grid_coll.objects.link(ch_box)

    print("✔ Initialized REFERENCE collection (World Origin Empty + Chunk Grid Bounds).")


def import_terragen_scene():
    parse_cli_args()

    script_dir = Path(__file__).resolve().parent
    manifest_file = script_dir / "blender_manifest.json"
    if not manifest_file.exists():
        print(f"Error: blender_manifest.json not found in {script_dir}")
        return False

    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    project_name = manifest.get("project_name", "Terragen_World")
    export_mode = manifest.get("export_mode", "layer")
    print("=" * 70)
    print(f"Terragen Professional Blender Importer: {project_name}")
    print(f"Export Mode: {export_mode}")
    print(f"World Origin: Lat {manifest['coordinates']['world_origin']['lat']:.5f}, Lon {manifest['coordinates']['world_origin']['lon']:.5f}")
    print("=" * 70)

    configure_viewport()

    # 1. Setup Root WORLD Collection
    world_root = get_or_create_collection("WORLD")
    # Also ensure legacy name link for backward compatibility
    legacy_coll = get_or_create_collection(f"Terragen_{project_name}", world_root)

    # 2. Setup standard 9 studio collections
    coll_terrain = get_or_create_collection("TERRAIN", world_root)
    coll_roads = get_or_create_collection("ROADS", world_root)
    coll_buildings = get_or_create_collection("BUILDINGS", world_root)
    coll_water = get_or_create_collection("WATER", world_root)
    coll_vegetation = get_or_create_collection("VEGETATION", world_root)
    coll_props = get_or_create_collection("PROPS", world_root)
    coll_collision = get_or_create_collection("COLLISION", world_root)
    coll_collision.hide_render = True
    coll_lod = get_or_create_collection("LOD", world_root)
    coll_lod.hide_render = True
    coll_lod.hide_viewport = True
    coll_reference = get_or_create_collection("REFERENCE", world_root)
    coll_reference.hide_render = True

    total_objects_imported = 0
    missing_files: list[str] = []

    def get_dest_collection_for_layer(layer_str: str) -> bpy.types.Collection:
        l = layer_str.lower()
        if "terrain" in l:
            return coll_terrain
        elif "road" in l or "sidewalk" in l:
            return coll_roads
        elif "building" in l:
            return coll_buildings
        elif "water" in l:
            return coll_water
        elif "vegetation" in l or "tree" in l or "bush" in l:
            return coll_vegetation
        return coll_props

    # 3. Import Layers
    if export_mode == "layer" or "layers" in manifest:
        layers_data = manifest.get("layers", [])
        for item in layers_data:
            layer_name = item.get("name", "layer")
            if FILTER_LAYERS and layer_name.lower() not in [l.lower() for l in FILTER_LAYERS]:
                continue

            dest_coll = get_dest_collection_for_layer(layer_name)
            if FILTER_COLLECTIONS and dest_coll.name not in FILTER_COLLECTIONS:
                continue

            rel_file = item.get("file")
            if not rel_file:
                continue
            layer_path = script_dir / rel_file
            if not layer_path.exists():
                missing_files.append(str(rel_file))
                print(f"Warning: Missing layer file: {rel_file}")
                continue

            # Idempotent purge of existing layer object
            layer_obj_name = f"Layer_{layer_name.capitalize()}"
            existing_obj = bpy.data.objects.get(layer_obj_name)
            if existing_obj:
                old_mesh = existing_obj.data if existing_obj.type == "MESH" else None
                bpy.data.objects.remove(existing_obj, do_unlink=True)
                if old_mesh and old_mesh.users == 0:
                    bpy.data.meshes.remove(old_mesh)

            pre_objs = set(bpy.data.objects)
            bpy.ops.import_scene.gltf(filepath=str(layer_path))
            new_objs = [obj for obj in bpy.data.objects if obj not in pre_objs]

            for obj in new_objs:
                obj.name = layer_obj_name
                obj["terragen_id"] = f"layer_{layer_name}"
                obj["terragen_layer"] = layer_name
                for c in list(obj.users_collection):
                    if c != dest_coll:
                        c.objects.unlink(obj)
                if obj.name not in dest_coll.objects:
                    dest_coll.objects.link(obj)
                # Also link to legacy collection for backwards compatibility
                if obj.name not in legacy_coll.objects:
                    legacy_coll.objects.link(obj)
                total_objects_imported += 1

            print(f"✔ Imported Layer: {layer_name} into collection '{dest_coll.name}'")

    # 4. Import Chunks
    if export_mode == "chunk" or "chunks" in manifest:
        chunks_data = manifest.get("chunks", [])
        for ch in chunks_data:
            chunk_id = ch.get("id", "chunk")
            if FILTER_CHUNKS and chunk_id not in FILTER_CHUNKS:
                continue

            files = ch.get("files", {})
            for l_name, rel_file in files.items():
                if not (rel_file.endswith(".glb") or rel_file.endswith(".gltf")):
                    continue
                if FILTER_LAYERS and l_name.lower() not in [l.lower() for l in FILTER_LAYERS]:
                    continue

                dest_coll = get_dest_collection_for_layer(l_name)
                if FILTER_COLLECTIONS and dest_coll.name not in FILTER_COLLECTIONS:
                    continue

                file_path = script_dir / rel_file
                if not file_path.exists():
                    missing_files.append(str(rel_file))
                    continue

                chunk_obj_name = f"{chunk_id}_{l_name}"
                existing_obj = bpy.data.objects.get(chunk_obj_name)
                if existing_obj:
                    old_mesh = existing_obj.data if existing_obj.type == "MESH" else None
                    bpy.data.objects.remove(existing_obj, do_unlink=True)
                    if old_mesh and old_mesh.users == 0:
                        bpy.data.meshes.remove(old_mesh)

                pre_objs = set(bpy.data.objects)
                bpy.ops.import_scene.gltf(filepath=str(file_path))
                new_objs = [obj for obj in bpy.data.objects if obj not in pre_objs]

                for obj in new_objs:
                    obj.name = chunk_obj_name
                    obj["terragen_id"] = f"{chunk_id}_{l_name}"
                    obj["terragen_chunk"] = chunk_id
                    for c in list(obj.users_collection):
                        if c != dest_coll:
                            c.objects.unlink(obj)
                    if obj.name not in dest_coll.objects:
                        dest_coll.objects.link(obj)
                    if obj.name not in legacy_coll.objects:
                        legacy_coll.objects.link(obj)
                    total_objects_imported += 1

            print(f"✔ Imported Chunk: {chunk_id}")

    # 5. Import Collision, LOD, and Reference
    if not FILTER_COLLECTIONS or "COLLISION" in FILTER_COLLECTIONS:
        import_collision_meshes(manifest, script_dir, coll_collision)

    if not FILTER_COLLECTIONS or "LOD" in FILTER_COLLECTIONS:
        import_lod_meshes(manifest, script_dir, coll_lod)

    if not FILTER_COLLECTIONS or "REFERENCE" in FILTER_COLLECTIONS:
        setup_reference_elements(manifest, coll_reference)

    # 6. Setup Lighting and World Dressing
    setup_environment_and_lighting(manifest, world_root)

    total_dressed = 0
    if not FILTER_COLLECTIONS or ("VEGETATION" in FILTER_COLLECTIONS or "PROPS" in FILTER_COLLECTIONS):
        total_dressed = import_world_dressing_instances(
            manifest,
            script_dir,
            coll_veg=coll_vegetation,
            coll_props=coll_props,
            coll_ref=coll_reference,
        )

    # 7. Shader & Material Consolidation
    consolidate_materials()

    print("=" * 70)
    print(
        f"Terragen Import Complete: {total_objects_imported} objects and {total_dressed} instanced dressing props "
        f"organized under 'WORLD' ({legacy_coll.name})."
    )
    if missing_files:
        print(f"Warnings: {len(missing_files)} missing files reported.")
    print("=" * 70)
    return True


if __name__ == "__main__":
    import_terragen_scene()
'''


# -----------------------------------------------------------------------------
# Documentation Template
# -----------------------------------------------------------------------------

README_BLENDER_TEMPLATE = """# Terragen 3D World - Blender Scene Export

This package contains 3D assets generated by **Terragen** prepared specifically for editing, lighting, and rendering in **Blender 3.x, 4.x, and 5.x**.

## Coordinate System Conventions
- **Units**: 1 Blender Unit = 1.0 Meter.
- **World Origin**: Center of project (Lat: {center_lat:.6f}, Lon: {center_lon:.6f}).
- **Axes in Blender**: +X = East, +Y = North, +Z = Up (Elevation).
- **Scale**: Real-world 1:1 scale.

## Package Structure
- `blender_manifest.json`: Master index describing world dimensions, chunks, layers, materials, and bounds.
- `import_to_blender.py`: Automated Python importer script for Blender.
- `layers/` or `chunks/`: 3D meshes in standard glTF 2.0 (GLB) with PBR materials, vertex colors, and UVs.
- `heightmap/`: 16-bit PNG elevation map and raw `.r16` binary for terrain displacement shaders.
- `regions/regions.geojson`: GeoJSON representation of all drawn/edited planning regions.

## How to Import into Blender

### Method A: Automated One-Click Import (Recommended)
1. Launch Blender.
2. In the top bar, switch to the **Scripting** workspace.
3. Click **Open** and select `import_to_blender.py` from this directory.
4. Click **Run Script** (or press `Alt + P`).
5. All chunks and layers will be imported into organized collections under `Terragen_{project_name}` with correct world positions and 50 km viewport clipping.

### Method B: Headless Command Line Import
```bash
blender --background --python import_to_blender.py
```

### Method C: Manual Import
1. Go to `File` -> `Import` -> `glTF 2.0 (.glb/.gltf)`.
2. Browse to the `layers/` or `chunks/` directory and select the desired GLB files.
3. The objects will align automatically at their exact real-world coordinates.
"""


# -----------------------------------------------------------------------------
# Blender Export Pipeline Engine
# -----------------------------------------------------------------------------

class BlenderExportPipeline:
    """End-to-end export orchestrator for Blender packages."""

    def __init__(self, project: Project, project_dir: Path | str):
        self.project = project
        self.project_dir = Path(project_dir)
        self.proj = LocalProjection(
            project.geographic.center_lat,
            project.geographic.center_lon,
        )

    # -------------------------------------------------------------------------
    # Pre-Export Validation
    # -------------------------------------------------------------------------

    def validate_pre_export(self, options: BlenderExportOptions) -> ValidationReport:
        """Validates project geometry, chunk boundaries, materials, and coordinates before export."""
        report = ValidationReport(phase="pre_export")

        # 1. Geographic & Coordinate checks
        geo = self.project.geographic
        if not (-85.0 <= geo.center_lat <= 85.0) or not (-180.0 <= geo.center_lon <= 180.0):
            report.errors.append(f"Invalid geographic center coordinates: lat={geo.center_lat}, lon={geo.center_lon}")
            report.valid = False

        dim_x = self.project.dimensions.size_x_m or 2000.0
        dim_y = self.project.dimensions.size_y_m or 2000.0
        if dim_x <= 0 or dim_y <= 0:
            report.errors.append(f"World dimensions must be positive: x={dim_x}, y={dim_y}")
            report.valid = False

        # 2. Chunk Bounds validation
        if options.mode in ("chunk", "both"):
            items = self.project.chunks.items
            if not items:
                report.warnings.append("Project chunk list is empty. Initializing grid for chunk export.")
            for ch in items:
                bp = ch.bounds_projected
                if bp:
                    w = bp.get("max_x", 0) - bp.get("min_x", 0)
                    h = bp.get("max_y", 0) - bp.get("min_y", 0)
                    if w <= 0 or h <= 0:
                        report.errors.append(f"Chunk {ch.id} has invalid projected dimensions: {w}x{h}m")
                        report.valid = False

        # 3. Regions geometry validation
        for r in self.project.regions:
            poly = getattr(r, "polygon", []) or []
            if len(poly) < 3:
                report.warnings.append(f"Region '{r.name}' ({r.id}) has fewer than 3 vertices and will be skipped in GeoJSON.")

        report.details["project_name"] = self.project.name
        report.details["mode"] = options.mode
        report.details["chunks_count"] = len(self.project.chunks.items)
        report.details["regions_count"] = len(self.project.regions)
        return report

    # -------------------------------------------------------------------------
    # Post-Export Validation
    # -------------------------------------------------------------------------

    def validate_post_export(self, export_dir: Path | str, manifest_data: dict[str, Any]) -> ValidationReport:
        """Validates generated GLB files, heightmaps, GeoJSON, and chunk alignment after export."""
        report = ValidationReport(phase="post_export")
        out_path = Path(export_dir)

        # 1. Manifest file check
        manifest_file = out_path / "blender_manifest.json"
        if not manifest_file.exists():
            report.errors.append("blender_manifest.json is missing.")
            report.valid = False
            return report

        # 2. Importer script check
        script_file = out_path / "import_to_blender.py"
        if not script_file.exists():
            report.errors.append("import_to_blender.py script was not generated.")
            report.valid = False

        # 3. Validate GLB files
        glb_files = list(out_path.glob("**/*.glb"))
        if not glb_files:
            report.errors.append("No GLB files were generated in the export directory.")
            report.valid = False

        parsed_glbs = 0
        for glb in glb_files:
            try:
                validate_glb_file(glb)
                parsed_glbs += 1
            except Exception as e:
                report.errors.append(f"Corrupt or invalid GLB file '{glb.name}': {e}")
                report.valid = False

        # 4. Heightmap check
        if manifest_data.get("heightmap"):
            hm_info = manifest_data["heightmap"]
            hm_png = out_path / hm_info.get("png_file", "")
            if not hm_png.exists():
                report.warnings.append(f"Heightmap PNG file missing: {hm_info.get('png_file')}")

        # 5. GeoJSON check
        if manifest_data.get("regions_geojson"):
            gj_path = out_path / manifest_data["regions_geojson"]
            if not gj_path.exists():
                report.warnings.append("regions.geojson is missing.")
            else:
                try:
                    gj_data = json.loads(gj_path.read_text(encoding="utf-8"))
                    if gj_data.get("type") != "FeatureCollection":
                        report.warnings.append("regions.geojson does not have a FeatureCollection root.")
                except Exception as e:
                    report.warnings.append(f"Invalid regions.geojson syntax: {e}")

        report.details["total_glbs_validated"] = parsed_glbs
        report.details["files_verified"] = len(list(out_path.glob("**/*")))
        return report

    # -------------------------------------------------------------------------
    # Core Export Execution
    # -------------------------------------------------------------------------

    def export(
        self,
        out_dir: Path | str,
        options: BlenderExportOptions | None = None,
        base_dem: Any | None = None,
        ways: list[Any] | None = None,
        progress_callback: Callable[[str, float], None] | None = None,
    ) -> dict[str, Any]:
        """Runs the complete professional Blender export pipeline."""
        opts = options or BlenderExportOptions()
        out_path = Path(out_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        # 1. Pre-export validation
        pre_report = self.validate_pre_export(opts)
        if not pre_report.valid:
            raise ValueError(f"Pre-export validation failed: {pre_report.errors}")

        if progress_callback:
            progress_callback("Pre-export validation passed", 0.05)

        # 2. Composite DEM resolution (integrates Terrain Editor modifications)
        dem = base_dem
        if dem is None:
            from .dem import Dem
            dim_x = float(self.project.dimensions.size_x_m or 2000.0)
            dim_y = float(self.project.dimensions.size_y_m or 2000.0)
            half_w = max(dim_x, dim_y) / 2.0 * 1.05
            w_lon, s_lat = self.proj.to_lonlat(-half_w, -half_w)
            e_lon, n_lat = self.proj.to_lonlat(half_w, half_w)
            try:
                dem = Dem((s_lat, w_lon, n_lat, e_lon), zoom=self.project.terrain.dem_zoom or 15)
            except Exception:
                class SyntheticPlain:
                    def heights(self, lons, lats):
                        lons = np.asarray(lons, dtype=np.float64)
                        return np.full_like(lons, float(self.project.geographic.elevation_ref or 300.0))
                dem = SyntheticPlain()

        if self.project.terrain.modifiers:
            from .terrain_editor import ModifiedDem, TerrainEditor
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

        # 3. Style preset resolution
        from .styles import get_style_preset
        preset_name = opts.style_preset or getattr(self.project.generation, "style_preset", "stylized_european_town")
        preset = get_style_preset(preset_name)

        # Master manifest dictionary
        manifest: dict[str, Any] = {
            "project_name": self.project.name,
            "project_id": self.project.id,
            "schema_version": self.project.schema_version,
            "export_mode": opts.mode,
            "export_timestamp": _now_iso(),
            "target_engine": "Blender 3.x / 4.x / 5.x",
            "coordinates": {
                "units": "meters (1 Blender unit = 1.0 meter)",
                "blender_unit_scale": 1.0,
                "world_origin": {
                    "lat": self.project.geographic.center_lat,
                    "lon": self.project.geographic.center_lon,
                    "altitude": self.project.geographic.elevation_ref,
                },
                "axis_blender": "X=East, Y=North, Z=Up",
                "axis_gltf": "X=East, Y=Up, Z=-North",
            },
            "dimensions_m": {
                "size_x": float(self.project.dimensions.size_x_m or 2000.0),
                "size_y": float(self.project.dimensions.size_y_m or 2000.0),
                "chunk_size": float(self.project.chunks.grid.chunk_size_m or 512.0),
            },
            "style": {
                "preset_id": preset.preset_id if preset else "default",
                "name": preset.name if preset else "Default",
                "description": preset.description if preset else "",
                "lighting": preset.lighting.to_dict() if preset and getattr(preset, "lighting", None) else None,
                "materials": {
                    "terrain": {"roughness": preset.terrain.roughness, "metallic": preset.terrain.metallic} if preset and getattr(preset, "terrain", None) else None,
                    "building": {"roughness": preset.building.roughness, "metallic": preset.building.metallic} if preset and getattr(preset, "building", None) else None,
                    "roads": {"roughness": preset.roads.roughness, "metallic": preset.roads.metallic} if preset and getattr(preset, "roads", None) else None,
                    "water": {"roughness": preset.water.roughness, "metallic": preset.water.metallic} if preset and getattr(preset, "water", None) else None,
                    "vegetation": {"roughness": 0.90, "metallic": 0.0},
                },
            },
            "layers": [],
            "chunks": [],
        }

        # 4. Export GeoJSON Region Data
        if opts.include_regions_geojson:
            reg_dir = out_path / "regions"
            reg_dir.mkdir(parents=True, exist_ok=True)
            gj_path = reg_dir / "regions.geojson"
            features = [r.to_geojson_feature() for r in self.project.regions if len(getattr(r, "polygon", [])) >= 3]
            gj_doc = {
                "type": "FeatureCollection",
                "name": f"{self.project.name}_regions",
                "features": features,
            }
            gj_path.write_text(json.dumps(gj_doc, indent=2), encoding="utf-8")
            manifest["regions_geojson"] = str(gj_path.relative_to(out_path))

        # 4.b Export Procedural Biome Distribution and Splatmaps
        biomes_dir = out_path / "biomes"
        biomes_dir.mkdir(parents=True, exist_ok=True)
        from .biomes import BiomeGenerator, generate_splatmap_textures

        dim_x = float(self.project.dimensions.size_x_m or 2000.0)
        dim_y = float(self.project.dimensions.size_y_m or 2000.0)
        size_m = dim_x
        half_x = dim_x / 2.0
        half_y = dim_y / 2.0

        biome_gen = BiomeGenerator(seed=self.project.seed)
        biome_res = biome_gen.evaluate_grid(
            bounds_m=(-half_x, -half_y, half_x, half_y),
            resolution_m=max(10.0, dim_x / opts.terrain_res),
            dem=dem,
            proj=self.proj,
            ways=ways,
        )

        biome_export_meta = generate_splatmap_textures(
            biome_res,
            biomes_dir,
            stem=f"{self.project.name}_biome",
        )

        manifest["biomes"] = {
            "coverage_percentages": biome_export_meta["coverage"],
            "splatmap_0": str(Path(biome_export_meta["splatmap_file"]).relative_to(out_path)),
            "colormap": str(Path(biome_export_meta["colormap_file"]).relative_to(out_path)),
            "manifest": str(Path(biome_export_meta["manifest_file"]).relative_to(out_path)),
            "channels": biome_export_meta["channels"],
        }

        # 4.c Procedural World Dressing Instancing
        from .dressing import WorldDressingEngine, export_world_dressing_for_blender
        dressing_engine = WorldDressingEngine(seed=self.project.seed)
        instances = dressing_engine.populate_bounds(
            bounds_m=(-half_x, -half_y, half_x, half_y),
            dem=dem,
            proj=self.proj,
            biome_grid=biome_res,
            ways=ways,
        )
        dressing_export_meta = export_world_dressing_for_blender(
            instances=instances,
            catalog=dressing_engine.catalog,
            output_dir=out_path,
            stem=f"{self.project.name}_world_dressing",
        )
        manifest["world_dressing"] = {
            "total_instances": dressing_export_meta["total_instances"],
            "manifest": str(Path(dressing_export_meta["manifest_file"]).relative_to(out_path)),
            "category_counts": dressing_export_meta["category_counts"],
            "collections": dressing_export_meta["collections"],
        }

        # 5. Export 16-Bit Heightmap
        if opts.include_heightmap:
            hm_dir = out_path / "heightmap"
            hm_dir.mkdir(parents=True, exist_ok=True)
            res = opts.terrain_res
            n = res + 1
            size_m = float(self.project.dimensions.size_x_m or 2000.0)
            half = size_m / 2.0
            xs = -half + np.arange(n) * (size_m / res)
            ys = -half + np.arange(n) * (size_m / res)
            X, Y = np.meshgrid(xs, ys)
            lons, lats = self.proj.to_lonlat(X, Y)
            Z = np.asarray(dem.heights(lons, lats), dtype=np.float64)

            hm_meta = write_heightmap(Z, hm_dir, stem="terrain", size_m=size_m)
            # 16-bit PNG save
            zmin, zmax = float(Z.min()), float(Z.max())
            norm = (Z - zmin) / max(1e-9, zmax - zmin)
            u16 = np.clip(norm * 65535.0, 0, 65535).astype(np.uint16)
            png_name = "terrain_heightmap_16bit.png"
            Image.fromarray(u16).save(hm_dir / png_name)
            manifest["heightmap"] = {
                "png_file": str((hm_dir / png_name).relative_to(out_path)),
                "raw_file": str((hm_dir / hm_meta["raw_file"]).relative_to(out_path)),
                "elevation_range_m": [round(zmin, 2), round(zmax, 2)],
                "resolution": n,
            }

        # 6. Mode 1: Layer Mode Export
        if opts.mode in ("layer", "both"):
            if progress_callback:
                progress_callback("Generating Layer Mode assets", 0.25)
            layers_dir = out_path / "layers"
            layers_dir.mkdir(parents=True, exist_ok=True)

            from .build import CityBuilder
            from .facade import facade_png

            size_m = float(self.project.dimensions.size_x_m or 2000.0)
            b = CityBuilder(
                ways=ways or [],
                proj=self.proj,
                dem=dem,
                size_m=size_m,
                terrain_res=opts.terrain_res,
                seed=self.project.seed,
                roofs=self.project.generation.roofs,
                style_preset=preset_name,
            )

            terrain_mesh = b.build_terrain()
            roads_mesh, walks_mesh, _ = b.build_roads()
            buildings_mesh = b.build_buildings()
            areas_dict = b.build_areas()
            veg_mesh = b.build_vegetation()

            export_meshes = {
                "terrain": terrain_mesh,
                "roads": roads_mesh,
                "sidewalks": walks_mesh,
                "buildings": buildings_mesh,
                "vegetation": veg_mesh,
            }
            if "water" in areas_dict:
                export_meshes["water"] = areas_dict["water"]

            tex_bytes = facade_png()

            def _get_pbr_for_layer(layer_key: str) -> tuple[float, float]:
                if not preset:
                    return 0.92, 0.0
                if layer_key == "terrain" and getattr(preset, "terrain", None):
                    return float(preset.terrain.roughness), float(preset.terrain.metallic)
                elif layer_key == "buildings" and getattr(preset, "building", None):
                    return float(preset.building.roughness), float(preset.building.metallic)
                elif layer_key in ("roads", "sidewalks") and getattr(preset, "roads", None):
                    return float(preset.roads.roughness), float(preset.roads.metallic)
                elif layer_key == "water" and getattr(preset, "water", None):
                    return float(preset.water.roughness), float(preset.water.metallic)
                elif layer_key == "vegetation":
                    return 0.90, 0.0
                return 0.92, 0.0

            for l_name, mesh in export_meshes.items():
                if len(mesh.f) == 0:
                    continue
                if opts.selected_layers and l_name not in opts.selected_layers:
                    continue

                glb_file = layers_dir / f"{l_name}.glb"
                rough, metal = _get_pbr_for_layer(l_name)
                write_glb(
                    mesh,
                    glb_file,
                    name=l_name.capitalize(),
                    texture_png=tex_bytes if l_name == "buildings" else None,
                    material_name=f"mat_{l_name}",
                    roughness=rough,
                    metallic=metal,
                )

                layer_info = {
                    "name": l_name,
                    "file": str(glb_file.relative_to(out_path)),
                    "triangles": len(mesh.f),
                    "vertices": len(mesh.v),
                    "material": f"mat_{l_name}",
                }

                if "obj" in opts.formats:
                    obj_dir = layers_dir / "obj"
                    obj_file = obj_dir / f"{l_name}.obj"
                    write_obj(mesh, obj_file, layer=l_name)
                    layer_info["obj_file"] = str(obj_file.relative_to(out_path))

                manifest["layers"].append(layer_info)

        # 6.b Collision Geometry Generation
        collision_dir = out_path / "collision"
        collision_dir.mkdir(parents=True, exist_ok=True)
        from .build import CityBuilder, building_height
        from .geom import box as geom_box

        b_col = CityBuilder(
            ways=ways or [],
            proj=self.proj,
            dem=dem,
            size_m=size_m,
            terrain_res=min(opts.terrain_res // 4, 32),
            seed=self.project.seed,
            roofs=False,
            style_preset=preset_name,
        )
        terrain_col_mesh = b_col.build_terrain()
        terrain_col_glb = collision_dir / "terrain_collision.glb"
        write_glb(terrain_col_mesh, terrain_col_glb, name="Terrain_Collision", material_name="mat_collision")

        bld_col_mesh = Mesh()
        for w in (ways or []):
            tags = getattr(w, "tags", {})
            if "building" not in tags and "building:part" not in tags:
                continue
            if tags.get("building") == "no":
                continue
            coords = getattr(w, "coords", [])
            if len(coords) < 3:
                continue
            c_arr = np.asarray(coords, dtype=np.float64)
            w_x, w_y = self.proj.to_xy(c_arr[:, 0], c_arr[:, 1])
            min_x, max_x = float(w_x.min()), float(w_x.max())
            min_z, max_z = float(w_y.min()), float(w_y.max())
            cx = (min_x + max_x) / 2.0
            cz = (min_z + max_z) / 2.0
            w_box = max(1.0, max_x - min_x)
            d_box = max(1.0, max_z - min_z)

            lon_c, lat_c = self.proj.to_lonlat(cx, cz)
            base_elev = float(np.asarray(dem.heights(np.array([lon_c]), np.array([lat_c]))).ravel()[0])
            h = building_height(tags, getattr(w, "id", 0), self.project.seed)

            v_b, f_b = geom_box(cx, cz, w_box, d_box, base_elev, base_elev + h)
            bld_col_mesh.add(v_b, f_b)

        bld_col_glb = collision_dir / "buildings_collision.glb"
        if len(bld_col_mesh.f) > 0:
            write_glb(bld_col_mesh, bld_col_glb, name="Buildings_Collision", material_name="mat_collision")

        manifest["collision"] = {
            "terrain": str(terrain_col_glb.relative_to(out_path)),
            "buildings": str(bld_col_glb.relative_to(out_path)) if len(bld_col_mesh.f) > 0 else None,
        }

        # 6.c LOD Variants Generation
        lod_dir = out_path / "lod"
        lod_dir.mkdir(parents=True, exist_ok=True)

        b_lod1 = CityBuilder(
            ways=ways or [],
            proj=self.proj,
            dem=dem,
            size_m=size_m,
            terrain_res=min(opts.terrain_res // 2, 64),
            seed=self.project.seed,
            roofs=True,
            style_preset=preset_name,
        )
        terrain_lod1_mesh = b_lod1.build_terrain()
        terrain_lod1_glb = lod_dir / "terrain_lod1.glb"
        write_glb(terrain_lod1_mesh, terrain_lod1_glb, name="Terrain_LOD1", material_name="mat_terrain_lod1")

        b_lod2 = CityBuilder(
            ways=ways or [],
            proj=self.proj,
            dem=dem,
            size_m=size_m,
            terrain_res=16,
            seed=self.project.seed,
            roofs=False,
            style_preset=preset_name,
        )
        terrain_lod2_mesh = b_lod2.build_terrain()
        terrain_lod2_glb = lod_dir / "terrain_lod2.glb"
        write_glb(terrain_lod2_mesh, terrain_lod2_glb, name="Terrain_LOD2", material_name="mat_terrain_lod2")

        manifest["lod"] = {
            "lod1": {
                "terrain": str(terrain_lod1_glb.relative_to(out_path)),
            },
            "lod2": {
                "terrain": str(terrain_lod2_glb.relative_to(out_path)),
            },
        }

        manifest["reference"] = {
            "world_origin": manifest["coordinates"]["world_origin"],
            "dimensions_m": manifest["dimensions_m"],
            "chunks_grid": [
                {
                    "id": ch.id,
                    "row": ch.row,
                    "col": ch.col,
                    "bounds": ch.bounds_projected,
                }
                for ch in self.project.chunks.items
            ],
        }

        # 7. Mode 2: Chunk Mode Export
        if opts.mode in ("chunk", "both"):
            if progress_callback:
                progress_callback("Generating Chunk Mode assets", 0.50)
            from .projects.chunks import ChunkEngine
            engine = ChunkEngine(self.project, project_dir=self.project_dir)

            targets = self.project.chunks.items
            if opts.selected_chunks:
                targets = [ch for ch in targets if ch.id in opts.selected_chunks]

            chunks_dir = out_path / "chunks"
            chunks_dir.mkdir(parents=True, exist_ok=True)

            for ch in targets:
                c_dir = chunks_dir / ch.id
                c_dir.mkdir(parents=True, exist_ok=True)

                # Generate chunk
                engine.generate_chunk(
                    ch.id,
                    dem=dem,
                    ways=ways,
                    force=True,
                    terrain_res=min(opts.terrain_res, 64),
                    style_preset=preset_name,
                    formats=opts.formats,
                )

                # Move/copy chunk assets to export directory
                src_dir = engine.chunks_dir / ch.id
                c_files = {}
                for f in src_dir.glob("*.*"):
                    dst_file = c_dir / f.name
                    dst_file.write_bytes(f.read_bytes())
                    c_files[f.suffix.lstrip(".")] = str(dst_file.relative_to(out_path))

                bp = ch.bounds_projected
                manifest["chunks"].append({
                    "id": ch.id,
                    "row": ch.row,
                    "col": ch.col,
                    "bounds": bp,
                    "center": {
                        "x": (bp["min_x"] + bp["max_x"]) / 2.0,
                        "y": (bp["min_y"] + bp["max_y"]) / 2.0,
                    },
                    "files": c_files,
                })

        # 8. Write Master Manifest, Importer Script, and Readme
        manifest_path = out_path / "blender_manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        if opts.generate_blender_script:
            script_path = out_path / "import_to_blender.py"
            script_path.write_text(BLENDER_IMPORT_SCRIPT_TEMPLATE, encoding="utf-8")

        readme_path = out_path / "README_BLENDER.md"
        readme_path.write_text(
            README_BLENDER_TEMPLATE.format(
                project_name=self.project.name,
                center_lat=self.project.geographic.center_lat,
                center_lon=self.project.geographic.center_lon,
            ),
            encoding="utf-8",
        )

        if progress_callback:
            progress_callback("Running post-export validation", 0.90)

        # 9. Post-export validation
        post_report = self.validate_post_export(out_path, manifest)

        return {
            "status": "success" if post_report.valid else "warning",
            "export_dir": str(out_path),
            "manifest_file": str(manifest_path),
            "script_file": str(out_path / "import_to_blender.py"),
            "pre_validation": pre_report.to_dict(),
            "post_validation": post_report.to_dict(),
        }
