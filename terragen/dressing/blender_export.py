"""Blender Export Utilities for Procedural World Dressing Instances & Collections."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .catalog import AssetCatalog
from .models import InstanceTransform


def export_world_dressing_for_blender(
    instances: list[InstanceTransform],
    catalog: AssetCatalog,
    output_dir: Path | str,
    stem: str = "world_dressing",
) -> dict[str, Any]:
    """Prepares and exports the complete world dressing package for Blender.

    1. Saves unique prototype models as GLB assets into <output_dir>/assets/prototypes/.
    2. Writes instance transform tables grouped by Blender Collections.
    3. Returns metadata for embedding in blender_manifest.json.
    """
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    prototypes_dir = out_path / "assets" / "prototypes"
    prototypes_dir.mkdir(parents=True, exist_ok=True)

    # 1. Export unique prototypes
    proto_files = catalog.export_prototype_glbs(prototypes_dir)

    # 2. Group instances by category and subcategory for clean Blender Collections
    collections_data: dict[str, list[dict[str, Any]]] = {}

    category_counts: dict[str, int] = {}
    subcategory_counts: dict[str, int] = {}

    for inst in instances:
        # e.g. "Nature/Trees", "Infrastructure/Street_Lighting"
        cat_name = inst.category.value.capitalize()
        subcat_name = inst.subcategory.value.replace("_", " ").title()
        coll_key = f"{cat_name}_{subcat_name}"

        if coll_key not in collections_data:
            collections_data[coll_key] = []
        collections_data[coll_key].append(inst.to_dict())

        category_counts[inst.category.value] = category_counts.get(inst.category.value, 0) + 1
        subcategory_counts[inst.subcategory.value] = subcategory_counts.get(inst.subcategory.value, 0) + 1

    manifest_data = {
        "generator": "Terragen Game World Generator - World Dressing Engine",
        "total_instances": len(instances),
        "category_counts": category_counts,
        "subcategory_counts": subcategory_counts,
        "prototypes_dir": str(prototypes_dir.relative_to(out_path)),
        "prototypes": [
            {
                "id": p.id,
                "name": p.name,
                "category": p.category.value,
                "subcategory": p.subcategory.value,
                "file": str(Path(proto_files[p.id]).relative_to(out_path)) if p.id in proto_files else None,
                "collision_radius_m": p.collision_radius_m,
                "height_m": p.height_m,
            }
            for p in catalog.list_all()
            if p.id in {inst.prototype_id for inst in instances}
        ],
        "collections": collections_data,
    }

    manifest_file = out_path / f"{stem}_manifest.json"
    manifest_file.write_text(json.dumps(manifest_data, indent=2), encoding="utf-8")

    return {
        "manifest_file": str(manifest_file),
        "total_instances": len(instances),
        "category_counts": category_counts,
        "collections": list(collections_data.keys()),
    }
