"""Generic, evidence-linked turn graph for Claude Code JSONL sessions."""

from collections import Counter
from pathlib import Path
import json
import re

from .core import parse_session, session_files
from .pricing import PRICING_CHECKED, PRICING_SOURCE, load_rates, merge_usage_snapshot, price_usage, summarize
from .graph_model import build_nodes, duration_seconds


TASK_ID = re.compile(r"<task-id>([^<]+)</task-id>")
TOOL_ID = re.compile(r"<tool-use-id>([^<]+)</tool-use-id>")


def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(b.get("text", "")) for b in content
                         if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _label(name, data):
    name = name if isinstance(name, str) and name else "Tool"
    if not isinstance(data, dict):
        return name or "Tool"
    for key in ("description", "file_path", "path", "command", "query", "url"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return (name or "Tool") + " · " + value.strip().replace("\n", " ")[:90]
    return name or "Tool"


def _usage_records(opener, rates, turn_lines=None):
    records, positions, snapshots = [], {}, {}
    current_turn = None
    next_turn = 0
    with opener() as stream:
        for line_no, raw in enumerate(stream, 1):
            if turn_lines:
                while next_turn < len(turn_lines) and line_no >= turn_lines[next_turn]:
                    current_turn = next_turn
                    next_turn += 1
            try:
                row = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if not isinstance(row, dict):
                continue
            message = row.get("message") or {}
            if row.get("type") != "assistant" or not isinstance(message, dict) or not message.get("usage"):
                continue
            message_id = message.get("id") or row.get("uuid")
            if not isinstance(message_id, str) or not message_id or not isinstance(message["usage"], dict):
                continue
            snapshots[message_id] = merge_usage_snapshot(snapshots.get(message_id), message["usage"])
            record = price_usage(message.get("model"), snapshots[message_id], rates)
            record["message_id"] = message_id
            record["line"] = line_no
            record["turn"] = current_turn
            # Streaming rows repeat a message ID with increasingly complete
            # usage. Keep the last snapshot, retaining its original turn.
            if message_id in positions:
                old = records[positions[message_id]]
                record["turn"] = old["turn"]
                records[positions[message_id]] = record
            else:
                positions[message_id] = len(records)
                records.append(record)
    return records


def graph_data(path: str, opener, rates=None) -> dict:
    """Build turns and explicit tool-use/result edges from one transcript."""
    rates = rates or load_rates()
    turns, tools, notifications = [], {}, {}
    current = None
    first_time, last_time, invalid = None, None, 0
    with opener() as stream:
        for line_no, raw in enumerate(stream, 1):
            try:
                row = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
                invalid += 1
                continue
            if not isinstance(row, dict):
                continue
            stamp = row.get("timestamp")
            if isinstance(stamp, str):
                first_time = min(first_time, stamp) if first_time else stamp
                last_time = max(last_time, stamp) if last_time else stamp
            message = row.get("message") or {}
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            origin_data = row.get("origin") or {}
            origin = origin_data.get("kind") if isinstance(origin_data, dict) else None
            candidate = _text(content)
            has_text_blocks = (isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "text"
                                                               and isinstance(b.get("text"), str) and b["text"].strip()
                                                               for b in content))
            is_prompt = (row.get("type") == "user" and
                         (origin == "human" or
                          (origin is None and (isinstance(content, str) or has_text_blocks) and
                           candidate.strip() and not candidate.lstrip().startswith("<"))))
            if is_prompt:
                current = {"id": len(turns), "line": line_no, "time": stamp,
                           "end": stamp, "prompt": candidate[:1200], "tools": [], "assistant": [],
                           "errors": 0}
                turns.append(current)
            elif current and isinstance(stamp, str):
                current["end"] = stamp
            if row.get("type") == "assistant" and current:
                text = _text(content)
                if text:
                    current["assistant"].append({"line": line_no, "text": text[:350]})
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "tool_use" and isinstance(block.get("id"), str) and block["id"] not in tools:
                        data = block.get("input") or {}
                        tool = {"id": block["id"], "name": block.get("name") if isinstance(block.get("name"), str) else "Tool",
                                "label": _label(block.get("name"), data), "line": line_no,
                                "time": stamp, "result_line": None, "result_time": None,
                                "duration_seconds": None, "result": None,
                                "error": False, "turn": current["id"] if current else None,
                                "task_id": None, "subagent": None}
                        tools[tool["id"]] = tool
                        if current:
                            current["tools"].append(tool["id"])
                    if block.get("type") == "tool_result" and isinstance(block.get("tool_use_id"), str) and block["tool_use_id"] in tools:
                        tool = tools[block["tool_use_id"]]
                        if tool["result_line"] is not None:
                            continue
                        tool["result_line"] = line_no
                        tool["result_time"] = stamp if isinstance(stamp, str) else None
                        tool["duration_seconds"] = duration_seconds(tool["time"], tool["result_time"])
                        tool["error"] = bool(block.get("is_error"))
                        tool["result"] = _text(block.get("content"))[:220]
                        if tool["error"] and tool["turn"] is not None:
                            turns[tool["turn"]]["errors"] += 1
            if origin == "task-notification":
                text = _text(content)
                task, tool = TASK_ID.search(text), TOOL_ID.search(text)
                if task and tool:
                    notifications[tool.group(1)] = task.group(1)
    for tool_id, task_id in notifications.items():
        if tool_id in tools:
            tools[tool_id]["task_id"] = task_id
    # Claude Code stores child transcripts under <session-stem>/subagents/.
    child_openers = {}
    local = Path(path)
    if local.is_file():
        child_dir = local.parent / local.stem / "subagents"
        if child_dir.is_dir():
            child_openers = {item.stem.removeprefix("agent-"): (str(item), lambda item=item: item.open(encoding="utf-8"))
                             for item in child_dir.glob("agent-*.jsonl")}
    elif path.startswith(("s3://", "gs://")):
        stem = path.rsplit("/", 1)[-1].removesuffix(".jsonl")
        prefix = path.rsplit("/", 1)[0] + "/" + stem + "/subagents"
        try:
            child_openers = {item.rsplit("/", 1)[-1].removeprefix("agent-").removesuffix(".jsonl"): (item, child_opener)
                             for item, child_opener in session_files(prefix)}
        except OSError:
            child_openers = {}
    child_records = {}
    child_summaries = {}
    for task_id, child in child_openers.items():
        child_records[task_id] = _usage_records(child[1], rates)
        child_summaries[task_id] = summarize(child_records[task_id])
    for tool in tools.values():
        child = child_openers.get(tool["task_id"])
        if child:
            summary = parse_session(*child)
            tool["subagent"] = {"path": child[0], "lines": summary["lines"],
                                "tools": sum(summary["tools"].values()),
                                "first": summary["first_timestamp"],
                                "last": summary["last_timestamp"],
                                "cost": child_summaries[tool["task_id"]]}
    main_records = _usage_records(opener, rates, [turn["line"] for turn in turns])
    by_turn = {turn["id"]: [] for turn in turns}
    for record in main_records:
        if record["turn"] is not None:
            by_turn[record["turn"]].append(record)
    linked_tasks = set()
    for tool in tools.values():
        task_id = tool["task_id"]
        if task_id in child_records and tool["turn"] is not None and task_id not in linked_tasks:
            by_turn[tool["turn"]].extend(child_records[task_id])
            linked_tasks.add(task_id)
    for turn in turns:
        turn["cost"] = summarize(by_turn[turn["id"]])
    all_records = main_records + [record for records in child_records.values() for record in records]
    cost = summarize(all_records)
    cost.update({"main": summarize(main_records),
                 "children": summarize([record for records in child_records.values() for record in records]),
                 "child_transcripts": len(child_records), "linked_child_transcripts": len(linked_tasks),
                 "pricing_source": PRICING_SOURCE, "pricing_checked": PRICING_CHECKED,
                 "basis": "Public Claude API standard rates; API-equivalent estimate, not an invoice or subscription charge"})
    counts = Counter(t["name"] for t in tools.values())
    nodes, edges = build_nodes(opener, tools, main_records, [turn["line"] for turn in turns])
    meta = parse_session(path, opener)
    unknown_models = sorted(str(model) for model in cost["models"]
                            if any(record["model"] == model and record["estimated_usd"] is None
                                   for record in all_records))
    duration = duration_seconds(first_time, last_time)
    from .plugins import run_insights
    insights = {"tool_counts": dict(counts), "model_counts": cost["models"],
                "errors": sum(t["error"] for t in tools.values()),
                "duration_seconds": duration, "warnings": list(cost["warnings"])}
    result = {"schema_version": 1, "title": meta["session_id"] or Path(path).stem,
            "session_id": meta["session_id"], "project": meta["project"],
            "first_timestamp": first_time, "last_timestamp": last_time,
            "duration_seconds": duration, "usage": cost["tokens"],
            "estimated_usd": cost["estimated_usd"],
            "pricing_complete": cost["estimated_usd"] is not None,
            "unpriced_models": unknown_models, "insights": insights,
            "nodes": nodes, "edges": edges,
            "source": path, "first": first_time, "last": last_time,
            "invalid_lines": invalid, "turn_count": len(turns), "tool_count": len(tools),
            "agent_count": counts.get("Agent", 0), "error_count": sum(t["error"] for t in tools.values()),
             "tools_by_type": dict(counts), "turns": turns, "tools": tools, "cost": cost}
    from .analytics import build_analytics
    result["analytics"] = build_analytics(turns, tools, nodes, main_records, child_records,
                                            linked_tasks, cost, by_turn)
    result["insights"].update(run_insights(result))
    return result


def write_graph(source: str, output: str, prices=None) -> dict:
    files = list(session_files(source))
    if len(files) != 1:
        raise ValueError("Graph a single .jsonl session file; the source matched %d files" % len(files))
    data = graph_data(*files[0], rates=load_rates(prices))
    document = render_graph(data)
    Path(output).write_text(document, encoding="utf-8")
    return data


def render_graph(data: dict) -> str:
    """Return a standalone HTML document suitable for saving or embedding."""
    template = Path(__file__).with_name("graph_view.html").read_text(encoding="utf-8")
    safe_json = json.dumps(data, ensure_ascii=False, default=str).replace("<", "\\u003c")
    vendor = "".join("<script>" + script.replace("</", "<\\/") + "</script>\n"
                     for script in _vendor_scripts())
    return template.replace("<script>", vendor + "<script>", 1).replace("__GRAPH_DATA__", safe_json)


def _vendor_scripts():
    directory = Path(__file__).with_name("vendor")
    return [(directory / name).read_text(encoding="utf-8")
            for name in ("cytoscape.min.js", "echarts.min.js")]


def viewer_assets() -> dict:
    """Return reusable viewer CSS and JavaScript without transcript data."""
    template = Path(__file__).with_name("graph_view.html").read_text(encoding="utf-8")
    style = re.search(r"<style>(.*?)</style>", template, re.S)
    script = re.search(r"<script>(.*?)</script>", template, re.S)
    if style is None or script is None:
        raise RuntimeError("Packaged graph template has no reusable viewer assets")
    js = re.sub(r"/\* CI_BOOTSTRAP_START \*/.*?/\* CI_BOOTSTRAP_END \*/", "", script.group(1), flags=re.S)
    if "CI_BOOTSTRAP_START" in js or "__GRAPH_DATA__" in js:
        raise RuntimeError("Could not separate viewer JavaScript from session data")
    return {"css": style.group(1).strip() + "\n",
            "js": "\n".join(_vendor_scripts()) + "\n" + js.strip() + "\n"}


def write_viewer_assets(output_dir: str) -> dict:
    """Write viewer.css and viewer.js for direct embedding; return their paths."""
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    assets = viewer_assets()
    paths = {"css": target / "viewer.css", "js": target / "viewer.js"}
    for name, path in paths.items():
        path.write_text(assets[name], encoding="utf-8")
    return {name: str(path) for name, path in paths.items()}
