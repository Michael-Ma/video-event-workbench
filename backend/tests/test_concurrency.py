"""Behavioral concurrency checks. Providers/media are controlled local fakes: no HTTP or fees."""

import json
import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from pathlib import Path

import pytest
from app import pipeline
from app.config import Settings
from app.contracts import QuerySpec, RunConfig
from app.db import Repository
from app.pipeline import CallBudgetExceeded, Cancelled, Journal
from app.providers import ProviderError, ProviderReply

WAIT_S = 2
USAGE = {"prompt_token_count": 10_000, "candidates_token_count": 1_000,
         "thoughts_token_count": 500}


@pytest.fixture
def journal_store(tmp_path, monkeypatch):
    # Pricing assertions do not depend on the host's calendar or its current free tier.
    monkeypatch.setattr(pipeline, "now", lambda: "2026-10-05T12:00:00+00:00")
    settings = Settings(data_dir=tmp_path, gemini_api_key="unused-local-test-key")
    settings.ensure_dirs()
    repo = Repository(settings.db_path)
    config = RunConfig(provider="gemini", model_concurrency=2)
    run, _ = repo.create_run({"media_id": "test-media", "query": "Find all events",
                              "config": config.model_dump(mode="json")})
    return settings, repo, config, run


def query_reply():
    return ProviderReply({"raw_query": "Find all events"}, {"test": "no remote call"}, dict(USAGE))


def assert_cost_receipt(settings, task):
    assert task["usage"] == USAGE
    assert task["cost"]["status"] == "estimated"
    assert task["cost"]["estimated_usd"] > 0
    receipt = json.loads((settings.data_dir / task["response_path"]).read_text())
    assert receipt["usage"] == USAGE
    assert receipt["cost"] == task["cost"]


def test_two_independent_journal_requests_overlap_in_the_provider(journal_store):
    settings, repo, config, run = journal_store
    rendezvous = threading.Barrier(2)
    active = 0
    peak = 0
    lock = threading.Lock()

    class Provider:
        def analyze(self, _operation, _request):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            try:
                # This fails if an intent for another active call is treated as stale,
                # or if the Journal keeps its synchronization lock over the network call.
                rendezvous.wait(timeout=WAIT_S)
                return query_reply()
            finally:
                with lock:
                    active -= 1

    journal = Journal(run, repo, settings, config, Provider())
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(journal.call, task, "query", "normalize_query", {})
                   for task in ("one", "two")]
        assert all(f.result(timeout=WAIT_S + 1).raw_query == "Find all events" for f in futures)
    assert peak == 2
    tasks = repo.list_tasks(run["id"])
    assert len(tasks) == 2 and all(t["status"] == "succeeded" for t in tasks)
    for task in tasks:
        assert_cost_receipt(settings, task)


def test_parallel_budget_reservation_cannot_send_more_than_one_request(journal_store):
    settings, repo, config, run = journal_store
    config = config.model_copy(update={"max_calls": 1})
    start_together = threading.Barrier(2)
    entered = threading.Event()
    release = threading.Event()
    calls = []
    lock = threading.Lock()

    class Provider:
        def analyze(self, operation, request):
            with lock:
                calls.append(request["tag"])
            entered.set()
            assert release.wait(timeout=WAIT_S), "test did not release the in-flight request"
            return query_reply()

    journal = Journal(run, repo, settings, config, Provider())

    def invoke(tag):
        start_together.wait(timeout=WAIT_S)
        return journal.call(tag, "query", "normalize_query", {"tag": tag})

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(invoke, tag) for tag in ("one", "two")]
        try:
            assert entered.wait(timeout=WAIT_S)
            # The losing reservation must finish without waiting for the active network call.
            done, _ = wait(futures, timeout=WAIT_S, return_when=FIRST_COMPLETED)
            assert len(done) == 1
            assert isinstance(next(iter(done)).exception(), CallBudgetExceeded)
            assert len(calls) == 1
        finally:
            release.set()
        outcomes = []
        for future in futures:
            try:
                outcomes.append(future.result(timeout=WAIT_S))
            except CallBudgetExceeded:
                pass
    assert len(outcomes) == len(calls) == 1
    assert sum(bool(t.get("request_intent")) for t in repo.list_tasks(run["id"])) == 1


def test_four_callers_share_two_model_slots_and_each_gets_one_receipt(journal_store):
    settings, repo, config, run = journal_store
    begin = threading.Barrier(5)
    first_wave_full = threading.Event()
    third_entered = threading.Event()
    release_first_wave = threading.Event()
    lock = threading.Lock()
    active = peak = 0
    entered = []

    class Provider:
        def analyze(self, _operation, request):
            nonlocal active, peak
            with lock:
                entered.append(request["tag"])
                number = len(entered)
                active += 1
                peak = max(peak, active)
                if number == 2:
                    first_wave_full.set()
                if number >= 3:
                    third_entered.set()
            try:
                if number <= 2:
                    assert release_first_wave.wait(timeout=WAIT_S)
                return query_reply()
            finally:
                with lock:
                    active -= 1

    journal = Journal(run, repo, settings, config, Provider())

    def invoke(tag):
        begin.wait(timeout=WAIT_S)
        return journal.call(tag, "query", "normalize_query", {"tag": tag})

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(invoke, f"request_{i}") for i in range(4)]
        try:
            begin.wait(timeout=WAIT_S)
            assert first_wave_full.wait(timeout=WAIT_S)
            # Both provider slots stay occupied; all four caller threads are already
            # released. The short bounded wait gives queued callers an admission chance.
            assert not third_entered.wait(timeout=0.1)
            assert sum(bool(t.get("request_intent")) for t in repo.list_tasks(run["id"])) == 2
        finally:
            release_first_wave.set()
        assert all(f.result(timeout=WAIT_S).raw_query == "Find all events" for f in futures)
    assert peak == 2 and len(entered) == len(set(entered)) == 4
    tasks = repo.list_tasks(run["id"])
    assert len(tasks) == 4
    assert len({t["attempt_id"] for t in tasks}) == 4
    assert all(t["status"] == "succeeded" and t["request_intent"] for t in tasks)
    for task in tasks:
        assert_cost_receipt(settings, task)
    assert len([r for r in repo.get_logs(run["id"]) if r["code"] == "model_call_cost"]) == 4


def test_unknown_call_blocks_new_work_but_inflight_success_keeps_its_receipt(journal_store):
    settings, repo, config, run = journal_store
    both_entered = threading.Barrier(3)
    release_bad, release_good = threading.Event(), threading.Event()
    calls = []
    lock = threading.Lock()

    class Provider:
        def analyze(self, operation, request):
            with lock:
                calls.append(request["tag"])
            both_entered.wait(timeout=WAIT_S)
            if request["tag"] == "bad":
                assert release_bad.wait(timeout=WAIT_S)
                raise ProviderError("request_timeout", "unknown response", request_unknown=True)
            assert release_good.wait(timeout=WAIT_S)
            return query_reply()

    journal = Journal(run, repo, settings, config, Provider())
    with ThreadPoolExecutor(max_workers=2) as pool:
        bad = pool.submit(journal.call, "bad", "query", "normalize_query", {"tag": "bad"})
        good = pool.submit(journal.call, "good", "query", "normalize_query", {"tag": "good"})
        try:
            both_entered.wait(timeout=WAIT_S)
            release_bad.set()
            with pytest.raises(ProviderError):
                bad.result(timeout=WAIT_S)
            with pytest.raises(ProviderError) as stopped:
                journal.preflight("third")
            assert stopped.value.request_unknown
            with pytest.raises(ProviderError):
                journal.call("third", "query", "normalize_query", {"tag": "third"})
        finally:
            release_bad.set()
            release_good.set()
        assert good.result(timeout=WAIT_S).raw_query == "Find all events"
    assert sorted(calls) == ["bad", "good"]
    tasks = {t["task_id"]: t for t in repo.list_tasks(run["id"])}
    assert tasks["bad"]["status"] == "request_unknown"
    assert tasks["bad"]["cost"]["estimated_usd"] is None
    assert tasks["good"]["status"] == "succeeded"
    assert_cost_receipt(settings, tasks["good"])
    assert not tasks.get("third", {}).get("request_intent")


def test_preexisting_unowned_submitting_intent_is_never_resent(journal_store):
    settings, repo, config, run = journal_store
    repo.upsert_task(run["id"], "old", "scan", "submitting", attempt_id="pre-restart",
                     request_intent={"operation": "propose"})

    class Provider:
        def analyze(self, *_):
            pytest.fail("An unowned previous-process intent must not trigger a paid call")

    journal = Journal(run, repo, settings, config, Provider())
    with pytest.raises(ProviderError):
        journal.preflight("new")
    with pytest.raises(ProviderError):
        journal.call("old", "scan", "propose", {})
    with pytest.raises(ProviderError):
        journal.call("new", "scan", "propose", {})
    tasks = {t["task_id"]: t for t in repo.list_tasks(run["id"])}
    assert tasks["old"]["status"] == "request_unknown"
    assert tasks["old"]["attempt_id"] == "pre-restart"
    assert not tasks.get("new", {}).get("request_intent")


def test_unknown_outcome_stops_submissions_before_debug_artifact_io(journal_store, monkeypatch):
    settings, repo, config, run = journal_store
    observed_unknown = threading.Event()
    allow_error_write = threading.Event()
    original_write = pipeline._write_json
    calls = []

    def slow_error_artifact(path, payload):
        if Path(path).name == "error.json":
            observed_unknown.set()
            assert allow_error_write.wait(timeout=WAIT_S)
        return original_write(path, payload)

    class Provider:
        def analyze(self, operation, request):
            calls.append(request["tag"])
            if request["tag"] == "bad":
                raise ProviderError("request_timeout", "unknown response", request_unknown=True)
            return query_reply()

    monkeypatch.setattr(pipeline, "_write_json", slow_error_artifact)
    journal = Journal(run, repo, settings, config, Provider())
    with ThreadPoolExecutor(max_workers=1) as pool:
        bad = pool.submit(journal.call, "bad", "query", "normalize_query", {"tag": "bad"})
        try:
            assert observed_unknown.wait(timeout=WAIT_S)
            # A slow artifact/fsync must not leave a known-unknown request looking live
            # while the coordinator admits another potentially billable call.
            with pytest.raises(ProviderError) as blocked:
                journal.call("new", "query", "normalize_query", {"tag": "new"})
            assert blocked.value.request_unknown
        finally:
            allow_error_write.set()
        with pytest.raises(ProviderError):
            bad.result(timeout=WAIT_S)
    assert calls == ["bad"]


def test_cancelled_concurrent_calls_archive_usage_without_publishing_results(journal_store):
    settings, repo, config, run = journal_store
    both_entered = threading.Barrier(3)
    release = threading.Event()

    class Provider:
        def analyze(self, _operation, _request):
            both_entered.wait(timeout=WAIT_S)
            assert release.wait(timeout=WAIT_S)
            return query_reply()

    journal = Journal(run, repo, settings, config, Provider())
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(journal.call, name, "query", "normalize_query", {})
                   for name in ("first", "second")]
        try:
            both_entered.wait(timeout=WAIT_S)
            repo.cancel_run(run["id"])
        finally:
            release.set()
        for future in futures:
            with pytest.raises(Cancelled):
                future.result(timeout=WAIT_S)
    current = repo.get_run(run["id"])
    assert current["status"] == "cancelled" and current["results"] is None
    tasks = repo.list_tasks(run["id"])
    assert len(tasks) == 2 and all(t["status"] == "cancelled" for t in tasks)
    for task in tasks:
        assert_cost_receipt(settings, task)
    cost_logs = [item for item in repo.get_logs(run["id"]) if item["code"] == "model_call_cost"]
    assert len(cost_logs) == 2


def test_parallel_debug_logging_preserves_complete_ordered_records(journal_store):
    settings, repo, config, run = journal_store
    journal = Journal(run, repo, settings, config, object())
    start = threading.Barrier(4)

    def write(writer):
        start.wait(timeout=WAIT_S)
        for index in range(12):
            journal.log("test", "parallel_debug", f"writer {writer} entry {index}",
                        writer=writer, index=index, payload="并发完整记录" * 1500)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(write, i) for i in range(4)]
        for future in futures:
            future.result(timeout=WAIT_S + 1)
    lines = (journal.folder / "debug.jsonl").read_text().splitlines()
    records = [json.loads(line) for line in lines]
    db_records = repo.get_logs(run["id"], limit=200)
    assert len(records) == len(db_records) == 48
    assert [entry["seq"] for entry in records] == [entry["seq"] for entry in db_records]
    assert {(r["details"]["writer"], r["details"]["index"]) for r in records} == {
        (writer, index) for writer in range(4) for index in range(12)}


def test_runtime_overlaps_independent_stages_and_keeps_dependencies_and_order(journal_store, monkeypatch):
    settings, repo, _config, _run = journal_store
    source = settings.data_dir / "media" / "synthetic.mp4"
    source.write_bytes(b"No actual codec required: media operations are local fakes")
    media = repo.create_media({"original_path": "media/synthetic.mp4", "duration_us": 8_000_000,
                               "is_demo": True})
    config = RunConfig(provider="fixture", profile="point", core_window_s=2,
                       context_s=0, scan_fps=4, refine_fps=6, refine_padding_s=0.5,
                       model_concurrency=2, clip_concurrency=2,
                       max_input_frames=128, max_calls=20)
    run, _ = repo.create_run({"media_id": media["id"], "query": "Find every contact",
                              "config": config.model_dump(mode="json")})
    prep_entered, query_entered = threading.Event(), threading.Event()
    lock = threading.Lock()
    active = {"scan": 0, "refine": 0, "clip": 0}
    peaks = dict(active)
    gates = {stage: threading.Barrier(2) for stage in active}
    completions = {stage: [] for stage in active}
    # Every lower-time model request waits until its adjacent higher-time request has
    # completed its provider step, creating deliberately out-of-order completion.
    later_done = {(stage, pair): threading.Event() for stage in active for pair in range(2)}

    @contextmanager
    def parallel_stage(stage, event_index):
        with lock:
            active[stage] += 1
            peaks[stage] = max(peaks[stage], active[stage])
            assert active[stage] <= 2
        try:
            gates[stage].wait(timeout=WAIT_S)
            pair = event_index // 2
            if event_index % 2 == 0:
                assert later_done[(stage, pair)].wait(timeout=WAIT_S)
            yield
            with lock:
                completions[stage].append(event_index)
            if event_index % 2:
                later_done[(stage, pair)].set()
        finally:
            with lock:
                active[stage] -= 1

    def prepare(_media, _settings, cancelled):
        prep_entered.set()
        assert query_entered.wait(timeout=WAIT_S), "Query normalization was serialized behind media prepare"
        assert not cancelled()
        return {"duration_us": 8_000_000, "frame_count": 32}

    def sample(_media, start_us, end_us, fps, max_frames, output_dir, _settings, **_):
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        count = max(1, int((end_us - start_us) * fps / 1_000_000))
        assert count <= max_frames
        frames = []
        for i in range(count):
            stamp = start_us + round(i * 1_000_000 / fps)
            path = output_dir / f"{i}.jpg"
            path.write_bytes(b"frame bytes consumed only by fake provider")
            frames.append({"frame_id": f"frame_{stamp}", "source_time_us": stamp,
                           "local_time_s": (stamp - start_us) / 1_000_000, "path": str(path)})
        return {"frames": frames, "input_origin_us": start_us,
                "source_range_us": [start_us, end_us], "max_gap_us": round(1_000_000 / fps),
                "requested_fps": fps, "actual_sampling_known": True}

    def event_for(anchor_us, request):
        local = (anchor_us - request["input_origin_us"]) / 1_000_000
        closest = min(request["frames"], key=lambda f: abs(f["source_time_us"] - anchor_us))
        return {"kind": "point", "decision": "supported", "anchor_s": local,
                "anchor_range_s": [local, local], "entity_key": f"object_{anchor_us}",
                "evidence_frame_ids": [closest["frame_id"]], "reason": "Declared test contact"}

    class Provider:
        def analyze(self, operation, request):
            if operation == "normalize_query":
                query_entered.set()
                assert prep_entered.wait(timeout=WAIT_S), "Media prepare was serialized behind normalization"
                return ProviderReply(QuerySpec(raw_query=request["raw_query"], event_kind="point").model_dump(), {})
            if operation == "propose":
                a, b = request["source_range_us"]
                anchor = (a + b) // 2
                index = a // 2_000_000
                with parallel_stage("scan", index):
                    return ProviderReply({"complete": True, "events": [event_for(anchor, request)]}, {})
            assert operation == "verify_refine"
            tasks = repo.list_tasks(run["id"])
            assert len([t for t in tasks if t["stage"] == "scan" and t["status"] == "succeeded"]) == 4
            assert len(request["candidates"]) == 1
            candidate = request["candidates"][0]
            anchor = candidate["location"]["anchor_us"]
            index = (anchor - 1_000_000) // 2_000_000
            with parallel_stage("refine", index):
                return ProviderReply({"complete": True, "events": [event_for(anchor, request)],
                                      "candidate_dispositions": [{
                                          "candidate_id": candidate["candidate_id"],
                                          "disposition": "mapped_to_event", "event_indices": [0]}]}, {})

    def cut(_media, start_us, end_us, output_path, _settings, cancelled):
        tasks = repo.list_tasks(run["id"])
        assert len([t for t in tasks if t["stage"] == "refine" and t["status"] == "succeeded"]) == 4
        # Point clips use the default one-second context before/after each known anchor.
        index = start_us // 2_000_000
        with parallel_stage("clip", index):
            assert not cancelled()
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_bytes(b"validity tested by separate real-media tests")
            return {"actual_range_us": [start_us, end_us], "frame_count": 1}

    monkeypatch.setattr(pipeline, "make_provider", lambda *_: Provider())
    monkeypatch.setattr(pipeline.media_io, "prepare_media", prepare)
    monkeypatch.setattr(pipeline.media_io, "sample_frames", sample)
    monkeypatch.setattr(pipeline.media_io, "cut_clip", cut)
    pipeline.execute_run(run, repo, settings)
    current = repo.get_run(run["id"])
    assert current["status"] == "completed", current
    assert peaks == {"scan": 2, "refine": 2, "clip": 2}
    for stage in completions:
        assert completions[stage].index(1) < completions[stage].index(0)
        assert completions[stage].index(3) < completions[stage].index(2)
    events = current["results"]["events"]
    assert [e["location"]["anchor_us"] for e in events] == [1_000_000, 3_000_000, 5_000_000, 7_000_000]
    assert all(e["clip_status"] == "succeeded" for e in events)
    assert current["results"]["coverage"]["covered_us"] == 8_000_000
    assert current["results"]["coverage"]["gaps"] == []
    folder = settings.data_dir / "runs" / run["id"]
    for path in folder.rglob("*.json"):
        json.loads(path.read_text())
    debug = [json.loads(line) for line in (folder / "debug.jsonl").read_text().splitlines()]
    assert len({entry["seq"] for entry in debug}) == len(debug)
    assert all(entry["run_id"] == run["id"] for entry in debug)
