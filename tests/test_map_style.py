import re

from terragen.map_style import map_building_style


def test_uses_osm_height_color_and_base():
    props = map_building_style({"height": "20 m", "building:colour": "#abc", "min_height": "3 m"}, 1)
    assert props == {"height": 20.0, "color": "#aabbcc", "base": 3.0}


def test_fallback_is_deterministic():
    first = map_building_style({"building": "house", "building:colour": "invalid"}, 42)
    assert first == map_building_style({"building": "house", "building:colour": "invalid"}, 42)
    assert re.fullmatch(r"#[0-9a-f]{6}", first["color"])
    assert first["height"] > 0


def test_min_level_and_invalid_base():
    assert map_building_style({"height": "20", "building:min_level": "2"}, 1)["base"] == 6.2
    assert map_building_style({"height": "10", "min_height": "15"}, 1)["base"] == 0


def test_named_color():
    assert map_building_style({"building:colour": "red"}, 1)["color"] == "#bc8174"
