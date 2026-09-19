"""Audit files Git would include from this folder before publishing.

Run ``python scripts/public_release_audit.py --check`` before committing.
Private session exports may remain here when they are ignored by Git.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
FIXED = {
    ".gitignore", ".gitattributes", ".github/workflows/python.yml", "README.md", "LICENSE",
    "pyproject.toml", "docs/PUBLIC_RELEASE.md",
    "scripts/make_demo.py", "scripts/generate_public_media.py",
    "scripts/public_release_audit.py", "src/claude_insight/__init__.py",
    "src/claude_insight/analytics.py", "src/claude_insight/cli.py",
    "src/claude_insight/core.py", "src/claude_insight/demo_session.jsonl",
    "src/claude_insight/graph.py", "src/claude_insight/graph_model.py",
    "src/claude_insight/graph_view.html", "src/claude_insight/plugins.py",
    "src/claude_insight/pricing.py", "src/claude_insight/py.typed",
    "tests/browser_embed_smoke.py", "tests/browser_session_map_smoke.py",
    "tests/browser_smoke.py", "tests/browser_visual_checkpoint.py",
    "tests/test_analytics.py", "tests/test_graph.py", "tests/test_pricing.py",
    "tests/test_public_release_audit.py", "tests/wheel_audit.py",
    "src/claude_insight/vendor/README.md",
    "src/claude_insight/vendor/cytoscape.LICENSE",
    "src/claude_insight/vendor/cytoscape.min.js",
    "src/claude_insight/vendor/echarts.LICENSE",
    "src/claude_insight/vendor/echarts.LICENSE-d3",
    "src/claude_insight/vendor/echarts.NOTICE",
    "src/claude_insight/vendor/echarts.min.js",
    "docs/media/manifest.json",
}
VENDOR_SHA256 = {
    "src/claude_insight/vendor/cytoscape.min.js":
        "5f3b5b529546d5af1fc5628590af033b74511a5b6f789f5f4682845863228b91",
    "src/claude_insight/vendor/echarts.min.js":
        "b66b25aeb4df84e33199dc21694014d336d222cbd9deb0e5a7c14bd6aa0d0fd0",
}
MEDIA_SUFFIXES = {".png", ".mp4"}
TEXT_SUFFIXES = {".py", ".md", ".toml", ".yml", ".json", ".jsonl", ".html", ".LICENSE", ".NOTICE"}
SENSITIVE = [
    ("user home path", re.compile(r"(?i)(?:[a-z]:[\\/]|/)(?:users|home)[\\/][^\\/\s'\"<>]{2,}")),
    ("Claude project path", re.compile(r"(?i)\.claude[\\/]projects[\\/]")),
    ("session UUID", re.compile(r"(?i)\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b")),
    ("email address", re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)),
    ("private key", re.compile("-----BEGIN " + "(?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("credential", re.compile(r"(?i)(?:api[_-]?key|access[_-]?token|secret[_-]?key)\s*[:=]\s*['\"][A-Za-z0-9_./+-]{12,}")),
    ("GitHub token", re.compile(r"\b(?:ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{20,})\b")),
    ("API token", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("Bearer token", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~-]{20,}\b")),
]


class AuditError(RuntimeError):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def text_digest(path: Path) -> str:
    """Hash the bytes Git writes on checkout with the repository's LF policy."""
    return digest(path.read_bytes().replace(b"\r\n", b"\n"))


def media_files(root: Path) -> set[str]:
    manifest_path = root / "docs/media/manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AuditError("missing or invalid synthetic media manifest") from exc
    if (manifest.get("provenance") != "synthetic"
            or manifest.get("hash_algorithm") != "sha256"
            or manifest.get("input_canonicalization") != "lf"
            or not isinstance(manifest.get("assets"), list)):
        raise AuditError("media provenance must be synthetic with an assets list")
    inputs = manifest.get("inputs")
    expected_inputs = {"scripts/generate_public_media.py", "src/claude_insight/graph.py",
                       "src/claude_insight/graph_view.html", "synthetic-graph-json"}
    if (not isinstance(inputs, list) or len(inputs) != len(expected_inputs)
            or not all(isinstance(item, dict) for item in inputs)
            or {item.get("path") for item in inputs} != expected_inputs):
        raise AuditError("synthetic media input hashes are missing")
    for item in inputs:
        name, sha = item.get("path"), item.get("sha256")
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
            raise AuditError("invalid synthetic input SHA256")
        if name != "synthetic-graph-json" and text_digest(root / name) != sha:
            raise AuditError(f"synthetic media input hash mismatch: {name}")
    spec = importlib.util.spec_from_file_location("public_media_generator", root / "scripts/generate_public_media.py")
    if spec is None or spec.loader is None:
        raise AuditError("synthetic media generator cannot be imported")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    graph = module.fabricated_graph()
    graph_bytes = json.dumps(graph, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    graph_sha = next(item["sha256"] for item in inputs if item["path"] == "synthetic-graph-json")
    if digest(graph_bytes) != graph_sha:
        raise AuditError("synthetic graph fixture hash mismatch")
    result = set()
    for entry in manifest["assets"]:
        if not isinstance(entry, dict):
            raise AuditError("invalid media entry")
        name, sha = entry.get("path"), entry.get("sha256")
        if (not isinstance(name, str) or not re.fullmatch(r"docs/media/[a-z0-9_-]+\.(?:png|mp4)", name)
                or not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha)):
            raise AuditError("invalid media path or SHA256")
        if name in result:
            raise AuditError("duplicate media entry")
        path = root / name
        if not path.is_file() or path.is_symlink() or digest(path.read_bytes()) != sha:
            raise AuditError(f"media hash mismatch: {name}")
        result.add(name)
    if not result or not any(p.endswith(".png") for p in result) or not any(p.endswith(".mp4") for p in result):
        raise AuditError("at least one PNG screenshot and one MP4 demo are required")
    actual = {p.relative_to(root).as_posix() for p in (root / "docs/media").iterdir()}
    if actual != result | {"docs/media/manifest.json"}:
        raise AuditError("docs/media contains unmanifested files")
    return result


def scan_text(name: str, data: bytes) -> None:
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AuditError(f"non UTF-8 text: {name}") from exc
    for label, pattern in SENSITIVE:
        if pattern.search(content):
            raise AuditError(f"{name}: {label} detected")
    # Literal local home directory is also checked, including nonstandard homes.
    for env_name in ("USERPROFILE", "HOME"):
        home = os.environ.get(env_name, "")
        if len(home) > 5 and (home.replace("\\", "/").casefold() in content.replace("\\", "/").casefold()):
            raise AuditError(f"{name}: local home path detected")


def scan_png(name: str, data: bytes) -> None:
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise AuditError(f"{name}: invalid PNG")
    # Screenshots need no metadata chunks; EXIF/text can disclose private paths.
    offset = 8
    while offset + 12 <= len(data):
        length = int.from_bytes(data[offset:offset + 4], "big")
        kind = data[offset + 4:offset + 8]
        end = offset + 12 + length
        if end > len(data) or kind not in {b"IHDR", b"IDAT", b"IEND", b"sRGB", b"gAMA", b"cHRM", b"pHYs"}:
            raise AuditError(f"{name}: unsupported PNG chunk or metadata")
        offset = end
        if kind == b"IEND":
            if offset != len(data):
                raise AuditError(f"{name}: trailing PNG bytes")
            return
    raise AuditError(f"{name}: incomplete PNG")


def scan_mp4(name: str, path: Path, data: bytes) -> None:
    if not (b"ftyp" in data[:32] and b"moov" in data):
        raise AuditError(f"{name}: invalid MP4")
    # ffprobe exposes uncompressed container and stream metadata without
    # interpreting arbitrary compressed video bytes as text.
    try:
        result = subprocess.run(["ffprobe", "-v", "error", "-show_format", "-show_streams",
                                 "-of", "json", str(path)], capture_output=True, text=True, check=True)
        info = json.loads(result.stdout)
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        raise AuditError(f"{name}: ffprobe failed") from exc
    streams = info.get("streams", [])
    if len(streams) != 1 or streams[0].get("codec_type") != "video":
        raise AuditError(f"{name}: video must have exactly one stream and no audio")
    video = streams[0]
    if video.get("codec_name") != "h264" or video.get("pix_fmt") != "yuv420p":
        raise AuditError(f"{name}: video must be H.264 yuv420p")
    try:
        duration = float(info["format"]["duration"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AuditError(f"{name}: missing video duration") from exc
    if not (0 < duration <= 120):
        raise AuditError(f"{name}: video duration outside approved range")
    approved_tags = {"major_brand", "minor_version", "compatible_brands", "encoder",
                     "language", "handler_name", "vendor_id"}
    for where in (info["format"], video):
        tags = where.get("tags", {})
        if set(tags) - approved_tags:
            raise AuditError(f"{name}: unexpected video metadata tags")
        scan_text(name, json.dumps(tags).encode("utf-8"))


def ignore_rules(root: Path) -> list[tuple[str, bool, bool]]:
    """Read the simple, explicit subset of Git ignore syntax used here."""
    rules = []
    for raw in (root / ".gitignore").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        include = line.startswith("!")
        pattern = line[1:] if include else line
        if (not pattern or "**" in pattern or "?" in pattern or "\\" in pattern
                or pattern.startswith("#") or pattern != pattern.strip()):
            raise AuditError(f"unsupported .gitignore rule: {line}")
        rules.append((pattern.lstrip("/"), include, pattern.endswith("/")))
    return rules


def segment_match(name: str, pattern: str) -> bool:
    """Match slash separated paths without letting * cross a directory."""
    value_parts, pattern_parts = name.split("/"), pattern.split("/")
    return len(value_parts) == len(pattern_parts) and all(
        fnmatch.fnmatchcase(value, piece) for value, piece in zip(value_parts, pattern_parts))


def ignored(name: str, is_dir: bool, rules: list[tuple[str, bool, bool]]) -> bool:
    pieces = name.split("/")
    directories = ["/".join(pieces[:i]) for i in range(1, len(pieces) + (1 if is_dir else 0))]
    state = False
    for pattern, include, directory_rule in rules:
        pattern = pattern.rstrip("/")
        if directory_rule:
            matched = any(segment_match(part, pattern) if "/" in pattern
                          else fnmatch.fnmatchcase(part.rsplit("/", 1)[-1], pattern)
                          for part in directories)
        else:
            matched = segment_match(name, pattern) if "/" in pattern else fnmatch.fnmatchcase(pieces[-1], pattern)
        if matched:
            state = not include
    return state


def candidate_files(root: Path) -> set[str]:
    """Use Git's exact index when present; otherwise apply our restricted rules."""
    if (root / ".git").exists():
        run = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=root,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        return {p.decode("utf-8").replace("\\", "/") for p in run.stdout.split(b"\0") if p}
    rules = ignore_rules(root)
    found = set()
    for folder, dirs, files in os.walk(root, followlinks=False):
        current = Path(folder)
        keep = []
        for directory in dirs:
            name = (current / directory).relative_to(root).as_posix()
            if directory == ".git":
                continue
            if ignored(name, True, rules):
                continue
            if (current / directory).is_symlink():
                found.add(name)
                continue
            keep.append(directory)
        dirs[:] = keep
        for filename in files:
            name = (current / filename).relative_to(root).as_posix()
            if not ignored(name, False, rules):
                found.add(name)
    return found


def check_candidates(root: Path, approved: set[str]) -> None:
    unknown = candidate_files(root) - approved
    if unknown:
        raise AuditError("Git candidates outside publication list: " + ", ".join(sorted(unknown)))
    if (root / ".git").exists():
        run = subprocess.run(["git", "diff", "--name-only", "-z", "--"], cwd=root,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        stale = {p.decode("utf-8").replace("\\", "/") for p in run.stdout.split(b"\0") if p}
        if stale:
            raise AuditError("Git index differs from reviewed files: " + ", ".join(sorted(stale)))


def audit(root: Path, check_git: bool = True) -> list[str]:
    root = root.resolve()
    approved = FIXED | media_files(root)
    for name in sorted(approved):
        path = root / name
        if (not path.is_file() or path.is_symlink() or root not in path.resolve().parents
                or any(parent.is_symlink() for parent in path.parents if parent != root and root in parent.parents)):
            raise AuditError(f"missing or linked publication file: {name}")
        data = path.read_bytes()
        if name in VENDOR_SHA256 and digest(data) != VENDOR_SHA256[name]:
            raise AuditError(f"vendored dependency hash mismatch: {name}")
        if name.endswith(".png"):
            scan_png(name, data)
        elif name.endswith(".mp4"):
            scan_mp4(name, path, data)
        elif name not in VENDOR_SHA256:
            scan_text(name, data)
    if check_git:
        check_candidates(root, approved)
    return sorted(approved)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", required=True,
                        help="audit files Git would include from this folder")
    parser.parse_args()
    try:
        print(f"Public release audit passed: {len(audit(ROOT))} approved files")
    except (AuditError, OSError, subprocess.CalledProcessError) as exc:
        print(f"Public release audit failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
