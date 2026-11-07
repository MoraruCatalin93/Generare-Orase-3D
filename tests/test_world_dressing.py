"""Tests for Terragen Procedural World Dressing & Object Instancing Engine (Etapa 4)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from terragen.dressing.blender_export import export_world_dressing_for_blender
from terragen.dressing.catalog import AssetCatalog
from terragen.dressing.engine import WorldDressingEngine
from terragen.dressing.models import (
    AssetCategory,
    AssetPrototype,
    AssetSubcategory,
    InstanceTransform,
    PlacementRule,
)
from terragen.dressing.spatial_index import SpatialHashGrid
from terragen.osmdata import Way
from terragen.project import LocalProjection
from terragen.projects import ChunkEngine, ProjectManager


class SyntheticDem:
    """Deterministic continuous elevation model for testing."""

    def heights(self, lons, lats) -> np.ndarray:
        lons = np.asarray(lons, dtype=np.float64)
        lats = np.asarray(lats, dtype=np.float64)
        return 300.0 + 30.0 * np.sin(lons * 150.0) + 20.0 * np.cos(lats * 150.0)


class TestAssetCatalog:
    """Verifies prototype library, procedural mesh generation, and GLB asset export."""

    def test_default_catalog_contains_standard_prototypes(self):
        catalog = AssetCatalog()
        prototypes = catalog.list_all()
        assert len(prototypes) >= 8

        ids = {p.id for p in prototypes}
        expected = {
            "tree_pine_01",
            "tree_deciduous_01",
            "bush_temperate_01",
            "rock_granite_boulder_01",
            "stone_pebble_cluster_01",
            "lamp_post_urban_01",
            "bench_park_01",
            "ruin_stone_colonnade_01",
        }
        assert expected.issubset(ids)

    def test_prototype_mesh_validity(self):
        catalog = AssetCatalog()
        for p in catalog.list_all():
            assert p.mesh is not None
            assert len(p.mesh.v) >= 4
            assert len(p.mesh.f) >= 4
            assert p.collision_radius_m > 0.0
            assert p.height_m > 0.0

    def test_prototype_glb_export(self, tmp_path: Path):
        catalog = AssetCatalog()
        out_dir = tmp_path / "prototypes"
        file_map = catalog.export_prototype_glbs(out_dir)

        assert len(file_map) >= 8
        for file_path in file_map.values():
            path_obj = Path(file_path)
            assert path_obj.exists()
            assert path_obj.suffix.lower() == ".glb"
            assert path_obj.stat().st_size > 300  # valid non-empty GLB

    def test_custom_prototype_registration(self):
        catalog = AssetCatalog()
        custom = AssetPrototype(
            id="custom_monument",
            name="Ancient Obelisk",
            category=AssetCategory.ENVIRONMENT_PROPS,
            subcategory=AssetSubcategory.POI,
            collision_radius_m=2.0,
            height_m=7.0,
        )
        catalog.register(custom)
        assert catalog.get("custom_monument") is not None
        assert catalog.get("custom_monument").name == "Ancient Obelisk"


class TestSpatialHashGrid:
    """Verifies O(1) collision detection and overlap prevention."""

    def test_spatial_collision_detection(self):
        grid = SpatialHashGrid(cell_size_m=4.0)
        assert grid.can_place(0.0, 0.0, radius=1.0)

        grid.insert(0.0, 0.0, radius=1.5)
        assert grid.count == 1

        # Point at (1.0, 0.0) with radius 1.0 collides (dist=1.0 < 1.5 + 1.0 = 2.5)
        assert not grid.can_place(1.0, 0.0, radius=1.0)

        # Point at (20.0, 20.0) is far away -> can place
        assert grid.can_place(20.0, 20.0, radius=1.0)

    def test_spatial_grid_clearing(self):
        grid = SpatialHashGrid(cell_size_m=4.0)
        grid.insert(5.0, 5.0, radius=2.0)
        grid.insert(25.0, 25.0, radius=2.0)
        assert grid.count == 2

        grid.clear()
        assert grid.count == 0
        assert grid.can_place(5.0, 5.0, radius=2.0)


class TestWorldDressingEngine:
    """Verifies biome-aware scattering, roadside placement, clearance, determinism, and clustering."""

    def test_bounds_population_and_biome_affinity(self):
        proj = LocalProjection(46.54, 24.56)
        dem = SyntheticDem()
        engine = WorldDressingEngine(seed=42)

        instances = engine.populate_bounds(
            bounds_m=(-150.0, -150.0, 150.0, 150.0),
            dem=dem,
            proj=proj,
        )

        assert len(instances) > 0
        cats = {inst.category for inst in instances}
        assert AssetCategory.NATURE in cats

    def test_road_and_building_clearance(self):
        proj = LocalProjection(46.54, 24.56)
        dem = SyntheticDem()
        engine = WorldDressingEngine(seed=101)

        # Build a synthetic road crossing east-west at lat=24.56
        lon_a, lat_a = proj.to_lonlat(-100.0, 0.0)
        lon_b, lat_b = proj.to_lonlat(100.0, 0.0)
        road_way = Way(
            id=101,
            tags={"highway": "secondary"},
            coords=[(lon_a, lat_a), (lon_b, lat_b)],
        )

        # Build a synthetic building footprint around (50, 50)
        b_pts = [
            proj.to_lonlat(40.0, 40.0),
            proj.to_lonlat(60.0, 40.0),
            proj.to_lonlat(60.0, 60.0),
            proj.to_lonlat(40.0, 60.0),
            proj.to_lonlat(40.0, 40.0),
        ]
        building_way = Way(
            id=202,
            tags={"building": "yes"},
            coords=b_pts,
        )

        instances = engine.populate_bounds(
            bounds_m=(-100.0, -100.0, 100.0, 100.0),
            dem=dem,
            proj=proj,
            ways=[road_way, building_way],
        )

        for inst in instances:
            # 1. Nature assets must not clip into road centerline
            if inst.category == AssetCategory.NATURE:
                dist_to_road = abs(inst.z - 0.0)
                assert dist_to_road >= 2.5, f"Nature asset placed too close to road: dist={dist_to_road}"

            # 2. Objects must not spawn inside building interior
            in_bld = (40.0 <= inst.x <= 60.0) and (40.0 <= inst.z <= 60.0)
            assert not in_bld, f"Asset spawned inside building bounds at ({inst.x}, {inst.z})"

    def test_roadside_infrastructure_placement(self):
        proj = LocalProjection(46.54, 24.56)
        dem = SyntheticDem()
        engine = WorldDressingEngine(seed=777)

        lon_a, lat_a = proj.to_lonlat(-150.0, 30.0)
        lon_b, lat_b = proj.to_lonlat(150.0, 30.0)
        road_way = Way(
            id=303,
            tags={"highway": "primary"},
            coords=[(lon_a, lat_a), (lon_b, lat_b)],
        )

        instances = engine.populate_bounds(
            bounds_m=(-150.0, -50.0, 150.0, 100.0),
            dem=dem,
            proj=proj,
            ways=[road_way],
        )

        infra_instances = [inst for inst in instances if inst.category == AssetCategory.INFRASTRUCTURE]
        assert len(infra_instances) > 0

        for inst in infra_instances:
            # Offset ~2m from road centerline at z=30.0
            dist_to_road = abs(inst.z - 30.0)
            assert dist_to_road <= 4.0, f"Infrastructure misplaced: dist={dist_to_road}"

    def test_deterministic_seeding(self):
        proj = LocalProjection(46.54, 24.56)
        dem = SyntheticDem()

        engine_a = WorldDressingEngine(seed=12345)
        inst_a = engine_a.populate_bounds((-100.0, -100.0, 100.0, 100.0), dem=dem, proj=proj)

        engine_b = WorldDressingEngine(seed=12345)
        inst_b = engine_b.populate_bounds((-100.0, -100.0, 100.0, 100.0), dem=dem, proj=proj)

        assert len(inst_a) == len(inst_b)
        for a, b in zip(inst_a, inst_b, strict=False):
            assert a.prototype_id == b.prototype_id
            assert np.isclose(a.x, b.x)
            assert np.isclose(a.y, b.y)
            assert np.isclose(a.z, b.z)
            assert np.isclose(a.scale_x, b.scale_x)
            assert np.isclose(a.rot_y_deg, b.rot_y_deg)

        # Different seed produces distinct placements
        engine_c = WorldDressingEngine(seed=99999)
        inst_c = engine_c.populate_bounds((-100.0, -100.0, 100.0, 100.0), dem=dem, proj=proj)
        assert [round(i.x, 2) for i in inst_a[:5]] != [round(i.x, 2) for i in inst_c[:5]]

    def test_natural_clustering_behavior(self):
        proj = LocalProjection(46.54, 24.56)
        dem = SyntheticDem()
        catalog = AssetCatalog()

        # Rule with 100% clustering probability and high child count
        cluster_rule = PlacementRule(
            prototype_id="bush_temperate_01",
            category=AssetCategory.NATURE,
            subcategory=AssetSubcategory.BUSH,
            density_per_ha=10.0,
            cluster_probability=1.0,
            cluster_count_range=(3, 6),
            cluster_radius_m=10.0,
        )

        engine = WorldDressingEngine(seed=42, catalog=catalog, rules=[cluster_rule])
        instances = engine.populate_bounds((-50.0, -50.0, 50.0, 50.0), dem=dem, proj=proj)

        # Clustered spawning produces multiple instances per grid cell
        assert len(instances) >= 3


class TestBlenderDressingExport:
    """Verifies Blender export format, linked duplicate structures, and manifest output."""

    def test_export_world_dressing_for_blender(self, tmp_path: Path):
        catalog = AssetCatalog()
        instances = [
            InstanceTransform(
                prototype_id="tree_pine_01",
                category=AssetCategory.NATURE,
                subcategory=AssetSubcategory.TREE,
                x=10.0,
                y=2.5,
                z=15.0,
                scale_x=1.1,
                scale_y=1.1,
                scale_z=1.1,
                rot_y_deg=45.0,
            ),
            InstanceTransform(
                prototype_id="lamp_post_urban_01",
                category=AssetCategory.INFRASTRUCTURE,
                subcategory=AssetSubcategory.LAMP_POST,
                x=20.0,
                y=1.0,
                z=25.0,
                scale_x=1.0,
                scale_y=1.0,
                scale_z=1.0,
                rot_y_deg=90.0,
            ),
        ]

        result = export_world_dressing_for_blender(
            instances=instances,
            catalog=catalog,
            output_dir=tmp_path,
            stem="world_dressing_test",
        )

        assert result["total_instances"] == 2
        assert "Nature_Tree" in result["collections"]
        assert "Infrastructure_Lamp Post" in result["collections"]

        manifest_path = Path(result["manifest_file"])
        assert manifest_path.exists()

        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert data["total_instances"] == 2
        assert len(data["prototypes"]) == 2
        assert "Nature_Tree" in data["collections"]
        assert len(data["collections"]["Nature_Tree"]) == 1

        proto_dir = tmp_path / "assets" / "prototypes"
        assert proto_dir.exists()
        assert (proto_dir / "tree_pine_01.glb").exists()
        assert (proto_dir / "lamp_post_urban_01.glb").exists()


class TestChunkEngineDressingIntegration:
    """Verifies chunk-level world dressing generation and manifest recording."""

    def test_chunk_dressing_population(self, tmp_path: Path):
        mgr = ProjectManager(tmp_path)
        proj = mgr.create_project("Chunk Dressing Test", center=(46.5446, 24.5612), size=1024.0)
        engine = ChunkEngine(proj, project_dir=tmp_path / proj.slug)
        engine.init_grid(chunk_size_m=512.0, world_size_x_m=1024.0, world_size_y_m=1024.0)

        dem = SyntheticDem()
        instances = engine.populate_chunk_dressing(
            chunk_id="chk_r01_c01",
            dem=dem,
            density_multiplier=1.0,
        )

        assert isinstance(instances, list)
        assert len(instances) > 0
        for inst in instances:
            assert isinstance(inst, InstanceTransform)
            assert inst.prototype_id != ""
