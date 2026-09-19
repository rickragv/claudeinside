"""Command-line interface."""

import argparse
import json
import sys
from .core import inspect


def main(argv=None):
    parser = argparse.ArgumentParser(prog="claude-insight", description="Inspect Claude Code JSONL transcripts")
    parser.add_argument("source", nargs="?", help="A .jsonl file, folder, s3:// prefix, or gs:// prefix")
    parser.add_argument("--demo", action="store_true", help="Use the bundled synthetic transcript")
    parser.add_argument("--events", action="store_true", help="Include user/assistant text and tool timeline (sensitive)")
    parser.add_argument("--search", metavar="TEXT", help="Find matching session events; implies --events")
    parser.add_argument("--limit", type=int, default=50, help="Maximum search matches (default: 50)")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    parser.add_argument("--graph", metavar="HTML_FILE", help="Write an interactive graph for one session file")
    parser.add_argument("--prices", metavar="JSON_FILE", help="Override USD-per-million-token rates by exact model ID (with --graph)")
    args = parser.parse_args(argv)
    if args.demo:
        from pathlib import Path
        args.source = str(Path(__file__).with_name("demo_session.jsonl"))
    if not args.source:
        parser.error("source is required unless --demo is set")
    if args.graph:
        from .graph import write_graph
        try:
            data = write_graph(args.source, args.graph, args.prices)
        except (OSError, RuntimeError, ValueError) as exc:
            parser.exit(1, f"claude-insight: {exc}\n")
        print(f"Wrote {args.graph}: {data['turn_count']} human turns, {data['tool_count']} distinct tool calls")
        return 0
    try:
        report = inspect(args.source, args.events or bool(args.search))
    except (OSError, RuntimeError) as exc:
        parser.exit(1, f"claude-insight: {exc}\n")
    if args.search:
        query = args.search.casefold()
        matches = []
        for session in report["sessions"]:
            for event in session.pop("events", []):
                if query in event["text"].casefold() or any(query in (tool["name"] or "").casefold() for tool in event["tools"]):
                    matches.append({"session_id": session["session_id"], "path": session["path"], **event})
                    if len(matches) >= max(args.limit, 0):
                        break
            if len(matches) >= max(args.limit, 0):
                break
        report["matches"] = matches
    if args.json or args.events or args.search:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(f"Sessions: {report['session_count']}")
        print(f"Usage: {report['usage']}")
        print(f"Tools: {report['tools']}")
        for session in report["sessions"]:
            print(f"{session['session_id'] or '?'}  {session['first_timestamp'] or '?'}  {session['project'] or session['path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
