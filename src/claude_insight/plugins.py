"""Small extension registry for transcript sources and computed insights."""

from typing import Any, Callable, Dict, Iterable, List, Optional, Protocol, TextIO, Tuple

SessionFile = Tuple[str, Callable[[], TextIO]]


class SourcePlugin(Protocol):
    def can_open(self, source: str) -> bool: ...
    def session_files(self, source: str) -> Iterable[SessionFile]: ...


class InsightPlugin(Protocol):
    name: str
    def analyze(self, graph: Dict[str, Any]) -> Any: ...


_sources: List[SourcePlugin] = []
_insights: List[InsightPlugin] = []


def register_source(plugin: SourcePlugin) -> None:
    """Register a source before calling inspect or graph_data."""
    _sources.append(plugin)


def register_insight(plugin: InsightPlugin) -> None:
    """Register a JSON-serializable insight for future graphs."""
    if any(existing.name == plugin.name for existing in _insights):
        raise ValueError("Insight name already registered: " + plugin.name)
    _insights.append(plugin)


def find_source(source: str) -> Optional[SourcePlugin]:
    return next((plugin for plugin in reversed(_sources) if plugin.can_open(source)), None)


def run_insights(graph: Dict[str, Any]) -> Dict[str, Any]:
    result = {}
    for plugin in _insights:
        try:
            result[plugin.name] = plugin.analyze(graph)
        except Exception as exc:
            result[plugin.name] = {"error": "%s: %s" % (type(exc).__name__, exc)}
    return result
