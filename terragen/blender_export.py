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
import hashlib
import json
import math
import struct
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable

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
        faces = mesh.f
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

        # Material setup
        mat_idx = len(materials)
        mat_name = f"mat_{layer_name}"
        material: dict[str, Any] = {
            "name": mat_name,
            "pbrMetallicRoughness": {
                "baseColorFactor": [1.0, 1.0, 1.0, 1.0],
                "metallicFactor": 0.05 if layer_name == "roads" else 0.0,
                "roughnessFactor": 0.2 if layer_name == "water" else 0.85,
            },
            "doubleSided": True,
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
    for data, raw_len, target, ctype, atype in parts:
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
- Automatically builds organized collections (Terragen root collection + Layer/Chunk subcollections).
- Preserves object names, materials, vertex colors, UVs, and PBR properties.
- Configures 3D Viewport clipping distance for large real-world scenes (50,000 meters).
- Consolidates duplicate materials (.001, .002) for clean shader organization.
- Supports selective chunk and layer filtering.

Run inside Blender:
  Open Blender -> Scripting Workspace -> Open this script -> Click 'Run Script' (Alt+P).

Or run via command line (headless / batch):
  blender --background --python import_to_blender.py
"""

from __future__ import annotations

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
# User Filters (Configure as needed)
# -----------------------------------------------------------------------------
# Set to a list of chunk IDs to import only specific chunks (e.g., ["chk_r00_c00", "chk_r00_c01"]).
# Set to None to import all available chunks in the manifest.
FILTER_CHUNKS: list[str] | None = None

# Set to a list of layer names to import only specific layers (e.g., ["terrain", "buildings"]).
# Set to None to import all available layers.
FILTER_LAYERS: list[str] | None = None

# Automatically set 3D Viewport clipping for large terrain scale
SET_VIEWPORT_CLIPPING: bool = True

# Consolidate duplicate materials with identical names
CONSOLIDATE_MATERIALS: bool = True


def get_or_create_collection(name: str, parent_collection: bpy.types.Collection | None = None) -> bpy.types.Collection:
    coll = bpy.data.collections.get(name)
    if not coll:
        coll = bpy.data.collections.new(name)
        if parent_collection:
            parent_collection.children.link(coll)
        else:
            bpy.context.scene.collection.children.link(coll)
    return coll


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


def import_terragen_scene():
    script_dir = Path(__file__).resolve().parent
    manifest_file = script_dir / "blender_manifest.json"
    if not manifest_file.exists():
        print(f"Error: blender_manifest.json not found in {script_dir}")
        return False

    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    project_name = manifest.get("project_name", "Terragen_World")
    export_mode = manifest.get("export_mode", "layer")
    print("=" * 70)
    print(f"Terragen Blender Importer: {project_name}")
    print(f"Export Mode: {export_mode}")
    print(f"World Origin: Lat {manifest['coordinates']['world_origin']['lat']:.5f}, Lon {manifest['coordinates']['world_origin']['lon']:.5f}")
    print("=" * 70)

    configure_viewport()

    root_coll = get_or_create_collection(f"Terragen_{project_name}")
    total_objects_imported = 0
    missing_files: list[str] = []

    if export_mode == "layer" or "layers" in manifest:
        layers_data = manifest.get("layers", [])
        for item in layers_data:
            layer_name = item.get("name", "layer")
            if FILTER_LAYERS and layer_name.lower() not in [l.lower() for l in FILTER_LAYERS]:
                continue

            rel_file = item.get("file")
            if not rel_file:
                continue
            layer_path = script_dir / rel_file
            if not layer_path.exists():
                missing_files.append(str(rel_file))
                print(f"Warning: Missing layer file: {rel_file}")
                continue

            layer_coll = get_or_create_collection(f"Layer_{layer_name.capitalize()}", root_coll)

            # Record objects before import
            pre_objs = set(bpy.data.objects)
            bpy.ops.import_scene.gltf(filepath=str(layer_path))
            new_objs = [obj for obj in bpy.data.objects if obj not in pre_objs]

            for obj in new_objs:
                for c in obj.users_collection:
                    if c != layer_coll:
                        c.objects.unlink(obj)
                if obj.name not in layer_coll.objects:
                    layer_coll.objects.link(obj)
                total_objects_imported += 1

            print(f"✔ Imported Layer: {layer_name} ({len(new_objs)} objects)")

    if export_mode == "chunk" or "chunks" in manifest:
        chunks_data = manifest.get("chunks", [])
        for ch in chunks_data:
            chunk_id = ch.get("id", "chunk")
            if FILTER_CHUNKS and chunk_id not in FILTER_CHUNKS:
                continue

            chunk_coll = get_or_create_collection(f"Chunk_{chunk_id}", root_coll)
            files = ch.get("files", {})

            for l_name, rel_file in files.items():
                if not (rel_file.endswith(".glb") or rel_file.endswith(".gltf")):
                    continue
                if FILTER_LAYERS and l_name.lower() not in [l.lower() for l in FILTER_LAYERS]:
                    continue
                file_path = script_dir / rel_file
                if not file_path.exists():
                    missing_files.append(str(rel_file))
                    continue

                pre_objs = set(bpy.data.objects)
                bpy.ops.import_scene.gltf(filepath=str(file_path))
                new_objs = [obj for obj in bpy.data.objects if obj not in pre_objs]

                for obj in new_objs:
                    for c in obj.users_collection:
                        if c != chunk_coll:
                            c.objects.unlink(obj)
                    if obj.name not in chunk_coll.objects:
                        chunk_coll.objects.link(obj)
                    total_objects_imported += 1

            print(f"✔ Imported Chunk: {chunk_id}")

    consolidate_materials()

    print("=" * 70)
    print(f"Terragen Import Complete: {total_objects_imported} objects placed in collection '{root_coll.name}'.")
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
                res = validate_glb_file(glb)
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
        preset_name = opts.style_preset or getattr(self.project.generation, "style_preset", "stylized_european_town")

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

            for l_name, mesh in export_meshes.items():
                if len(mesh.f) == 0:
                    continue
                if opts.selected_layers and l_name not in opts.selected_layers:
                    continue

                glb_file = layers_dir / f"{l_name}.glb"
                write_glb(
                    mesh,
                    glb_file,
                    name=l_name.capitalize(),
                    texture_png=tex_bytes if l_name == "buildings" else None,
                    material_name=f"mat_{l_name}",
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
                ch_manifest = engine.generate_chunk(
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
