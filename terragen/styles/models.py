"""Visual Style Engine Data Models & Configurations.

Provides structured, serializable dataclasses for visual styling:
- Palette & Shader parameters
- Geometry simplification & roof profiles
- Vegetation distribution & scales
- Road & water visual properties
- Lighting & atmospheric settings for Blender/Game engines
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

RGB = tuple[int, int, int]


@dataclass
class BuildingStyle:
    simplification_tol: float = 0.8        # Shapely simplify tolerance in meters
    min_area: float = 12.0                 # Minimum footprint area to generate
    height_scale: float = 1.0              # Overall height multiplier
    height_variation: float = 0.35         # Deterministic random height perturbation
    facade_palette: list[RGB] = field(default_factory=list)
    roof_palette: list[RGB] = field(default_factory=list)
    roof_styles: list[str] = field(default_factory=lambda: ["gable", "flat", "hip"])
    roof_overhang: float = 0.45            # Eaves overhang in meters
    roof_ridge_scale: float = 1.0          # Ridge height scale for gabled roofs
    ground_floor_darkening: float = 0.85   # Contrast ratio for ground level
    add_chimneys: bool = True
    roughness: float = 0.80                # PBR roughness
    metallic: float = 0.02                 # PBR metallic


@dataclass
class VegetationStyle:
    enabled: bool = True
    density_per_ha: float = 15.0           # Trees per hectare in open terrain
    forest_density_per_ha: float = 140.0   # Trees per hectare in forest areas
    tree_types: list[str] = field(default_factory=lambda: ["pine", "deciduous"])
    foliage_palette: list[RGB] = field(default_factory=list)
    trunk_palette: list[RGB] = field(default_factory=lambda: [(102, 72, 48), (85, 62, 42)])
    scale_min: float = 0.75
    scale_max: float = 1.35
    max_slope_deg: float = 34.0            # Maximum terrain slope for tree growth
    exclude_roads_buffer_m: float = 3.0    # Buffer distance around roads
    exclude_buildings_buffer_m: float = 2.0


@dataclass
class TerrainStyle:
    elevation_scale: float = 1.0           # Exaggeration factor (non-destructive)
    elevation_offset_m: float = 0.0        # Vertical shift in meters
    low_color: RGB = (135, 175, 105)       # Lush valley green
    mid_color: RGB = (160, 185, 120)       # Upland meadow
    high_color: RGB = (175, 170, 145)      # Subalpine transition
    rock_color: RGB = (120, 122, 128)      # Cliff slate rock
    peak_color: RGB | None = None          # Snow / high alpine peak
    slope_threshold_deg: float = 20.0
    slope_blend_range_deg: float = 16.0
    roughness: float = 0.85                # PBR roughness
    metallic: float = 0.0                  # PBR metallic


@dataclass
class RoadStyle:
    asphalt_color: RGB = (68, 72, 80)
    sidewalk_color: RGB = (168, 164, 155)
    track_color: RGB = (145, 130, 110)
    width_scale: float = 1.0
    roughness: float = 0.70
    metallic: float = 0.04


@dataclass
class WaterStyle:
    color: RGB = (62, 138, 182)
    opacity: float = 0.85
    surface_offset_m: float = -0.30
    roughness: float = 0.12
    metallic: float = 0.05


@dataclass
class LightingAtmosphereStyle:
    sun_azimuth_deg: float = 45.0          # Compass direction of sun (0=North, 90=East)
    sun_elevation_deg: float = 52.0        # Sun height above horizon (degrees)
    sun_color: RGB = (255, 248, 235)       # Sunlight color
    sun_intensity: float = 3.5             # Sun strength in Blender
    ambient_color: RGB = (185, 205, 230)   # Sky ambient light
    sky_horizon_color: RGB = (210, 225, 245)
    sky_zenith_color: RGB = (120, 160, 215)
    fog_enabled: bool = False
    fog_density: float = 0.001
    fog_color: RGB = (200, 215, 230)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sun_azimuth_deg": self.sun_azimuth_deg,
            "sun_elevation_deg": self.sun_elevation_deg,
            "sun_color_rgb": list(self.sun_color),
            "sun_intensity": self.sun_intensity,
            "ambient_sky_color_rgb": list(self.ambient_color),
            "sky_horizon_color_rgb": list(self.sky_horizon_color),
            "sky_zenith_color_rgb": list(self.sky_zenith_color),
            "fog_enabled": self.fog_enabled,
            "fog_density": self.fog_density,
            "fog_color_rgb": list(self.fog_color),
        }


@dataclass
class MaterialSettings:
    roughness: float = 0.80                # Baseline matte stylized finish
    metallic: float = 0.04


@dataclass
class StylePreset:
    name: str
    label: str
    description: str
    version: str = "1.0.0"
    building: BuildingStyle = field(default_factory=BuildingStyle)
    vegetation: VegetationStyle = field(default_factory=VegetationStyle)
    terrain: TerrainStyle = field(default_factory=TerrainStyle)
    roads: RoadStyle = field(default_factory=RoadStyle)
    water: WaterStyle = field(default_factory=WaterStyle)
    lighting: LightingAtmosphereStyle = field(default_factory=LightingAtmosphereStyle)
    material: MaterialSettings = field(default_factory=MaterialSettings)

    @property
    def id(self) -> str:
        return self.name

    @property
    def preset_id(self) -> str:
        return self.name

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "description": self.description,
            "version": self.version,
            "building": {
                "simplification_tol": self.building.simplification_tol,
                "min_area": self.building.min_area,
                "height_scale": self.building.height_scale,
                "height_variation": self.building.height_variation,
                "roof_overhang": self.building.roof_overhang,
                "roof_ridge_scale": self.building.roof_ridge_scale,
                "add_chimneys": self.building.add_chimneys,
                "roof_styles": list(self.building.roof_styles),
                "facade_palette": list(self.building.facade_palette),
                "roof_palette": list(self.building.roof_palette),
                "roughness": self.building.roughness,
                "metallic": self.building.metallic,
            },
            "vegetation": {
                "enabled": self.vegetation.enabled,
                "density_per_ha": self.vegetation.density_per_ha,
                "forest_density_per_ha": self.vegetation.forest_density_per_ha,
                "tree_types": list(self.vegetation.tree_types),
                "foliage_palette": list(self.vegetation.foliage_palette),
                "trunk_palette": list(self.vegetation.trunk_palette),
                "scale_min": self.vegetation.scale_min,
                "scale_max": self.vegetation.scale_max,
                "max_slope_deg": self.vegetation.max_slope_deg,
                "exclude_roads_buffer_m": self.vegetation.exclude_roads_buffer_m,
                "exclude_buildings_buffer_m": self.vegetation.exclude_buildings_buffer_m,
            },
            "terrain": {
                "elevation_scale": self.terrain.elevation_scale,
                "elevation_offset_m": self.terrain.elevation_offset_m,
                "low_color": list(self.terrain.low_color),
                "mid_color": list(self.terrain.mid_color),
                "high_color": list(self.terrain.high_color),
                "rock_color": list(self.terrain.rock_color),
                "peak_color": list(self.terrain.peak_color) if self.terrain.peak_color else None,
                "slope_threshold_deg": self.terrain.slope_threshold_deg,
                "slope_blend_range_deg": self.terrain.slope_blend_range_deg,
                "roughness": self.terrain.roughness,
                "metallic": self.terrain.metallic,
            },
            "roads": {
                "asphalt_color": list(self.roads.asphalt_color),
                "sidewalk_color": list(self.roads.sidewalk_color),
                "track_color": list(self.roads.track_color),
                "width_scale": self.roads.width_scale,
                "roughness": self.roads.roughness,
                "metallic": self.roads.metallic,
            },
            "water": {
                "color": list(self.water.color),
                "opacity": self.water.opacity,
                "surface_offset_m": self.water.surface_offset_m,
                "roughness": self.water.roughness,
                "metallic": self.water.metallic,
            },
            "lighting": {
                "sun_azimuth_deg": self.lighting.sun_azimuth_deg,
                "sun_elevation_deg": self.lighting.sun_elevation_deg,
                "sun_color": list(self.lighting.sun_color),
                "sun_intensity": self.lighting.sun_intensity,
                "ambient_color": list(self.lighting.ambient_color),
                "sky_horizon_color": list(self.lighting.sky_horizon_color),
                "sky_zenith_color": list(self.lighting.sky_zenith_color),
                "fog_enabled": self.lighting.fog_enabled,
                "fog_density": self.lighting.fog_density,
                "fog_color": list(self.lighting.fog_color),
            },
            "material": {
                "roughness": self.material.roughness,
                "metallic": self.material.metallic,
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StylePreset:
        from .validator import validate_style_preset_dict
        validated = validate_style_preset_dict(data)

        b_data = validated.get("building", {})
        v_data = validated.get("vegetation", {})
        t_data = validated.get("terrain", {})
        r_data = validated.get("roads", {})
        w_data = validated.get("water", {})
        l_data = validated.get("lighting", {})
        m_data = validated.get("material", {})

        return cls(
            name=validated["name"],
            label=validated.get("label", validated["name"]),
            description=validated.get("description", ""),
            version=validated.get("version", "1.0.0"),
            building=BuildingStyle(
                simplification_tol=float(b_data.get("simplification_tol", 0.8)),
                min_area=float(b_data.get("min_area", 12.0)),
                height_scale=float(b_data.get("height_scale", 1.0)),
                height_variation=float(b_data.get("height_variation", 0.35)),
                facade_palette=[tuple(c) for c in b_data.get("facade_palette", [])],
                roof_palette=[tuple(c) for c in b_data.get("roof_palette", [])],
                roof_styles=list(b_data.get("roof_styles", ["gable", "flat", "hip"])),
                roof_overhang=float(b_data.get("roof_overhang", 0.45)),
                roof_ridge_scale=float(b_data.get("roof_ridge_scale", 1.0)),
                ground_floor_darkening=float(b_data.get("ground_floor_darkening", 0.85)),
                add_chimneys=bool(b_data.get("add_chimneys", True)),
                roughness=float(b_data.get("roughness", 0.80)),
                metallic=float(b_data.get("metallic", 0.02)),
            ),
            vegetation=VegetationStyle(
                enabled=bool(v_data.get("enabled", True)),
                density_per_ha=float(v_data.get("density_per_ha", 15.0)),
                forest_density_per_ha=float(v_data.get("forest_density_per_ha", 140.0)),
                tree_types=list(v_data.get("tree_types", ["pine", "deciduous"])),
                foliage_palette=[tuple(c) for c in v_data.get("foliage_palette", [])],
                trunk_palette=[tuple(c) for c in v_data.get("trunk_palette", [(102, 72, 48)])],
                scale_min=float(v_data.get("scale_min", 0.75)),
                scale_max=float(v_data.get("scale_max", 1.35)),
                max_slope_deg=float(v_data.get("max_slope_deg", 34.0)),
                exclude_roads_buffer_m=float(v_data.get("exclude_roads_buffer_m", 3.0)),
                exclude_buildings_buffer_m=float(v_data.get("exclude_buildings_buffer_m", 2.0)),
            ),
            terrain=TerrainStyle(
                elevation_scale=float(t_data.get("elevation_scale", 1.0)),
                elevation_offset_m=float(t_data.get("elevation_offset_m", 0.0)),
                low_color=tuple(t_data.get("low_color", (135, 175, 105))),
                mid_color=tuple(t_data.get("mid_color", (160, 185, 120))),
                high_color=tuple(t_data.get("high_color", (175, 170, 145))),
                rock_color=tuple(t_data.get("rock_color", (120, 122, 128))),
                peak_color=tuple(t_data["peak_color"]) if t_data.get("peak_color") else None,
                slope_threshold_deg=float(t_data.get("slope_threshold_deg", 20.0)),
                slope_blend_range_deg=float(t_data.get("slope_blend_range_deg", 16.0)),
                roughness=float(t_data.get("roughness", 0.85)),
                metallic=float(t_data.get("metallic", 0.0)),
            ),
            roads=RoadStyle(
                asphalt_color=tuple(r_data.get("asphalt_color", (68, 72, 80))),
                sidewalk_color=tuple(r_data.get("sidewalk_color", (168, 164, 155))),
                track_color=tuple(r_data.get("track_color", (145, 130, 110))),
                width_scale=float(r_data.get("width_scale", 1.0)),
                roughness=float(r_data.get("roughness", 0.70)),
                metallic=float(r_data.get("metallic", 0.04)),
            ),
            water=WaterStyle(
                color=tuple(w_data.get("color", (62, 138, 182))),
                opacity=float(w_data.get("opacity", 0.85)),
                surface_offset_m=float(w_data.get("surface_offset_m", -0.30)),
                roughness=float(w_data.get("roughness", 0.12)),
                metallic=float(w_data.get("metallic", 0.05)),
            ),
            lighting=LightingAtmosphereStyle(
                sun_azimuth_deg=float(l_data.get("sun_azimuth_deg", 45.0)),
                sun_elevation_deg=float(l_data.get("sun_elevation_deg", 52.0)),
                sun_color=tuple(l_data.get("sun_color", (255, 248, 235))),
                sun_intensity=float(l_data.get("sun_intensity", 3.5)),
                ambient_color=tuple(l_data.get("ambient_color", (185, 205, 230))),
                sky_horizon_color=tuple(l_data.get("sky_horizon_color", (210, 225, 245))),
                sky_zenith_color=tuple(l_data.get("sky_zenith_color", (120, 160, 215))),
                fog_enabled=bool(l_data.get("fog_enabled", False)),
                fog_density=float(l_data.get("fog_density", 0.001)),
                fog_color=tuple(l_data.get("fog_color", (200, 215, 230))),
            ),
            material=MaterialSettings(
                roughness=float(m_data.get("roughness", 0.80)),
                metallic=float(m_data.get("metallic", 0.04)),
            ),
        )
