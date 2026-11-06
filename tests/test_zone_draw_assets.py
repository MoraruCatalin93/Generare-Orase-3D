from terragen.server import app


def test_free_drawing_page_and_script():
    with app.test_client() as client:
        root = client.get("/")
        assert b"/ui/zone-draw.html" in root.data
        root.close()
        page = client.get("/ui/zone-draw.html")
        assert page.status_code == 200
        assert b"/ui/zone-draw.js" in page.data
        page.close()
        res_js = client.get("/ui/zone-draw.js")
        assert res_js.status_code == 200
        res_js.close()
