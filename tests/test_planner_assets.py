from terragen.server import app


def test_planner_assets_are_served():
    with app.test_client() as client:
        page = client.get("/")
        assert page.status_code == 200
        assert b"/ui/zone-draw.html" in page.data
        page.close()
        script = client.get("/ui/planner.js")
        assert script.status_code == 200
        assert b"zone-grid" in script.data
        script.close()
