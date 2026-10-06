from datetime import date

import pytest
from app.billing import estimate_call_cost, pricing_snapshot


def snapshot(model="gemini-3.8-flash", day=date(2026, 10, 5)):
    return pricing_snapshot("gemini", model, day)


def test_cached_tokens_are_not_double_charged_and_thinking_is_output():
    cost = estimate_call_cost(snapshot(), {
        "prompt_token_count": 10_000, "cached_content_token_count": 2_000,
        "candidates_token_count": 1_000, "thoughts_token_count": 500,
    })
    assert cost["status"] == "estimated"
    assert cost["estimated_usd"] == pytest.approx(0.011775)
    assert cost["token_counts"]["uncached_input_tokens"] == 8_000
    assert cost["token_counts"]["billable_output_tokens"] == 1_500
    assert cost["basis"] == "paid_standard_list_estimate"
    assert cost["pricing_version"] and cost["pricing_source"]


def test_effective_date_selects_published_rate_and_model_prefix_is_supported():
    current = estimate_call_cost(snapshot("models/gemini-3.8-flash", date(2026, 12, 31)),
                                 {"prompt_token_count": 1_000_000, "candidates_token_count": 0})
    future = estimate_call_cost(snapshot(day=date(2027, 1, 1)),
                                {"prompt_token_count": 1_000_000, "candidates_token_count": 0})
    assert current["estimated_usd"] == 0.75
    assert future["estimated_usd"] == 1.5


@pytest.mark.parametrize("model,usage,outcome_unknown,reason", [
    ("unknown-model", {"prompt_token_count": 1, "candidates_token_count": 1}, False,
     "model_pricing_unavailable"),
    ("gemini-3.8-flash", {}, False, "usage_unavailable"),
    ("gemini-3.8-flash", {"prompt_token_count": 1}, False, "usage_unavailable"),
    ("gemini-3.8-flash", {}, True, "request_outcome_unknown"),
    ("gemini-3.8-flash", {"prompt_token_count": 1, "candidates_token_count": -1}, False,
     "invalid_usage"),
    ("gemini-3.8-flash", {"prompt_token_count": True, "candidates_token_count": 0}, False,
     "invalid_usage"),
    ("gemini-3.8-flash", {"prompt_token_count": 1, "candidates_token_count": 0,
                          "cached_content_token_count": 2}, False, "invalid_usage"),
    ("gemini-3.8-flash", {"prompt_token_count": 1, "candidates_token_count": 0,
                          "tool_use_prompt_token_count": 2}, False,
     "tool_usage_pricing_unavailable"),
])
def test_missing_or_ambiguous_cost_is_unknown_never_zero(model, usage, outcome_unknown, reason):
    cost = estimate_call_cost(snapshot(model), usage, outcome_unknown=outcome_unknown)
    assert cost["status"] == "unknown" and cost["estimated_usd"] is None
    assert cost["reason"] == reason


def test_fixture_is_explicitly_free_without_remote_usage():
    cost = estimate_call_cost(pricing_snapshot("fixture", "unused", date(2026, 10, 5)), {})
    assert cost["status"] == "not_billable" and cost["estimated_usd"] == 0


def test_verified_price_is_not_applied_to_older_historical_dates():
    cost = estimate_call_cost(snapshot(day=date(2026, 9, 1)), {
        "prompt_token_count": 10, "candidates_token_count": 1,
    })
    assert cost["status"] == "unknown" and cost["estimated_usd"] is None
