"""Turn transcript rows into a small, JSON-safe causal graph."""

from datetime import datetime
import json


def duration_seconds(first, last):
    try:
        start = datetime.fromisoformat(first.replace("Z", "+00:00"))
        end = datetime.fromisoformat(last.replace("Z", "+00:00"))
        elapsed = (end - start).total_seconds()
        return elapsed if elapsed >= 0 else None
    except (AttributeError, TypeError, ValueError):
        return None


def build_nodes(opener, tools, records, turn_lines):
    """Build nodes in source order and resolve parentUuid after all rows arrive."""
    nodes, edges, aliases, pending = [], [], {}, []
    tool_nodes = {}
    messages = {}
    record_by_message = {record["message_id"]: record for record in records if "message_id" in record}
    turn = None
    next_turn = 0
    last_id = None
    seen_events = set()
    with opener() as stream:
        for line_no, raw in enumerate(stream, 1):
            while next_turn < len(turn_lines) and line_no >= turn_lines[next_turn]:
                turn = next_turn
                next_turn += 1
            try:
                row = json.loads(raw)
            except (ValueError, TypeError, UnicodeDecodeError):
                continue
            if not isinstance(row, dict) or row.get("type") not in ("user", "assistant"):
                continue
            message = row.get("message")
            if not isinstance(message, dict):
                continue
            blocks = message.get("content")
            row_id = row.get("uuid")
            row_id = str(row_id) if isinstance(row_id, str) and row_id else f"line:{line_no}"
            if row_id in seen_events:
                continue
            seen_events.add(row_id)
            kind = row["type"]
            message_id = message.get("id") if kind == "assistant" and isinstance(message.get("id"), str) else None
            text = blocks if isinstance(blocks, str) else "\n".join(
                str(block.get("text", "")) for block in blocks
                if isinstance(block, dict) and block.get("type") == "text"
            ) if isinstance(blocks, list) else ""
            # Tool-result records are represented by their tool node.
            results_only = (kind == "user" and isinstance(blocks, list) and
                            any(isinstance(b, dict) and b.get("type") == "tool_result" for b in blocks) and not text.strip())
            if results_only and isinstance(row.get("uuid"), str):
                result_tools = [b.get("tool_use_id") for b in blocks if isinstance(b, dict) and b.get("type") == "tool_result"]
                for tool_id in result_tools:
                    if isinstance(tool_id, str) and tool_id in tool_nodes:
                        aliases[row["uuid"]] = tool_nodes[tool_id]["id"]
                        last_id = tool_nodes[tool_id]["id"]
                        break
            if message_id in messages:
                previous = messages[message_id]
                if len(text) >= len(previous["text"]):
                    previous["text"] = text[:4000]
                    previous["label"] = text.strip()[:72] or "Assistant"
                if isinstance(row.get("uuid"), str):
                    aliases[row["uuid"]] = previous["id"]
                row_id = previous["id"]
            elif not results_only and (text.strip() or kind == "assistant"):
                node = {"id": row_id, "type": kind, "label": (text.strip()[:72] or "Assistant"),
                        "text": text[:4000], "line": line_no,
                        "timestamp": row.get("timestamp") if isinstance(row.get("timestamp"), str) else None,
                        "parent_id": None, "turn": turn, "tool_name": None,
                        "model": message.get("model") if kind == "assistant" and isinstance(message.get("model"), str) else None,
                        "message_id": message_id,
                        "usage_line": record_by_message.get(message_id, {}).get("line"),
                        "usage": record_by_message.get(message_id, {}).get("tokens"),
                        "estimated_usd": record_by_message.get(message_id, {}).get("estimated_usd"), "is_error": False}
                nodes.append(node)
                if message_id:
                    messages[message_id] = node
                parent = row.get("parentUuid")
                pending.append((node, parent, last_id))
                if isinstance(row.get("uuid"), str):
                    aliases[row["uuid"]] = row_id
                last_id = row_id
            if isinstance(blocks, list):
                for block in blocks:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    tool_id = block.get("id")
                    if not isinstance(tool_id, str) or tool_id in tool_nodes:
                        continue
                    tool = tools.get(tool_id, {})
                    node_id = "tool:" + tool_id
                    node = {"id": node_id, "type": "tool", "label": tool.get("label") or block.get("name") or "Tool",
                            "text": tool.get("result") or "", "line": tool.get("line", line_no),
                            "result_line": tool.get("result_line"),
                            "result_time": tool.get("result_time"),
                            "duration_seconds": tool.get("duration_seconds"),
                            "timestamp": row.get("timestamp"),
                            "parent_id": row_id if row_id in aliases else last_id, "turn": turn,
                            "tool_id": tool_id,
                            "tool_name": tool.get("name") or block.get("name"), "model": None,
                            "usage": None, "estimated_usd": None, "is_error": bool(tool.get("error"))}
                    nodes.append(node)
                    tool_nodes[tool_id] = node
                    if node["parent_id"]:
                        edges.append({"source": node["parent_id"], "target": node_id, "type": "tool_use"})
    ids = {node["id"] for node in nodes}
    for node, parent, fallback in pending:
        source = aliases.get(parent) if isinstance(parent, str) else None
        if source not in ids or source == node["id"]:
            source = fallback if fallback in ids and fallback != node["id"] else None
        node["parent_id"] = source
        if source:
            edges.append({"source": source, "target": node["id"], "type": "causal" if parent in aliases else "sequence"})
    return nodes, edges
