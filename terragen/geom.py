"""Primitive de geometrie: mesh, triangulare, extrudare cu pereți plați, reeșantionare."""

from __future__ import annotations

import mapbox_earcut
import numpy as np
from shapely.geometry import Polygon

# module de fațadă — folosite de UV-uri și de textura procedurală (facade.py)
MODULE_U = 3.20  # lățimea unui modul de fereastră, în metri
FLOOR_H = 3.10  # înălțimea unui etaj, în metri


class Mesh:
    """Mesh indexat cu culori per vertex. Coordonate: x=Est, y=Sus, z=Nord."""

    def __init__(self):
        self.v = np.zeros((0, 3), dtype=np.float64)
        self.f = np.zeros((0, 3), dtype=np.int64)
        self.c = np.zeros((0, 4), dtype=np.uint8)
        self.uv = np.zeros((0, 2), dtype=np.float64)

    def __len__(self) -> int:
        return len(self.f)

    @property
    def empty(self) -> bool:
        return len(self.f) == 0

    def add(self, verts, faces, color=None, uv=None) -> None:
        verts = np.asarray(verts, dtype=np.float64)
        faces = np.asarray(faces, dtype=np.int64)
        if len(faces) == 0:
            return
        off = len(self.v)
        self.v = np.vstack([self.v, verts])

        if color is None:
            cols = np.full((len(verts), 4), 255, dtype=np.uint8)
        elif np.isscalar(color):
            cols = np.full((len(verts), 4), 255, dtype=np.uint8)
            cols[:, :3] = int(color)
        else:
            arr = np.asarray(color, dtype=np.uint8)
            cols = np.full((len(verts), 4), 255, dtype=np.uint8)
            if arr.ndim == 2 and arr.shape[1] == 4:
                cols[:] = arr
            else:
                cols[:, :3] = arr.reshape(-1, 3)

        if uv is None:
            uvs = np.zeros((len(verts), 2), dtype=np.float64)
        else:
            uvs = np.asarray(uv, dtype=np.float64).reshape(-1, 2)

        self.f = np.vstack([self.f, faces + off])
        self.c = np.vstack([self.c, cols])
        self.uv = np.vstack([self.uv, uvs]) if len(self.uv) else uvs

    def merged(self, other: Mesh) -> Mesh:
        out = Mesh()
        out.v = np.vstack([self.v, other.v])
        out.c = np.vstack([self.c, other.c])
        out.uv = np.vstack([self.uv, other.uv])
        out.f = np.vstack([self.f, other.f + len(self.v)])
        return out

    def bounds(self):
        if not len(self.v):
            return None
        return self.v.min(axis=0), self.v.max(axis=0)

    def __repr__(self) -> str:
        return f"<Mesh {len(self.v)} vârfuri, {len(self.f)} triunghiuri>"


# ---------------------------------------------------------------- triangulare
def _clean_ring(arr) -> np.ndarray:
    arr = np.asarray(arr, dtype=np.float64)
    if len(arr) < 3:
        return np.zeros((0, 2))
    keep = np.ones(len(arr), dtype=bool)
    keep[1:] = np.linalg.norm(np.diff(arr, axis=0), axis=1) > 1e-4
    arr = arr[keep]
    if len(arr) > 1 and np.linalg.norm(arr[0] - arr[-1]) < 1e-4:
        arr = arr[:-1]
    return arr


def triangulate_polygon(poly: Polygon):
    """Poligon shapely (2D) -> (verts (n,2), faces (m,3)), cu găuri, orientat CCW."""
    rings = [_clean_ring(poly.exterior.coords)]
    rings += [_clean_ring(r.coords) for r in poly.interiors]
    rings = [r for r in rings if len(r) >= 3]
    if not rings:
        return np.zeros((0, 2)), np.zeros((0, 3), dtype=np.int64)

    verts = np.vstack(rings).astype(np.float64)
    ends = np.cumsum([len(r) for r in rings]).astype(np.uint32)
    idx = mapbox_earcut.triangulate_float64(verts, ends).reshape(-1, 3).astype(np.int64)
    if len(idx) == 0:
        return np.zeros((0, 2)), np.zeros((0, 3), dtype=np.int64)

    a, b, c = verts[idx[:, 0]], verts[idx[:, 1]], verts[idx[:, 2]]
    cross = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])
    flip = cross < 0
    idx[flip] = idx[flip][:, [0, 2, 1]]
    return verts, idx


def extrude(
    poly: Polygon, base: float, top: float, floor_h: float = FLOOR_H, module_u: float = MODULE_U
):
    """Prismă: capac superior + pereți, fără fund.

    Pereții sunt **separati pe muchie** (fiecare segment de contur are propriile 4 vârfuri),
    deci normalele ies „flat" — colțurile clădirilor rămân ascuțite, look-ul low-poly corect.

    UV-uri de fațadă, în module:
      u = distanța pe contur / module_u   (o celulă de textură = o fereastră)
      v = numărul etajului                (0 = parter, n = streașină)

    Returnează (verts, faces, cap_mask, uv) sau (None, None, None, None).
    """
    vv, ff = triangulate_polygon(poly)
    if not len(vv):
        return None, None, None, None

    height = max(0.1, top - base)
    floors = max(1.0, height / floor_h)

    # capacul (fără UV de fațadă, fără ferestre)
    cap_verts = np.column_stack([vv[:, 0], np.full(len(vv), top), vv[:, 1]])
    vlist = [cap_verts]
    flist = [ff]
    uvlist = [np.zeros((len(vv), 2), dtype=np.float64)]
    cap_flags = [np.ones(len(vv), dtype=bool)]
    count = len(vv)

    rings = [np.asarray(poly.exterior.coords)[:-1]]
    rings += [np.asarray(r.coords)[:-1] for r in poly.interiors]

    for ring2d in rings:
        ring2d = np.asarray(ring2d, dtype=np.float64)
        m = len(ring2d)
        if m < 3:
            continue
        closed = np.vstack([ring2d, ring2d[:1]])
        seg = np.linalg.norm(np.diff(closed, axis=0), axis=1)
        cum = np.concatenate([[0.0], np.cumsum(seg)])
        u_at = cum / max(0.5, module_u)

        # orientare: pereții trebuie să privească spre exterior
        area2 = np.sum(
            ring2d[:, 0] * np.roll(ring2d[:, 1], -1) - np.roll(ring2d[:, 0], -1) * ring2d[:, 1]
        )
        ccw = area2 > 0

        # câte 4 vârfuri per segment (deduplicare intenționată: normale flat)
        wall_v = np.zeros((4 * m, 3), dtype=np.float64)
        wall_uv = np.zeros((4 * m, 2), dtype=np.float64)
        for k in range(m):
            kn = (k + 1) % m
            x0, z0 = ring2d[k]
            x1, z1 = ring2d[kn]
            b = 4 * k
            wall_v[b + 0] = (x0, base, z0)
            wall_v[b + 1] = (x1, base, z1)
            wall_v[b + 2] = (x1, top, z1)
            wall_v[b + 3] = (x0, top, z0)
            wall_uv[b + 0] = (u_at[k], 0.0)
            wall_uv[b + 1] = (u_at[k + 1], 0.0)
            wall_uv[b + 2] = (u_at[k + 1], floors)
            wall_uv[b + 3] = (u_at[k], floors)

        faces = []
        for k in range(m):
            b = 4 * k
            if ccw:
                faces.append([b + 0, b + 1, b + 2])
                faces.append([b + 0, b + 2, b + 3])
            else:
                faces.append([b + 0, b + 2, b + 1])
                faces.append([b + 0, b + 3, b + 2])

        vlist.append(wall_v)
        flist.append(np.asarray(faces, dtype=np.int64) + count)
        uvlist.append(wall_uv)
        cap_flags.append(np.zeros(4 * m, dtype=bool))
        count += 4 * m

    allv = np.vstack(vlist)
    all_uv = np.vstack(uvlist)
    cap_mask = np.concatenate(cap_flags)
    faces = np.vstack(flist)

    # normală în sus pentru capac, indiferent de orientarea conturului
    cap_faces = faces[: len(ff)]
    tri = allv[cap_faces]
    n_y = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])[:, 1]
    if len(n_y) and np.mean(n_y) < 0:
        faces[: len(ff)] = cap_faces[:, [0, 2, 1]]

    return allv, faces, cap_mask, all_uv


def box(cx: float, cz: float, w: float, d: float, y0: float, y1: float):
    """Cutie fără fund (hornuri, casete tehnice, antene) -> (verts, faces)."""
    hw, hd = w / 2.0, d / 2.0
    ring = np.array(
        [[cx - hw, cz - hd], [cx + hw, cz - hd], [cx + hw, cz + hd], [cx - hw, cz + hd]]
    )
    verts = np.vstack(
        [
            np.column_stack([ring[:, 0], np.full(4, y1), ring[:, 1]]),
            np.column_stack([ring[:, 0], np.full(4, y0), ring[:, 1]]),
        ]
    )
    faces = np.array(
        [
            [0, 1, 2],
            [0, 2, 3],  # capac
            [0, 4, 5],
            [0, 5, 1],  # pereți
            [1, 5, 6],
            [1, 6, 2],
            [2, 6, 7],
            [2, 7, 3],
            [3, 7, 4],
            [3, 4, 0],
        ]
    )
    return verts, faces


# ---------------------------------------------------------------- linii / relief
def resample(coords2d: np.ndarray, step: float) -> np.ndarray:
    """Reeșantionare uniformă a unei polilinii 2D la ~step metri."""
    if len(coords2d) < 2:
        return coords2d
    seg = np.linalg.norm(np.diff(coords2d, axis=0), axis=1)
    total = seg.sum()
    if total <= 0:
        return coords2d[:1]
    n = max(2, int(np.ceil(total / step)) + 1)
    s = np.linspace(0, total, n)
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    x = np.interp(s, cum, coords2d[:, 0])
    y = np.interp(s, cum, coords2d[:, 1])
    return np.column_stack([x, y])


def smooth_1d(a: np.ndarray, window: int = 5) -> np.ndarray:
    if window <= 1 or len(a) < 3:
        return a
    k = np.ones(window) / window
    pad = window // 2
    ap = np.pad(a, pad, mode="edge")
    return np.convolve(ap, k, mode="valid")[: len(a)]
