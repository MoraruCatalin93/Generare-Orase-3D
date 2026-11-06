import numpy as np
from conftest import LAT0, LON0

from terragen.build import CityBuilder
from terragen.project import LocalProjection


def _builder(ways, dem, size=600):
    proj = LocalProjection(LAT0, LON0)
    b = CityBuilder(ways, proj, dem, size_m=size, terrain_res=64)
    b.build_terrain()
    return b


def test_terrain_matches_dem(ways, fake_dem):
    b = _builder(ways, fake_dem, size=400)
    z = b.terrain_z
    assert z.shape == (65, 65)
    assert 300 < z.min() < 340 and 300 < z.max() < 340
    assert b.stats["terrain_elev_range"][0] <= b.stats["terrain_elev_range"][1]


def test_roads_follow_terrain(ways, fake_dem):
    """Benzile de drum trebuie să stea pe teren (nu la o altitudine constantă)."""
    b = _builder(ways, fake_dem, size=700)
    roads, walks, network = b.build_roads()
    assert len(roads.f) > 0 and len(walks.f) > 0
    assert len(network) > 0
    z = roads.v[:, 1]
    assert z.max() - z.min() > 0.5  # urmează relieful
    expected = b.hz(roads.v[:, 0], roads.v[:, 2]) + 0.16
    assert np.abs(z - expected).max() < 1e-6  # drapat corect, nu terasat


def test_buildings_have_roofs_and_walls(ways, fake_dem):
    b = _builder(ways, fake_dem, size=600)
    m = b.build_buildings()
    assert b.stats["buildings"] > 20
    assert len(m.f) == len(b.build_buildings().f)  # determinist
    assert (m.c[:, 3] == 255).all()
    tops = m.v[:, 1].max()
    bottoms = m.v[:, 1].min()
    assert tops - bottoms > 3  # are înălțime reală
    assert m.uv[:, 1].max() >= 1  # UV-uri de fațadă (etaje)


def test_areas_split_by_kind(ways, fake_dem):
    b = _builder(ways, fake_dem, size=600)
    areas = b.build_areas()
    assert "green" in areas and len(areas["green"].f) > 0


def test_seed_only_changes_style(ways, fake_dem):
    b1 = CityBuilder(
        ways, LocalProjection(LAT0, LON0), fake_dem, size_m=600, terrain_res=64, seed=1
    )
    b2 = CityBuilder(
        ways, LocalProjection(LAT0, LON0), fake_dem, size_m=600, terrain_res=64, seed=999
    )
    b1.build_terrain()
    b2.build_terrain()
    m1, m2 = b1.build_buildings(), b2.build_buildings()
    assert b1.stats["buildings"] == b2.stats["buildings"]  # aceleași clădiri
    c1 = m1.c[:, :3].mean(axis=0)
    c2 = m2.c[:, :3].mean(axis=0)
    assert not np.allclose(c1, c2)  # stil diferit (culori)
