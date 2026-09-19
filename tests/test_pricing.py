import io
import json
import unittest

from claude_insight.graph import _usage_records
from claude_insight.pricing import load_rates, price_usage


class PricingTests(unittest.TestCase):
    def test_cache_durations_and_web_search_are_separate(self):
        usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000,
                 "cache_creation_input_tokens": 2_000_000,
                 "cache_read_input_tokens": 1_000_000,
                 "cache_creation": {"ephemeral_5m_input_tokens": 1_000_000,
                                    "ephemeral_1h_input_tokens": 1_000_000},
                 "server_tool_use": {"web_search_requests": 1},
                 "speed": "standard", "service_tier": "standard"}
        rates = load_rates()
        rates["claude-fable-5-1"] = {**rates["claude-fable-5-1"], "web_search_request": 0.01}
        result = price_usage("claude-fable-5-1", usage, rates)
        self.assertAlmostEqual(result["estimated_usd"], 92.76)
        self.assertAlmostEqual(result["components"]["cache_read"], 0.25)

    def test_missing_price_or_cache_duration_does_not_claim_total(self):
        usage = {"output_tokens": 100, "cache_creation_input_tokens": 20}
        self.assertIsNone(price_usage("unknown-model", usage, load_rates())["estimated_usd"])
        self.assertIsNone(price_usage("claude-opus-5", usage, load_rates())["estimated_usd"])
        self.assertEqual(price_usage("<synthetic>", {"output_tokens": 0}, load_rates())["estimated_usd"], 0)

    def test_repeated_message_snapshot_is_counted_once(self):
        row = {"type": "assistant", "message": {"id": "msg-1", "model": "claude-opus-5",
                "usage": {"output_tokens": 100}}}
        transcript = json.dumps(row) + "\n" + json.dumps(row) + "\n"
        records = _usage_records(lambda: io.StringIO(transcript), load_rates())
        self.assertEqual(len(records), 1)
        self.assertAlmostEqual(records[0]["estimated_usd"], 0.0025)


if __name__ == "__main__":
    unittest.main()
