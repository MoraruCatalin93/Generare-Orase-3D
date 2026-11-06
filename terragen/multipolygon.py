"""Asamblarea relațiilor `type=multipolygon` OSM în poligoane cu găuri.

O relație multipolygon nu are geometrie proprie: e o listă de **membre** (way-uri) cu rol
`outer` sau `inner`, care împreună închid unul sau mai multe inele. Inelele trebuie:

1. închise (OSM le ține deschise: primul nod = ultimul nod);
2. lipite în bucle complete (un inel poate fi spart în mai multe way-uri);
3. atribuite corect: găurile (`inner`) se scad din conturul care le conține.

Modulul e independent de sursa de date (Overpass / pyosmium / cititorul intern) și de
sistemul de coordonate — merge la fel în lon/lat cât și după proiecția în metri.
"""

from __future__ import annotations

from shapely.geometry import LineString
from shapely.ops import polygonize, unary_union

# două capete de segment se consideră „lipite" dacă sunt mai apropiate de atât
# (în grade: ~1 mm; după proiecție în metri, apelantul poate trece o toleranță mai mică)
TOL_DEG = 1e-9


def _clean(coords, tol: float = 1e-9):
    """Scoate punctele duplicate consecutive; NU închide inelul.

    Închiderea se face la final, de `polygonize`: un inel poate fi spart în mai multe
    way-uri membre, iar dacă am închide fiecare way separat am inventa poligoane.
    """
    pts = [tuple(map(float, c[:2])) for c in coords]
    clean = [pts[0]] if pts else []
    for p in pts[1:]:
        if abs(p[0] - clean[-1][0]) > tol or abs(p[1] - clean[-1][1]) > tol:
            clean.append(p)
    return clean if len(clean) >= 2 else []


def _lines(rings) -> list[LineString]:
    out = []
    for r in rings:
        c = _clean(r)
        if len(c) >= 2:
            out.append(LineString(c))
    return out


def assemble(members, tol: float = TOL_DEG) -> list:
    """Membre `[(role, coords), ...]` → listă de `shapely.Polygon` (cu găuri).

    `role` = "outer"/"inner" (orice altceva e tratat ca outer, cum face și OSM la
    relațiile fără rol explicit). Întoarce `[]` dacă inelele nu se închid (membre
    lipsă din extract, geometrie ruptă etc.) — apelantul ignoră relația.
    """
    outer_rings, inner_rings = [], []
    for role, coords in members:
        if not coords or len(coords) < 3:
            continue
        (inner_rings if str(role).strip() == "inner" else outer_rings).append(coords)

    if not outer_rings:
        return []

    outer_lines = _lines(outer_rings)
    inner_lines = _lines(inner_rings)
    if not outer_lines:
        return []

    try:
        outers = list(polygonize(unary_union(outer_lines)))
        inners = list(polygonize(unary_union(inner_lines))) if inner_lines else []
        if not outers:
            return []
        geom = unary_union(outers)
        if inners:
            geom = geom.difference(unary_union(inners))
    except Exception:
        return []

    if geom.is_empty:
        return []
    if geom.geom_type == "Polygon":
        return [geom]
    return [g for g in getattr(geom, "geoms", []) if g.geom_type == "Polygon" and not g.is_empty]


def rings_of(poly) -> list[list[tuple[float, float]]]:
    """`shapely.Polygon` → `[exterior, gaură1, gaură2, …]` ca liste de (x, y)."""
    rings = [[(float(x), float(y)) for x, y in poly.exterior.coords]]
    rings += [[(float(x), float(y)) for x, y in r.coords] for r in poly.interiors]
    return rings
