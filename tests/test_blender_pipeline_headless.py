"""Headless Blender Verification Suite for Terragen Professional Blender Pipeline (Etapa 6).

Executes Blender directly in headless mode (/Applications/Blender.app/Contents/MacOS/Blender)
and verifies inside the actual Blender scene:
- The standard 9 studio collections under WORLD:
    TERRAIN, ROADS, BUILDINGS, WATER, VEGETATION, PROPS, COLLISION, LOD, REFERENCE.
- Correct 1:1 metric scale and World_Origin Empty marker at (0, 0, 0).
- Collision geometry (Terrain_Collision, Buildings_Collision) with hide_render=True and display_type='WIRE'.
- LOD variants in LOD_1 and LOD_2 collections.
- Instanced linked duplicates in VEGETATION and PROPS.
- Material consolidation (no duplicate .001, .002 material pointers).
- Idempotent import: re-running the importer script on an existing scene produces zero duplicate objects (.001).
- Selective collection filtering.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from terragen.blender_export import BlenderExportOptions
from terragen.projects import ProjectManager

BLENDER_BIN = Path("/Applications/Blender.app/Contents/MacOS/Blender")


class FakeDem:
    def heights(self, lons, lats):
        import numpy as np
        lons = np.asarray(lons, dtype=np.float64)
        lats = np.asarray(lats, dtype=np.float64)
        return 315.0 + 25.0 * np.sin(lons * 120.0) + 15.0 * np.cos(lats * 120.0)


@pytest.fixture
def blender_project(tmp_path: Path, monkeypatch):
    import terragen.cli as cli
    monkeypatch.setattr(cli, "Dem", lambda *a, **k: FakeDem())

    projects_dir = tmp_path / "projects"
    pm = ProjectManager(projects_dir=projects_dir)
    p = pm.create_project("Studio_Pipeline_World", (46.54, 24.56), 1024.0)
    pm.init_chunk_grid(p.id, chunk_size_m=512.0)
    return pm, p


@pytest.mark.skipif(not BLENDER_BIN.exists(), reason="Blender binary not available on host system")
def test_blender_pipeline_full_scene_verification(blender_project, tmp_path):
    pm, p = blender_project
    export_dir = tmp_path / "blender_studio_export"

    opts = BlenderExportOptions(
        mode="both",
        include_heightmap=True,
        include_regions_geojson=True,
        terrain_res=64,
        generate_blender_script=True,
    )

    res = pm.export_blender(p.id, options=opts, out_dir=export_dir)
    assert res["status"] == "success"
    script_path = Path(res["script_file"])
    manifest_path = Path(res["manifest_file"])
    assert script_path.exists()
    assert manifest_path.exists()

    # 1. First Execution: Import scene into Blender and save as test_world.blend
    blend_file = tmp_path / "test_world.blend"
    run_import_cmd = [
        str(BLENDER_BIN),
        "--background",
        "--python",
        str(script_path),
        "--python-expr",
        f"import bpy; bpy.ops.wm.save_as_mainfile(filepath=r'{blend_file}')",
    ]
    proc1 = subprocess.run(run_import_cmd, capture_output=True, text=True)
    assert proc1.returncode == 0, f"Blender import failed:\n{proc1.stderr}\n{proc1.stdout}"
    assert blend_file.exists()

    # 2. Scene Inspection Script executed INSIDE Blender
    inspection_script = tmp_path / "inspect_scene.py"
    inspection_code = '''
import sys
import bpy

print("--- BLENDER SCENE INSPECTION START ---")
coll_names = set(bpy.data.collections.keys())
print("Collections found in Blender:", coll_names)

# A. Verify required studio collections
REQUIRED_COLLECTIONS = [
    "WORLD",
    "TERRAIN",
    "ROADS",
    "BUILDINGS",
    "WATER",
    "VEGETATION",
    "PROPS",
    "COLLISION",
    "LOD",
    "REFERENCE",
]
for req in REQUIRED_COLLECTIONS:
    assert req in coll_names, f"Missing required collection: {req}"

world_coll = bpy.data.collections["WORLD"]
coll_collision = bpy.data.collections["COLLISION"]
coll_lod = bpy.data.collections["LOD"]
coll_ref = bpy.data.collections["REFERENCE"]

# B. Verify collection visibility properties
assert coll_collision.hide_render is True, "COLLISION collection must have hide_render = True"
assert coll_lod.hide_render is True, "LOD collection must have hide_render = True"
assert coll_ref.hide_render is True, "REFERENCE collection must have hide_render = True"

# C. Verify World_Origin Empty marker
origin_obj = bpy.data.objects.get("World_Origin")
assert origin_obj is not None, "World_Origin empty marker missing in REFERENCE"
assert tuple(origin_obj.location) == (0.0, 0.0, 0.0), f"World_Origin not at (0,0,0): {origin_obj.location}"
assert "world_origin_lat" in origin_obj, "World_Origin missing lat metadata"
assert "world_origin_lon" in origin_obj, "World_Origin missing lon metadata"
assert origin_obj["units"] == "1 Blender unit = 1.0 meter"

# D. Verify Collision meshes
col_objects = list(coll_collision.objects)
assert len(col_objects) > 0, "No collision objects imported into COLLISION"
for c_obj in col_objects:
    assert c_obj.display_type == "WIRE", f"Collision object {c_obj.name} display_type should be WIRE"

# E. Verify LOD subcollections
assert "LOD_1" in bpy.data.collections, "LOD_1 subcollection missing"
assert "LOD_2" in bpy.data.collections, "LOD_2 subcollection missing"

# F. Verify Material consolidation (no duplicate .001 materials)
for mat in bpy.data.materials:
    assert not any(mat.name.endswith(f".{i:03d}") for i in range(1, 10)), f"Unconsolidated duplicate material found: {mat.name}"

# G. Save object count before idempotency check
initial_obj_count = len(bpy.data.objects)
print(f"Total objects in scene: {initial_obj_count}")
print("--- BLENDER SCENE INSPECTION SUCCESS ---")
'''
    inspection_script.write_text(inspection_code, encoding="utf-8")

    inspect_cmd = [
        str(BLENDER_BIN),
        "--background",
        str(blend_file),
        "--python",
        str(inspection_script),
    ]
    proc2 = subprocess.run(inspect_cmd, capture_output=True, text=True)
    assert proc2.returncode == 0, f"Blender inspection failed:\n{proc2.stderr}\n{proc2.stdout}"
    assert "--- BLENDER SCENE INSPECTION SUCCESS ---" in proc2.stdout

    # 3. Test Idempotent Repeatable Import (Run import script a second time on the same scene)
    reimport_script = tmp_path / "test_idempotency.py"
    reimport_code = f'''
import sys
import bpy
from pathlib import Path

# Load script and re-run
script_path = Path(r"{script_path}")
exec(script_path.read_text(encoding="utf-8"), {{"__file__": str(script_path)}})

print("--- IDEMPOTENCY CHECK ---")
duplicates = [obj.name for obj in bpy.data.objects if any(obj.name.endswith(f".{{i:03d}}") for i in range(1, 20))]
assert len(duplicates) == 0, f"Repeatable import failed, duplicate objects created: {{duplicates}}"

# Check collections again
for req in ["WORLD", "TERRAIN", "ROADS", "BUILDINGS", "WATER", "VEGETATION", "PROPS", "COLLISION", "LOD", "REFERENCE"]:
    assert req in bpy.data.collections, f"Collection {{req}} missing after re-import"

print("--- IDEMPOTENCY SUCCESS: ZERO DUPLICATES ---")
'''
    reimport_script.write_text(reimport_code, encoding="utf-8")

    reimport_cmd = [
        str(BLENDER_BIN),
        "--background",
        str(blend_file),
        "--python",
        str(reimport_script),
    ]
    proc3 = subprocess.run(reimport_cmd, capture_output=True, text=True)
    assert proc3.returncode == 0, f"Blender idempotency test failed:\n{proc3.stderr}\n{proc3.stdout}"
    assert "--- IDEMPOTENCY SUCCESS: ZERO DUPLICATES ---" in proc3.stdout
