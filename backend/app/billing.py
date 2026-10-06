"""Versioned paid-list estimates; provider usage is not a billing receipt."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

PRICING_SOURCE = "https://ai.google.dev/gemini-api/docs/pricing"
PRICING_VERSION = "google-gemini-standard-2026-10-05"


def pricing_snapshot(provider: str, model_id: str, submitted_on: date) -> dict:
    snapshot = {
        "provider": provider, "model_id": model_id, "currency": "USD",
        "basis": "paid_standard_list_estimate", "pricing_version": PRICING_VERSION,
        "pricing_source": PRICING_SOURCE, "submitted_on": submitted_on.isoformat(),
        "rates_per_million_tokens": None,
    }
    if provider == "fixture":
        snapshot["basis"] = "local_fixture_no_remote_call"
    elif provider == "gemini" and model_id.removeprefix("models/") == "gemini-3.8-flash":
        # These two effective periods were explicitly published in the source above.
        future = submitted_on >= date(2027, 1, 1)
        if submitted_on >= date(2026, 10, 5):
            snapshot["rates_per_million_tokens"] = {
                "input": 1.5 if future else 0.75,
                "cached_input": 0.15 if future else 0.075,
                "output_including_thinking": 7.5 if future else 3.75,
            }
            snapshot["effective_from"] = "2027-01-01" if future else "2026-10-05"
    return snapshot


def estimate_call_cost(snapshot: dict, usage: dict, *, outcome_unknown: bool = False) -> dict:
    result = {
        **snapshot, "status": "unknown", "estimated_usd": None,
        "input_usd": None, "cached_input_usd": None, "output_usd": None,
        "token_counts": None,
    }
    if snapshot["provider"] == "fixture":
        return {**result, "status": "not_billable", "estimated_usd": 0.0,
                "input_usd": 0.0, "cached_input_usd": 0.0, "output_usd": 0.0}
    if outcome_unknown:
        return {**result, "reason": "request_outcome_unknown"}
    rates = snapshot.get("rates_per_million_tokens")
    if rates is None:
        return {**result, "reason": "model_pricing_unavailable"}
    if any(usage.get(key) is None for key in ("prompt_token_count", "candidates_token_count")):
        return {**result, "reason": "usage_unavailable"}
    keys = ("prompt_token_count", "cached_content_token_count", "candidates_token_count",
            "thoughts_token_count", "tool_use_prompt_token_count")
    counts = {key: 0 if usage.get(key) is None else usage[key] for key in keys}
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 0
           for value in counts.values()):
        return {**result, "reason": "invalid_usage"}
    if counts["cached_content_token_count"] > counts["prompt_token_count"]:
        return {**result, "reason": "invalid_usage"}
    if counts["tool_use_prompt_token_count"]:
        # The current adapter enables no tools. Do not silently omit future tool fees.
        return {**result, "reason": "tool_usage_pricing_unavailable"}
    uncached = counts["prompt_token_count"] - counts["cached_content_token_count"]
    generated = counts["candidates_token_count"] + counts["thoughts_token_count"]
    million = Decimal(1_000_000)
    input_cost = Decimal(uncached) * Decimal(str(rates["input"])) / million
    cached_cost = (Decimal(counts["cached_content_token_count"])
                   * Decimal(str(rates["cached_input"])) / million)
    output_cost = Decimal(generated) * Decimal(str(rates["output_including_thinking"])) / million
    return {
        **result, "status": "estimated", "estimated_usd": float(input_cost + cached_cost + output_cost),
        "input_usd": float(input_cost), "cached_input_usd": float(cached_cost),
        "output_usd": float(output_cost),
        "token_counts": {**counts, "uncached_input_tokens": uncached,
                         "billable_output_tokens": generated},
    }


def summarize_call_costs(tasks: list[dict]) -> dict:
    """Sum known estimates only, keeping unsubmitted tasks out of request counts."""
    calls = [task for task in tasks if task.get("request_intent")]
    subtotal = Decimal(0)
    estimated, unknown, pending, local = 0, 0, 0, 0
    for task in calls:
        cost = task.get("cost") or {}
        if cost.get("status") == "estimated" and cost.get("estimated_usd") is not None:
            subtotal += Decimal(str(cost["estimated_usd"]))
            estimated += 1
        elif cost.get("status") == "not_billable":
            local += 1
        elif not cost and task.get("status") == "submitting":
            pending += 1
        else:
            unknown += 1
    return {
        "currency": "USD", "basis": "paid_standard_list_estimate",
        "estimated_usd": float(subtotal) if estimated or local else None,
        "estimated_calls": estimated, "unknown_calls": unknown, "pending_calls": pending,
        "local_calls": local, "total_calls": len(calls), "complete": not unknown and not pending,
    }
