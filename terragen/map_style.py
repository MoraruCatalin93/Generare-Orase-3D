"""Proprietăți vizuale pentru clădirile OSM afișate pe hartă."""

import math
import re

from .build import building_color, building_height

# Numai culori cunoscute, evitând valori OSM invalide pentru MapLibre.
NAMED_COLORS = {
    "white": "#eeeae2", "cream": "#eadfc9", "beige": "#d5c5a8",
    "yellow": "#ddca91", "orange": "#d69a73", "red": "#bc8174",
    "brown": "#a58d7b", "grey": "#aaa9a5", "gray": "#aaa9a5",
    "darkgrey": "#777a79", "darkgray": "#777a79", "black": "#666665",
    "blue": "#93a9b7", "green": "#9eaf96", "pink": "#d1aca9",
}


def _meters(value):
    try:
        text = str(value).strip().lower().replace(",", ".")
        match = re.fullmatch(r"([0-9]+(?:[.][0-9]+)?)\s*(?:m|metres|meters)?", text)
        if not match:
            return None
        number = float(match.group(1))
        return number if math.isfinite(number) else None
    except (ValueError, TypeError):
        return None


def map_building_style(tags, way_id):
    """Înălțime din OSM sau estimată, culoare OSM sau paletă deterministă."""
    height = float(building_height(tags, way_id))
    raw_color = str(tags.get("building:colour") or tags.get("building:color") or "").strip().lower()
    if re.fullmatch(r"#[0-9a-f]{6}", raw_color):
        color = raw_color
    elif re.fullmatch(r"#[0-9a-f]{3}", raw_color):
        color = "#" + "".join(ch * 2 for ch in raw_color[1:])
    elif raw_color in NAMED_COLORS:
        color = NAMED_COLORS[raw_color]
    else:
        color = "#%02x%02x%02x" % building_color(way_id)

    base = _meters(tags.get("min_height"))
    if base is None:
        levels = _meters(tags.get("building:min_level"))
        base = levels * 3.1 if levels is not None else 0.0
    # Valorile greșite nu trebuie să ascundă întreaga clădire.
    if base < 0 or base >= height:
        base = 0.0
    return {"height": height, "base": base, "color": color}
