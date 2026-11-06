import pytest

from terragen import server
from terragen.cli import GenerationCancelled, generate


def test_cancel_before_generation_does_not_create_output(tmp_path):
    output = tmp_path / "city"
    with pytest.raises(GenerationCancelled):
        generate(center=(46.54, 24.56), out=str(output), is_cancelled=lambda: True)
    assert not output.exists()


def test_server_marks_cooperatively_cancelled_job(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "SITES_DIR", tmp_path.resolve())
    def fake_generate(**kwargs):
        assert kwargs["is_cancelled"]() is False
        job.cancelled = True
        raise GenerationCancelled("anulat")
    monkeypatch.setattr(server, "generate", fake_generate)
    job = server.Job({"lat": 46.54, "lon": 24.56, "size": 500, "name": "oras"})
    server.run_job(job)
    assert job.status == "cancelled"
    assert job.error is None
    assert any("anulat" in line.lower() for line in job.lines)


def test_completed_job_cannot_be_cancelled(monkeypatch):
    job = server.Job({})
    job.status = "done"
    monkeypatch.setitem(server.JOBS, job.id, job)
    response = server.app.test_client().post(f"/api/job/{job.id}/cancel")
    assert response.status_code == 409
    assert not job.cancelled
