"""Deterministic temporal bookkeeping; none of these rules establish semantic recall."""
from __future__ import annotations

import math
from bisect import bisect_left
from typing import Iterable

from .contracts import Candidate, EventResult, Location, ModelEvent, QuerySpec, RunConfig, Window


class InvalidModelOutput(ValueError):
    pass


def plan_windows(duration_us: int, spec: QuerySpec, config: RunConfig) -> list[Window]:
    if duration_us <= 0:
        raise ValueError("Video has no positive display duration")
    point = config.profile == "point" or (
        config.profile == "auto" and spec.event_kind == "point"
    )
    fps = config.scan_fps or (6.0 if point else 2.0)
    context_us = round((config.context_s if config.context_s is not None else
                        (2.0 if point else 5.0)) * 1_000_000)
    requested_core = round((config.core_window_s or (10.0 if point else 30.0)) * 1_000_000)
    # Leave room for endpoint rounding. Sampling density is never silently reduced.
    max_read_us = math.floor((config.max_input_frames - 2) / fps * 1_000_000)
    core_us = min(requested_core, max_read_us - 2 * context_us)
    if core_us < 1:
        raise ValueError("Frame budget cannot accommodate context at the requested FPS; "
                         "increase max_input_frames or reduce context_s")
    if math.ceil(duration_us / core_us) > 100_000:
        raise ValueError("Window plan exceeds the prototype's 100000-window limit")
    windows = []
    start = 0
    while start < duration_us:
        end = min(duration_us, start + core_us)
        windows.append(Window(
            window_id=f"window_{len(windows):05d}",
            core_start_us=start, core_end_us=end,
            read_start_us=max(0, start - context_us),
            read_end_us=min(duration_us, end + context_us), sample_fps=fps,
        ))
        start = end
    return windows


def split_window(window: Window) -> list[Window]:
    midpoint = (window.core_start_us + window.core_end_us) // 2
    if midpoint in (window.core_start_us, window.core_end_us):
        return []
    left_context = window.core_start_us - window.read_start_us
    right_context = window.read_end_us - window.core_end_us
    return [Window(
        window_id=window.window_id + suffix,
        core_start_us=start, core_end_us=end,
        read_start_us=max(window.read_start_us, start - left_context),
        read_end_us=min(window.read_end_us, end + right_context),
        sample_fps=window.sample_fps,
    ) for suffix, start, end in (
        ("a", window.core_start_us, midpoint), ("b", midpoint, window.core_end_us)
    )]


def coverage(duration_us: int, ranges: Iterable[tuple[int, int]]) -> dict:
    merged: list[list[int]] = []
    for a, b in sorted((max(0, a), min(duration_us, b)) for a, b in ranges):
        if b <= a:
            continue
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    gaps, cursor = [], 0
    for a, b in merged:
        if a > cursor:
            gaps.append([cursor, a])
        cursor = b
    if cursor < duration_us:
        gaps.append([cursor, duration_us])
    return {"duration_us": duration_us, "covered_us": sum(b - a for a, b in merged),
            "covered_ranges": merged, "gaps": gaps}


def model_evidence_refs(event: ModelEvent, manifest: dict) -> list[str]:
    """Native-video evidence is a timestamp reference, never a claimed JPEG observation."""
    if manifest.get("input_mode", "images") != "video":
        valid_ids = {f["frame_id"] for f in manifest.get("frames", [])}
        if any(ref not in valid_ids for ref in event.evidence_frame_ids):
            raise InvalidModelOutput("Evidence references a frame absent from this request")
        if event.evidence_times_s:
            raise InvalidModelOutput("Image evidence must reference supplied frame IDs")
        return event.evidence_frame_ids
    if event.evidence_frame_ids:
        raise InvalidModelOutput("Native video evidence must use timestamps, not JPEG frame IDs")
    origin = manifest["input_origin_us"]
    a, b = manifest["source_range_us"]
    references = []
    for value in event.evidence_times_s:
        if not math.isfinite(value) or value < 0:
            raise InvalidModelOutput("Video evidence timestamp is invalid")
        source = origin + round(value * 1_000_000)
        if not max(a, origin) <= source < b:
            raise InvalidModelOutput("Video evidence timestamp falls outside this input")
        references.append(f"video:{manifest.get('media_id', 'input')}:{source}")
    return list(dict.fromkeys(references))


def map_model_event(event: ModelEvent, manifest: dict, duration_us: int,
                    expected_kind: str, *, enforce_sampling_uncertainty: bool = True) -> Location:
    """Map explicit frame-timeline seconds, never frame number / nominal FPS."""
    if event.kind != expected_kind:
        raise InvalidModelOutput("Event kind differs from the query")
    frames = manifest.get("frames", [])
    model_evidence_refs(event, manifest)
    origin = manifest["input_origin_us"]
    a, b = manifest.get("source_range_us", [manifest.get("read_start_us", origin),
                                           manifest.get("read_end_us", duration_us)])

    def timestamp(value: float | None, *, endpoint: bool = False) -> int | None:
        if value is None:
            return None
        if not math.isfinite(value):
            raise InvalidModelOutput("Timestamp is not finite")
        result = origin + round(value * 1_000_000)
        # Input local zero is the first actual sampled frame, not requested crop start.
        if value < 0 or result < max(a, origin) or result > min(b, duration_us):
            raise InvalidModelOutput("Timestamp falls outside the submitted timeline")
        if not endpoint and result == min(b, duration_us):
            raise InvalidModelOutput("Point/start lies at the exclusive input end")
        return result

    def uncertainty(values: tuple[float, float] | None, estimate: int | None):
        if values is None:
            return None
        lower, upper = timestamp(values[0], endpoint=True), timestamp(values[1], endpoint=True)
        if lower > upper or (estimate is not None and not lower <= estimate <= upper):
            raise InvalidModelOutput("Uncertainty interval is reversed or excludes estimate")
        if enforce_sampling_uncertainty and estimate is not None and frames:
            if manifest.get("input_mode") == "video":
                # Native decoding/sampling is server-side; the source index is not an
                # observation log. Bound precision using the requested sampling period.
                period = math.ceil(1_000_000 / manifest["sample_fps"])
                earlier, later = max(origin, estimate - period), min(b, estimate + period)
            else:
                observed = sorted({f["source_time_us"] for f in frames})
                position = bisect_left(observed, estimate)
                earlier = observed[position - 1] if position else origin
                later_position = position + int(position < len(observed) and observed[position] == estimate)
                later = observed[later_position] if later_position < len(observed) else min(b, duration_us)
            lower, upper = min(lower, earlier), max(upper, later)
        return lower, upper

    if event.kind == "point":
        if any(x is not None for x in (event.start_s, event.end_s,
                                      event.start_range_s, event.end_range_s)):
            raise InvalidModelOutput("Point output contains interval boundaries")
        anchor = timestamp(event.anchor_s)
        return Location(kind="point", anchor_us=anchor,
                        anchor_range_us=uncertainty(event.anchor_range_s, anchor),
                        open_left=event.open_left, open_right=event.open_right)
    if event.anchor_s is not None or event.anchor_range_s is not None:
        raise InvalidModelOutput("Interval output contains a point anchor")
    start, end = timestamp(event.start_s), timestamp(event.end_s, endpoint=True)
    if start is not None and end is not None and start >= end:
        raise InvalidModelOutput("Interval start must precede end")
    return Location(kind="interval", start_us=start, end_us=end,
                    start_range_us=uncertainty(event.start_range_s, start),
                    end_range_us=uncertainty(event.end_range_s, end),
                    open_left=event.open_left, open_right=event.open_right)


def extent(location: Location) -> tuple[int, int] | None:
    if location.kind == "point":
        if location.anchor_range_us is not None:
            return location.anchor_range_us
        if location.anchor_us is not None:
            return location.anchor_us, location.anchor_us
        return None
    if location.start_us is None or location.end_us is None:
        return None
    return ((location.start_range_us or (location.start_us, location.start_us))[0],
            (location.end_range_us or (location.end_us, location.end_us))[1])


def _overlaps(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return max(a[0], b[0]) <= min(a[1], b[1])


def group_candidates(candidates: list[Candidate], max_span_us: int) -> list[list[Candidate]]:
    """Complete-link associations only; cross-window identity labels may differ.

    Overlapping proposals from independent windows are verified together without
    assuming one actor or one event. Every proposal survives for the 0/1/N verifier.
    """
    groups: list[list[Candidate]] = []
    for candidate in sorted(candidates, key=lambda c: (extent(c.location) or (0, 0))[0]):
        bounds = extent(candidate.location)
        joined = False
        if bounds:
            for group in groups:
                previous = [extent(c.location) for c in group]
                if (all(c.location.kind == candidate.location.kind and
                        ((candidate.entity_key != "unknown" and c.entity_key == candidate.entity_key)
                         or c.window_id != candidate.window_id) for c in group)
                        and all(p is not None and _overlaps(bounds, p) for p in previous)
                        and max([bounds[1]] + [p[1] for p in previous]) -
                        min([bounds[0]] + [p[0] for p in previous]) <= max_span_us):
                    group.append(candidate)
                    joined = True
                    break
        if not joined:
            groups.append([candidate])
    return groups


def classify_event(event: EventResult, tolerance_us: int, duration_us: int) -> EventResult:
    """Model decisions are not allowed to select their own product result bucket."""
    loc = event.location
    if event.decision == "rejected":
        event.result_bucket = "rejected"
        event.clip_status = "not_required"
        return event
    if loc.open_left or loc.open_right:
        event.boundary_status = "open"
    elif extent(loc) is None:
        event.boundary_status = "unknown"
    else:
        event.boundary_status = "bounded"
    estimates = ([loc.anchor_us] if loc.kind == "point" else [loc.start_us, loc.end_us])
    ranges = ([loc.anchor_range_us] if loc.kind == "point" else
              [loc.start_range_us, loc.end_range_us])
    problems = []
    if not event.evidence_refs:
        problems.append("missing_visual_evidence")
    if any(r is None for r in ranges):
        problems.append("unknown_boundary_uncertainty")
    elif any(r[1] - r[0] > tolerance_us for r in ranges):
        problems.append("boundary_tolerance_exceeded")
    for estimate, bounds in zip(estimates, ranges):
        if estimate is None:
            problems.append("missing_location")
        elif not 0 <= estimate <= duration_us:
            problems.append("location_out_of_source_range")
        if bounds and (bounds[0] < 0 or bounds[1] > duration_us or
                       (estimate is not None and not bounds[0] <= estimate <= bounds[1])):
            problems.append("invalid_uncertainty_range")
    event.uncertainty_reasons = list(dict.fromkeys(event.uncertainty_reasons + problems))
    event.result_bucket = (
        "matched" if event.decision == "supported" and event.boundary_status == "bounded"
        and not event.uncertainty_reasons else "uncertain"
    )
    return event


def _same_location(a: Location, b: Location) -> bool:
    if a.kind != b.kind:
        return False
    if a.kind == "point":
        return a.anchor_us is not None and a.anchor_us == b.anchor_us
    return (a.start_us is not None and a.end_us is not None and
            a.start_us == b.start_us and a.end_us == b.end_us)


def reconcile_events(events: list[EventResult]) -> list[EventResult]:
    """Only identical locations + known identity + shared evidence are auto-duplicates.

    Nearby events with distinct anchors are retained even if their clip context overlaps.
    Ambiguous overlapping instances are surfaced rather than merged transitively.
    """
    canonical: list[EventResult] = []
    for event in events:
        if event.result_bucket == "rejected":
            continue
        for prior in canonical:
            if event.location.kind != prior.location.kind:
                continue
            common_evidence = bool(set(event.evidence_refs) & set(prior.evidence_refs))
            same_entity = event.entity_key != "unknown" and event.entity_key == prior.entity_key
            if not (same_entity and common_evidence):
                continue
            if _same_location(event.location, prior.location):
                event.duplicate_of = prior.event_id
                event.clip_status = "not_required"
                prior.source_candidate_ids = list(dict.fromkeys(
                    prior.source_candidate_ids + event.source_candidate_ids))
                prior.evidence_refs = list(dict.fromkeys(prior.evidence_refs + event.evidence_refs))
                break
            a, b = extent(event.location), extent(prior.location)
            overlaps = (a is not None and b is not None and
                        (max(a[0], b[0]) < min(a[1], b[1]) if event.location.kind == "interval"
                         else _overlaps(a, b)))
            if overlaps:
                # Distinct point anchors whose uncertainty bands do not overlap never enter here.
                for item in (prior, event):
                    if "possible_duplicate_or_instance_conflict" not in item.uncertainty_reasons:
                        item.uncertainty_reasons.append("possible_duplicate_or_instance_conflict")
                    item.result_bucket = "uncertain"
        if event.duplicate_of is None:
            canonical.append(event)
    return events


def select_occurrences(events: list[EventResult], policy: str,
                       gaps: list[list[int]]) -> tuple[list[EventResult], list[str]]:
    if policy == "all":
        return events, []
    candidates = [e for e in events if not e.duplicate_of and e.result_bucket != "rejected"]
    located = [e for e in candidates if extent(e.location) is not None]
    if not located:
        return candidates, []
    reverse = policy == "last"

    def order(e: EventResult) -> int:
        loc = e.location
        value = loc.anchor_us if loc.kind == "point" else loc.start_us
        if value is not None:
            return value
        bounds = loc.anchor_range_us if loc.kind == "point" else loc.start_range_us
        return (bounds[0] + bounds[1]) // 2 if bounds else 0

    def order_range(e: EventResult) -> tuple[int, int] | None:
        loc = e.location
        bounds = loc.anchor_range_us if loc.kind == "point" else loc.start_range_us
        if bounds is not None:
            return bounds
        value = loc.anchor_us if loc.kind == "point" else loc.start_us
        return (value, value) if value is not None else None

    # Prefer the first/last supported instance while retaining uncertainty that can precede it.
    supported = [e for e in located if e.decision == "supported"]
    selected = sorted(supported or located, key=order, reverse=reverse)[0]
    loc = selected.location
    selected_range = (loc.anchor_range_us if loc.kind == "point" else loc.start_range_us)
    selected_range = selected_range or (order(selected), order(selected))
    blocking = []
    for item in candidates:
        if item is selected:
            continue
        bounds = order_range(item)
        if bounds is None:
            blocking.append(item)
        elif ((not reverse and bounds[0] <= selected_range[1]) or
              (reverse and bounds[1] >= selected_range[0])):
            blocking.append(item)
    gap_blocks = any((a <= selected_range[1] if not reverse else b >= selected_range[0])
                     for a, b in gaps)
    if blocking or gap_blocks:
        selected.result_bucket = "uncertain"
        selected.uncertainty_reasons.append("global_occurrence_order_not_established")
    kept = [selected] + blocking
    kept_ids = {e.event_id for e in kept}
    return kept, [e.event_id for e in events if e.event_id not in kept_ids]


def clip_range(event: EventResult, config: RunConfig, duration_us: int,
               fallback: tuple[int, int] | None = None) -> tuple[int, int] | None:
    if event.duplicate_of or event.result_bucket == "rejected":
        return None
    if event.result_bucket == "uncertain":
        if not config.export_uncertain or fallback is None:
            return None
        a, b = fallback
    elif event.location.kind == "point":
        bounds = event.location.anchor_range_us
        a = bounds[0] - round(config.point_before_s * 1_000_000)
        b = bounds[1] + round(config.point_after_s * 1_000_000)
    else:
        a = event.location.start_range_us[0] - round(config.clip_before_s * 1_000_000)
        b = event.location.end_range_us[1] + round(config.clip_after_s * 1_000_000)
    a, b = max(0, a), min(duration_us, b)
    return (a, b) if b > a else None
