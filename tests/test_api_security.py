import pytest

from terragen.server import app


@pytest.fixture
def client():
    return app.test_client()


def test_cross_origin_post_is_rejected(client):
    response = client.post("/api/generate", json={}, headers={"Origin": "https://evil.example"})
    assert response.status_code == 403


def test_cross_site_delete_is_rejected(client):
    response = client.delete("/api/sites/example", headers={"Sec-Fetch-Site": "cross-site"})
    assert response.status_code == 403


def test_simple_form_post_is_rejected(client):
    response = client.post("/api/generate", data="{}", content_type="text/plain")
    assert response.status_code == 415


def test_same_origin_json_is_accepted_by_guard(client):
    response = client.post("/api/elevation", json={}, headers={"Origin": "http://localhost"})
    assert response.status_code == 200


def test_cancel_without_body_is_accepted_by_guard(client):
    response = client.post("/api/job/nonexistent/cancel")
    assert response.status_code == 404


def test_arbitrary_download_url_is_rejected(client):
    response = client.post("/api/download-pbf", json={"url": "https://example.com/file"})
    assert response.status_code == 400


def test_oversized_body_is_rejected(client):
    response = client.post("/api/generate", data='{"x":"' + 'a' * 70000 + '"}', content_type="application/json")
    assert response.status_code == 413
