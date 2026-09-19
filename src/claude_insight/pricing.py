"""Transparent API-equivalent cost estimates from recorded Claude usage."""

from collections import Counter
import json
import math


PRICING_SOURCE = "https://platform.claude.com/docs/en/about-claude/pricing"
PRICING_CHECKED = "2026-09-19"
# Public standard API rates, USD per million tokens, checked at PRICING_SOURCE.
# Exact model IDs only: unidentified and future variants remain unpriced.
DEFAULT_RATES = {
    "claude-fable-5-1": {"input": 10, "cache_5m": 12.5, "cache_1h": 20, "cache_read": .25, "output": 50},
    "claude-opus-5": {"input": 5, "cache_5m": 6.25, "cache_1h": 10, "cache_read": .5, "output": 25},
    "claude-sonnet-5": {"input": 2, "cache_5m": 2.5, "cache_1h": 4, "cache_read": .2, "output": 10},
    "claude-sonnet-4-6": {"input": 3, "cache_5m": 3.75, "cache_1h": 6, "cache_read": .3, "output": 15},
    "claude-sonnet-4-5": {"input": 3, "cache_5m": 3.75, "cache_1h": 6, "cache_read": .3, "output": 15},
    "claude-haiku-4-5-20251001": {"input": 1, "cache_5m": 1.25, "cache_1h": 2, "cache_read": .1, "output": 5},
}
TOKEN_KEYS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


def merge_usage_snapshot(previous, current):
    """Keep maximum counters across repeated streaming snapshots."""
    merged = dict(previous or {})
    for key, value in current.items():
        old = merged.get(key)
        if isinstance(value, dict):
            merged[key] = merge_usage_snapshot(old if isinstance(old, dict) else {}, value)
        elif isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            merged[key] = max(old, value) if isinstance(old, (int, float)) else value
        elif value is not None:
            merged[key] = value
    return merged


def load_rates(path=None):
    rates = dict(DEFAULT_RATES)
    if path:
        with open(path, encoding="utf-8") as source:
            custom = json.load(source)
        if not isinstance(custom, dict):
            raise ValueError("Pricing file must be a JSON object keyed by exact model ID")
        for model, values in custom.items():
            if not isinstance(values, dict) or any(k not in values or isinstance(values[k], bool) or not isinstance(values[k], (int, float)) or not math.isfinite(values[k]) or values[k] < 0
                                                    for k in ("input", "cache_5m", "cache_1h", "cache_read", "output")):
                raise ValueError("Invalid USD/MTok rates for model %s" % model)
            extra = values.get("web_search_request")
            if extra is not None and (isinstance(extra, bool) or not isinstance(extra, (int, float))
                                      or not math.isfinite(extra) or extra < 0):
                raise ValueError("Invalid web search request rate for model %s" % model)
            rates[model] = values
    return rates


def price_usage(model, usage, rates):
    """Return a priced record, with coverage warnings instead of invented costs."""
    model = model if isinstance(model, str) else None
    usage = usage if isinstance(usage, dict) else {}
    def count(value):
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError, OverflowError):
            return 0
    tokens = {key: count(usage.get(key)) for key in TOKEN_KEYS}
    cache = usage.get("cache_creation") or {}
    cache = cache if isinstance(cache, dict) else {}
    tokens["cache_5m_tokens"] = count(cache.get("ephemeral_5m_input_tokens"))
    tokens["cache_1h_tokens"] = count(cache.get("ephemeral_1h_input_tokens"))
    server = usage.get("server_tool_use") or {}
    server = server if isinstance(server, dict) else {}
    tokens["web_search_requests"] = count(server.get("web_search_requests"))
    if not any(tokens.values()):
        return {"model": model, "tokens": tokens, "estimated_usd": 0.0,
                "components": {}, "warnings": []}
    warnings = []
    rate = rates.get(model)
    if not rate:
        warnings.append("Unknown model rate: " + str(model))
    if tokens["web_search_requests"] and rate and "web_search_request" not in rate:
        warnings.append("Web search request rate is not configured")
    if usage.get("speed") not in (None, "standard"):
        warnings.append("Non-standard speed: " + str(usage.get("speed")))
    if usage.get("service_tier") not in (None, "standard"):
        warnings.append("Non-standard service tier: " + str(usage.get("service_tier")))
    unclassified = tokens["cache_creation_input_tokens"] - tokens["cache_5m_tokens"] - tokens["cache_1h_tokens"]
    if unclassified > 0:
        warnings.append("Cache writes lack duration breakdown")
    # Only price a record completely when every known modifier can be handled.
    if warnings:
        return {"model": model, "tokens": tokens, "estimated_usd": None, "warnings": warnings}
    components = {
        "input": tokens["input_tokens"] * rate["input"] / 1_000_000,
        "output": tokens["output_tokens"] * rate["output"] / 1_000_000,
        "cache_5m": tokens["cache_5m_tokens"] * rate["cache_5m"] / 1_000_000,
        "cache_1h": tokens["cache_1h_tokens"] * rate["cache_1h"] / 1_000_000,
        "cache_read": tokens["cache_read_input_tokens"] * rate["cache_read"] / 1_000_000,
        "web_search": tokens["web_search_requests"] * rate.get("web_search_request", 0),
    }
    if usage.get("inference_geo") == "us":
        for key in ("input", "output", "cache_5m", "cache_1h", "cache_read"):
            components[key] *= 1.1
    elif usage.get("inference_geo") not in (None, "not_available", "global"):
        return {"model": model, "tokens": tokens, "estimated_usd": None,
                "warnings": ["Unknown inference geography: " + str(usage.get("inference_geo"))]}
    return {"model": model, "tokens": tokens,
            "estimated_usd": sum(components.values()), "components": components, "warnings": []}


def summarize(records):
    tokens, components, models, model_costs = Counter(), Counter(), Counter(), Counter()
    priced, warnings = 0, Counter()
    total = 0.0
    for record in records:
        tokens.update(record["tokens"])
        models[record["model"] or "unknown"] += 1
        if record["estimated_usd"] is None:
            warnings.update(record["warnings"])
        else:
            priced += 1
            total += record["estimated_usd"]
            model_costs[record["model"] or "unknown"] += record["estimated_usd"]
            components.update(record.get("components", {}))
    return {"usage_records": len(records), "priced_records": priced,
            "estimated_usd": total if priced == len(records) else None,
            "priced_subtotal_usd": total, "tokens": dict(tokens),
            "components": dict(components), "models": dict(models),
            "model_cost_usd": dict(model_costs),
            "warnings": dict(warnings)}
