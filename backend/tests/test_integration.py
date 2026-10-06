"""Real HTTP boundary, SQLite journal and FFmpeg; declared fixture labels only."""
import shutil

import av
import pytest
from app.config import Settings
from app.main import create_app
from app.pipeline import execute_run
from fastapi.testclient import TestClient


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg required")
@pytest.mark.parametrize("query,profile,expected", [
    ("找出绿色方块出现的所有区间", "action", [(3_000_000, 5_000_000), (8_000_000, 10_000_000)]),
    ("找到小球第一次触地时刻", "point", [3_000_000]),
    ("找到小球最后一次触地时刻", "point", [9_000_000]),
])
def test_demo_through_api_worker_and_exported_clips(tmp_path, query, profile, expected):
    settings = Settings(tmp_path)
    client = TestClient(create_app(settings))
    media = client.post("/api/demo").json()
    response = client.post("/api/runs", json={
        "media_id": media["id"], "query": query,
        "config": {"provider": "fixture", "profile": profile, "core_window_s": 4,
                   "context_s": 1, "scan_fps": 4},
    })
    assert response.status_code == 202
    repo = client.app.state.repo
    run = repo.claim_next_run()
    execute_run(run, repo, settings)
    current = client.get(f"/api/runs/{run['id']}").json()
    assert current["status"] == "completed", current.get("error")
    results = current["results"]
    assert results["provider"] == "fixture"
    assert results["scan_complete"] is True
    assert results["coverage"]["gaps"] == []
    assert len([task for task in current["tasks"] if task["stage"] == "scan"]) >= 3
    events = [event for event in results["events"] if not event["duplicate_of"]]
    assert all(event["result_bucket"] == "matched" for event in events)
    actual = [event["location"]["anchor_us"] if profile == "point" else
              (event["location"]["start_us"], event["location"]["end_us"]) for event in events]
    assert sorted(actual) == expected
    for event in events:
        assert event["clip_status"] == "succeeded"
        assert client.get(event["clip"]["url"]).status_code == 200
        with av.open(str(tmp_path / event["clip"]["path"])) as container:
            frames = list(container.decode(video=0))
        assert len(frames) == event["clip"]["metadata"]["frame_count"]
        assert frames[0].pts == 0
    assert "not semantic video inference" in results["limitations"][0]
    logs = client.get(f"/api/runs/{run['id']}/logs").json()["items"]
    references = [path for entry in logs for path in entry["details"].get("artifact_refs", [])]
    assert references
    assert all(client.get("/api/artifacts/" + path).status_code == 200 for path in references)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg required")
@pytest.mark.parametrize("scan_mode,refine_mode", [
    ("video", "images"), ("images", "video"), ("video", "video"),
])
def test_native_input_combinations_preserve_source_times_and_per_call_costs(tmp_path, scan_mode,
                                                                          refine_mode):
    client = TestClient(create_app(Settings(tmp_path)))
    media = client.post("/api/demo").json()
    response = client.post("/api/runs", json={
        "media_id": media["id"], "query": "all green square intervals",
        "config": {"provider": "fixture", "scan_input_mode": scan_mode,
                   "refine_input_mode": refine_mode, "core_window_s": 6, "context_s": 1},
    })
    assert response.status_code == 202
    repo = client.app.state.repo
    execute_run(repo.claim_next_run(), repo, client.app.state.settings)
    current = client.get(f"/api/runs/{response.json()['id']}").json()
    assert current["status"] == "completed", current.get("error")
    events = [e for e in current["results"]["events"] if not e["duplicate_of"]]
    assert [(e["location"]["start_us"], e["location"]["end_us"]) for e in events] == [
        (3_000_000, 5_000_000), (8_000_000, 10_000_000)]
    assert all(e["clip_status"] == "succeeded" for e in events)
    assert all(e["evidence_refs"] for e in events)
    if refine_mode == "video":
        assert all(ref.startswith("video:") for e in events for ref in e["evidence_refs"])
    calls = [t for t in current["tasks"] if t.get("request_intent")]
    assert all(t["cost"]["status"] == "not_billable" for t in calls)
    assert all(t["input_mode"] == ("text" if t["stage"] == "query" else
               scan_mode if t["stage"] == "scan" else refine_mode) for t in calls)
    assert current["cost_summary"]["estimated_usd"] == 0
    assert current["cost_summary"]["local_calls"] == len(calls)
