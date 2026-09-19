"""Reconciled, evidence-linked analytics for transcript graph exports."""

from collections import defaultdict
from statistics import median

from .graph_model import duration_seconds
from .pricing import summarize


TOKEN_KEYS = ("input_tokens", "output_tokens", "cache_creation_input_tokens",
              "cache_read_input_tokens")


def _total_tokens(tokens):
    return sum(tokens.get(key, 0) for key in TOKEN_KEYS)


def _request_input(record):
    return sum(record["tokens"].get(key, 0) for key in
               ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))


def _priced_view(records):
    summary = summarize(records)
    tokens = summary["tokens"]
    return {"tokens": tokens, "total_tokens": _total_tokens(tokens),
            "estimated_usd": summary["estimated_usd"],
            "priced_subtotal_usd": summary["priced_subtotal_usd"],
            "pricing_complete": summary["estimated_usd"] is not None,
            "usage_records": summary["usage_records"],
            "priced_records": summary["priced_records"]}


def build_analytics(turns, tools, nodes, main_records, child_records, linked_tasks, cost, by_turn):
    """Summarize recorded evidence; preserve unattributed usage as a remainder."""
    nodes_by_turn = defaultdict(list)
    for node in nodes:
        if isinstance(node.get("turn"), int):
            nodes_by_turn[node["turn"]].append(node)

    child_by_turn = defaultdict(set)
    assigned_tasks = set()
    for tool in tools.values():
        task = tool.get("task_id")
        if task in linked_tasks and task not in assigned_tasks and isinstance(tool.get("turn"), int):
            child_by_turn[tool["turn"]].add(task)
            assigned_tasks.add(task)

    turn_views = []
    main_by_turn = defaultdict(list)
    for record in main_records:
        if isinstance(record.get("turn"), int):
            main_by_turn[record["turn"]].append(record)
    for turn in turns:
        turn_id = turn["id"]
        prompt = turn["prompt"]
        lines = prompt.strip().splitlines()
        own_nodes = nodes_by_turn[turn_id]
        view = {"id": turn_id, "prompt": prompt,
                "title": lines[0][:90] if lines else f"Turn {turn_id + 1}",
                "time": turn["time"], "end": turn.get("end"),
                "elapsed_seconds": duration_seconds(turn["time"], turn.get("end")),
                "node_ids": [node["id"] for node in own_nodes],
                "event_count": len(own_nodes), "tool_count": len(turn["tools"]),
                "error_count": turn["errors"],
                "child_count": len(child_by_turn[turn_id]),
                "peak_request_input_tokens": max((_request_input(record) for record in main_by_turn[turn_id]),
                                                 default=None)}
        view.update(_priced_view(by_turn[turn_id]))
        turn_views.append(view)

    tool_groups = defaultdict(list)
    for tool in tools.values():
        tool_groups[tool["name"]].append(tool)
    tool_views = []
    for name, group in tool_groups.items():
        timings = sorted(tool["duration_seconds"] for tool in group
                         if tool.get("duration_seconds") is not None)
        errors = sum(bool(tool["error"]) for tool in group)
        tool_views.append({"name": name, "count": len(group), "errors": errors,
                           "error_rate": errors / len(group),
                           "pending_count": sum(tool["result_line"] is None for tool in group),
                           "timing_sample_count": len(timings),
                           "median_seconds": median(timings) if timings else None,
                           "p95_seconds": timings[(95 * len(timings) + 99) // 100 - 1] if timings else None,
                           "max_seconds": timings[-1] if timings else None})
    tool_views.sort(key=lambda item: (-item["count"], item["name"]))
    slowest_calls = [{"id": tool["id"], "node_id": "tool:" + tool["id"],
                      "turn_id": tool["turn"], "name": tool["name"],
                      "duration_seconds": tool["duration_seconds"],
                      "evidence_line": tool["line"], "result_line": tool["result_line"]}
                     for tool in tools.values() if tool.get("duration_seconds") is not None]
    slowest_calls.sort(key=lambda item: (-item["duration_seconds"], item["evidence_line"]))
    slowest_calls = slowest_calls[:5]

    all_records = main_records + [record for child in child_records.values() for record in child]
    model_groups = defaultdict(list)
    for record in all_records:
        model_groups[record["model"] or "unknown"].append(record)
    model_views = []
    for name, records in model_groups.items():
        model_views.append({"name": name, **_priced_view(records)})
    model_views.sort(key=lambda item: (-item["total_tokens"], item["name"]))

    total_tokens = _total_tokens(cost["tokens"])
    attributed = [record for records in by_turn.values() for record in records]
    attributed_summary = summarize(attributed)
    attributed_tokens = _total_tokens(attributed_summary["tokens"])
    unlinked = set(child_records) - linked_tasks
    unattributed = [record for record in main_records if record["turn"] is None]
    unattributed += [record for task in unlinked for record in child_records[task]]
    unattributed_summary = summarize(unattributed)
    input_demand = sum(cost["tokens"].get(key, 0) for key in
                       ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    totals = {"tokens": cost["tokens"], "total_tokens": total_tokens,
              "input_demand_tokens": input_demand,
              "cache_read_share": (cost["tokens"].get("cache_read_input_tokens", 0) / input_demand
                                   if input_demand else None),
              "estimated_usd": cost["estimated_usd"],
              "priced_subtotal_usd": cost["priced_subtotal_usd"],
              "pricing_complete": cost["estimated_usd"] is not None,
              "attributed_tokens": attributed_tokens,
              "unattributed_tokens": _total_tokens(unattributed_summary["tokens"]),
              "attributed_priced_subtotal_usd": attributed_summary["priced_subtotal_usd"],
              "unattributed_priced_subtotal_usd": unattributed_summary["priced_subtotal_usd"],
              "unlinked_child_count": len(unlinked)}

    findings = []
    def add(title, detail, severity, turn_id=None, node_id=None, evidence_line=None):
        findings.append({"title": title, "detail": detail, "severity": severity,
                         "turn_id": turn_id, "node_id": node_id, "evidence_line": evidence_line})

    if turn_views:
        largest = max(turn_views, key=lambda item: (item["priced_subtotal_usd"], -item["id"]))
        if largest["priced_subtotal_usd"] > 0:
            share = largest["priced_subtotal_usd"] / attributed_summary["priced_subtotal_usd"]
            add("Cost concentration", f"Turn {largest['id'] + 1} accounts for {share:.1%} of "
                "turn-attributed priced cost; unpriced usage may change the ranking.", "info", largest["id"],
                largest["node_ids"][0] if largest["node_ids"] else None,
                turns[largest["id"]]["line"])
        peak = max((view for view in turn_views if view["peak_request_input_tokens"] is not None),
                   key=lambda item: (item["peak_request_input_tokens"], -item["id"]), default=None)
        if peak and peak["peak_request_input_tokens"] > 0:
            record = max(main_by_turn[peak["id"]], key=_request_input)
            node = next((node for node in nodes_by_turn[peak["id"]]
                         if node.get("message_id") == record["message_id"]), None)
            add("Peak request input", f"A single assistant request in turn {peak['id'] + 1} "
                f"recorded {peak['peak_request_input_tokens']:,} input tokens including cache traffic.",
                "info", peak["id"], node["id"] if node else None, record["line"])
    errors = [tool for tool in tools.values() if tool["error"]]
    if errors:
        first = min(errors, key=lambda tool: tool["result_line"] or tool["line"])
        add("Recorded tool errors", f"{len(errors)} tool call(s) returned an error.", "warning",
            first["turn"], "tool:" + first["id"], first["result_line"])
    if tool_views:
        top = tool_views[0]
        if top["count"] > 1:
            example = next(tool for tool in tools.values() if tool["name"] == top["name"])
            add("Most used tool", f"{top['name']} accounts for {top['count']} of {len(tools)} recorded calls.",
                "info", example["turn"], "tool:" + example["id"], example["line"])
    if slowest_calls:
        slowest = slowest_calls[0]
        add("Slowest observed tool", f"{slowest['name']} took {slowest['duration_seconds']:g}s "
            "from call to recorded result, including any wait.", "info", slowest["turn_id"],
            slowest["node_id"], slowest["evidence_line"])
    if cost["estimated_usd"] is None:
        unpriced_main = next((record for record in main_records
                              if record["estimated_usd"] is None), None)
        unpriced_node = next((node for node in nodes
                              if unpriced_main and node.get("message_id") == unpriced_main["message_id"]), None)
        add("Incomplete price coverage", f"{cost['usage_records'] - cost['priced_records']} usage "
            "record(s) cannot be priced with the supplied rates and metadata.", "warning",
            unpriced_main["turn"] if unpriced_main else None,
            unpriced_node["id"] if unpriced_node else None,
            unpriced_main["line"] if unpriced_main else None)
    if unlinked:
        add("Unlinked child usage", f"{len(unlinked)} child transcript(s) have no matching "
            "task notification and turn; their usage is in session totals only.", "warning")

    return {"turns": turn_views, "tools": tool_views, "models": model_views,
            "slowest_calls": slowest_calls, "totals": totals, "findings": findings}
