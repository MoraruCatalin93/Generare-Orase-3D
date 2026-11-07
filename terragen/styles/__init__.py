"""Visual Style Engine package: Modular Presets, Shaders, Procedural Vegetation, and Atmospheric Lighting."""

from __future__ import annotations

from .loader import (
    _ensure_builtin_presets_written,
    get_presets_dir,
    get_style_preset,
    list_style_presets,
    load_all_presets,
    load_preset_file,
)
from .models import (
    RGB,
    BuildingStyle,
    LightingAtmosphereStyle,
    MaterialSettings,
    RoadStyle,
    StylePreset,
    TerrainStyle,
    VegetationStyle,
    WaterStyle,
)
from .validator import (
    StyleValidationError,
    validate_style_preset_dict,
)
from .vegetation import build_tree_mesh, generate_vegetation_layer


def validate_style_preset(data: dict) -> StylePreset:
    """Validates raw dictionary representation and returns parsed StylePreset instance."""
    return StylePreset.from_dict(data)

# Backwards-compatible dictionary accessor
class _StylePresetsProxy(dict):
    def __getitem__(self, key: str):
        presets = load_all_presets()
        k = key.lower()
        if k in presets:
            return presets[k]
        return super().__getitem__(key)

    def __contains__(self, key: object):
        presets = load_all_presets()
        return str(key).lower() in presets or super().__contains__(key)

    def keys(self):
        return load_all_presets().keys()

    def values(self):
        return load_all_presets().values()

    def items(self):
        return load_all_presets().items()


STYLE_PRESETS = _StylePresetsProxy()

__all__ = [
    "RGB",
    "STYLE_PRESETS",
    "BuildingStyle",
    "LightingAtmosphereStyle",
    "MaterialSettings",
    "RoadStyle",
    "StylePreset",
    "StyleValidationError",
    "TerrainStyle",
    "VegetationStyle",
    "WaterStyle",
    "_ensure_builtin_presets_written",
    "build_tree_mesh",
    "generate_vegetation_layer",
    "get_presets_dir",
    "get_style_preset",
    "list_style_presets",
    "load_all_presets",
    "load_preset_file",
    "validate_style_preset",
    "validate_style_preset_dict",
]
