"""Tests for shared navigation bar and static assets (Map, Zones, Terrain, Export, Viewer)."""

from terragen.server import app


def test_common_navbar_and_project_selector_on_root():
    with app.test_client() as client:
        root = client.get("/")
        assert root.status_code == 200
        # Common navbar presence
        assert b'class="app-nav"' in root.data
        assert b'href="/"' in root.data
        assert b'href="/ui/zone-draw.html"' in root.data
        assert b'href="/ui/terrain.html"' in root.data
        assert b'href="/ui/export.html"' in root.data
        assert b'href="/viewer/"' in root.data
        # Project Selector & Management controls
        assert b'id="projectSelect"' in root.data
        assert b'id="btnNewProject"' in root.data
        assert b'id="newProjectForm"' in root.data
        assert b'id="btnRenameProject"' in root.data
        assert b'id="btnDuplicateProject"' in root.data
        assert b'id="btnDeleteProject"' in root.data
        root.close()


def test_zone_draw_has_navbar():
    with app.test_client() as client:
        page = client.get("/ui/zone-draw.html")
        assert page.status_code == 200
        assert b'class="app-nav"' in page.data
        assert b'href="/ui/terrain.html"' in page.data
        assert b'href="/ui/export.html"' in page.data
        page.close()


def test_terrain_page_and_script():
    with app.test_client() as client:
        page = client.get("/ui/terrain.html")
        assert page.status_code == 200
        assert b'class="app-nav"' in page.data
        assert b"/ui/terrain.js" in page.data
        # Brushes
        assert b'id="brushRaiseLower"' in page.data
        assert b'id="brushSmooth"' in page.data
        assert b'id="brushFlatten"' in page.data
        assert b'id="brushHill"' in page.data
        assert b'id="brushValley"' in page.data
        assert b'id="brushWater"' in page.data
        # Controls for radius, intensity, falloff
        assert b'id="brushRadius"' in page.data
        assert b'id="brushIntensity"' in page.data
        assert b'id="brushFalloff"' in page.data
        # Undo/Redo & List
        assert b'id="btnUndo"' in page.data
        assert b'id="btnRedo"' in page.data
        assert b'id="modifiersList"' in page.data
        # Regenerate terrain
        assert b'id="btnRegenerateTerrain"' in page.data
        # Clear Romanian alert for unselected project
        assert b'id="noProjectAlert"' in page.data
        page.close()

        script = client.get("/ui/terrain.js")
        assert script.status_code == 200
        assert b"loadProjects" in script.data
        assert b"makeCircleFeature" in script.data
        assert b"applyBrush" in script.data
        assert b"performUndo" in script.data
        assert b"performRedo" in script.data
        script.close()


def test_export_page_and_script():
    with app.test_client() as client:
        page = client.get("/ui/export.html")
        assert page.status_code == 200
        assert b'class="app-nav"' in page.data
        assert b"/ui/export.js" in page.data
        # Chunks grid setup & generation
        assert b'id="btnSetupGrid"' in page.data
        assert b'id="btnGenerateAllChunks"' in page.data
        assert b'id="btnGenerateSelectedChunk"' in page.data
        assert b'id="btnCancelGeneration"' in page.data
        assert b'id="btnValidateChunks"' in page.data
        # Blender export & copy command
        assert b'id="btnExportBlender"' in page.data
        assert b'id="btnCopyBlenderCmd"' in page.data
        # M4 RAM warning
        assert b'id="ramWarningBox"' in page.data
        page.close()

        script = client.get("/ui/export.js")
        assert script.status_code == 200
        assert b"loadProjects" in script.data
        assert b"selectChunk" in script.data
        assert b"generateSingleChunk" in script.data
        assert b"btnCopyBlenderCmd" in script.data
        script.close()


def test_viewer_has_navbar_and_theme():
    with app.test_client() as client:
        page = client.get("/viewer/")
        assert page.status_code == 200
        assert b'class="app-nav"' in page.data
        assert b'href="/ui/terrain.html"' in page.data
        assert b'href="/ui/export.html"' in page.data
        page.close()

        theme = client.get("/viewer/theme.css")
        assert theme.status_code == 200
        assert b"app-nav" in theme.data
        theme.close()

