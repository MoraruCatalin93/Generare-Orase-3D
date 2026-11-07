"""Biome-Aware Placement Rules and Environmental Constraint Configurations."""

from __future__ import annotations

from ..biomes.types import BiomeType
from .models import AssetCategory, AssetSubcategory, PlacementRule


def create_default_placement_rules() -> list[PlacementRule]:
    """Generates the standard ecological and urban placement rules for all asset prototypes."""
    return [
        # 1. Pine Conifer Trees
        PlacementRule(
            prototype_id="tree_pine_01",
            category=AssetCategory.NATURE,
            subcategory=AssetSubcategory.TREE,
            biome_affinity={
                BiomeType.DENSE_FOREST: 1.2,
                BiomeType.FOREST: 0.9,
                BiomeType.ALPINE: 0.8,
                BiomeType.ROCKY_MOUNTAINS: 0.3,
                BiomeType.SNOW: 0.15,
                BiomeType.GRASSLAND: 0.2,
            },
            density_per_ha=32.0,
            max_slope_deg=34.0,
            road_buffer_m=(3.5, None),
            building_buffer_m=(3.0, None),
            water_buffer_m=(2.0, None),
            collision_radius_m=1.6,
            scale_range=(0.75, 1.35),
            cluster_probability=0.40,
            cluster_radius_m=14.0,
            cluster_count_range=(2, 5),
        ),

        # 2. Broadleaf Deciduous Trees
        PlacementRule(
            prototype_id="tree_deciduous_01",
            category=AssetCategory.NATURE,
            subcategory=AssetSubcategory.TREE,
            biome_affinity={
                BiomeType.FOREST: 1.1,
                BiomeType.DENSE_FOREST: 0.8,
                BiomeType.GRASSLAND: 0.5,
                BiomeType.WETLANDS: 0.4,
            },
            density_per_ha=26.0,
            max_slope_deg=30.0,
            road_buffer_m=(3.0, None),
            building_buffer_m=(2.8, None),
            water_buffer_m=(2.5, None),
            collision_radius_m=2.2,
            scale_range=(0.80, 1.30),
            cluster_probability=0.35,
            cluster_radius_m=16.0,
            cluster_count_range=(2, 4),
        ),

        # 3. Bushes and Foliage
        PlacementRule(
            prototype_id="bush_temperate_01",
            category=AssetCategory.NATURE,
            subcategory=AssetSubcategory.BUSH,
            biome_affinity={
                BiomeType.FOREST: 1.0,
                BiomeType.DENSE_FOREST: 1.1,
                BiomeType.GRASSLAND: 0.8,
                BiomeType.WETLANDS: 0.9,
                BiomeType.ALPINE: 0.5,
                BiomeType.COASTAL: 0.6,
            },
            density_per_ha=45.0,
            max_slope_deg=35.0,
            road_buffer_m=(1.8, None),
            building_buffer_m=(1.5, None),
            water_buffer_m=(1.0, None),
            collision_radius_m=0.8,
            scale_range=(0.70, 1.40),
            cluster_probability=0.50,
            cluster_radius_m=8.0,
            cluster_count_range=(3, 7),
        ),

        # 4. Granite Boulders
        PlacementRule(
            prototype_id="rock_granite_boulder_01",
            category=AssetCategory.NATURE,
            subcategory=AssetSubcategory.BOULDER,
            biome_affinity={
                BiomeType.ROCKY_MOUNTAINS: 1.5,
                BiomeType.ALPINE: 1.0,
                BiomeType.DESERT: 0.7,
                BiomeType.FOREST: 0.4,
                BiomeType.COASTAL: 0.6,
            },
            density_per_ha=18.0,
            min_slope_deg=4.0,
            max_slope_deg=55.0,  # Boulders can sit on steep cliffs
            road_buffer_m=(2.5, None),
            building_buffer_m=(2.0, None),
            water_buffer_m=(0.5, None),
            collision_radius_m=1.4,
            scale_range=(0.60, 1.50),
            align_to_normal=True,
            max_tilt_deg=25.0,
            cluster_probability=0.30,
            cluster_radius_m=10.0,
            cluster_count_range=(2, 5),
        ),

        # 5. Pebble / Shore Stone Clusters
        PlacementRule(
            prototype_id="stone_pebble_cluster_01",
            category=AssetCategory.NATURE,
            subcategory=AssetSubcategory.STONE,
            biome_affinity={
                BiomeType.COASTAL: 1.4,
                BiomeType.WETLANDS: 1.0,
                BiomeType.ROCKY_MOUNTAINS: 0.8,
                BiomeType.DESERT: 0.9,
            },
            density_per_ha=30.0,
            max_slope_deg=25.0,
            road_buffer_m=(1.2, None),
            building_buffer_m=(1.0, None),
            water_buffer_m=(0.0, 50.0),  # Favors close proximity to water
            collision_radius_m=0.5,
            scale_range=(0.70, 1.30),
            align_to_normal=True,
            cluster_probability=0.45,
            cluster_radius_m=6.0,
            cluster_count_range=(3, 8),
        ),

        # 6. Urban Street Lamp Posts (linear along roads)
        PlacementRule(
            prototype_id="lamp_post_urban_01",
            category=AssetCategory.INFRASTRUCTURE,
            subcategory=AssetSubcategory.LAMP_POST,
            biome_affinity={
                BiomeType.URBAN: 2.0,
                BiomeType.GRASSLAND: 0.8,
                BiomeType.FOREST: 0.6,
                BiomeType.COASTAL: 0.8,
            },
            density_per_ha=35.0,
            max_slope_deg=14.0,
            road_buffer_m=(0.6, 2.8),     # Strictly roadside corridor
            building_buffer_m=(1.0, None),
            collision_radius_m=0.4,
            scale_range=(0.95, 1.05),
            random_yaw=False,              # Aligned facing perpendicular to street
            cluster_probability=0.0,
        ),

        # 7. Park Benches
        PlacementRule(
            prototype_id="bench_park_01",
            category=AssetCategory.INFRASTRUCTURE,
            subcategory=AssetSubcategory.BENCH,
            biome_affinity={
                BiomeType.URBAN: 1.5,
                BiomeType.GRASSLAND: 0.8,
                BiomeType.FOREST: 0.5,
                BiomeType.COASTAL: 0.7,
            },
            density_per_ha=12.0,
            max_slope_deg=10.0,
            road_buffer_m=(1.2, 5.0),
            building_buffer_m=(1.5, None),
            collision_radius_m=0.9,
            scale_range=(0.95, 1.05),
            cluster_probability=0.15,
            cluster_radius_m=6.0,
            cluster_count_range=(1, 2),
        ),

        # 8. Ancient Stone Ruins (POIs)
        PlacementRule(
            prototype_id="ruin_stone_colonnade_01",
            category=AssetCategory.ENVIRONMENT_PROPS,
            subcategory=AssetSubcategory.RUIN,
            biome_affinity={
                BiomeType.DENSE_FOREST: 0.8,
                BiomeType.DESERT: 1.0,
                BiomeType.ALPINE: 0.7,
                BiomeType.FOREST: 0.5,
            },
            density_per_ha=0.4,  # Sparse POI (e.g. 1-2 per square kilometer)
            max_slope_deg=12.0,  # Flat clearing required
            road_buffer_m=(20.0, None),   # Secluded away from modern roads
            building_buffer_m=(25.0, None),
            water_buffer_m=(10.0, None),
            collision_radius_m=2.8,
            scale_range=(0.90, 1.20),
            cluster_probability=0.0,
        ),
    ]
