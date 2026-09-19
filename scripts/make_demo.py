"""Regenerate the entirely synthetic package demo transcript."""

import json
from pathlib import Path

OUTPUT = Path(__file__).resolve().parents[1] / "src" / "claude_insight" / "demo_session.jsonl"
tasks = [
    ("Map the sample project.", "Read", {"file_path": "src/example.py"}, "The entry point calls load_config and run.", False),
    ("Find configuration references.", "Grep", {"query": "load_config"}, "Three files refer to load_config.", False),
    ("Run the example tests.", "Bash", {"command": "pytest -q"}, "One synthetic assertion failed.", True),
    ("Inspect the failing assertion.", "Read", {"file_path": "tests/test_example.py"}, "The test expects three items.", False),
    ("Check the implementation.", "Read", {"file_path": "src/example.py"}, "The function returns two configured items.", False),
    ("Fix the sample test expectation.", "Edit", {"file_path": "tests/test_example.py"}, "Changed the expected count to two.", False),
    ("Run tests again.", "Bash", {"command": "pytest -q"}, "All three synthetic tests passed.", False),
    ("Review edge cases in parallel.", "Agent", {"description": "Review sample edge cases"}, "The synthetic review found no other cases.", False),
    ("Summarize the token and tool use.", "Bash", {"command": "printf summary"}, "Summary prepared.", False),
    ("Give me the final status.", "Read", {"file_path": "README.md"}, "The example workflow is complete.", False),
]


def stamp(second):
    return "2026-01-01T10:%02d:%02dZ" % divmod(second, 60)


rows = []
parent = None
for index, (prompt, tool_name, tool_input, result, error) in enumerate(tasks, 1):
    base = (index - 1) * 34
    user_id, assistant_id, result_id = f"demo-u{index}", f"demo-a{index}", f"demo-r{index}"
    tool_id, message_id = f"demo-tool-{index}", f"demo-msg-{index}"
    user = {"type": "user", "uuid": user_id, "parentUuid": parent,
            "sessionId": "synthetic-demo", "cwd": "/synthetic/sample-project",
            "timestamp": stamp(base), "origin": {"kind": "human"},
            "message": {"content": prompt}}
    rows.append(user)
    model = "claude-sonnet-5" if index <= 7 else "claude-haiku-4-5-20251001"
    blocks = [{"type": "text", "text": f"I will use {tool_name} to handle this example task."},
              {"type": "tool_use", "id": tool_id, "name": tool_name, "input": tool_input}]
    for snapshot, output_tokens in enumerate((16, 48)):
        assistant = {"type": "assistant", "uuid": assistant_id if snapshot == 0 else f"{assistant_id}-stream",
                     "parentUuid": user_id, "sessionId": "synthetic-demo", "timestamp": stamp(base + 3 + snapshot),
                     "message": {"id": message_id, "model": model, "content": blocks,
                                 "usage": {"input_tokens": 280 + index * 17,
                                           "output_tokens": output_tokens,
                                           "cache_creation_input_tokens": 40,
                                           "cache_read_input_tokens": 100,
                                           "cache_creation": {"ephemeral_5m_input_tokens": 40}}}}
        rows.append(assistant)
    rows.append({"type": "user", "uuid": result_id, "parentUuid": assistant_id,
                 "sessionId": "synthetic-demo", "timestamp": stamp(base + 9),
                 "message": {"content": [{"type": "tool_result", "tool_use_id": tool_id,
                                          "is_error": error, "content": result}]}})
    rows.append({"type": "assistant", "uuid": f"demo-summary-{index}", "parentUuid": result_id,
                 "sessionId": "synthetic-demo", "timestamp": stamp(base + 14),
                 "message": {"id": f"demo-summary-msg-{index}", "model": model,
                             "content": [{"type": "text", "text": result}],
                             "usage": {"input_tokens": 180, "output_tokens": 24}}})
    parent = f"demo-summary-{index}"

OUTPUT.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
print(f"Wrote {OUTPUT}: {len(tasks)} synthetic turns, {len(rows)} rows")
