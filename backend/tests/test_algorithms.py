import math

import pytest
from app.algorithms import (
    InvalidModelOutput,
    classify_event,
    clip_range,
    coverage,
    group_candidates,
    map_model_event,
    plan_windows,
    reconcile_events,
    select_occurrences,
    split_window,
)
from app.contracts import Candidate, EventResult, Location, ModelEvent, QuerySpec, RunConfig


def point_event(name, anchor, bounds=None, entity="ball", evidence=None):
    return EventResult(
        event_id=name, source_candidate_ids=[name], decision="supported",
        boundary_status="bounded", result_bucket="matched", entity_key=entity,
        location=Location(kind="point", anchor_us=anchor,
                          anchor_range_us=bounds or (anchor, anchor)),
        evidence_refs=evidence if evidence is not None else ["f1"],
    )


def manifest():
    return {"input_origin_us": 250_000, "source_range_us": [150_000, 800_000],
            "frames": [{"frame_id": f"f{i}", "source_time_us": t}
                       for i, t in enumerate([250_000, 500_000, 700_000])]}


@pytest.mark.parametrize("kind", ["point", "interval"])
def test_hour_plan_covers_all_cores_without_silently_lowering_fps(kind):
    config = RunConfig(max_input_frames=128)
    windows = plan_windows(3_600_000_000, QuerySpec(raw_query="each event", event_kind=kind), config)
    assert windows[0].core_start_us == 0
    assert windows[-1].core_end_us == 3_600_000_000
    assert all(left.core_end_us == right.core_start_us for left, right in zip(windows, windows[1:]))
    for window in windows:
        assert math.ceil((window.read_end_us - window.read_start_us) * window.sample_fps / 1e6) + 1 <= 128
        assert window.sample_fps == (12 if kind == "point" else 4)


def test_impossible_context_budget_is_explicit_error():
    with pytest.raises(ValueError, match="context"):
        plan_windows(1_000_000, QuerySpec(raw_query="event"),
                     RunConfig(scan_fps=30, max_input_frames=16, context_s=20))


def test_coverage_does_not_count_context_or_double_count_overlap():
    assert coverage(100, [(0, 30), (60, 100), (0, 30)]) == {
        "duration_us": 100, "covered_us": 70, "covered_ranges": [[0, 30], [60, 100]],
        "gaps": [[30, 60]],
    }
    window = plan_windows(60_000_000, QuerySpec(raw_query="event"), RunConfig())[0]
    children = split_window(window)
    assert children[0].core_end_us == children[1].core_start_us
    assert sum(c.core_end_us - c.core_start_us for c in children) == window.core_end_us


def test_actual_sample_zero_and_observation_bracket_are_used():
    event = ModelEvent(kind="point", anchor_s=0.25, anchor_range_s=(0.25, 0.25),
                       evidence_frame_ids=["f1"])
    loc = map_model_event(event, manifest(), 1_000_000, "point")
    assert loc.anchor_us == 500_000  # Not requested read start (150000) + 250000.
    assert loc.anchor_range_us == (250_000, 700_000)
    output = point_event("e", loc.anchor_us, loc.anchor_range_us)
    assert classify_event(output, 10_000, 1_000_000).result_bucket == "uncertain"


def test_fixture_can_explicitly_bypass_visual_precision_floor():
    loc = map_model_event(ModelEvent(kind="point", anchor_s=0.25, anchor_range_s=(0.25, 0.25)),
                          manifest(), 1_000_000, "point", enforce_sampling_uncertainty=False)
    assert loc.anchor_range_us == (500_000, 500_000)


@pytest.mark.parametrize("event", [
    ModelEvent(kind="point", anchor_s=-0.1),
    ModelEvent(kind="point", anchor_s=0.55),  # Exclusive end.
    ModelEvent(kind="point", anchor_s=float("nan")),
    ModelEvent(kind="point", anchor_s=0.1, anchor_range_s=(0.2, 0.3)),
    ModelEvent(kind="point", anchor_s=0.1, evidence_frame_ids=["invented"]),
    ModelEvent(kind="interval", start_s=0.2, end_s=0.1),
])
def test_invalid_model_time_or_evidence_never_reaches_clipper(event):
    with pytest.raises((InvalidModelOutput, ValueError)):
        map_model_event(event, manifest(), 1_000_000, event.kind)


def test_missing_range_is_unknown_not_zero_error():
    event = point_event("event", 500_000)
    event.location.anchor_range_us = None
    assert classify_event(event, 1_000_000, 2_000_000).result_bucket == "uncertain"
    assert "unknown_boundary_uncertainty" in event.uncertainty_reasons


def test_candidate_grouping_does_not_transitively_swallow_chain():
    proposals = [Candidate(candidate_id=str(i), window_id="w", entity_key="arm",
                            location=Location(kind="interval", start_us=a, end_us=b))
                 for i, (a, b) in enumerate([(0, 20), (15, 35), (30, 50)])]
    groups = group_candidates(proposals, 100)
    assert sorted(len(group) for group in groups) == [1, 2]
    assert sum(len(group) for group in groups) == 3


def test_exact_shared_evidence_duplicate_but_near_repetition_remains():
    events = [point_event("a", 1_000_000), point_event("duplicate", 1_000_000),
              point_event("next", 1_050_000)]
    reconcile_events(events)
    assert events[1].duplicate_of == "a"
    assert events[2].duplicate_of is None
    assert events[2].result_bucket == "matched"


def test_ambiguous_overlap_is_exposed_instead_of_merged():
    events = [point_event("a", 100, (90, 120)), point_event("b", 110, (100, 130))]
    reconcile_events(events)
    assert all(e.duplicate_of is None and e.result_bucket == "uncertain" for e in events)


def test_global_first_is_uncertain_when_prior_core_failed():
    kept, omitted = select_occurrences([point_event("first", 1_000_000), point_event("later", 5_000_000)],
                                       "first", [[0, 500_000]])
    assert [event.event_id for event in kept] == ["first"]
    assert kept[0].result_bucket == "uncertain"
    assert omitted == ["later"]


def test_unknown_anchor_with_range_does_not_crash_global_selection():
    unknown = point_event("unknown", 200)
    unknown.location.anchor_us = None
    unknown.location.anchor_range_us = (100, 300)
    unknown.decision = "unresolved"
    unknown.result_bucket = "uncertain"
    selected, _ = select_occurrences([unknown, point_event("known", 400)], "first", [])
    assert {e.event_id for e in selected} == {"known", "unknown"}
    assert next(e for e in selected if e.event_id == "known").result_bucket == "uncertain"


def test_last_interval_orders_by_start_not_by_earlier_interval_end():
    def interval(name, start, end):
        return EventResult(
            event_id=name, decision="supported", boundary_status="bounded", result_bucket="matched",
            location=Location(kind="interval", start_us=start, end_us=end,
                              start_range_us=(start, start), end_range_us=(end, end)),
        )
    selected, omitted = select_occurrences([interval("earlier", 1, 100), interval("later", 50, 60)],
                                           "last", [])
    assert [e.event_id for e in selected] == ["later"]
    assert selected[0].result_bucket == "matched"
    assert omitted == ["earlier"]


def test_adjacent_half_open_intervals_do_not_become_duplicate_conflicts():
    events = [EventResult(event_id=str(start), decision="supported", boundary_status="bounded",
                          result_bucket="matched", entity_key="arm", evidence_refs=["shared_boundary"],
                          location=Location(kind="interval", start_us=start, end_us=start + 100,
                                            start_range_us=(start, start),
                                            end_range_us=(start + 100, start + 100)))
              for start in (0, 100)]
    assert all(e.result_bucket == "matched" and not e.duplicate_of for e in reconcile_events(events))


def test_uncertain_clip_uses_observed_context_not_false_event_bounds():
    event = point_event("e", 3_000_000)
    event.result_bucket = "uncertain"
    assert clip_range(event, RunConfig(), 12_000_000, (1_000_000, 6_000_000)) == (1_000_000, 6_000_000)
    assert clip_range(event, RunConfig(), 12_000_000) is None
