"""Streaming Claude Code transcript parser and source adapters."""

from collections import Counter
from pathlib import Path
import json
from .plugins import find_source
from .pricing import merge_usage_snapshot


def session_files(source: str):
    """Yield (display path, opener) for local JSONL files or cloud objects."""
    plugin = find_source(source)
    if plugin is not None:
        yield from plugin.session_files(source)
        return
    if source.startswith(("s3://", "gs://", "gcs://")):
        try:
            import fsspec
        except ImportError as exc:
            raise RuntimeError("Cloud support requires: pip install 'claude-session-insight[cloud]'") from exc
        protocol = "gs" if source.startswith(("gs://", "gcs://")) else "s3"
        normalized = source.replace("gcs://", "gs://", 1)
        fs = fsspec.filesystem(protocol)
        path = fs._strip_protocol(normalized)
        paths = [path] if fs.isfile(path) else fs.find(path)
        for item in sorted(paths):
            if item.endswith(".jsonl"):
                yield (f"{protocol}://{item}", lambda item=item: fs.open(item, "rt", encoding="utf-8"))
        return
    root = Path(source).expanduser()
    if not root.exists():
        raise FileNotFoundError(source)
    paths = [root] if root.is_file() else root.rglob("*.jsonl")
    for item in sorted(paths):
        if item.suffix == ".jsonl":
            yield (str(item), lambda item=item: item.open("r", encoding="utf-8"))


def content_text(content) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(block.get("text", "") for block in content
                     if isinstance(block, dict) and block.get("type") == "text")


def parse_session(path: str, opener, include_events: bool = False) -> dict:
    counts, tools, models, usage = Counter(), Counter(), Counter(), Counter()
    events, usage_snapshots, seen_tools = [], {}, set()
    meta = {"path": path, "session_id": None, "project": None,
            "first_timestamp": None, "last_timestamp": None, "lines": 0,
            "invalid_lines": 0}
    with opener() as stream:
        for line_number, line in enumerate(stream, 1):
            meta["lines"] += 1
            try:
                row = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
                meta["invalid_lines"] += 1
                continue
            if not isinstance(row, dict):
                continue
            kind = row.get("type", "unknown")
            kind = kind if isinstance(kind, str) else "unknown"
            counts[kind] += 1
            if isinstance(row.get("sessionId"), str):
                meta["session_id"] = meta["session_id"] or row["sessionId"]
            if isinstance(row.get("cwd"), str):
                meta["project"] = meta["project"] or row["cwd"]
            stamp = row.get("timestamp")
            if isinstance(stamp, str):
                if meta["first_timestamp"] is None or stamp < meta["first_timestamp"]:
                    meta["first_timestamp"] = stamp
                if meta["last_timestamp"] is None or stamp > meta["last_timestamp"]:
                    meta["last_timestamp"] = stamp
            message = row.get("message") or {}
            if not isinstance(message, dict):
                message = {}
            model = message.get("model")
            if isinstance(model, str) and model:
                models[model] += 1
            message_usage = message.get("usage") or {}
            message_id = message.get("id") or row.get("uuid")
            # Claude may write multiple snapshots of the same API message.
            if kind == "assistant" and isinstance(message_usage, dict) and message_usage and isinstance(message_id, str) and message_id:
                usage_snapshots[message_id] = merge_usage_snapshot(usage_snapshots.get(message_id), message_usage)
            blocks = message.get("content")
            if isinstance(blocks, list):
                for block in blocks:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        tool_id = block.get("id")
                        tool_id = tool_id if isinstance(tool_id, str) else (line_number, len(seen_tools))
                        if tool_id not in seen_tools:
                            seen_tools.add(tool_id)
                            name = block.get("name")
                            tools[name if isinstance(name, str) and name else "unknown"] += 1
            if include_events and kind in ("user", "assistant"):
                events.append({"line": line_number, "timestamp": stamp, "type": kind,
                               "text": content_text(blocks),
                               "tools": [{"name": b.get("name"), "id": b.get("id")}
                                         for b in blocks if isinstance(b, dict) and b.get("type") == "tool_use"]
                               if isinstance(blocks, list) else [],
                               "tool_results": [b.get("tool_use_id") for b in blocks
                                                if isinstance(b, dict) and b.get("type") == "tool_result"]
                               if isinstance(blocks, list) else []})
    for snapshot in usage_snapshots.values():
        for key in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"):
            value = snapshot.get(key, 0) or 0
            if isinstance(value, (int, float)) and value >= 0:
                usage[key] += int(value)
    meta.update({"record_types": dict(counts), "tools": dict(tools),
                 "models": dict(models), "usage": dict(usage)})
    if include_events:
        meta["events"] = events
    return meta


def inspect(source: str, include_events: bool = False) -> dict:
    sessions = [parse_session(path, opener, include_events)
                for path, opener in session_files(source)]
    total_usage, total_tools = Counter(), Counter()
    for session in sessions:
        total_usage.update(session["usage"])
        total_tools.update(session["tools"])
    return {"source": source, "session_count": len(sessions),
            "usage": dict(total_usage), "tools": dict(total_tools), "sessions": sessions}
