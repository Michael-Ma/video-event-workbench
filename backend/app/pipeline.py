"""Offline, journaled proposal -> verify/refine -> clip pipeline."""
from __future__ import annotations

import json
import math
import os
import threading
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from . import media as media_io
from .algorithms import (
    InvalidModelOutput,
    classify_event,
    clip_range,
    coverage,
    extent,
    group_candidates,
    map_model_event,
    model_evidence_refs,
    plan_windows,
    reconcile_events,
    select_occurrences,
    split_window,
)
from .billing import estimate_call_cost, pricing_snapshot
from .config import Settings
from .contracts import (
    Candidate,
    ClipResult,
    EventResult,
    ModelResponse,
    QuerySpec,
    RunConfig,
    Window,
)
from .db import Repository, now
from .providers import PROMPT_VERSION, ProviderError, ProviderReply, make_provider


class Cancelled(RuntimeError):
    pass


class CallBudgetExceeded(RuntimeError):
    pass


class IncompleteResponse(RuntimeError):
    pass


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _safe_path(settings: Settings, relative: str) -> Path:
    path = (settings.data_dir / relative).resolve()
    if not path.is_relative_to(settings.data_dir.resolve()):
        raise ValueError("Artifact path is outside the data directory")
    return path


def _source_signature(media: dict, settings: Settings) -> dict:
    stat = _safe_path(settings, media["original_path"]).stat()
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _prepared_available(media: dict, settings: Settings) -> bool:
    try:
        if media.get("prepared_source_signature") != _source_signature(media, settings):
            return False
        for key in ("frame_index_path", "preview_path", "preview_mapping_path"):
            if not media.get(key) or not _safe_path(settings, media[key]).is_file():
                return False
        index = json.loads(_safe_path(settings, media["frame_index_path"]).read_text())
        return (index.get("version") == media_io.INDEX_VERSION and index.get("media_id") == media["id"]
                and index.get("original_path") == media["original_path"]
                and index.get("duration_us") == media["duration_us"]
                and len(index.get("frames", [])) == media["frame_count"] > 0)
    except (OSError, ValueError, KeyError, TypeError):
        return False


class Journal:
    def __init__(self, run: dict, repo: Repository, settings: Settings, config: RunConfig,
                 provider):
        self.run_id = run["id"]
        self.repo, self.settings, self.config, self.provider = repo, settings, config, provider
        self.folder = _safe_path(settings, f"runs/{self.run_id}")
        self.folder.mkdir(parents=True, exist_ok=True)
        self._submission_lock = threading.RLock()
        self._log_lock = threading.RLock()
        self._active_tasks: set[str] = set()
        self._model_slots = threading.BoundedSemaphore(config.model_concurrency)
        self._unknown_task_id: str | None = None
        if callable(getattr(provider, "bind_cancelled", None)):
            provider.bind_cancelled(self.cancelled)

    def cancelled(self) -> bool:
        return self.repo.is_cancelled(self.run_id)

    def check_cancelled(self) -> None:
        if self.cancelled():
            raise Cancelled("Run was cancelled")

    def relative(self, path: Path) -> str:
        return str(path.resolve().relative_to(self.settings.data_dir.resolve()))

    def log(self, stage: str, code: str, message: str, level="info", **details):
        with self._log_lock:
            entry = self.repo.log(self.run_id, stage, code, message, level, details)
            with (self.folder / "debug.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def call_count(self) -> int:
        return sum(bool(t.get("request_intent")) for t in self.repo.list_tasks(self.run_id))

    def preflight(self, task_id: str):
        with self._submission_lock:
            self.check_cancelled()
            tasks = self.repo.list_tasks(self.run_id)
            existing = next((t for t in tasks if t["task_id"] == task_id), None)
            if existing and existing.get("response_path"):
                return
            if self._unknown_task_id is not None:
                raise ProviderError("blocked_by_unknown_request", "Unsettled request intent stops new model calls",
                                    request_unknown=True, details={"blocking_task_id": self._unknown_task_id})
            unsettled = next((t for t in tasks if t["status"] == "request_unknown" or
                              (t["status"] == "submitting" and t["task_id"] not in self._active_tasks)), None)
            if unsettled:
                raise ProviderError("blocked_by_unknown_request", "Unsettled request intent stops new model calls",
                                    request_unknown=True, details={"blocking_task_id": unsettled["task_id"]})
            if self.call_count() >= self.config.max_calls:
                raise CallBudgetExceeded("Maximum model request count reached")

    def _validate(self, reply: ProviderReply, operation: str):
        if reply.finish_reason in ("MAX_TOKENS", "LENGTH"):
            raise IncompleteResponse("Model output was truncated")
        if reply.finish_reason != "STOP":
            raise InvalidModelOutput(f"Model did not finish normally ({reply.finish_reason})")
        if reply.error or reply.payload is None:
            raise InvalidModelOutput(reply.error or "Empty model response")
        model = QuerySpec if operation == "normalize_query" else ModelResponse
        parsed = model.model_validate(reply.payload)
        if isinstance(parsed, ModelResponse):
            if not parsed.complete or len(parsed.events) >= self.config.max_events_per_window:
                raise IncompleteResponse("Response did not enumerate the full input")
        return parsed

    def log_call_cost(self, stage: str, operation: str, task_id: str, attempt_id: str,
                      cost: dict, *, usage: dict | None = None, metrics: dict | None = None,
                      artifact_path: Path):
        if cost["status"] == "estimated":
            message = f"Gemini {operation}: 估算费用 ${cost['estimated_usd']:.6f} USD（付费标准价）"
        elif cost["status"] == "not_billable":
            message = f"Fixture {operation}: 本地调用，费用 $0 USD"
        else:
            message = f"Gemini {operation}: 费用未知，未计为 $0（{cost['reason']}）"
        self.log(stage, "model_call_cost", message, task_id=task_id, attempt_id=attempt_id,
                 operation=operation, model_id=self.config.model_id, cost=cost,
                 usage=usage or {}, request_metrics=metrics or {},
                 artifact_refs=[self.relative(artifact_path)])

    def call(self, task_id: str, stage: str, operation: str, request: dict, **fields):
        while not self._model_slots.acquire(timeout=0.1):
            self.check_cancelled()
        try:
            return self._call_reserved(task_id, stage, operation, request, **fields)
        finally:
            self._model_slots.release()

    def _call_reserved(self, task_id: str, stage: str, operation: str, request: dict, **fields):
        # Reserve the budget and durable intent atomically; never hold this lock over
        # the remote request. Only this live Journal owns its active submitting tasks.
        with self._submission_lock:
            self.check_cancelled()
            existing = next((t for t in self.repo.list_tasks(self.run_id)
                             if t["task_id"] == task_id), None)
            if existing and existing.get("response_path"):
                saved = json.loads(_safe_path(self.settings, existing["response_path"]).read_text())
                reply = ProviderReply(**saved)
                parsed = self._validate(reply, operation)
                self.log(stage, "response_reused", "Reused a persisted response", task_id=task_id)
                return parsed
            if existing and existing["status"] in ("submitting", "request_unknown"):
                if task_id in self._active_tasks:
                    raise ProviderError("task_in_flight", "This task already has a live request")
                self.repo.upsert_task(self.run_id, task_id, stage, "request_unknown")
                raise ProviderError("request_unknown", "A prior request outcome is unknown; "
                                    "it was not sent again", request_unknown=True)
            if existing and existing["status"] == "failed" and existing.get("request_intent"):
                raise ProviderError(existing.get("error_code", "prior_request_failed"),
                                    "A prior request failed; its paid call is not repeated")
            self.preflight(task_id)
            attempt_id = uuid.uuid4().hex
            submitted_at = now()
            mode = "text" if operation == "normalize_query" else request.get("input_mode", "images")
            pricing = pricing_snapshot(self.config.provider, self.config.model_id,
                                       date.fromisoformat(submitted_at[:10]))
            attempt_folder = self.folder / stage / task_id / attempt_id
            request_path = attempt_folder / "request.json"
            response_path = attempt_folder / "response.json"
            debug_request = dict(request)
            debug_request["frames"] = [
                {**f, "path": self.relative(Path(f["path"]))} if f.get("path") else dict(f)
                for f in request.get("frames", [])]
            if request.get("video"):
                debug_request["video"] = {**request["video"],
                                           "path": self.relative(Path(request["video"]["path"]))}
            _write_json(request_path, {
                "operation": operation, "prompt_version": PROMPT_VERSION,
                "model_id": self.config.model_id, "provider": self.config.provider,
                "request": debug_request, "pricing": pricing,
            })
            self.repo.upsert_task(
                self.run_id, task_id, stage, "submitting", attempt_id=attempt_id,
                request_intent={"operation": operation, "created_at": submitted_at,
                                "model_id": self.config.model_id, "input_mode": mode,
                                "pricing": pricing},
                request_path=self.relative(request_path), **{**fields, "input_mode": mode},
            )
            self._active_tasks.add(task_id)
            self.log(stage, "request_submitting", "Persisted request intent before model submission",
                     task_id=task_id, attempt_id=attempt_id, input_mode=mode,
                     active_requests=len(self._active_tasks),
                     artifact_refs=[self.relative(request_path)])
        started = time.monotonic()
        try:
            try:
                reply = self.provider.analyze(operation, request)
            except ProviderError as exc:
                if exc.request_unknown:
                    with self._submission_lock:
                        self._unknown_task_id = task_id
                status = "request_unknown" if exc.request_unknown else "failed"
                cost = estimate_call_cost(pricing, {}, outcome_unknown=exc.request_unknown)
                error_path = attempt_folder / "error.json"
                _write_json(error_path, {"code": exc.code, "message": str(exc), "details": exc.details,
                                         "request_unknown": exc.request_unknown, "cost": cost})
                self.repo.upsert_task(self.run_id, task_id, stage, status,
                                      error_code=exc.code, error=str(exc), error_details=exc.details,
                                      error_path=self.relative(error_path), cost=cost,
                                      request_metrics=exc.details)
                self.log_call_cost(stage, operation, task_id, attempt_id, cost,
                                   metrics=exc.details, artifact_path=error_path)
                self.log(stage, exc.code, str(exc), level="error", task_id=task_id,
                         error_details=exc.details, artifact_refs=[self.relative(error_path)])
                raise
            except Exception as exc:
                with self._submission_lock:
                    self._unknown_task_id = task_id
                cost = estimate_call_cost(pricing, {}, outcome_unknown=True)
                error_path = attempt_folder / "error.json"
                _write_json(error_path, {"code": "adapter_unknown", "request_unknown": True, "cost": cost})
                self.repo.upsert_task(self.run_id, task_id, stage, "request_unknown",
                                      error_code="adapter_unknown", cost=cost,
                                      error_path=self.relative(error_path))
                self.log_call_cost(stage, operation, task_id, attempt_id, cost,
                                   artifact_path=error_path)
                raise ProviderError("request_unknown", "Unexpected adapter failure; request outcome "
                                    "is unknown", request_unknown=True) from exc
            reply.cost = estimate_call_cost(pricing, reply.usage)
            metrics = reply.raw.get("request_metrics", {
                "elapsed_s": round(time.monotonic() - started, 3),
                "latency_scope": "provider_adapter", "input_mode": mode,
            })
            _write_json(response_path, {
                "payload": reply.payload, "raw": reply.raw, "usage": reply.usage,
                "finish_reason": reply.finish_reason, "error": reply.error, "cost": reply.cost,
            })
            current = next(t for t in self.repo.list_tasks(self.run_id) if t["task_id"] == task_id)
            if current.get("attempt_id") != attempt_id:
                raise ProviderError("stale_attempt", "A newer attempt owns this task")
            self.repo.upsert_task(self.run_id, task_id, stage, current["status"], cost=reply.cost,
                                  usage=reply.usage, request_metrics=metrics)
            self.log_call_cost(stage, operation, task_id, attempt_id, reply.cost, usage=reply.usage,
                               metrics=metrics, artifact_path=response_path)
            if self.cancelled():
                self.repo.upsert_task(self.run_id, task_id, stage, "cancelled",
                                      response_path=self.relative(response_path), usage=reply.usage)
                raise Cancelled("Response arrived after cancellation")
            try:
                parsed = self._validate(reply, operation)
            except (ValidationError, InvalidModelOutput, IncompleteResponse) as exc:
                self.repo.upsert_task(self.run_id, task_id, stage, "failed",
                                      response_path=self.relative(response_path), usage=reply.usage,
                                      error_code="invalid_response", error=str(exc))
                self.log(stage, "invalid_response", str(exc), level="warning", task_id=task_id,
                         artifact_refs=[self.relative(response_path)])
                raise
            self.repo.upsert_task(self.run_id, task_id, stage, "succeeded",
                                  response_path=self.relative(response_path), usage=reply.usage)
            self.log(stage, "response_received", "Saved and validated model response",
                     task_id=task_id, artifact_refs=[self.relative(response_path)])
            return parsed
        finally:
            with self._submission_lock:
                self._active_tasks.discard(task_id)



def _model_request(spec: QuerySpec, manifest: dict, config: RunConfig,
                   candidates: list[Candidate] | None = None) -> dict:
    origin = manifest["input_origin_us"]
    candidate_inputs = []
    for candidate in candidates or []:
        source = candidate.model_dump(mode="json")
        local = {}
        for key, value in source["location"].items():
            if key.endswith("_us") and value is not None:
                local[key.replace("_us", "_s")] = (
                    [(v - origin) / 1_000_000 for v in value] if isinstance(value, list)
                    else (value - origin) / 1_000_000)
            else:
                local[key] = value
        source["local_location"] = local
        candidate_inputs.append(source)
    return {
        "raw_query": spec.raw_query, "query_spec": spec.model_dump(mode="json"),
        "frames": manifest["frames"], "input_origin_us": origin,
        "source_range_us": manifest["source_range_us"],
        "local_input_end_s": (manifest["source_range_us"][1] - origin) / 1_000_000,
        "max_observed_gap_us": manifest.get("max_gap_us"),
        "max_events": config.max_events_per_window,
        "candidates": candidate_inputs,
        "input_mode": manifest.get("input_mode", "images"),
        "sample_fps": manifest.get("sample_fps"),
        **({"video": manifest["video"]} if manifest.get("video") else {}),
    }


def _parallel_apply(items, operation, workers: int, check_cancelled) -> None:
    """Bound submitted work as well as running work; drain receipts before exiting."""
    source = iter(items)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = set()

        def submit_next():
            check_cancelled()
            try:
                item = next(source)
            except StopIteration:
                return False
            pending.add(pool.submit(operation, item))
            return True

        for _ in range(workers):
            if not submit_next():
                break
        while pending:
            finished, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in finished:
                pending.remove(future)
                future.result()
                submit_next()


def _prepare_input(media: dict, start_us: int, end_us: int, fps: float,
                   mode: str, output: Path, settings: Settings, config: RunConfig,
                   cancelled) -> dict:
    prepare = media_io.prepare_video_input if mode == "video" else media_io.sample_frames
    manifest = prepare(media, start_us, end_us, fps, config.max_input_frames, output, settings,
                       max_width=config.max_frame_width, cancelled=cancelled)
    manifest.update(input_mode=mode, sample_fps=fps, media_id=media["id"])
    return manifest


def _fallback(candidate: Candidate, reason: str, event_id: str,
              decision: str = "unresolved") -> EventResult:
    return EventResult(
        event_id=event_id, source_candidate_ids=[candidate.candidate_id], decision=decision,
        boundary_status="unknown", result_bucket="rejected" if decision == "rejected" else "uncertain",
        location=candidate.location.model_copy(deep=True), entity_key=candidate.entity_key,
        evidence_refs=candidate.evidence_refs, reason=reason,
        uncertainty_reasons=[] if decision == "rejected" else [reason],
        clip_status="not_required" if decision == "rejected" else "pending",
    )


def refinement_results(response: ModelResponse, candidates: list[Candidate], manifest: dict,
                       duration_us: int, spec: QuerySpec, config: RunConfig,
                       group_id: str) -> list[EventResult]:
    """Every input proposal is accounted for; empty output is not blanket rejection."""
    candidate_ids = {c.candidate_id for c in candidates}
    seen, mappings = set(), {i: [] for i in range(len(response.events))}
    dispositions = {}
    for disposition in response.candidate_dispositions:
        cid = disposition.candidate_id
        if cid not in candidate_ids or cid in seen:
            raise InvalidModelOutput("Unknown or duplicate candidate disposition")
        seen.add(cid)
        if disposition.disposition == "mapped_to_event":
            if not disposition.event_indices or any(i not in mappings for i in disposition.event_indices):
                raise InvalidModelOutput("Candidate mapping has absent/out-of-range event indices")
            for i in set(disposition.event_indices):
                mappings[i].append(cid)
        elif disposition.event_indices:
            if (any(i not in mappings for i in disposition.event_indices) or
                    any(response.events[i].decision != disposition.disposition
                        for i in disposition.event_indices)):
                raise InvalidModelOutput("Rejected/unresolved mapping contradicts the output event decision")
            # Some replies express the matching decision in both fields while also
            # explicitly identifying the corresponding event. Preserve that decision,
            # validate the association, and never turn it into a supported match.
            for i in set(disposition.event_indices):
                mappings[i].append(cid)
            disposition = disposition.model_copy(update={"disposition": "mapped_to_event"})
        dispositions[cid] = disposition
    results = []
    for index, event in enumerate(response.events):
        if not mappings[index]:
            raise InvalidModelOutput("Output event has no source candidate mapping")
        location = map_model_event(event, manifest, duration_us, spec.event_kind,
                                   enforce_sampling_uncertainty=config.provider != "fixture")
        result = EventResult(
            event_id=f"event_{group_id}_{index:03d}", source_candidate_ids=mappings[index],
            decision=event.decision, boundary_status="unknown", result_bucket="uncertain",
            location=location, entity_key=event.entity_key,
            evidence_refs=model_evidence_refs(event, manifest), reason=event.reason,
            uncertainty_reasons=event.uncertainty_reasons,
        )
        results.append(classify_event(result, config.boundary_tolerance_ms * 1000, duration_us))
    for index, candidate in enumerate(candidates):
        disposition = dispositions.get(candidate.candidate_id)
        if disposition is None or disposition.disposition != "mapped_to_event":
            rejected = disposition is not None and disposition.disposition == "rejected"
            reason = (disposition.reason if disposition and disposition.reason else
                      "candidate_not_accounted_for" if disposition is None else "candidate_unresolved")
            result = _fallback(candidate, reason, f"event_{group_id}_unresolved_{index:03d}",
                               "rejected" if rejected else "unresolved")
            results.append(classify_event(result, config.boundary_tolerance_ms * 1000, duration_us))
    return results



def _results_snapshot(run: dict, media: dict, spec: QuerySpec, config: RunConfig,
                      events: list[EventResult], candidate_count: int, cov: dict,
                      calls: int, errors: list[dict], *, provisional: bool,
                      omitted: list[str] | None = None) -> dict:
    records = [event.model_dump(mode="json") for event in events]
    if provisional:
        for event in records:
            if event["result_bucket"] == "matched":
                event["result_bucket"] = "uncertain"
                event["uncertainty_reasons"] = list(dict.fromkeys(
                    event["uncertainty_reasons"] + ["pending_global_reconciliation"]))
    stats = {
        "candidate_count": candidate_count, "model_calls": calls,
        "matched": sum(e["result_bucket"] == "matched" and not e.get("duplicate_of") for e in records),
        "uncertain": sum(e["result_bucket"] == "uncertain" and not e.get("duplicate_of") for e in records),
        "rejected": sum(e["result_bucket"] == "rejected" for e in records),
        "duplicates": sum(e.get("duplicate_of") is not None for e in records),
        "clips_succeeded": sum(e["clip_status"] == "succeeded" for e in records),
        "clips_failed": sum(e["clip_status"] == "failed" for e in records),
    }
    limitations = [
        "Scan completion records successful core windows, not a guarantee of finding every event.",
        "Model inputs contain no audio; short events can fall between sampled observations.",
        "Native-video evidence references timestamps; server-side sampling is not an observed JPEG log.",
        "Model decisions and uncertainty are automatic outputs, not human-verified labels.",
    ]
    if config.provider == "fixture":
        limitations.insert(0, "DEMO FIXTURE: results use declared events, not semantic video inference.")
    return {"run_id": run["id"], "media_id": media["id"], "provider": config.provider,
            "model_id": config.model_id, "query_spec": spec.model_dump(mode="json"),
            "config": config.model_dump(mode="json"), "prompt_version": PROMPT_VERSION,
            "events": records, "scan_complete": not cov["gaps"], "coverage": cov,
            "stats": stats, "limitations": limitations, "errors": list(errors),
            "omitted_event_ids": omitted or [], "provisional": provisional}

def execute_run(run: dict, repo: Repository, settings: Settings) -> None:
    """Synchronous worker entry point. It never makes a paid call in fixture mode."""
    execution_started = time.monotonic()
    config = RunConfig.model_validate(run["config"])
    run_id = run["id"]
    journal = None
    try:
        media = repo.get_media(run["media_id"])
        provider = make_provider(settings, config, media)
        journal = Journal(run, repo, settings, config, provider)
        journal.check_cancelled()
        _write_json(journal.folder / "input.json", {
            "media_id": media["id"], "raw_query": run["query"],
            "config": config.model_dump(mode="json"), "prompt_version": PROMPT_VERSION,
        })
        repo.update_run(run_id, stage="preparing")
        def prepare_source():
            if _prepared_available(media, settings):
                journal.log("preparing", "media_reused", "Reused prepared immutable media and source frame index")
                return media
            updates = media_io.prepare_media(media, settings, journal.cancelled)
            return repo.update_media(media["id"], **updates,
                                     prepared_source_signature=_source_signature(media, settings))

        # Query parsing uses no video, so it overlaps indexing/proxy preparation.
        with ThreadPoolExecutor(max_workers=2) as preparation:
            media_future = preparation.submit(prepare_source)
            query_future = preparation.submit(journal.call, "normalize_query", "query", "normalize_query", {
                "raw_query": run["query"], "profile": config.profile,
            })
            media = media_future.result()
            journal.check_cancelled()
            repo.update_run(run_id, stage="normalizing_query")
            spec = query_future.result()
        duration = media["duration_us"]
        if spec.raw_query != run["query"]:
            journal.log("query", "raw_query_preserved", "Ignored model rewrite of original query")
        spec.raw_query = run["query"]
        repo.update_run(run_id, query_spec=spec.model_dump(mode="json"), stage="planning")
        windows = plan_windows(duration, spec, config)
        effective_windows = {w.window_id: w for w in windows}
        successful_ranges: list[tuple[int, int]] = []
        candidates: list[Candidate] = []
        candidate_windows: dict[str, Window] = {}
        scan_errors, refine_errors, clip_errors = [], [], []
        events: list[EventResult] = []
        refine_total, refine_done, clips_total, clips_done = 0, 0, 0, 0
        state_lock = threading.RLock()

        def publish_snapshot(*, provisional: bool):
            journal.check_cancelled()
            snapshot = _results_snapshot(
                run, media, spec, config, events, len(candidates),
                coverage(duration, successful_ranges), journal.call_count(),
                scan_errors + refine_errors + clip_errors, provisional=provisional)
            _write_json(journal.folder / "progress_results.json", snapshot)
            repo.update_run(run_id, results=snapshot)


        def save_plan():
            with state_lock:
                _write_json(journal.folder / "window_plan.json", {
                    "initial_windows": [w.model_dump(mode="json") for w in windows],
                    "effective_windows": [w.model_dump(mode="json") for w in effective_windows.values()],
                    "coverage_basis": "successful leaf core ranges, not read/context ranges",
                })

        def update_progress(stage: str):
            current = coverage(duration, successful_ranges)
            repo.update_run(run_id, stage=stage, progress={
                "coverage": current, "scan_complete": not current["gaps"],
                "scan_total_windows": len(effective_windows),
                "scan_completed_windows": len(successful_ranges),
                "candidate_count": len(candidates), "model_calls": journal.call_count(),
                "refine_total_groups": refine_total, "refine_completed_groups": refine_done,
                "clip_total": clips_total, "clip_completed": clips_done,
            })

        save_plan()
        publish_snapshot(provisional=True)
        existing_ids = {t["task_id"] for t in repo.list_tasks(run_id)}
        for window in windows:
            if window.window_id not in existing_ids:
                repo.upsert_task(run_id, window.window_id, "scan", "pending",
                                 window=window.model_dump(), **window.model_dump())
        journal.log("planning", "window_plan", f"Planned {len(windows)} exhaustive core windows",
                    artifact_refs=[journal.relative(journal.folder / "window_plan.json")])

        def scan(window: Window, depth: int = 0):
            journal.check_cancelled()
            task_id = window.window_id
            fields = {**window.model_dump(), "window": window.model_dump()}
            try:
                journal.preflight(task_id)
                manifest = _prepare_input(
                    media, window.read_start_us, window.read_end_us, window.sample_fps,
                    config.scan_input_mode, journal.folder / "inputs" / task_id,
                    settings, config, journal.cancelled,
                )
                response = journal.call(task_id, "scan", "propose",
                                        _model_request(spec, manifest, config), **fields,
                                        sampling={k: v for k, v in manifest.items() if k != "frames"})
                invalid = []
                local_candidates = []
                for i, event in enumerate(response.events):
                    try:
                        location = map_model_event(event, manifest, duration, spec.event_kind,
                                                   enforce_sampling_uncertainty=config.provider != "fixture")
                    except (InvalidModelOutput, ValidationError) as exc:
                        invalid.append(str(exc))
                        continue
                    if event.decision == "rejected":
                        continue
                    candidate = Candidate(
                        candidate_id=f"{task_id}_{i:03d}", window_id=task_id, location=location,
                        entity_key=event.entity_key, evidence_refs=model_evidence_refs(event, manifest),
                        reason=event.reason,
                    )
                    local_candidates.append(candidate)
                with state_lock:
                    candidates.extend(local_candidates)
                    candidate_windows.update({c.candidate_id: window for c in local_candidates})
                if invalid:
                    repo.upsert_task(run_id, task_id, "scan", "failed",
                                     error_code="invalid_event", errors=invalid, **fields)
                    scan_errors.append({"window_id": task_id, "code": "invalid_event"})
                    journal.log("scan", "invalid_event", "Rejected invalid event timestamps/evidence",
                                level="warning", task_id=task_id, errors=invalid)
                else:
                    with state_lock:
                        successful_ranges.append((window.core_start_us, window.core_end_us))
                    journal.log("scan", "window_complete", f"Window returned {len(response.events)} candidates",
                                task_id=task_id, candidate_count=len(response.events))
            except IncompleteResponse as exc:
                children = split_window(window) if depth < 2 else []
                if children:
                    with state_lock:
                        effective_windows.pop(task_id, None)
                        effective_windows.update({child.window_id: child for child in children})
                    repo.upsert_task(run_id, task_id, "scan", "failed",
                                     replacement_task_ids=[c.window_id for c in children], **fields)
                    save_plan()
                    journal.log("scan", "window_split", "Split an incomplete window; retained its raw reply",
                                task_id=task_id, children=[c.window_id for c in children])
                    _parallel_apply(children, lambda child: scan(child, depth + 1),
                                    config.model_concurrency, journal.check_cancelled)
                else:
                    scan_errors.append({"window_id": task_id, "code": "incomplete_response"})
                    journal.log("scan", "incomplete_response", str(exc), level="warning", task_id=task_id)
            except Cancelled:
                raise
            except Exception as exc:
                if journal.cancelled():
                    raise Cancelled() from exc
                code = getattr(exc, "code", "call_budget" if isinstance(exc, CallBudgetExceeded)
                               else "scan_failed")
                scan_errors.append({"window_id": task_id, "task_id": task_id, "stage": "scan",
                                    "code": code, "message": str(exc),
                                    "details": getattr(exc, "details", {})})
                old = next((t for t in repo.list_tasks(run_id) if t["task_id"] == task_id), {})
                status = ("request_unknown" if old.get("status") == "request_unknown" else
                          "interrupted" if code in ("blocked_by_unknown_request", "call_budget") else "failed")
                repo.upsert_task(run_id, task_id, "scan", status, error_code=code,
                                 error=str(exc), error_details=getattr(exc, "details", {}), **fields)
                if old.get("error_code") != code:
                    journal.log("scan", code, str(exc), level="error", task_id=task_id)
            with state_lock:
                update_progress("scanning")
                publish_snapshot(provisional=True)

        repo.update_run(run_id, stage="scanning")
        _parallel_apply(windows, scan, config.model_concurrency, journal.check_cancelled)
        candidates.sort(key=lambda c: ((extent(c.location) or (0, 0))[0], c.candidate_id))
        _write_json(journal.folder / "candidates.json", [c.model_dump(mode="json") for c in candidates])
        journal.check_cancelled()
        cap_us = min(round(config.max_refine_window_s * 1_000_000),
                     math.floor((config.max_input_frames - 2) / config.refine_fps * 1_000_000))
        groups = group_candidates(candidates, cap_us)
        refine_total = len(groups)
        _write_json(journal.folder / "candidate_groups.json", [[c.candidate_id for c in g] for g in groups])
        journal.log("grouping", "candidate_groups", f"Associated {len(candidates)} candidates into {len(groups)} groups",
                    artifact_refs=[journal.relative(journal.folder / "candidate_groups.json")])
        fallback_ranges: dict[str, tuple[int, int]] = {}

        def refine(item):
            nonlocal refine_done
            gi, group = item
            journal.check_cancelled()
            group_id = f"g{gi:05d}"
            bounds = [extent(c.location) or (candidate_windows[c.candidate_id].read_start_us,
                                             candidate_windows[c.candidate_id].read_end_us) for c in group]
            full_a, full_b = min(p[0] for p in bounds), max(p[1] for p in bounds)
            padding = round(config.refine_padding_s * 1_000_000)
            a, b = max(0, full_a - padding), min(duration, full_b + padding)
            if b - a > cap_us:
                # Never reduce sampling density to fit an oversized activity silently.
                b = a + cap_us
            if b <= a:
                b = min(duration, a + max(1, round(1_000_000 / config.refine_fps)))
            group_events = []
            repo.update_run(run_id, stage="refining")
            for expansion in range(config.max_refinement_expansions + 1):
                task_id = f"refine_{group_id}_{expansion}"
                fields = {"candidate_ids": [c.candidate_id for c in group],
                          "read_start_us": a, "read_end_us": b, "expansion": expansion}
                try:
                    journal.preflight(task_id)
                    manifest = _prepare_input(
                        media, a, b, config.refine_fps, config.refine_input_mode,
                        journal.folder / "inputs" / task_id, settings, config, journal.cancelled,
                    )
                    response = journal.call(task_id, "refine", "verify_refine",
                                            _model_request(spec, manifest, config, group), **fields,
                                            sampling={k: v for k, v in manifest.items() if k != "frames"})
                    group_events = refinement_results(response, group, manifest, duration,
                                                      spec, config, group_id)
                    compatible = [d.candidate_id for d in response.candidate_dispositions
                                  if d.disposition != "mapped_to_event" and d.event_indices]
                    if compatible:
                        journal.log("refine", "candidate_mapping_normalized",
                                    "Preserved consistent rejected/unresolved event associations",
                                    level="warning", task_id=task_id, candidate_ids=compatible,
                                    matching_decisions_changed=False)
                    for event in group_events:
                        if event.decision != "rejected" and (full_a < a or full_b > b):
                            event.location.open_left |= full_a < a
                            event.location.open_right |= full_b > b
                            event.uncertainty_reasons.append("proposal_exceeds_refinement_input")
                            classify_event(event, config.boundary_tolerance_ms * 1000, duration)
                        fallback_ranges[event.event_id] = (a, b)
                    open_left = any(e.location.open_left for e in group_events if e.decision != "rejected")
                    open_right = any(e.location.open_right for e in group_events if e.decision != "rejected")
                    if not (open_left or open_right):
                        break
                    extend = max(padding, (b - a) // 2, 1)
                    new_a = max(0, a - extend) if open_left else a
                    new_b = min(duration, b + extend) if open_right else b
                    if (expansion >= config.max_refinement_expansions or new_b - new_a > cap_us
                            or (new_a, new_b) == (a, b)):
                        for event in group_events:
                            if event.location.open_left or event.location.open_right:
                                event.uncertainty_reasons.append("boundary_unresolved_within_refinement_budget")
                                event.result_bucket = "uncertain"
                        break
                    a, b = new_a, new_b
                except Cancelled:
                    raise
                except Exception as exc:
                    if journal.cancelled():
                        raise Cancelled() from exc
                    code = getattr(exc, "code", "call_budget" if isinstance(exc, CallBudgetExceeded)
                                   else "refinement_failed")
                    refine_errors.append({"group_id": group_id, "task_id": task_id, "stage": "refine",
                                          "code": code, "message": str(exc),
                                          "details": getattr(exc, "details", {})})
                    old = next((t for t in repo.list_tasks(run_id) if t["task_id"] == task_id), {})
                    status = "request_unknown" if old.get("status") == "request_unknown" else "failed"
                    repo.upsert_task(run_id, task_id, "refine", status,
                                     error_code=code, error=str(exc),
                                     error_details=getattr(exc, "details", {}), **fields)
                    journal.log("refine", code, str(exc), level="warning", task_id=task_id)
                    group_events = [_fallback(c, code, f"event_{group_id}_failed_{i:03d}")
                                    for i, c in enumerate(group)]
                    for event in group_events:
                        fallback_ranges[event.event_id] = (a, b)
                    break
            with state_lock:
                events.extend(group_events)
                refine_done += 1
                update_progress("refining")
                publish_snapshot(provisional=True)

        repo.update_run(run_id, stage="refining")
        _parallel_apply(enumerate(groups), refine, config.model_concurrency, journal.check_cancelled)
        def event_order(event):
            estimate = event.location.anchor_us if event.location.kind == "point" else event.location.start_us
            return (estimate if estimate is not None else
                    (extent(event.location) or (duration + 1, duration + 1))[0], event.event_id)

        events.sort(key=event_order)
        events = reconcile_events(events)
        cov = coverage(duration, successful_ranges)
        _write_json(journal.folder / "all_events.json", [e.model_dump(mode="json") for e in events])
        events, omitted = select_occurrences(events, spec.occurrence_policy, cov["gaps"])
        journal.log("reconciling", "events_reconciled", "Reconciled source evidence and global occurrence policy",
                    event_count=len(events), omitted_event_ids=omitted,
                    artifact_refs=[journal.relative(journal.folder / "all_events.json")])

        clips_total = sum(clip_range(event, config, duration, fallback_ranges.get(event.event_id)) is not None
                          for event in events)
        publish_snapshot(provisional=False)
        update_progress("clipping")
        def export_clip(event):
            nonlocal clips_done
            journal.check_cancelled()
            cut = clip_range(event, config, duration, fallback_ranges.get(event.event_id))
            if cut is None:
                event.clip_status = "not_required"
                return
            task_id = f"clip_{event.event_id}"
            attempt_id = uuid.uuid4().hex
            output = journal.folder / "clips" / event.result_bucket / event.event_id / f"{attempt_id}.mp4"
            repo.upsert_task(run_id, task_id, "clip", "running", attempt_id=attempt_id,
                             requested_range_us=cut)
            try:
                metadata = media_io.cut_clip(media, cut[0], cut[1], output, settings, journal.cancelled)
                journal.check_cancelled()
                current = next(t for t in repo.list_tasks(run_id) if t["task_id"] == task_id)
                if current.get("attempt_id") != attempt_id:
                    raise ValueError("A newer attempt owns this clip task")
                with state_lock:
                    event.clip = ClipResult(
                        path=journal.relative(output), cut_range_us=cut,
                        actual_range_us=metadata.get("actual_range_us"),
                        kind="context_fallback" if event.result_bucket == "uncertain" else "event_with_context",
                        metadata=metadata,
                    )
                    event.clip_status = "succeeded"
                repo.upsert_task(run_id, task_id, "clip", "succeeded",
                                 artifact_path=event.clip.path, metadata=metadata)
            except Cancelled:
                repo.upsert_task(run_id, task_id, "clip", "cancelled")
                raise
            except Exception as exc:
                if journal.cancelled():
                    raise Cancelled() from exc
                with state_lock:
                    event.clip_status = "failed"
                    clip_errors.append({"event_id": event.event_id, "task_id": task_id, "stage": "clip",
                                        "code": "clip_failed", "message": str(exc)})
                repo.upsert_task(run_id, task_id, "clip", "failed", error_code="clip_failed", error=str(exc))
                journal.log("clip", "clip_failed", str(exc), level="error", event_id=event.event_id)

            with state_lock:
                clips_done += 1
                update_progress("clipping")
                publish_snapshot(provisional=False)

        _parallel_apply(events, export_clip, config.clip_concurrency, journal.check_cancelled)
        results = _results_snapshot(
            run, media, spec, config, events, len(candidates), cov, journal.call_count(),
            scan_errors + refine_errors + clip_errors, provisional=False, omitted=omitted)
        stats = results["stats"]
        _write_json(journal.folder / "results.json", results)
        journal.check_cancelled()
        partial = bool(cov["gaps"] or refine_errors or clip_errors)
        codes = {error["code"] for error in results["errors"]}
        stop_reason = ("request_timeout" if "request_timeout" in codes else
                       "request_unknown" if codes & {"request_unknown", "blocked_by_unknown_request"} else
                       "call_budget" if "call_budget" in codes else "partial_processing" if partial else None)
        repo.update_run(run_id, status="partial" if partial else "completed", stage="completed",
                        results=results, stop_reason=stop_reason,
                        processing_elapsed_s=round(time.monotonic() - execution_started, 3),
                        progress={**repo.get_run(run_id).get("progress", {}), **stats,
                                  "coverage": cov, "scan_complete": not cov["gaps"]})
        journal.log("completed", "run_partial" if partial else "run_completed",
                    "Published automatic event results", stats=stats,
                    artifact_refs=[journal.relative(journal.folder / "results.json")])
    except Cancelled:
        if journal:
            journal.log("cancelled", "run_cancelled", "Stopped scheduling work; late replies remain archived")
    except Exception as exc:
        if repo.is_cancelled(run_id):
            return
        code = getattr(exc, "code", "call_budget" if isinstance(exc, CallBudgetExceeded) else "pipeline_failed")
        error = {"code": code, "message": str(exc), "details": getattr(exc, "details", {})}
        last_stage = repo.get_run(run_id)["stage"]
        repo.update_run(run_id, status="partial" if getattr(exc, "request_unknown", False) or code == "call_budget" else "failed",
                        stage="stopped", last_stage=last_stage, error=error, stop_reason=code,
                        processing_elapsed_s=round(time.monotonic() - execution_started, 3))
        if journal:
            journal.log("stopped", code, str(exc), level="error")
        else:
            repo.log(run_id, "stopped", code, str(exc), "error")
