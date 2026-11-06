"""Export: GLB (glTF 2.0, cu textură de fațadă încorporată), OBJ, heightmap, rețea de drumuri."""

from __future__ import annotations

import json
import struct
from pathlib import Path

import numpy as np
from PIL import Image


def _vertex_normals(v: np.ndarray, f: np.ndarray) -> np.ndarray:
    n = np.zeros_like(v)
    if not len(f):
        return n
    a, b, c = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
    fn = np.cross(b - a, c - a)
    for k in range(3):
        np.add.at(n, f[:, k], fn)
    ln = np.linalg.norm(n, axis=1, keepdims=True)
    ln[ln == 0] = 1.0
    return n / ln


def _srgb_to_linear(c: np.ndarray) -> np.ndarray:
    """COLOR_0 din glTF e liniar; culorile din mesh sunt sRGB -> conversie."""
    out = c.copy()
    x = out[:, :3].astype(np.float32) / 255.0
    lin = np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)
    out[:, :3] = np.clip(np.rint(lin * 255.0), 0, 255).astype(np.uint8)
    return out


def write_glb(mesh, path: Path, name: str | None = None, texture_png: bytes | None = None) -> int:
    """Scriitor GLB minimal: POSITION, NORMAL, COLOR_0, TEXCOORD_0 (+ textură opțională).

    Axe glTF: X = Est, Y = Sus, Z = -Nord (right-handed).
    `texture_png` = textura de fațadă (o celulă de fereastră, REPEAT) — dacă e dată,
    materialul o pune ca baseColorTexture, deci geamurile apar în orice engine, fără shader.
    """
    if not len(mesh.f):
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)

    v = mesh.v.copy()
    v[:, 2] *= -1.0
    faces = mesh.f
    normals = _vertex_normals(v, faces)
    colors = _srgb_to_linear(
        mesh.c if len(mesh.c) == len(v) else np.full((len(v), 4), 255, np.uint8)
    )
    uv = mesh.uv if len(mesh.uv) == len(v) else np.zeros((len(v), 2))

    parts: list[tuple[bytes, int, int, int, str]] = []

    def add(arr: np.ndarray, target: int, ctype: int, atype: str) -> int:
        data = np.ascontiguousarray(arr).tobytes()
        data += b"\0" * ((-len(data)) % 4)
        parts.append((data, len(np.ascontiguousarray(arr).tobytes()), target, ctype, atype))
        return len(parts) - 1

    i_pos = add(v.astype(np.float32), 34962, 5126, "VEC3")
    i_nrm = add(normals.astype(np.float32), 34962, 5126, "VEC3")
    i_col = add(colors.astype(np.uint8), 34962, 5121, "VEC4")
    i_uv = add(uv.astype(np.float32), 34962, 5126, "VEC2")
    i_idx = add(faces.astype(np.uint32).reshape(-1), 34963, 5125, "SCALAR")
    i_img = (
        add(np.frombuffer(texture_png, dtype=np.uint8), 0, 5121, "SCALAR") if texture_png else None
    )

    blob = b"".join(p[0] for p in parts)
    views, accessors = [], []
    offset = 0
    counts = [len(v), len(v), len(v), len(v), faces.size]
    if texture_png:
        counts.append(len(texture_png))
    for i, (data, raw_len, target, ctype, atype) in enumerate(parts):
        view = {"buffer": 0, "byteOffset": offset, "byteLength": raw_len}
        if target:
            view["target"] = target
        views.append(view)
        acc = {"bufferView": i, "componentType": ctype, "count": counts[i], "type": atype}
        if ctype == 5121:
            acc["normalized"] = not (i_img is not None and i == i_img)
        if i == 0:
            acc["min"] = [float(x) for x in np.min(v, axis=0)]
            acc["max"] = [float(x) for x in np.max(v, axis=0)]
        accessors.append(acc)
        offset += len(data)

    material = {
        "name": (name or path.stem) + "_mat",
        "pbrMetallicRoughness": {
            "baseColorFactor": [1, 1, 1, 1],
            "metallicFactor": 0.0,
            "roughnessFactor": 0.92,
        },
        "doubleSided": True,
    }
    attrs = {"POSITION": i_pos, "NORMAL": i_nrm, "COLOR_0": i_col, "TEXCOORD_0": i_uv}
    prim = {"attributes": attrs, "indices": i_idx, "mode": 4, "material": 0}

    doc = {
        "asset": {"version": "2.0", "generator": "terragen-web"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": name or path.stem}],
        "meshes": [{"name": name or path.stem, "primitives": [prim]}],
        "materials": [material],
        "accessors": accessors,
        "bufferViews": views,
        "buffers": [{"byteLength": len(blob)}],
    }
    if tex_index_ok := (i_img is not None):
        doc["images"] = [{"bufferView": i_img, "mimeType": "image/png", "name": "facade"}]
        doc["samplers"] = [
            {
                "magFilter": 9729,
                "minFilter": 9987,  # LINEAR / LINEAR_MIPMAP_LINEAR
                "wrapS": 10497,
                "wrapT": 10497,
            }
        ]  # REPEAT
        doc["textures"] = [{"sampler": 0, "source": 0, "name": "facade"}]
        material["pbrMetallicRoughness"]["baseColorTexture"] = {"index": 0}
        assert tex_index_ok

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


OBJ_COLORS = {
    "roads": (56, 60, 66),
    "sidewalks": (150, 150, 143),
    "buildings": (205, 190, 168),
    "terrain": (124, 158, 92),
    "green": (110, 158, 86),
    "forest": (72, 114, 66),
    "water": (68, 128, 165),
    "pitch": (96, 150, 82),
    "parking": (86, 90, 96),
    "industrial": (116, 112, 102),
    "other": (138, 132, 120),
}


def write_obj(
    mesh, path: Path, mtl_name: str = "material", texture_png: bytes | None = None, layer: str = ""
) -> int:
    """OBJ + MTL (pentru importatori clasici). Pentru clădiri scrie și textura de fațadă."""
    if not len(mesh.f):
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    v = mesh.v
    f = mesh.f[:, [0, 2, 1]] + 1  # winding pentru importatori stângaci (Unity)
    uv = mesh.uv if len(mesh.uv) == len(v) else np.zeros((len(v), 2))
    lines = [
        "# terragen-web — metri; X=Est, Y=Sus, Z=Nord; origine = centrul zonei",
        f"mtllib {mtl_name}.mtl",
        f"o {path.stem}",
        f"usemtl {path.stem}",
    ]
    lines += [f"v {x:.3f} {y:.3f} {z:.3f}" for x, y, z in v]
    lines += [f"vt {u:.4f} {w:.4f}" for u, w in uv]
    lines += [f"f {a}/{a} {b}/{b} {c}/{c}" for a, b, c in f]
    path.write_text("\n".join(lines) + "\n")

    mtl = path.parent / f"{mtl_name}.mtl"
    if not mtl.exists():
        text = []
        for nm, (r, g, b) in OBJ_COLORS.items():
            text += [
                f"newmtl {nm}",
                f"Kd {r / 255:.3f} {g / 255:.3f} {b / 255:.3f}",
                "Ks 0.000 0.000 0.000",
                "Ns 1.0",
                "d 1.0",
                "illum 1",
                "",
            ]
        mtl.write_text("\n".join(text))

    if texture_png and layer == "buildings":
        tex = path.parent / "facade.png"
        tex.write_bytes(texture_png)
        # folosim materialul de clădiri (singurul cu textura de fațadă)
        extra = path.parent / "buildings.mtl"
        extra.write_text(
            "newmtl buildings\n"
            "Kd 0.800 0.745 0.659\n"
            "Ks 0.000 0.000 0.000\n"
            "Ns 1.0\nd 1.0\nillum 1\n"
            "map_Kd facade.png\n"
        )
    return path.stat().st_size


def write_heightmap(
    terrain_z: np.ndarray, out_dir: Path, stem: str = "terrain", size_m: float = 2000.0
) -> dict:
    """Heightmap 16 biți (pentru Unity Terrain / Godot / orice) + PNG de verificare."""
    out_dir.mkdir(parents=True, exist_ok=True)
    z = np.asarray(terrain_z, dtype=np.float64)
    n = z.shape[0]
    zmin, zmax = float(z.min()), float(z.max())
    norm = (z - zmin) / max(1e-9, zmax - zmin)
    u16 = np.clip(norm * 65535.0, 0, 65535).astype(np.uint16)
    raw_path = out_dir / f"{stem}_heightmap_{n}.r16"
    u16.tofile(raw_path)  # rânduri S->N, coloane V->E
    Image.fromarray((norm * 255).astype(np.uint8)).save(out_dir / f"{stem}_heightmap_preview.png")
    meta = {
        "raw_file": raw_path.name,
        "format": "16-bit unsigned little-endian, single channel, row-major (S->N, W->E)",
        "resolution": n,
        "size_meters": round(size_m, 3),
        "meters_per_sample": round(size_m / (n - 1), 4),
        "elevation_min": round(zmin, 2),
        "elevation_max": round(zmax, 2),
        "elevation_range": round(zmax - zmin, 2),
    }
    (out_dir / f"{stem}_heightmap.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    return meta


def build_network_graph(network: list[dict], tol: float = 1.5) -> dict:
    """Noduri de intersecție + segmente, pentru trafic/AI/minimap."""
    nodes: dict[tuple, int] = {}
    node_list: list[dict] = []

    def node_id(pt):
        key = (round(pt[0] / tol), round(pt[2] / tol))
        if key not in nodes:
            nodes[key] = len(node_list)
            node_list.append({"id": nodes[key], "x": pt[0], "y": pt[1], "z": pt[2]})
        return nodes[key]

    edges = []
    for w in network:
        pts = w["points"]
        if len(pts) < 2:
            continue
        edges.append(
            {
                "id": w["id"],
                "from": node_id(pts[0]),
                "to": node_id(pts[-1]),
                "name": w["name"],
                "class": w["class"],
                "width": w["width"],
                "lanes": w["lanes"],
                "oneway": w["oneway"],
                "maxspeed": w["maxspeed"],
                "surface": w["surface"],
                "bridge": w["bridge"],
                "points": pts,
                "length": round(
                    float(
                        np.sum(np.linalg.norm(np.diff(np.asarray(pts)[:, [0, 2]], axis=0), axis=1))
                    ),
                    2,
                ),
            }
        )
    return {"nodes": node_list, "edges": edges}


def write_json(obj, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False))
