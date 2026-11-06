import json
import struct

import numpy as np
from shapely.geometry import Polygon

from terragen.export import build_network_graph, write_glb
from terragen.facade import facade_png
from terragen.geom import Mesh, extrude


def _read_glb(path):
    data = path.read_bytes()
    magic, ver, total = struct.unpack("<III", data[:12])
    assert magic == 0x46546C67 and ver == 2 and total == len(data)
    jlen, _jtype = struct.unpack("<II", data[12:20])
    doc = json.loads(data[20 : 20 + jlen])
    blen, _btype = struct.unpack("<II", data[20 + jlen : 28 + jlen])
    return doc, data[28 + jlen : 28 + jlen + blen]


def _cube_mesh():
    m = Mesh()
    v, f, _cap, uv = extrude(Polygon([(0, 0), (10, 0), (10, 10), (0, 10)]), 0, 10)
    cols = np.full((len(v), 4), 200, dtype=np.uint8)
    m.add(v, f, cols, uv)
    return m


def test_glb_roundtrip_with_texture(tmp_path):
    path = tmp_path / "b.glb"
    size = write_glb(_cube_mesh(), path, "buildings", texture_png=facade_png())
    assert size == path.stat().st_size
    doc, blob = _read_glb(path)
    assert "textures" in doc and doc["images"][0]["mimeType"] == "image/png"
    mat = doc["materials"][0]["pbrMetallicRoughness"]
    assert mat["baseColorTexture"]["index"] == 0
    assert set(doc["meshes"][0]["primitives"][0]["attributes"]) == {
        "POSITION",
        "NORMAL",
        "COLOR_0",
        "TEXCOORD_0",
    }
    # textura chiar e în blob
    img_view = doc["bufferViews"][doc["images"][0]["bufferView"]]
    chunk = blob[img_view["byteOffset"] : img_view["byteOffset"] + img_view["byteLength"]]
    assert chunk[:8] == b"\x89PNG\r\n\x1a\n"


def test_glb_axis_conversion(tmp_path):
    path = tmp_path / "c.glb"
    write_glb(_cube_mesh(), path)
    doc, _blob = _read_glb(path)
    acc = doc["accessors"][0]
    assert acc["min"][2] <= 0 <= acc["max"][2]  # Nord -> -Z
    assert acc["min"][1] == 0 and acc["max"][1] == 10  # Y rămâne sus


def test_network_graph_nodes_dedup():
    net = [
        {
            "id": 1,
            "name": "a",
            "class": "residential",
            "width": 7,
            "lanes": "",
            "oneway": "no",
            "maxspeed": "",
            "surface": "",
            "bridge": "no",
            "points": [[0, 0, 0], [10, 0, 0]],
        },
        {
            "id": 2,
            "name": "b",
            "class": "residential",
            "width": 7,
            "lanes": "",
            "oneway": "no",
            "maxspeed": "",
            "surface": "",
            "bridge": "no",
            "points": [[10, 0, 0], [10, 0, 10]],
        },
    ]
    g = build_network_graph(net)
    assert len(g["edges"]) == 2
    assert len(g["nodes"]) == 3
    assert g["edges"][0]["to"] == g["edges"][1]["from"]
    assert g["edges"][0]["length"] == 10.0
