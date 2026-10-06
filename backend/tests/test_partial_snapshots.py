import shutil

import pytest
from app import pipeline
from app.config import Settings
from app.contracts import RunConfig
from app.db import Repository
from app.diagnostics import run_diagnostics
from app.media import MediaError, generate_demo

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg required")


def setup_run(tmp_path):
    settings = Settings(tmp_path)
    settings.ensure_dirs()
    data = generate_demo(tmp_path / "media/demo.mp4")
    repo = Repository(settings.db_path)
    media = repo.create_media({**data, "original_path": "media/demo.mp4"})
    run, _ = repo.create_run({"media_id": media["id"], "query": "all green square intervals",
                             "config": RunConfig(provider="fixture").model_dump()})
    return settings, repo, repo.claim_next_run()


def test_failed_later_clip_keeps_earlier_clip_visible(tmp_path, monkeypatch):
    settings, repo, run = setup_run(tmp_path)
    real_cut = pipeline.media_io.cut_clip
    calls = []

    def cut(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            live = repo.get_run(run["id"])
            assert live["status"] == "running"
            assert live["results"]["stats"]["clips_succeeded"] == 1
            assert live["results"]["provisional"] is False
            raise MediaError("test_cut_failure", "local cut test failure")
        return real_cut(*args, **kwargs)

    monkeypatch.setattr(pipeline.media_io, "cut_clip", cut)
    pipeline.execute_run(run, repo, settings)
    saved = repo.get_run(run["id"])
    assert saved["status"] == "partial"
    assert saved["results"]["stats"]["clips_succeeded"] == 1
    assert saved["results"]["stats"]["clips_failed"] == 1
    first, second = saved["results"]["events"]
    assert first["clip_status"] == "succeeded" and first["clip"]["path"]
    assert second["clip_status"] == "failed"
    diagnostics = run_diagnostics(saved, repo.list_tasks(run["id"]))
    assert diagnostics["available_clips"] == 1
    assert diagnostics["issues"][0]["code"] == "clip_failed"
    assert diagnostics["issues"][0]["technical_message"] == "local cut test failure"


def test_cancel_during_later_clip_preserves_the_published_first_clip(tmp_path, monkeypatch):
    settings, repo, run = setup_run(tmp_path)
    real_cut = pipeline.media_io.cut_clip
    calls = []

    def cut(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            repo.cancel_run(run["id"])
            raise MediaError("cancelled", "cancelled before second cut")
        return real_cut(*args, **kwargs)

    monkeypatch.setattr(pipeline.media_io, "cut_clip", cut)
    pipeline.execute_run(run, repo, settings)
    saved = repo.get_run(run["id"])
    assert saved["status"] == "cancelled"
    assert saved["last_stage"] == "clipping"
    assert saved["results"]["stats"]["clips_succeeded"] == 1
    assert saved["results"]["events"][0]["clip_status"] == "succeeded"
    repo.update_run(run["id"], results={"late": True})
    assert repo.get_run(run["id"])["results"]["stats"]["clips_succeeded"] == 1

