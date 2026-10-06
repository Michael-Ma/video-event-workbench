import json
import shutil

import pytest
from app import pipeline
from app.config import Settings
from app.contracts import Candidate, Location, ModelResponse, QuerySpec, RunConfig
from app.db import Repository
from app.media import generate_demo
from app.pipeline import Cancelled, IncompleteResponse, Journal, refinement_results
from app.providers import ProviderError, ProviderReply
from app.worker import recover_interrupted_runs


def create_run(repo, media_id="media", config=None):
    run, _ = repo.create_run({"media_id": media_id, "query": "Find every green square interval",
                              "config": (config or RunConfig(provider="fixture")).model_dump()})
    return run


@pytest.fixture
def store(tmp_path):
    settings = Settings(data_dir=tmp_path)
    settings.ensure_dirs()
    return settings, Repository(settings.db_path)


def sample_manifest():
    return {"input_origin_us": 0, "source_range_us": [0, 10_000_000],
            "frames": [{"frame_id": "f1", "source_time_us": 1_000_000},
                       {"frame_id": "f2", "source_time_us": 2_000_000}]}


def candidates():
    return [Candidate(candidate_id=f"c{i}", window_id="w", entity_key="arm",
                      location=Location(kind="interval", start_us=i * 1_000_000,
                                        end_us=(i + 1) * 1_000_000)) for i in (1, 2)]


@pytest.mark.parametrize("finish,cancel", [("STOP", False), ("MAX_TOKENS", False), ("STOP", True)])
def test_call_cost_persists_even_for_truncated_or_cancelled_replies(store, finish, cancel):
    settings, repo = store
    config = RunConfig(provider="gemini")
    run = create_run(repo, config=config)
    calls = []

    class Provider:
        def analyze(self, operation, request):
            calls.append(operation)
            if cancel:
                repo.cancel_run(run["id"])
            return ProviderReply({"raw_query": "find events"}, {"response_id": "test-response"},
                                 {"prompt_token_count": 10_000, "candidates_token_count": 1_000,
                                  "thoughts_token_count": 500}, finish)

    journal = Journal(run, repo, settings, config, Provider())
    if finish != "STOP" or cancel:
        with pytest.raises(Cancelled if cancel else IncompleteResponse):
            journal.call("query", "query", "normalize_query", {"raw_query": "find events"})
    else:
        journal.call("query", "query", "normalize_query", {"raw_query": "find events"})
        journal.call("query", "query", "normalize_query", {"raw_query": "find events"})
    task = repo.list_tasks(run["id"])[0]
    assert task["cost"]["estimated_usd"] == pytest.approx(0.013125)
    response = json.loads((settings.data_dir / task["response_path"]).read_text())
    assert response["cost"] == task["cost"]
    intent = json.loads((settings.data_dir / task["request_path"]).read_text())
    assert response["cost"]["pricing_version"] == intent["pricing"]["pricing_version"]
    costs = [entry for entry in repo.get_logs(run["id"]) if entry["code"] == "model_call_cost"]
    assert len(costs) == len(calls) == 1
    assert costs[0]["details"]["cost"] == task["cost"]


def test_unknown_request_cost_is_saved_without_retry_or_zero_charge(store):
    settings, repo = store
    config = RunConfig(provider="gemini")
    run = create_run(repo, config=config)
    calls = []

    class Provider:
        def analyze(self, operation, request):
            calls.append(operation)
            raise ProviderError("request_timeout", "request outcome unknown", request_unknown=True)

    journal = Journal(run, repo, settings, config, Provider())
    for _ in range(2):
        with pytest.raises(ProviderError):
            journal.call("query", "query", "normalize_query", {"raw_query": "find events"})
    task = repo.list_tasks(run["id"])[0]
    assert task["cost"]["estimated_usd"] is None
    assert task["cost"]["reason"] == "request_outcome_unknown"
    assert json.loads((settings.data_dir / task["error_path"]).read_text())["cost"] == task["cost"]
    assert len(calls) == 1
    assert len([entry for entry in repo.get_logs(run["id"]) if entry["code"] == "model_call_cost"]) == 1


def test_empty_refine_response_does_not_reject_unmentioned_candidates():
    results = refinement_results(ModelResponse(events=[]), candidates(), sample_manifest(),
                                 10_000_000, QuerySpec(raw_query="all grabs"), RunConfig(), "group")
    assert len(results) == 2
    assert all(r.decision == "unresolved" and r.result_bucket == "uncertain" for r in results)


@pytest.mark.parametrize("decision", ["rejected", "unresolved"])
def test_consistent_negative_or_uncertain_mapping_preserves_the_matching_decision(decision):
    response = ModelResponse.model_validate({
        "events": [{"kind": "interval", "decision": decision, "start_s": 1, "end_s": 1.5,
                    "start_range_s": [1, 1], "end_range_s": [1.5, 1.5],
                    "evidence_frame_ids": ["f1"], "reason": "Visible matching decision"}],
        "candidate_dispositions": [{"candidate_id": "c1", "disposition": decision,
                                    "event_indices": [0]}],
    })
    output = refinement_results(response, candidates(), sample_manifest(), 10_000_000,
                                QuerySpec(raw_query="all grabs"), RunConfig(provider="fixture"), "group")
    assert output[0].source_candidate_ids == ["c1"]
    assert output[0].decision == decision
    assert output[0].result_bucket == ("rejected" if decision == "rejected" else "uncertain")
    assert output[0].result_bucket != "matched"
    assert response.candidate_dispositions[0].disposition == decision


def test_zero_one_many_response_maps_each_input_or_keeps_it_unresolved():
    response = ModelResponse.model_validate({
        "events": [{"kind": "interval", "start_s": 1, "end_s": 1.5,
                    "start_range_s": [1, 1], "end_range_s": [1.5, 1.5],
                    "evidence_frame_ids": ["f1"]},
                   {"kind": "interval", "start_s": 1.6, "end_s": 2,
                    "start_range_s": [1.6, 1.6], "end_range_s": [2, 2],
                    "evidence_frame_ids": ["f2"]}],
        "candidate_dispositions": [{"candidate_id": "c1", "disposition": "mapped_to_event",
                                    "event_indices": [0, 1]}],
    })
    output = refinement_results(response, candidates(), sample_manifest(), 10_000_000,
                                QuerySpec(raw_query="all grabs"), RunConfig(provider="fixture"), "group")
    assert len(output) == 3
    assert [r.result_bucket for r in output] == ["matched", "matched", "uncertain"]
    assert output[-1].source_candidate_ids == ["c2"]


def test_disposition_cannot_invent_candidate_id():
    with pytest.raises(ValueError, match="Unknown"):
        refinement_results(ModelResponse.model_validate({"candidate_dispositions": [
            {"candidate_id": "invented", "disposition": "rejected"}]}),
            candidates(), sample_manifest(), 10_000_000, QuerySpec(raw_query="all grabs"),
            RunConfig(), "group")


def test_intent_is_committed_before_provider_call_and_response_is_reused(store):
    settings, repo = store
    run = create_run(repo)
    calls = []

    class Provider:
        def analyze(self, operation, request):
            assert repo.list_tasks(run["id"])[0]["status"] == "submitting"
            calls.append(operation)
            return ProviderReply({"raw_query": request["raw_query"]}, {"response": "safe"})

    journal = Journal(run, repo, settings, RunConfig(provider="fixture"), Provider())
    first = journal.call("query", "query", "normalize_query", {"raw_query": "original"})
    second = journal.call("query", "query", "normalize_query", {"raw_query": "original"})
    assert first == second
    assert len(calls) == 1
    task = repo.list_tasks(run["id"])[0]
    assert (settings.data_dir / task["request_path"]).is_file()
    assert (settings.data_dir / task["response_path"]).is_file()


def test_unknown_request_prevents_new_paid_requests(store):
    settings, repo = store
    run = create_run(repo)
    calls = []

    class Provider:
        def analyze(self, operation, request):
            calls.append(operation)
            raise ProviderError("request_unknown", "transport timeout", request_unknown=True)

    journal = Journal(run, repo, settings, RunConfig(provider="fixture"), Provider())
    with pytest.raises(ProviderError):
        journal.call("one", "scan", "propose", {})
    with pytest.raises(ProviderError):
        journal.call("one", "scan", "propose", {})
    with pytest.raises(ProviderError):
        journal.call("two", "scan", "propose", {})
    assert calls == ["propose"]
    assert repo.list_tasks(run["id"])[0]["status"] == "request_unknown"


def test_late_reply_is_archived_without_reviving_cancelled_run(store):
    settings, repo = store
    run = create_run(repo)

    class Provider:
        def analyze(self, operation, request):
            repo.cancel_run(run["id"])
            return ProviderReply({"raw_query": "original"}, {"text": "received after cancel"})

    journal = Journal(run, repo, settings, RunConfig(provider="fixture"), Provider())
    with pytest.raises(pipeline.Cancelled):
        journal.call("query", "query", "normalize_query", {})
    task = repo.list_tasks(run["id"])[0]
    assert task["status"] == "cancelled"
    assert (settings.data_dir / task["response_path"]).exists()
    assert repo.get_run(run["id"])["status"] == "cancelled"


def test_restart_marks_unsettled_intent_unknown_not_pending(store):
    _, repo = store
    run = create_run(repo)
    repo.update_run(run["id"], status="running")
    repo.upsert_task(run["id"], "scan1", "scan", "submitting", request_intent={"sent": "maybe"})
    repo.upsert_task(run["id"], "clip1", "clip", "running")
    recover_interrupted_runs(repo)
    tasks = {t["task_id"]: t for t in repo.list_tasks(run["id"])}
    assert tasks["scan1"]["status"] == "request_unknown"
    assert tasks["clip1"]["status"] == "interrupted"
    assert repo.get_run(run["id"])["status"] == "queued"


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg required for real media fixture")
def test_real_fixture_pipeline_clips_and_reuses_prepared_media(store, monkeypatch):
    settings, repo = store
    path = settings.data_dir / "media" / "demo.mp4"
    data = generate_demo(path)
    media = repo.create_media({**data, "original_path": "media/demo.mp4"})
    config = RunConfig(provider="fixture", core_window_s=4, context_s=1,
                       scan_fps=4, max_input_frames=128)
    run = create_run(repo, media["id"], config)
    pipeline.execute_run(run, repo, settings)
    current = repo.get_run(run["id"])
    assert current["status"] == "completed", current
    assert current["results"]["scan_complete"] is True
    assert current["results"]["stats"]["matched"] == 2
    matched = [e for e in current["results"]["events"] if e["result_bucket"] == "matched" and not e["duplicate_of"]]
    assert [(e["location"]["start_us"], e["location"]["end_us"]) for e in matched] == [
        (3_000_000, 5_000_000), (8_000_000, 10_000_000)]
    for event in matched:
        assert event["clip_status"] == "succeeded"
        assert (settings.data_dir / event["clip"]["path"]).stat().st_size > 100
    assert "DEMO FIXTURE" in current["results"]["limitations"][0]
    assert all("window" in t for t in repo.list_tasks(run["id"]) if t["stage"] == "scan")

    def must_reuse(*_, **__):
        raise AssertionError("Existing immutable media should not be decoded/transcoded again")

    monkeypatch.setattr(pipeline.media_io, "prepare_media", must_reuse)
    next_run = create_run(repo, media["id"], config.model_copy(update={"max_calls": 1}))
    pipeline.execute_run(next_run, repo, settings)
    next_result = repo.get_run(next_run["id"])
    assert next_result["status"] == "partial"
    assert next_result["results"]["scan_complete"] is False
    assert next_result["results"]["coverage"]["gaps"] == [[0, 12_000_000]]
    assert any(log["code"] == "media_reused" for log in repo.get_logs(next_run["id"]))
    assert json.loads((settings.data_dir / "runs" / next_run["id"] / "results.json").read_text())
