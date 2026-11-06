"""Teste pentru relațiile OSM `type=multipolygon` (offline, fără rețea).

Acoperă cele trei straturi atinse de funcționalitate:
  1. `terragen.multipolygon.assemble` — inele sparte în mai multe way-uri, găuri,
     inele incomplete;
  2. cititorul intern PBF — un fișier .osm.pbf construit bucată cu bucată în test
     (nodes + ways + relation), ca să verificăm decodarea câmpului 4 = Relation;
  3. `CityBuilder` — clădire/parc multipolygon cu curte interioară.
"""

import struct
import zlib

import numpy as np
import pytest
from conftest import LAT0, LON0

from terragen.build import CityBuilder
from terragen.multipolygon import assemble, rings_of
from terragen.osmdata import Way, ways_from_relation
from terragen.project import LocalProjection

# --------------------------------------------------------------- 1. asamblare


def square(x0, y0, x1, y1, close=True):
    pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    return [*pts, pts[0]] if close else pts


def test_ring_split_in_two_ways_is_joined():
    """Un inel poate fi spart în mai multe way-uri — trebuie lipit la loc."""
    polys = assemble(
        [
            ("outer", [(0, 0), (5, 0), (10, 0)]),
            ("outer", [(10, 0), (10, 10), (0, 10), (0, 0)]),
        ]
    )
    assert len(polys) == 1
    assert polys[0].area == pytest.approx(100.0)


def test_inner_ring_becomes_hole():
    polys = assemble([("outer", square(0, 0, 10, 10)), ("inner", square(2, 2, 4, 4))])
    assert len(polys) == 1
    assert polys[0].area == pytest.approx(96.0)
    assert len(rings_of(polys[0])) == 2  # exterior + o gaură


def test_two_outers_give_two_polygons():
    polys = assemble([("outer", square(0, 0, 1, 1)), ("outer", square(5, 5, 6, 6))])
    assert len(polys) == 2
    assert sorted(round(p.area, 6) for p in polys) == [1.0, 1.0]


def test_missing_role_is_treated_as_outer():
    assert len(assemble([("", square(0, 0, 4, 4))])) == 1


def test_broken_ring_is_dropped_not_crashing():
    """Extract tăiat la marginea bbox-ului → inel deschis, imposibil de închis."""
    assert assemble([("outer", [(0, 0), (10, 0), (10, 10)])]) == []
    assert assemble([("outer", [(0, 0), (1, 1)])]) == []
    assert assemble([("inner", square(0, 0, 1, 1))]) == []
    assert assemble([]) == []


def test_ways_from_relation_filters_tags_and_keeps_holes():
    members = [("outer", square(0, 0, 10, 10)), ("inner", square(1, 1, 2, 2))]
    wanted = lambda tags: "building" in tags  # noqa: E731
    kept = ways_from_relation(777, {"building": "church"}, members, wanted=wanted)
    assert len(kept) == 1
    assert kept[0].id == 777
    assert len(kept[0].rings) == 2
    assert kept[0].coords == kept[0].rings[0]
    assert ways_from_relation(778, {"type": "multipolygon"}, members, wanted=wanted) == []


# --------------------------------------------------------- 2. PBF sintetic
def _varint(n: int) -> bytes:
    n &= (1 << 64) - 1
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _zigzag(n: int) -> bytes:
    return _varint((n << 1) ^ (n >> 63))


def _tag(fn: int, wt: int) -> bytes:
    return _varint((fn << 3) | wt)


def _ld(fn: int, payload: bytes) -> bytes:
    return _tag(fn, 2) + _varint(len(payload)) + payload


def _vi(fn: int, value: int) -> bytes:
    return _tag(fn, 0) + _varint(value)


def _packed_signed(fn: int, values) -> bytes:
    return _ld(fn, b"".join(_zigzag(v) for v in values))


def _packed_unsigned(fn: int, values) -> bytes:
    return _ld(fn, b"".join(_varint(v) for v in values))


def _blob(btype: str, payload: bytes) -> bytes:
    z = zlib.compress(payload)
    blob = _vi(2, len(payload)) + _ld(3, z)
    header = _ld(1, btype.encode()) + _vi(3, len(blob))
    return struct.pack(">I", len(header)) + header + blob


def _dense_block(nodes, ways, relations, granularity=100):
    """nodes: {id: (lon, lat)}; ways: {id: (refs, tags)}; relations: [(id, tags, members)]."""
    strings = [""]

    def sid(txt):
        if txt not in strings:
            strings.append(txt)
        return strings.index(txt)

    ids = sorted(nodes)
    lats = [round(nodes[i][1] * 1e9 / granularity) for i in ids]
    lons = [round(nodes[i][0] * 1e9 / granularity) for i in ids]
    prev_id = prev_lat = prev_lon = 0
    d_ids, d_lats, d_lons = [], [], []
    for i, la, lo in zip(ids, lats, lons, strict=True):
        d_ids.append(i - prev_id)
        d_lats.append(la - prev_lat)
        d_lons.append(lo - prev_lon)
        prev_id, prev_lat, prev_lon = i, la, lo
    dense = _packed_signed(1, d_ids) + _packed_signed(8, d_lats) + _packed_signed(9, d_lons)

    way_msgs = b""
    for wid, (refs, wtags) in ways.items():
        keys = [sid(k) for k in wtags] or [sid("_none")]
        vals = [sid(v) for v in wtags.values()] or [sid("_none")]
        deltas, prev = [], 0
        for r in refs:
            deltas.append(r - prev)
            prev = r
        way_msgs += _ld(
            3,
            _vi(1, wid)
            + _packed_unsigned(2, keys)
            + _packed_unsigned(3, vals)
            + _packed_signed(8, deltas),
        )

    rel_msgs = b""
    for rid, rtags, members in relations:
        keys = [sid(k) for k in rtags]
        vals = [sid(v) for v in rtags.values()]
        roles = [sid(role) for role, _ref in members]
        prev = 0
        deltas = []
        for _role, mref in members:
            deltas.append(mref - prev)
            prev = mref
        rel_msgs += _ld(
            4,
            _vi(1, rid)
            + _packed_unsigned(2, keys)
            + _packed_unsigned(3, vals)
            + _packed_unsigned(8, roles)
            + _packed_signed(9, deltas)
            + _packed_unsigned(10, [1] * len(members)),  # 1 = WAY
        )

    stringtable = b"".join(_ld(1, s.encode()) for s in strings)
    group = _ld(2, dense) + way_msgs + rel_msgs  # 2 = DenseNodes
    block = _ld(1, stringtable) + _ld(2, group) + _vi(17, granularity)
    return _blob("OSMData", block)


def test_pbflite_reads_multipolygon_relation(tmp_path):
    """Way-urile inelare nu au tag-uri → există doar ca membri ai relației."""
    from terragen.pbflite import read_ways

    lat0, lon0 = LAT0, LON0
    d = 0.001
    nodes = {
        1: (lon0 - d, lat0 - d),
        2: (lon0 + d, lat0 - d),
        3: (lon0 + d, lat0 + d),
        4: (lon0 - d, lat0 + d),
        5: (lon0 - d / 3, lat0 - d / 3),
        6: (lon0 + d / 3, lat0 - d / 3),
        7: (lon0 + d / 3, lat0 + d / 3),
        8: (lon0 - d / 3, lat0 + d / 3),
    }
    ways = {
        101: ([1, 2, 3, 4, 1], {}),  # inel exterior
        102: ([5, 6, 7, 8, 5], {}),  # inel interior (curte)
        201: ([1, 2, 3], {"building": "yes"}),  # way obișnuit, cu tag propriu
    }
    relations = [
        (900, {"type": "multipolygon", "building": "church"}, [("outer", 101), ("inner", 102)]),
        (901, {"type": "multipolygon", "boundary": "administrative"}, [("outer", 101)]),
    ]
    pbf = tmp_path / "test.osm.pbf"
    pbf.write_bytes(_dense_block(nodes, ways, relations))

    bbox = (lat0 - 0.01, lon0 - 0.01, lat0 + 0.01, lon0 + 0.01)
    out = read_ways(str(pbf), bbox, lambda tags: "building" in tags)

    # relația de clădire (900) + way-ul cu tag propriu (201); cea administrativă (901) e ignorată
    assert sorted(w["id"] for w in out) == [201, 900], out
    rel = next(w for w in out if w["id"] == 900)
    assert rel["tags"]["building"] == "church"
    assert len(rel["rings"]) == 2  # exterior + gaură
    assert len(rel["coords"]) >= 4
    assert rel["coords"] == rel["rings"][0]


# --------------------------------------------------- 3. construcție cu gaură
def _builder(ways, dem, size=400):
    proj = LocalProjection(LAT0, LON0)
    b = CityBuilder(ways, proj, dem, size_m=size, terrain_res=64)
    b.build_terrain()
    return b


def _face_count(mesh) -> int:
    return len(mesh.f)


def _area_xz(mesh) -> float:
    """Aria totală a triunghiurilor, proiectată pe planul orizontal (x, z)."""
    tri = mesh.v[mesh.f]  # (m, 3, 3)
    x = tri[:, :, 0]
    z = tri[:, :, 2]
    a = 0.5 * np.abs(
        (x[:, 1] - x[:, 0]) * (z[:, 2] - z[:, 0]) - (x[:, 2] - x[:, 0]) * (z[:, 1] - z[:, 0])
    )
    return float(a.sum())


def test_building_with_courtyard_has_inner_walls(fake_dem):
    """Biserică cu curte: zidurile apar pe ambele contururi (exterior + curte)."""
    d = 0.0012
    outer = [
        (LON0 - d, LAT0 - d),
        (LON0 + d, LAT0 - d),
        (LON0 + d, LAT0 + d),
        (LON0 - d, LAT0 + d),
        (LON0 - d, LAT0 - d),
    ]
    inner = [
        (LON0 - d / 3, LAT0 - d / 3),
        (LON0 + d / 3, LAT0 - d / 3),
        (LON0 + d / 3, LAT0 + d / 3),
        (LON0 - d / 3, LAT0 + d / 3),
        (LON0 - d / 3, LAT0 - d / 3),
    ]
    solid = Way(id=800, tags={"building": "church"}, coords=outer)
    holed = Way(id=801, tags={"building": "church"}, coords=outer, rings=[outer, inner])

    faces_solid = _face_count(_builder([solid], fake_dem).build_buildings())
    faces_holed = _face_count(_builder([holed], fake_dem).build_buildings())
    assert faces_holed > faces_solid * 1.3


def test_relation_area_park_with_hole(fake_dem):
    """Parc multipolygon cu lac înăuntru → suprafața generată nu acoperă lacul."""
    d = 0.0015
    outer = [
        (LON0 - d, LAT0 - d),
        (LON0 + d, LAT0 - d),
        (LON0 + d, LAT0 + d),
        (LON0 - d, LAT0 + d),
        (LON0 - d, LAT0 - d),
    ]
    inner = [
        (LON0 - d / 2, LAT0 - d / 2),
        (LON0 + d / 2, LAT0 - d / 2),
        (LON0 + d / 2, LAT0 + d / 2),
        (LON0 - d / 2, LAT0 + d / 2),
        (LON0 - d / 2, LAT0 - d / 2),
    ]
    full = Way(id=802, tags={"leisure": "park"}, coords=outer)
    holed = Way(id=803, tags={"leisure": "park"}, coords=outer, rings=[outer, inner])

    b_full = _builder([full], fake_dem)
    b_holed = _builder([holed], fake_dem)
    area_full = _area_xz(b_full.build_areas()["green"])
    area_holed = _area_xz(b_holed.build_areas()["green"])
    # cu gaură se generează mai puțină suprafață: curtea nu mai e acoperită
    assert area_holed < area_full * 0.95
    assert area_holed == pytest.approx(area_full * 0.75, rel=0.05)
    assert b_holed.stats["areas"]["green"] == 1
