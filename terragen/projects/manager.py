"""Project lifecycle manager: Create, Open, Save, Save As, Duplicate, Rename, Delete."""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

from .migration import CURRENT_SCHEMA_VERSION
from .model import (
    CoordinateSystem,
    GeographicBounds,
    GeographicLocation,
    Project,
    Region,
    TerrainSettings,
    WorldDimensions,
)
from .regions import (
    DEFAULT_REGION_COLORS,
    calculate_region_metrics,
    detect_overlaps,
    duplicate_region as geom_duplicate_region,
    merge_regions as geom_merge_regions,
    split_region as geom_split_region,
    to_shapely_polygon,
)
from .validation import CorruptedProjectError, ProjectValidationError, validate_project_dict

PROJECT_FILE_NAME = "project.json"
BACKUP_FILE_NAME = "project.json.bak"


import unicodedata

def safe_slug(name: str) -> str:
    name = (name or "").strip() or "project"
    # Transliterate unicode characters (e.g. â, ș, î, ț -> a, s, i, t)
    norm = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^\w\-. ]+", "", norm).strip().replace(" ", "-")
    return slug[:48].lower() or "project"


class ProjectManager:
    """Manages persistent Terragen projects in the filesystem."""

    def __init__(self, projects_dir: str | Path | None = None):
        if projects_dir:
            self.root = Path(projects_dir).resolve()
        else:
            env = os.environ.get("TERRAGEN_PROJECTS")
            self.root = (
                Path(env).resolve() if env else (Path.cwd() / "projects").resolve()
            )
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve_project_dir(self, identifier: str | Path) -> Path:
        """Resolves project directory from ID, slug, or direct Path."""
        p = Path(identifier)
        if p.is_dir() and (p / PROJECT_FILE_NAME).exists():
            return p.resolve()
        if p.is_file() and p.name in (PROJECT_FILE_NAME, BACKUP_FILE_NAME):
            return p.parent.resolve()

        ident_str = str(identifier).strip()
        # Direct check in root by folder name
        direct_candidate = (self.root / ident_str).resolve()
        if direct_candidate.is_dir() and (
            (direct_candidate / PROJECT_FILE_NAME).exists()
            or (direct_candidate / BACKUP_FILE_NAME).exists()
        ):
            return direct_candidate

        # Check by slug
        candidate = (self.root / safe_slug(ident_str)).resolve()
        if candidate.is_dir() and (
            (candidate / PROJECT_FILE_NAME).exists()
            or (candidate / BACKUP_FILE_NAME).exists()
        ):
            return candidate

        # Check by id across all projects, inspecting both project.json and project.json.bak
        target_id = ident_str
        corrupted_candidates = []
        for d in self.root.iterdir():
            if not d.is_dir():
                continue
            for fname in (PROJECT_FILE_NAME, BACKUP_FILE_NAME):
                fpath = d / fname
                if not fpath.exists():
                    continue
                try:
                    raw = json.loads(fpath.read_text(encoding="utf-8"))
                    if raw.get("id") == target_id or raw.get("slug") == target_id:
                        return d.resolve()
                except Exception:
                    corrupted_candidates.append(d)

        # If project.json was completely overwritten with invalid data and target_id is directory name
        for cd in corrupted_candidates:
            if cd.name == target_id or cd.name == safe_slug(target_id):
                return cd.resolve()

        raise FileNotFoundError(f"Project '{identifier}' not found in {self.root}")


    def list_projects(self) -> list[dict[str, Any]]:
        """Returns summary list of all available projects."""
        projects = []
        if not self.root.exists():
            return projects

        for d in sorted(self.root.iterdir()):
            if not d.is_dir():
                continue
            pf = d / PROJECT_FILE_NAME
            if not pf.exists():
                continue
            try:
                data = json.loads(pf.read_text(encoding="utf-8"))
                geo = data.get("geographic", {})
                center = geo.get("center", {})
                dims = data.get("dimensions", {})
                projects.append(
                    {
                        "id": data.get("id"),
                        "name": data.get("name"),
                        "slug": data.get("slug") or d.name,
                        "description": data.get("description", ""),
                        "schema_version": data.get("schema_version", 1),
                        "created_at": data.get("created_at"),
                        "updated_at": data.get("updated_at"),
                        "center": center,
                        "size_m": dims.get("size_x_m", 1500.0),
                        "path": str(d.resolve()),
                    }
                )
            except Exception:
                continue

        return sorted(projects, key=lambda p: str(p.get("updated_at", "")), reverse=True)

    def create_project(
        self,
        name: str,
        center: tuple[float, float],
        size: float = 1500.0,
        description: str = "",
        seed: int = 7,
        terrain_res: int = 256,
        dem_zoom: int = 15,
        formats: list[str] | tuple[str, ...] = ("glb",),
        roofs: bool = True,
        extra_tags: dict[str, Any] | None = None,
    ) -> Project:
        """Creates and persists a new Project."""
        lat, lon = float(center[0]), float(center[1])
        if not (-85.0 <= lat <= 85.0 and -180.0 <= lon <= 180.0):
            raise ProjectValidationError(f"Invalid center coordinates: {lat}, {lon}")
        if size <= 0:
            raise ProjectValidationError(f"Project size must be positive, got {size}")

        half = size / 2.0
        dlat = half / 111320.0
        dlon = half / (111320.0 * max(0.01, math.cos(math.radians(lat))))
        bounds = GeographicBounds(
            south=lat - dlat, west=lon - dlon, north=lat + dlat, east=lon + dlon
        )
        geo = GeographicLocation(center_lat=lat, center_lon=lon, bounds=bounds)

        proj_id = uuid.uuid4().hex
        slug = safe_slug(name)

        # Ensure directory uniqueness
        target_dir = self.root / slug
        suffix = 1
        while target_dir.exists():
            target_dir = self.root / f"{slug}-{suffix}"
            suffix += 1

        project = Project(
            id=proj_id,
            name=name,
            slug=target_dir.name,
            description=description,
            seed=seed,
            geographic=geo,
            dimensions=WorldDimensions(size_x_m=size, size_y_m=size),
            terrain=TerrainSettings(
                resolution=terrain_res, dem_zoom=dem_zoom, step_m=size / max(1, terrain_res)
            ),
            generation=Project.from_dict({"name": name, "formats": list(formats), "roofs": roofs}).generation,
            extra_metadata=extra_tags or {},
        )

        self.save_project(project, target_dir)
        return project

    def open_project(self, identifier: str | Path) -> Project:
        """Loads and validates a project from directory, slug, or file path."""
        p_dir = self._resolve_project_dir(identifier)
        pf = p_dir / PROJECT_FILE_NAME
        bak = p_dir / BACKUP_FILE_NAME

        raw_text = None
        is_recovered = False
        try:
            raw_text = pf.read_text(encoding="utf-8")
            data = json.loads(raw_text)
        except Exception as primary_exc:
            # Attempt recovery from backup
            if bak.exists():
                try:
                    data = json.loads(bak.read_text(encoding="utf-8"))
                    is_recovered = True
                except Exception:
                    raise CorruptedProjectError(
                        f"Project at {p_dir} is corrupted and backup is unreadable: {primary_exc}"
                    ) from primary_exc
            else:
                raise CorruptedProjectError(
                    f"Project at {p_dir} is corrupted and no backup exists: {primary_exc}"
                ) from primary_exc

        project = Project.from_dict(data)

        # If project was recovered from backup, save repaired version
        if is_recovered:
            self.save_project(project, p_dir)

        return project

    def save_project(self, project: Project, target_dir: str | Path | None = None) -> Path:
        """Saves project to disk atomically with backup creation."""
        if target_dir:
            dest_dir = Path(target_dir).resolve()
        else:
            dest_dir = (self.root / project.slug).resolve()

        dest_dir.mkdir(parents=True, exist_ok=True)
        pf = dest_dir / PROJECT_FILE_NAME
        bak = dest_dir / BACKUP_FILE_NAME

        project.touch()
        payload = json.dumps(project.to_dict(), indent=2, ensure_ascii=False)

        # 1. Update backup if valid project file already exists
        if pf.exists():
            try:
                shutil.copy2(pf, bak)
            except OSError:
                pass

        # 2. Atomic write using temporary file in the same directory
        temp_file = dest_dir / f".{PROJECT_FILE_NAME}.tmp"
        try:
            temp_file.write_text(payload, encoding="utf-8")
            temp_file.replace(pf)
        except Exception:
            if temp_file.exists():
                temp_file.unlink(missing_ok=True)
            raise

        return pf

    def save_project_as(
        self, project: Project, new_name: str, new_slug: str | None = None
    ) -> Project:
        """Clones project with a new name, ID, and slug."""
        if not new_name.strip():
            raise ProjectValidationError("New project name cannot be empty")

        slug = safe_slug(new_slug or new_name)
        target_dir = self.root / slug
        suffix = 1
        while target_dir.exists():
            target_dir = self.root / f"{slug}-{suffix}"
            suffix += 1

        clone_dict = project.to_dict()
        clone_dict["id"] = uuid.uuid4().hex
        clone_dict["name"] = new_name.strip()
        clone_dict["slug"] = target_dir.name
        clone_dict["created_at"] = ""
        clone_dict["updated_at"] = ""

        clone = Project.from_dict(clone_dict)
        self.save_project(clone, target_dir)
        return clone

    def duplicate_project(self, identifier: str | Path, new_name: str | None = None) -> Project:
        """Duplicates an existing project."""
        original = self.open_project(identifier)
        name = new_name or f"{original.name} (Copy)"
        return self.save_project_as(original, name)

    def rename_project(self, identifier: str | Path, new_name: str) -> Project:
        """Renames a project, updating its name and slug."""
        project = self.open_project(identifier)
        if not new_name.strip():
            raise ProjectValidationError("Project name cannot be empty")

        old_dir = self._resolve_project_dir(identifier)
        project.name = new_name.strip()
        new_slug = safe_slug(new_name)
        project.slug = new_slug

        new_dir = self.root / new_slug
        if new_dir != old_dir:
            if new_dir.exists():
                new_dir = self.root / f"{new_slug}-{uuid.uuid4().hex[:6]}"
                project.slug = new_dir.name
            old_dir.rename(new_dir)
            self.save_project(project, new_dir)
        else:
            self.save_project(project, old_dir)

        return project

    def delete_project(self, identifier: str | Path, confirm: bool = False) -> bool:
        """Deletes a project directory. Requires explicit confirmation."""
        if not confirm:
            raise ValueError(
                "Deletion requires explicit confirmation: delete_project(..., confirm=True)"
            )

        p_dir = self._resolve_project_dir(identifier)
        # Never delete projects root or outside projects root
        if p_dir == self.root or self.root not in p_dir.parents:
            raise ValueError("Cannot delete directory outside projects root")

        shutil.rmtree(p_dir)
        return True

    # -------------------------------------------------------------------------
    # Region Management
    # -------------------------------------------------------------------------

    def get_regions(self, identifier: str | Path) -> list[Region]:
        """Returns all regions associated with a project."""
        project = self.open_project(identifier)
        return list(project.regions)

    def get_region(self, identifier: str | Path, region_id: str) -> Region:
        """Finds a specific region by ID in a project."""
        project = self.open_project(identifier)
        for r in project.regions:
            if r.id == region_id:
                return r
        raise KeyError(f"Region '{region_id}' not found in project '{project.name}'.")

    def add_region(
        self,
        identifier: str | Path,
        region_data: dict[str, Any] | Region,
    ) -> Region:
        """Adds a new region to a project, computes metrics, and persists project."""
        project = self.open_project(identifier)
        p_dir = self._resolve_project_dir(identifier)

        if isinstance(region_data, Region):
            reg = region_data
        else:
            reg = Region.from_dict(region_data)

        if not reg.id:
            reg.id = f"reg_{uuid.uuid4().hex[:8]}"

        # Calculate metrics if polygon provided
        if reg.polygon and len(reg.polygon) >= 3:
            proj_origin = (project.geographic.center_lat, project.geographic.center_lon)
            geo_metrics, projected_coords = calculate_region_metrics(
                reg.polygon, reg.holes, proj_origin=proj_origin
            )
            reg.geographic = geo_metrics
            reg.projected = projected_coords

        # Ensure unique ID within project
        existing_ids = {r.id for r in project.regions}
        if reg.id in existing_ids:
            reg.id = f"{reg.id}_{uuid.uuid4().hex[:4]}"

        project.regions.append(reg)
        self.save_project(project, p_dir)
        return reg

    def update_region(
        self,
        identifier: str | Path,
        region_id: str,
        update_data: dict[str, Any],
    ) -> Region:
        """Updates region properties, recalculating metrics if geometry changes."""
        project = self.open_project(identifier)
        p_dir = self._resolve_project_dir(identifier)

        target: Region | None = None
        for r in project.regions:
            if r.id == region_id:
                target = r
                break

        if not target:
            raise KeyError(f"Region '{region_id}' not found in project '{project.name}'.")

        # Check lock status if modifying geometry
        is_geom_change = "polygon" in update_data or "holes" in update_data
        if target.locked and is_geom_change and not update_data.get("override_lock", False):
            raise ValueError(f"Region '{target.name}' is locked. Unlock it to modify geometry.")

        # Apply updates
        if "name" in update_data and update_data["name"]:
            target.name = str(update_data["name"]).strip()
        if "region_type" in update_data:
            target.region_type = str(update_data["region_type"]).lower()
            if "color" not in update_data and target.color == DEFAULT_REGION_COLORS.get("urban", "#e74c3c"):
                target.color = DEFAULT_REGION_COLORS.get(target.region_type, target.color)
        if "color" in update_data and update_data["color"]:
            target.color = str(update_data["color"]).strip()
        if "description" in update_data:
            target.description = str(update_data["description"])
        if "style_preset" in update_data:
            target.style_preset = str(update_data["style_preset"])
        if "seed" in update_data:
            target.seed = int(update_data["seed"])
        if "generation" in update_data and isinstance(update_data["generation"], dict):
            target.generation = dict(update_data["generation"])
        if "visible" in update_data:
            target.visible = bool(update_data["visible"])
        if "enabled" in update_data:
            target.enabled = bool(update_data["enabled"])
        if "locked" in update_data:
            target.locked = bool(update_data["locked"])
        if "status" in update_data:
            target.status = str(update_data["status"])
        if "export_status" in update_data:
            target.export_status = str(update_data["export_status"])
        if "tags" in update_data and isinstance(update_data["tags"], dict):
            target.tags = dict(update_data["tags"])

        # Geometry changes
        if is_geom_change:
            new_poly = update_data.get("polygon", target.polygon)
            new_holes = update_data.get("holes", target.holes)
            target.polygon = new_poly
            target.holes = new_holes
            proj_origin = (project.geographic.center_lat, project.geographic.center_lon)
            geo_metrics, projected_coords = calculate_region_metrics(
                target.polygon, target.holes, proj_origin=proj_origin
            )
            target.geographic = geo_metrics
            target.projected = projected_coords
            # Modifying geometry marks region unbuilt
            target.status = "unbuilt"

        import datetime
        target.updated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

        self.save_project(project, p_dir)
        return target

    def delete_region(self, identifier: str | Path, region_id: str) -> bool:
        """Deletes a region from a project."""
        project = self.open_project(identifier)
        p_dir = self._resolve_project_dir(identifier)

        initial_len = len(project.regions)
        project.regions = [r for r in project.regions if r.id != region_id]
        if len(project.regions) == initial_len:
            raise KeyError(f"Region '{region_id}' not found in project '{project.name}'.")

        self.save_project(project, p_dir)
        return True

    def duplicate_region(
        self,
        identifier: str | Path,
        region_id: str,
        offset_m: tuple[float, float] = (15.0, 15.0),
    ) -> Region:
        """Duplicates an existing region with offset vertices."""
        project = self.open_project(identifier)
        p_dir = self._resolve_project_dir(identifier)

        source = next((r for r in project.regions if r.id == region_id), None)
        if not source:
            raise KeyError(f"Region '{region_id}' not found in project '{project.name}'.")

        proj_origin = (project.geographic.center_lat, project.geographic.center_lon)
        clone = geom_duplicate_region(source, offset_m=offset_m, proj_origin=proj_origin)
        project.regions.append(clone)
        self.save_project(project, p_dir)
        return clone

    def split_region(
        self,
        identifier: str | Path,
        region_id: str,
        cutting_line: list[list[float]],
    ) -> list[Region]:
        """Splits a region by a cutting line, replacing it with resulting sub-regions."""
        project = self.open_project(identifier)
        p_dir = self._resolve_project_dir(identifier)

        idx = next((i for i, r in enumerate(project.regions) if r.id == region_id), None)
        if idx is None:
            raise KeyError(f"Region '{region_id}' not found in project '{project.name}'.")

        source = project.regions[idx]
        if source.locked:
            raise ValueError(f"Region '{source.name}' is locked and cannot be split.")

        proj_origin = (project.geographic.center_lat, project.geographic.center_lon)
        sub_regions = geom_split_region(source, cutting_line, proj_origin=proj_origin)

        # Replace original region with split sub-regions
        project.regions[idx : idx + 1] = sub_regions
        self.save_project(project, p_dir)
        return sub_regions

    def merge_regions(
        self,
        identifier: str | Path,
        region_ids: list[str],
        merged_name: str | None = None,
    ) -> Region:
        """Merges multiple regions into one, removing source regions."""
        project = self.open_project(identifier)
        p_dir = self._resolve_project_dir(identifier)

        targets = [r for r in project.regions if r.id in set(region_ids)]
        if len(targets) < 2:
            raise ValueError(f"Need at least 2 matching regions to merge, found {len(targets)}.")

        for t in targets:
            if t.locked:
                raise ValueError(f"Region '{t.name}' is locked and cannot be merged.")

        proj_origin = (project.geographic.center_lat, project.geographic.center_lon)
        merged = geom_merge_regions(targets, merged_name=merged_name, proj_origin=proj_origin)

        # Remove source regions and append merged
        target_id_set = {t.id for t in targets}
        project.regions = [r for r in project.regions if r.id not in target_id_set]
        project.regions.append(merged)

        self.save_project(project, p_dir)
        return merged

    def validate_regions(self, identifier: str | Path) -> dict[str, Any]:
        """Runs geometry validation and conflict/overlap detection across project regions."""
        project = self.open_project(identifier)
        conflicts = detect_overlaps(project.regions)

        invalid_regions: list[dict[str, Any]] = []
        for r in project.regions:
            try:
                poly = to_shapely_polygon(r.polygon, r.holes)
                if poly.is_empty or poly.area <= 0:
                    invalid_regions.append({"id": r.id, "name": r.name, "reason": "Empty or zero area."})
            except Exception as e:
                invalid_regions.append({"id": r.id, "name": r.name, "reason": str(e)})

        return {
            "valid": len(conflicts) == 0 and len(invalid_regions) == 0,
            "region_count": len(project.regions),
            "conflicts": conflicts,
            "invalid_regions": invalid_regions,
        }

    # -------------------------------------------------------------------------
    # Chunk Management & Engine Integration
    # -------------------------------------------------------------------------

    def get_chunk_engine(self, identifier: str | Path) -> Any:
        """Instantiates a ChunkEngine bound to the resolved project directory."""
        from .chunks import ChunkEngine

        project = self.open_project(identifier)
        p_dir = self._resolve_project_dir(identifier)
        return ChunkEngine(project, project_dir=p_dir)

    def init_chunk_grid(
        self,
        identifier: str | Path,
        chunk_size_m: float = 512.0,
        world_size_x_m: float | None = None,
        world_size_y_m: float | None = None,
    ) -> Any:
        """Computes and persists a chunk grid for the project."""
        p_dir = self._resolve_project_dir(identifier)
        engine = self.get_chunk_engine(identifier)
        chunks_settings = engine.init_grid(
            chunk_size_m=chunk_size_m,
            world_size_x_m=world_size_x_m,
            world_size_y_m=world_size_y_m,
        )
        self.save_project(engine.project, p_dir)
        return chunks_settings

    def get_chunks(self, identifier: str | Path) -> list[Any]:
        """Returns the list of ChunkItems for the project."""
        engine = self.get_chunk_engine(identifier)
        return list(engine.project.chunks.items)

    def get_chunk(self, identifier: str | Path, chunk_id: str) -> Any | None:
        """Finds a specific ChunkItem by ID."""
        engine = self.get_chunk_engine(identifier)
        return engine.get_chunk(chunk_id)

    def generate_chunk(
        self,
        identifier: str | Path,
        chunk_id: str,
        dem: Any = None,
        ways: list[Any] | None = None,
        force: bool = False,
        style_preset: Any = None,
    ) -> dict[str, Any]:
        """Generates 3D assets for a single chunk and updates project state."""
        p_dir = self._resolve_project_dir(identifier)
        engine = self.get_chunk_engine(identifier)
        if dem is None:
            from ..dem import Dem
            ch = engine.get_chunk(chunk_id)
            if not ch:
                raise KeyError(f"Chunk '{chunk_id}' not found")
            s, w = ch.bounds.south, ch.bounds.west
            n, e = ch.bounds.north, ch.bounds.east
            dem = Dem((s, w, n, e), zoom=engine.project.terrain.dem_zoom or 15)

        if engine.project.terrain.modifiers:
            from ..terrain_editor import ModifiedDem, TerrainEditor
            editor = TerrainEditor.from_dict(
                {
                    "modifiers": engine.project.terrain.modifiers,
                    "undo_stack": engine.project.terrain.undo_stack,
                },
                base_dem=dem,
                proj=engine.proj,
                regions=engine.project.regions,
            )
            dem = ModifiedDem(base_dem=dem, editor=editor, proj=engine.proj)

        preset = style_preset or getattr(engine.project.generation, "style_preset", None)
        manifest = engine.generate_chunk(chunk_id, dem=dem, ways=ways, force=force, style_preset=preset)
        self.save_project(engine.project, p_dir)
        return manifest

    def get_terrain_editor(
        self,
        identifier: str | Path,
        base_dem: Any | None = None,
    ) -> Any:
        """Returns a TerrainEditor bound to the project's terrain modifier stack."""
        project = self.open_project(identifier)
        from ..project import LocalProjection
        from ..terrain_editor import TerrainEditor
        proj = LocalProjection(project.geographic.center_lat, project.geographic.center_lon)
        return TerrainEditor.from_dict(
            {
                "modifiers": project.terrain.modifiers,
                "undo_stack": project.terrain.undo_stack,
            },
            base_dem=base_dem,
            proj=proj,
            regions=project.regions,
        )

    def save_terrain_editor(
        self,
        identifier: str | Path,
        editor: Any,
    ) -> Project:
        """Saves active and undone terrain modifiers back to the project file."""
        p_dir = self._resolve_project_dir(identifier)
        project = self.open_project(identifier)
        state = editor.to_dict()
        project.terrain.modifiers = state["modifiers"]
        project.terrain.undo_stack = state["undo_stack"]
        self.save_project(project, p_dir)
        return project

    def export_blender_manifest(self, identifier: str | Path) -> Path:
        """Exports blender_manifest.json and import_to_blender.py for the project."""
        engine = self.get_chunk_engine(identifier)
        return engine.export_blender_manifest()

    def export_blender(
        self,
        identifier: str | Path,
        options: Any | None = None,
        out_dir: Path | str | None = None,
        base_dem: Any | None = None,
        ways: list[Any] | None = None,
    ) -> dict[str, Any]:
        """Runs the professional Blender export pipeline for the project."""
        p_dir = self._resolve_project_dir(identifier)
        project = self.open_project(identifier)
        from ..blender_export import BlenderExportOptions, BlenderExportPipeline

        if isinstance(options, dict):
            # Filter valid keys
            valid_keys = {
                "mode", "formats", "include_heightmap", "include_regions_geojson",
                "selected_chunks", "selected_layers", "terrain_res", "style_preset",
                "generate_blender_script"
            }
            opts_kwargs = {k: v for k, v in options.items() if k in valid_keys}
            opts = BlenderExportOptions(**opts_kwargs)
        elif isinstance(options, BlenderExportOptions):
            opts = options
        else:
            opts = BlenderExportOptions()

        target_dir = Path(out_dir) if out_dir else (p_dir / "exports" / "blender")
        pipeline = BlenderExportPipeline(project, project_dir=p_dir)
        return pipeline.export(out_dir=target_dir, options=opts, base_dem=base_dem, ways=ways)
