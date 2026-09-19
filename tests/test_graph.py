import io
import json
import unittest

from claude_insight import graph_data, parse_session, register_source, session_files, viewer_assets
from claude_insight.graph import _usage_records, render_graph
from claude_insight.pricing import load_rates
from claude_insight.plugins import register_insight


def source(rows):
    content = "\n".join(json.dumps(row) if not isinstance(row, str) else row for row in rows)
    return lambda: io.StringIO(content)


class GraphTests(unittest.TestCase):
    def test_malformed_rows_duplicate_tools_and_max_usage(self):
        rows = ["{bad", [], {"type": "user", "uuid": "u", "timestamp": "2026-01-01T00:00:00Z",
                           "origin": {"kind": "human"}, "message": {"content": "hello"}},
                {"type": "assistant", "uuid": "a", "parentUuid": "u", "timestamp": "2026-01-01T00:00:02Z",
                 "message": {"id": "m", "model": "claude-sonnet-5", "usage": {"output_tokens": 2},
                             "content": [{"type": "tool_use", "id": "t", "name": "Read", "input": {}}]}},
                {"type": "assistant", "uuid": "a2", "parentUuid": "u", "timestamp": "2026-01-01T00:00:03Z",
                 "message": {"id": "m", "model": "claude-sonnet-5", "usage": {"output_tokens": 5},
                             "content": [{"type": "tool_use", "id": "t", "name": "Read", "input": {}}]}},
                {"type": "user", "uuid": "r", "parentUuid": "a", "timestamp": "2026-01-01T00:00:04Z",
                 "message": {"content": [{"type": "tool_result", "tool_use_id": "t", "is_error": True,
                                          "content": "failed"}]}}]
        data = graph_data("synthetic.jsonl", source(rows))
        self.assertEqual(data["invalid_lines"], 1)
        self.assertEqual(data["tool_count"], 1)
        self.assertEqual(data["error_count"], 1)
        self.assertEqual(data["usage"]["output_tokens"], 5)
        self.assertEqual(data["duration_seconds"], 4)
        self.assertEqual(len([n for n in data["nodes"] if n["type"] == "assistant"]), 1)
        tool_node = next(n for n in data["nodes"] if n["type"] == "tool")
        self.assertEqual(tool_node["tool_id"], "t")
        self.assertEqual(tool_node["line"], 4)
        self.assertEqual(tool_node["result_line"], 6)
        self.assertEqual(tool_node["duration_seconds"], 2)
        self.assertTrue(all(e["source"] in {n["id"] for n in data["nodes"]} for e in data["edges"]))

    def test_unknown_rate_is_explicit(self):
        rows = [{"type": "assistant", "message": {"id": "m", "model": "future-model",
                 "usage": {"output_tokens": 5}, "content": "answer"}}]
        data = graph_data("synthetic.jsonl", source(rows))
        self.assertIsNone(data["estimated_usd"])
        self.assertFalse(data["pricing_complete"])
        self.assertEqual(data["unpriced_models"], ["future-model"])

    def test_html_escapes_script_end_tag(self):
        rendered = render_graph({"title": "</script><script>alert(1)</script>"})
        self.assertNotIn("</script><script>alert(1)", rendered)
        self.assertIn("\\u003c/script>", rendered)

    def test_reusable_assets_contain_no_transcript_or_bootstrap(self):
        assets = viewer_assets()
        self.assertIn("ClaudeInsight", assets["js"])
        self.assertIn(".ci", assets["css"])
        self.assertNotIn("__GRAPH_DATA__", assets["js"])
        self.assertNotIn("CI_BOOTSTRAP_START", assets["js"])
        self.assertNotIn('mount(document.getElementById("claude-insight")', assets["js"])
        self.assertNotIn("synthetic-demo", assets["js"])

    def test_summary_and_graph_agree_on_uuid_fallback(self):
        rows = [{"type": "assistant", "uuid": "row-1", "message": {"model": "claude-sonnet-5",
                 "usage": {"output_tokens": 1}, "content": "a"}},
                {"type": "assistant", "uuid": "row-1", "message": {"model": "claude-sonnet-5",
                 "usage": {"output_tokens": 9}, "content": "answer"}}]
        summary = parse_session("test", source(rows))
        graph = graph_data("test", source(rows))
        self.assertEqual(summary["usage"]["output_tokens"], 9)
        self.assertEqual(graph["usage"]["output_tokens"], 9)

    def test_text_block_human_turn_and_result_parent(self):
        rows = [{"type": "user", "uuid": "u", "message": {"content": [{"type": "text", "text": "hello"}]}},
                {"type": "assistant", "uuid": "a", "parentUuid": "u", "message": {"content": [
                    {"type": "tool_use", "id": "t", "name": "Read", "input": {}}]}},
                {"type": "user", "uuid": "r", "parentUuid": "a", "message": {"content": [
                    {"type": "tool_result", "tool_use_id": "t", "content": "done"}]}},
                {"type": "assistant", "uuid": "a2", "parentUuid": "r", "message": {"content": "done"}}]
        data = graph_data("test", source(rows))
        self.assertEqual(data["turn_count"], 1)
        node = next(n for n in data["nodes"] if n["id"] == "a2")
        self.assertEqual(node["parent_id"], "tool:t")

    def test_plugin_source_and_failure_isolation(self):
        rows = [{"type": "user", "uuid": "u", "message": {"content": "hello"}}]
        class Source:
            def can_open(self, value): return value == "synthetic://test"
            def session_files(self, value): yield value, source(rows)
        class Broken:
            name = "broken_test_plugin"
            def analyze(self, graph): raise RuntimeError("example failure")
        register_source(Source())
        register_insight(Broken())
        path, opener = next(session_files("synthetic://test"))
        data = graph_data(path, opener)
        self.assertIn("RuntimeError", data["insights"]["broken_test_plugin"]["error"])

    def test_malformed_ids_and_records_do_not_crash(self):
        rows = [42, {"type": [], "message": []},
                {"type": "assistant", "message": {"id": [], "model": [],
                 "usage": {"output_tokens": "nan"}, "content": [
                     {"type": "tool_use", "id": [], "name": [], "input": {}}]}}]
        graph_data("test", source(rows))
        parse_session("test", source(rows))


if __name__ == "__main__":
    unittest.main()
