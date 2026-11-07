import numpy as np
from shapely.geometry import Polygon

from terragen.geom import MODULE_U, extrude, resample, smooth_1d, triangulate_polygon


def test_triangulate_square_with_hole():
    poly = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)], [[(4, 4), (6, 4), (6, 6), (4, 6)]])
    v, f = triangulate_polygon(poly)
    assert len(f) > 0

    def cross2(d1, d2):
        return d1[0] * d2[1] - d1[1] * d2[0]

    area = sum(abs(cross2(v[b] - v[a], v[c] - v[a])) / 2 for a, b, c in f)
    assert abs(area - (100 - 4)) < 1e-6  # gaura chiar e scăzută


def test_triangulate_orients_up():
    v, f = triangulate_polygon(Polygon([(0, 0), (0, 10), (10, 10), (10, 0)]))  # CW
    tri = v[f]
    d1, d2 = tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]
    cross_z = d1[:, 0] * d2[:, 1] - d1[:, 1] * d2[:, 0]
    assert (cross_z > 0).all()


def test_extrude_uv_are_in_module():
    poly = Polygon([(0, 0), (32, 0), (32, 10), (0, 10)])  # perimetru 84 m
    v, _f, cap, uv = extrude(poly, 0, 9.3)  # 3 etaje
    assert cap.sum() == 4
    assert len(v) == 4 + 4 * 4  # capac + 4 segmente × 4 vârfuri
    walls = uv[~cap]
    assert walls[:, 0].max() <= 84 / MODULE_U + 1e-6
    assert set(np.round(np.unique(walls[:, 1]), 3)) == {0.0, 3.0}  # parter -> streașină


def test_extrude_walls_are_flat_shaded():
    v, f, cap, _uv = extrude(Polygon([(0, 0), (10, 0), (10, 10), (0, 10)]), 0, 10)
    # fiecare segment are propriile vârfuri => normala per față e perpendiculară pe muchie
    wall_faces = f[~cap[f[:, 0]]]
    tri = v[wall_faces]
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    n /= np.linalg.norm(n, axis=1, keepdims=True)
    for vec in n:
        assert abs(vec[1]) < 1e-9  # pereți verticali
        assert abs(abs(vec[0]) + abs(vec[2]) - 1.0) < 1e-6  # axați pe X sau Z
    center = np.array([5.0, 5.0, 5.0])
    tri_centers = np.mean(tri, axis=1)
    outward_vectors = tri_centers - center
    dots = np.sum(n * outward_vectors, axis=1)
    assert (dots > 0).all()  # toate normalele de perete indică spre exterior


def test_extrude_cap_faces_up():
    v, f, cap, _uv = extrude(Polygon([(0, 0), (0, 10), (10, 10), (10, 0)]), 0, 5)  # contur CW
    top = f[cap[f].all(axis=1)]
    tri = v[top]
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    assert (n[:, 1] > 0).all()


def test_resample_and_smooth():
    line = np.array([[0.0, 0.0], [100.0, 0.0]])
    r = resample(line, 4.0)
    assert len(r) == 26
    noisy = np.array([0.0, 10.0, 0.0, 10.0, 0.0])
    assert np.abs(smooth_1d(noisy, 3)).max() <= 10
