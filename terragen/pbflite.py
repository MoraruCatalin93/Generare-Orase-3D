"""Cititor minimal de fișiere .osm.pbf, în Python pur.

Nu are nevoie de pyosmium sau de utilitarul `osmium`: decodează direct protobuf-ul
și blocurile zlib. Returnează doar way-urile care trec de filtrul de tag-uri și au
geometrie completă în bbox — exact ce are nevoie terragen.

Format (OSM PBF):
  [uint32 BE lungime BlobHeader][BlobHeader][Blob] … repetat
  BlobHeader: 1=type, 3=datasize ; Blob: 1=raw, 2=raw_size, 3=zlib_data
  PrimitiveBlock: 1=stringtable, 2=groups, 17=granularity, 19=lat_offset, 20=lon_offset
  PrimitiveGroup: 1=nodes, 2=dense, 3=ways, 4=relations
  Relation: 1=id, 2=keys, 3=vals, 8=roles_sid, 9=memids (delta), 10=types
"""

from __future__ import annotations

import os
import struct
import zlib

NANODEG = 1e-9


# ------------------------------------------------------------------ primitives
def _varint(buf: bytes, pos: int):
    result = 0
    shift = 0
    while True:
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, pos
        shift += 7


def _signed(n: int) -> int:
    """zigzag decode"""
    return (n >> 1) ^ -(n & 1)


def _fields(buf: bytes):
    """Generator (field_no, wire_type, value) pentru un mesaj protobuf."""
    pos, end = 0, len(buf)
    while pos < end:
        key, pos = _varint(buf, pos)
        fn, wt = key >> 3, key & 7
        if wt == 0:
            v, pos = _varint(buf, pos)
        elif wt == 2:
            ln, pos = _varint(buf, pos)
            v = buf[pos : pos + ln]
            pos += ln
        elif wt == 1:
            v = buf[pos : pos + 8]
            pos += 8
        elif wt == 5:
            v = buf[pos : pos + 4]
            pos += 4
        else:
            raise ValueError(f"wire type necunoscut: {wt}")
        yield fn, wt, v


def _packed(buf: bytes):
    out, pos, n = [], 0, len(buf)
    while pos < n:
        v, pos = _varint(buf, pos)
        out.append(v)
    return out


# ------------------------------------------------------------------ blocks
def iter_blocks(path: str, progress=None, every_blocks: int = 400):
    """Iterează (tip_bloc, bytecode_decomprimat) din fișierul .osm.pbf."""
    total = os.path.getsize(path)
    read = 0
    n = 0
    with open(path, "rb") as f:
        while True:
            head = f.read(4)
            if len(head) < 4:
                return
            hlen = struct.unpack(">I", head)[0]
            blob_header = f.read(hlen)
            btype, datasize = None, None
            for fn, _wt, v in _fields(blob_header):
                if fn == 1:
                    btype = v.decode("ascii", "replace")
                elif fn == 3:
                    datasize = v
            blob = f.read(datasize or 0)
            read += 4 + hlen + (datasize or 0)
            if progress and (n % every_blocks == 0):
                progress(read, total)
            n += 1

            raw = None
            zdata = None
            for fn, _wt, v in _fields(blob):
                if fn == 1:
                    raw = v
                elif fn == 2:
                    pass
                elif fn == 3:
                    zdata = v
            if raw is not None:
                data = raw
            elif zdata is not None:
                data = zlib.decompress(zdata)
            else:
                continue
            yield btype, data


# ------------------------------------------------------------------ reader
def read_ways(path: str, bbox, wanted, progress=None):
    """Citește way-urile dorite din .osm.pbf.

    bbox = (sud, vest, nord, est). `wanted(tags: dict) -> bool`.
    Returnează listă de dict-uri: {id, tags, coords:[(lon, lat), ...]}
    """
    s, w, n, e = bbox
    nodes: dict[int, tuple[float, float]] = {}
    out: list[dict] = []
    # toate way-urile (și cele fără tag-uri dorite) → refs, pentru inelele de relație
    way_refs: dict[int, list[int]] = {}
    relations: list[dict] = []

    def handle_group(gdata: bytes, strings: list[str]):
        for fn, _wt, v in _fields(gdata):
            if fn == 2:  # DenseNodes
                _dense(v)
            elif fn == 1:  # Node simplu
                _node(v)
            elif fn == 3:  # Way
                _way(v)
            elif fn == 4:  # Relation (multipolygon)
                _relation(v)

    def _coord(raw: int, offset: int, gran: int) -> float:
        return NANODEG * (offset + gran * raw)

    def _node(buf: bytes):
        nid = lat = lon = 0
        for fn, _wt, v in _fields(buf):
            if fn == 1:
                nid = _signed(v)
            elif fn == 8:
                lat = v
            elif fn == 9:
                lon = v
        la = _coord(lat, lat_off_ref[0], gran_ref[0])
        lo = _coord(lon, lon_off_ref[0], gran_ref[0])
        if s <= la <= n and w <= lo <= e:
            nodes[nid] = (lo, la)

    def _dense(buf: bytes):
        ids = lats = lons = b""
        for fn, _wt, v in _fields(buf):
            if fn == 1:
                ids = v
            elif fn == 8:
                lats = v
            elif fn == 9:
                lons = v
        if not ids:
            return
        gran = gran_ref[0]
        lat_o = lat_off_ref[0]
        lon_o = lon_off_ref[0]
        cur_id = cur_lat = cur_lon = 0
        pi = pl = po = 0
        ni, _nl, _no = len(ids), len(lats), len(lons)
        while pi < ni:
            raw_id, pi = _varint(ids, pi)
            cur_id += _signed(raw_id)
            raw_lat, pl = _varint(lats, pl)
            cur_lat += _signed(raw_lat)
            raw_lon, po = _varint(lons, po)
            cur_lon += _signed(raw_lon)
            la = NANODEG * (lat_o + gran * cur_lat)
            if s <= la <= n:
                lo = NANODEG * (lon_o + gran * cur_lon)
                if w <= lo <= e:
                    nodes[cur_id] = (lo, la)

    def _way(buf: bytes):
        wid = 0
        keys: list[int] = []
        vals: list[int] = []
        refs_raw = b""
        for fn, _wt, v in _fields(buf):
            if fn == 1:
                wid = v
            elif fn == 2:
                keys = _packed(v)
            elif fn == 3:
                vals = _packed(v)
            elif fn == 8:
                refs_raw = v
        if not keys and not refs_raw:
            return
        # reținem refs-urile chiar dacă way-ul nu ne interesează direct:
        # poate fi membru `outer`/`inner` al unei relații multipolygon
        if wid not in way_refs:
            ids, prev_id, pos = [], 0, 0
            while pos < len(refs_raw):
                raw, pos = _varint(refs_raw, pos)
                prev_id += _signed(raw)
                ids.append(prev_id)
            way_refs[wid] = ids
        tags = {}
        for k, val in zip(keys, vals):
            if k < len(strings) and val < len(strings):
                tags[strings[k]] = strings[val]
        if not wanted(tags):
            return
        runs: list[list] = []
        cur: list = []
        prev = 0
        pos = 0
        nref = len(refs_raw)
        while pos < nref:
            raw, pos = _varint(refs_raw, pos)
            prev += _signed(raw)
            c = nodes.get(prev)
            if c is None:
                if len(cur) >= 2:
                    runs.append(cur)
                cur = []
            else:
                cur.append(c)
        if len(cur) >= 2:
            runs.append(cur)
        for r in runs:
            out.append({"id": wid, "tags": tags, "coords": r})

    def _relation(buf: bytes):
        """Relation: 1=id, 2=keys, 3=vals, 8=roles_sid, 9=memids, 10=types."""
        rid = 0
        keys: list[int] = []
        vals: list[int] = []
        roles: list[int] = []
        memids: list[int] = []
        types: list[int] = []
        for fn, _wt, v in _fields(buf):
            if fn == 1:
                rid = v
            elif fn == 2:
                keys = _packed(v)
            elif fn == 3:
                vals = _packed(v)
            elif fn == 8:
                roles = _packed(v)
            elif fn == 9:
                memids = _packed(v)
            elif fn == 10:
                types = _packed(v)
        if not keys or not memids:
            return
        tags = {}
        for k, val in zip(keys, vals):
            if k < len(strings) and val < len(strings):
                tags[strings[k]] = strings[val]
        if tags.get("type") != "multipolygon":
            return
        if wanted is not None and not wanted(tags):
            return
        members = []
        ref = 0
        for i, raw in enumerate(memids):
            ref += _signed(raw)
            mtype = types[i] if i < len(types) else 1  # 0=node, 1=way, 2=relation
            if mtype != 1:
                continue
            role_id = roles[i] if i < len(roles) else -1
            role = strings[role_id] if 0 <= role_id < len(strings) else "outer"
            members.append([role, ref])
        relations.append({"id": rid, "tags": tags, "members": members})

    gran_ref = [100]
    lat_off_ref = [0]
    lon_off_ref = [0]

    for btype, data in iter_blocks(path, progress=progress):
        if btype != "OSMData":
            continue
        strings: list[str] = []
        groups: list[bytes] = []
        gran_ref[0], lat_off_ref[0], lon_off_ref[0] = 100, 0, 0
        for fn, _wt, v in _fields(data):
            if fn == 1:  # StringTable
                for sfn, _swt, sv in _fields(v):
                    if sfn == 1:
                        strings.append(sv.decode("utf-8", "replace"))
            elif fn == 2:
                groups.append(v)
            elif fn == 17:
                gran_ref[0] = v
            elif fn == 19:
                lat_off_ref[0] = _signed(v)
            elif fn == 20:
                lon_off_ref[0] = _signed(v)
        for g in groups:
            handle_group(g, strings)

    if relations:
        from .multipolygon import assemble, rings_of

        for rel in relations:
            members = []
            for role, wid in rel["members"]:
                coords = [nodes[r] for r in way_refs.get(wid, ()) if r in nodes]
                if len(coords) >= 2:
                    members.append((role, coords))
            for poly in assemble(members):
                rings = rings_of(poly)
                if not rings or len(rings[0]) < 3:
                    continue
                out.append(
                    {
                        "id": rel["id"],
                        "tags": rel["tags"],
                        "coords": rings[0],
                        "rings": rings,
                        "relation": True,
                    }
                )
    return out
