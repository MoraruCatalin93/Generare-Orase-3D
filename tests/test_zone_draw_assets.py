from terragen.server import app


def test_free_drawing_page_and_script():
    client = app.test_client()
    root = client.get("/")
    assert b"/ui/zone-draw.html" in root.data
    page = client.get("/ui/zone-draw.html")
    assert page.status_code == 200
    assert b"/ui/zone-draw.js" in page.data
    assert client.get("/ui/zone-draw.js").status_code == 200
