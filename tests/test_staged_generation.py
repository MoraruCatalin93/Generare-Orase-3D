from pathlib import Path

from terragen import server
from terragen.cli import GenerationCancelled


def make_job():
    return server.Job({"lat": 46.54, "lon": 24.56, "size": 500, "name": "oras"})


def prepare_old(tmp_path):
    old = tmp_path / "oras"
    old.mkdir()
    (old / "old.txt").write_text("old", encoding="utf-8")
    return old


def test_failure_keeps_existing_site(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "SITES_DIR", tmp_path.resolve())
    old = prepare_old(tmp_path)
    def failing_generate(**kwargs):
        staged = Path(kwargs["out"])
        staged.mkdir()
        (staged / "partial").write_text("partial", encoding="utf-8")
        raise RuntimeError("failed")
    monkeypatch.setattr(server, "generate", failing_generate)
    job = make_job()
    server.run_job(job)
    assert job.status == "error"
    assert (old / "old.txt").read_text(encoding="utf-8") == "old"
    assert not list(tmp_path.glob(".terragen-*"))


def test_cancellation_keeps_existing_site(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "SITES_DIR", tmp_path.resolve())
    old = prepare_old(tmp_path)
    def cancelled_generate(**kwargs):
        Path(kwargs["out"]).mkdir()
        raise GenerationCancelled("cancelled")
    monkeypatch.setattr(server, "generate", cancelled_generate)
    job = make_job()
    server.run_job(job)
    assert job.status == "cancelled"
    assert (old / "old.txt").exists()
    assert not list(tmp_path.glob(".terragen-*"))


def test_success_replaces_existing_site(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "SITES_DIR", tmp_path.resolve())
    old = prepare_old(tmp_path)
    def successful_generate(**kwargs):
        staged = Path(kwargs["out"])
        staged.mkdir()
        (staged / "new.txt").write_text("new", encoding="utf-8")
        return {"stats": {}, "layers_triangles": {}}
    monkeypatch.setattr(server, "generate", successful_generate)
    job = make_job()
    server.run_job(job)
    assert job.status == "done"
    assert not (old / "old.txt").exists()
    assert (old / "new.txt").exists()
    assert not list(tmp_path.glob(".terragen-*"))
