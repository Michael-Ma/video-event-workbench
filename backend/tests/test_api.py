import pytest
from app.config import Settings
from app.db import now
from app.main import create_app
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(Settings(tmp_path)))


def register(client, is_demo=True):
    return client.app.state.repo.create_media({"filename": "demo.mp4", "status": "ready",
                                              "duration_us": 12_000_000, "is_demo": is_demo})


def request_body(media, query="Find every event"):
    return {"media_id": media["id"], "query": query, "config": {"provider": "fixture"}}


def test_missing_key_is_settings_error_and_no_run_is_created(client):
    media = register(client)
    response = client.post("/api/runs", json={"media_id": media["id"], "query": "Find events"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "api_key_missing"
    assert client.get("/api/runs").json() == []


def test_fixture_cannot_claim_to_recognize_uploaded_video(client):
    media = register(client, is_demo=False)
    response = client.post("/api/runs", json=request_body(media))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "fixture_requires_demo"


def test_idempotent_submission_and_cancel_cannot_be_revived(client):
    media = register(client)
    headers = {"Idempotency-Key": "local-submit-1"}
    first = client.post("/api/runs", json=request_body(media), headers=headers)
    repeated = client.post("/api/runs", json=request_body(media), headers=headers)
    assert first.status_code == repeated.status_code == 202
    run_id = first.json()["id"]
    assert repeated.json()["id"] == run_id
    conflict = client.post("/api/runs", json=request_body(media, "Find last event"), headers=headers)
    assert conflict.status_code == 409
    assert client.get(f"/api/runs/{run_id}/results").status_code == 409
    assert client.post(f"/api/runs/{run_id}/cancel").json()["status"] == "cancelled"
    repo = client.app.state.repo
    repo.update_run(run_id, status="completed", stage="done", results={"late": True})
    repo.update_run(run_id, stage="refining")
    current = client.get(f"/api/runs/{run_id}").json()
    assert current["status"] == current["stage"] == "cancelled"
    assert current["results"] is None
    assert repo.claim_next_run() is None
    logs = client.get(f"/api/runs/{run_id}/logs").json()["items"]
    assert len(logs) == 2
    assert client.get(f"/api/runs/{run_id}/logs?after={logs[0]['seq']}").json()["items"] == logs[1:]


def test_artifacts_contain_only_public_files_and_support_video_ranges(client, tmp_path):
    media = tmp_path / "media" / "safe.mp4"
    media.write_bytes(b"0123456789")
    response = client.get("/api/artifacts/media/safe.mp4", headers={"Range": "bytes=2-5"})
    assert response.status_code == 206
    assert response.content == b"2345"
    assert client.get("/api/artifacts/state.sqlite3").status_code == 404
    assert client.get("/api/artifacts/media/%2E%2E/state.sqlite3").status_code == 404
    outside = tmp_path.parent / "private-api-test.txt"
    outside.write_text("private")
    try:
        (tmp_path / "media" / "link").symlink_to(outside)
        assert client.get("/api/artifacts/media/link").status_code == 404
    finally:
        outside.unlink()


def test_upload_error_cleans_partial_file_and_success_sanitizes_filename(client, tmp_path, monkeypatch):
    from app import main

    empty = client.post("/api/media", files={"file": ("empty.mp4", b"", "video/mp4")})
    assert empty.status_code == 422
    assert not list((tmp_path / "media").iterdir())
    monkeypatch.setattr(main, "probe_video", lambda _path: {"duration_us": 2_000_000,
                                                           "width": 100, "height": 100})
    response = client.post("/api/media", files={"file": ("../../video.mp4", b"video", "video/mp4")})
    assert response.status_code == 201
    assert response.json()["filename"] == "video.mp4"
    assert response.json()["is_demo"] is False
    url = response.json()["original_url"]
    assert client.get(url).content == b"video"
    assert len(list((tmp_path / "media").iterdir())) == 1


def test_health_exposes_only_key_presence_and_worker_liveness(tmp_path):
    client = TestClient(create_app(Settings(tmp_path, gemini_api_key="private-test-key",
                                           gemini_model="configured-model")))
    client.app.state.repo.set_meta("worker_heartbeat", now())
    response = client.get("/api/health")
    assert response.json()["api_key_configured"] is True
    assert response.json()["worker_alive"] is True
    assert "private-test-key" not in response.text
    media = register(client)
    run = client.post("/api/runs", json=request_body(media)).json()
    assert run["config"]["model_id"] == response.json()["default_model"] == "configured-model"


def test_results_include_public_clip_link_without_mutating_journal(client):
    media = register(client)
    run = client.post("/api/runs", json=request_body(media)).json()
    original = {"events": [{"clip": {"path": f"runs/{run['id']}/clips/event 1.mp4"}}]}
    repo = client.app.state.repo
    repo.update_run(run["id"], status="completed", results=original)
    results = client.get(f"/api/runs/{run['id']}/results").json()
    assert results["events"][0]["clip"]["url"].endswith("event%201.mp4")
    assert "url" not in repo.get_run(run["id"])["results"]["events"][0]["clip"]
