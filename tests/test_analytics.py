import json
from pathlib import Path
import tempfile
import unittest

from claude_insight import graph_data


def write_rows(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")


def analyze(path):
    return graph_data(str(path), lambda: path.open(encoding="utf-8"))["analytics"]


class AnalyticsTests(unittest.TestCase):
    def test_peak_request_is_max_and_p95_uses_recorded_results_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "timings.jsonl"
            rows = [{"type": "user", "uuid": "u", "timestamp": "2026-01-01T00:00:00Z",
                     "origin": {"kind": "human"}, "message": {"content": "Benchmark"}}]
            for index in range(21):
                rows.append({"type": "assistant", "uuid": f"a{index}",
                             "timestamp": "2026-01-01T00:00:00Z",
                             "message": {"id": f"m{index}", "model": "claude-sonnet-5",
                                         "usage": {"input_tokens": 10 if index == 0 else 20},
                                         "content": [{"type": "tool_use", "id": f"t{index}",
                                                      "name": "Read", "input": {}}]}})
                if index < 20:
                    rows.append({"type": "user", "uuid": f"r{index}",
                                 "timestamp": f"2026-01-01T00:00:{index + 1:02d}Z",
                                 "message": {"content": [{"type": "tool_result",
                                                          "tool_use_id": f"t{index}", "content": "ok"}]}})
            write_rows(path, rows)
            data = analyze(path)
            self.assertEqual(data["turns"][0]["peak_request_input_tokens"], 20)
            self.assertEqual(data["tools"][0]["timing_sample_count"], 20)
            self.assertEqual(data["tools"][0]["pending_count"], 1)
            self.assertEqual(data["tools"][0]["p95_seconds"], 19)
            self.assertEqual(data["tools"][0]["max_seconds"], 20)
            self.assertEqual(data["slowest_calls"][0]["duration_seconds"], 20)
            self.assertEqual(len(data["slowest_calls"]), 5)

    def test_empty_human_prompt_has_safe_title(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "empty-prompt.jsonl"
            write_rows(path, [{"type": "user", "origin": {"kind": "human"},
                               "message": {"content": None}}])
            self.assertEqual(analyze(path)["turns"][0]["title"], "Turn 1")

    def test_turns_models_tools_and_remainders_reconcile(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.jsonl"
            write_rows(path, [
                {"type": "user", "uuid": "u1", "timestamp": "2026-01-01T00:00:00Z",
                 "origin": {"kind": "human"}, "message": {"content": "First request"}},
                {"type": "assistant", "uuid": "a1", "timestamp": "2026-01-01T00:00:01Z",
                 "message": {"id": "m1", "model": "claude-sonnet-5",
                             "usage": {"input_tokens": 10, "output_tokens": 2, "cache_read_input_tokens": 20},
                             "content": [{"type": "tool_use", "id": "t1", "name": "Read", "input": {}}]}},
                {"type": "assistant", "uuid": "a1-repeat", "timestamp": "2026-01-01T00:00:02Z",
                 "message": {"id": "m1", "model": "claude-sonnet-5",
                             "usage": {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 20},
                             "content": [{"type": "tool_use", "id": "t1", "name": "Read", "input": {}}]}},
                {"type": "user", "uuid": "r1", "timestamp": "2026-01-01T00:00:04Z",
                 "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "is_error": True,
                                          "content": "failed"}]}},
                {"type": "user", "uuid": "u2", "timestamp": "2026-01-01T00:01:00Z",
                 "origin": {"kind": "human"}, "message": {"content": "Second request"}},
                {"type": "assistant", "uuid": "a2", "timestamp": "2026-01-01T00:01:02Z",
                 "message": {"id": "m2", "model": "future-model",
                             "usage": {"input_tokens": 7, "output_tokens": 3}, "content": "Done"}},
            ])
            child = path.parent / path.stem / "subagents" / "agent-orphan.jsonl"
            write_rows(child, [{"type": "assistant", "uuid": "c1",
                                "message": {"id": "cm1", "model": "claude-sonnet-5",
                                            "usage": {"output_tokens": 11}, "content": "Child"}}])
            data = analyze(path)
            first, second = data["turns"]
            self.assertEqual(first["tokens"]["output_tokens"], 5)
            self.assertEqual(first["total_tokens"], 35)
            self.assertEqual(first["elapsed_seconds"], 4)
            self.assertEqual(first["tool_count"], 1)
            self.assertEqual(first["error_count"], 1)
            self.assertEqual(second["elapsed_seconds"], 2)
            self.assertIsNone(second["estimated_usd"])
            self.assertEqual(data["totals"]["total_tokens"], 56)
            self.assertEqual(data["totals"]["attributed_tokens"], 45)
            self.assertEqual(data["totals"]["unattributed_tokens"], 11)
            self.assertEqual(data["totals"]["unlinked_child_count"], 1)
            self.assertEqual(data["totals"]["cache_read_share"], 20 / 37)
            self.assertEqual(sum(m["total_tokens"] for m in data["models"]), 56)
            self.assertFalse(data["totals"]["pricing_complete"])
            self.assertAlmostEqual(data["tools"][0]["median_seconds"], 3)
            self.assertEqual(data["tools"][0]["pending_count"], 0)
            self.assertEqual(data["tools"][0]["error_rate"], 1)
            self.assertTrue(any(f["node_id"] == "tool:t1" and f["evidence_line"] == 4
                                for f in data["findings"]))
            self.assertTrue(any(f["title"] == "Unlinked child usage" for f in data["findings"]))

    def test_empty_and_missing_timestamps_do_not_create_rates_or_latencies(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "empty.jsonl"
            write_rows(path, [{"type": "user", "uuid": "u", "origin": {"kind": "human"},
                               "message": {"content": "Run"}},
                              {"type": "assistant", "uuid": "a", "message": {"content": [
                                  {"type": "tool_use", "id": "t", "name": "Bash", "input": {}}]}}])
            data = analyze(path)
            self.assertIsNone(data["totals"]["cache_read_share"])
            self.assertIsNone(data["turns"][0]["elapsed_seconds"])
            self.assertIsNone(data["tools"][0]["median_seconds"])
            self.assertEqual(data["tools"][0]["timing_sample_count"], 0)
            self.assertEqual(data["tools"][0]["pending_count"], 1)
            self.assertEqual(data["tools"][0]["error_rate"], 0)


if __name__ == "__main__":
    unittest.main()
