"""Release gate rejection cases; all sensitive looking strings are invented."""

import hashlib
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from public_release_audit import (AuditError, candidate_files, check_candidates, ignored, ignore_rules,
                                  media_files, scan_gif, scan_mp4, scan_png, scan_text, text_digest)


SCRATCH_ROOT = Path(__file__).resolve().parents[1] / "artifacts" / "private-audit-tests"


class PublicReleaseAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)

    def test_rejects_home_path_and_session_id(self):
        path = "C:" + "\\Users\\invented-person\\.claude\\projects\\example"
        with self.assertRaisesRegex(AuditError, "user home path"):
            scan_text("test.txt", path.encode())
        identifier = "-".join(("1" * 8, "2" * 4, "3" * 4, "4" * 4, "5" * 12))
        with self.assertRaisesRegex(AuditError, "session UUID"):
            scan_text("test.txt", identifier.encode())

    def test_rejects_email_and_secret(self):
        with self.assertRaisesRegex(AuditError, "email address"):
            scan_text("test.txt", ("person" + "@" + "private.example").encode())
        with self.assertRaisesRegex(AuditError, "private key"):
            scan_text("test.txt", ("-----BEGIN " + "PRIVATE KEY-----").encode())

    def test_rejects_unlabeled_tokens(self):
        samples = (("GitHub token", "ghp_" + "A" * 36),
                   ("API token", "sk-" + "B" * 40),
                   ("AWS access key", "AKIA" + "C" * 16),
                   ("Bearer token", "Bearer " + "D" * 32))
        for label, value in samples:
            with self.subTest(label=label), self.assertRaisesRegex(AuditError, label):
                scan_text("test.txt", value.encode())

    def test_media_manifest_rejects_changed_and_extra_files(self):
        with TemporaryDirectory(dir=SCRATCH_ROOT) as scratch:
            root = Path(scratch)
            media = root / "docs" / "media"
            media.mkdir(parents=True)
            filenames = ("overview.png", "session-map.png", "event-inspector.png", "demo.mp4", "demo.gif")
            files = [media / name for name in filenames]
            screenshot, video = files[0], files[3]
            generator = root / "scripts/generate_public_media.py"
            generator.parent.mkdir()
            generator.write_text("import json\ndef fabricated_graph():\n    return {'title': 'Synthetic'}\n"
                                 "def canonical_graph_bytes(graph):\n    return json.dumps(graph, sort_keys=True, "
                                 "separators=(',', ':')).encode()\n")
            preview_generator = root / "scripts/generate_demo_preview.py"
            preview_generator.write_text("# synthetic preview test\n")
            graph_source = root / "src/claude_insight/graph.py"
            graph_source.parent.mkdir(parents=True)
            graph_source.write_text("# synthetic test\n")
            template = graph_source.with_name("graph_view.html")
            template.write_text("<p>synthetic</p>\n")
            for file in files:
                file.write_bytes(("synthetic " + file.name).encode())
            assets = [{"path": f"docs/media/{file.name}", "sha256": hashlib.sha256(file.read_bytes()).hexdigest()}
                      for file in files]
            inputs = [{"path": file.relative_to(root).as_posix(), "sha256": text_digest(file)}
                      for file in (generator, preview_generator, graph_source, template)]
            graph_json = json.dumps({"title": "Synthetic"}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            inputs.append({"path": "synthetic-graph-json", "sha256": hashlib.sha256(graph_json.encode()).hexdigest()})
            (media / "manifest.json").write_text(json.dumps({
                "provenance": "synthetic", "hash_algorithm": "sha256", "input_canonicalization": "lf",
                "fixture_float_decimals": 9, "inputs": inputs, "assets": assets,
                "preview": {"source": "docs/media/demo.mp4", "source_sha256": assets[3]["sha256"],
                            "output": "docs/media/demo.gif", "generator": "scripts/generate_demo_preview.py"},
            }))
            self.assertEqual(media_files(root), {asset["path"] for asset in assets})
            screenshot.write_bytes(b"changed")
            with self.assertRaisesRegex(AuditError, "hash mismatch"):
                media_files(root)
            screenshot.write_bytes(b"synthetic overview.png")
            (media / "extra.txt").write_text("not reviewed")
            with self.assertRaisesRegex(AuditError, "unmanifested"):
                media_files(root)

    def test_input_hash_is_stable_across_line_endings(self):
        with TemporaryDirectory(dir=SCRATCH_ROOT) as scratch:
            source = Path(scratch) / "example.py"
            source.write_bytes(b"first\nsecond\n")
            expected = text_digest(source)
            source.write_bytes(b"first\r\nsecond\r\n")
            self.assertEqual(text_digest(source), expected)

    def test_ignored_private_files_are_not_git_candidates_without_repo(self):
        with TemporaryDirectory(dir=SCRATCH_ROOT) as scratch:
            root = Path(scratch)
            (root / ".gitignore").write_text("artifacts/\n*.html\n*.jsonl\n!demo.jsonl\n")
            (root / "artifacts").mkdir()
            (root / "artifacts" / "private.txt").write_text("private")
            (root / "session.html").write_text("private")
            (root / "session.jsonl").write_text("private")
            (root / "demo.jsonl").write_text("synthetic")
            self.assertEqual(candidate_files(root), {".gitignore", "demo.jsonl"})

    def test_slash_ignore_pattern_does_not_cross_directory(self):
        with TemporaryDirectory(dir=SCRATCH_ROOT) as scratch:
            root = Path(scratch)
            (root / ".gitignore").write_text("docs/media/*.tmp\n")
            rules = ignore_rules(root)
            self.assertTrue(ignored("docs/media/frame.tmp", False, rules))
            self.assertFalse(ignored("docs/media/nested/frame.tmp", False, rules))

    def test_force_added_private_file_is_rejected(self):
        with TemporaryDirectory(dir=SCRATCH_ROOT) as scratch:
            root = Path(scratch)
            (root / ".git").write_text("simulated Git index marker")
            class Result:
                stdout = b"README.md\0artifacts/private.html\0"
            with patch("public_release_audit.subprocess.run", return_value=Result()):
                with self.assertRaisesRegex(AuditError, "artifacts/private.html"):
                    check_candidates(root, {"README.md"})

    def test_staged_copy_cannot_differ_from_scanned_worktree(self):
        with TemporaryDirectory(dir=SCRATCH_ROOT) as scratch:
            root = Path(scratch)
            (root / ".git").write_text("simulated Git index marker")
            class Result:
                def __init__(self, stdout):
                    self.stdout = stdout
            def fake_run(args, **kwargs):
                return Result(b"README.md\0" if "ls-files" in args else b"README.md\0")
            with patch("public_release_audit.subprocess.run", side_effect=fake_run):
                with self.assertRaisesRegex(AuditError, "index differs"):
                    check_candidates(root, {"README.md"})

    def test_png_metadata_is_rejected(self):
        header = b"\x89PNG\r\n\x1a\n"
        metadata = (4).to_bytes(4, "big") + b"tEXt" + b"name" + b"\0\0\0\0"
        with self.assertRaisesRegex(AuditError, "metadata"):
            scan_png("screenshot.png", header + metadata)

    def test_mp4_rejects_audio_stream(self):
        class Result:
            stdout = json.dumps({"format": {"duration": "1.0"}, "streams": [
                {"codec_type": "video", "codec_name": "h264", "pix_fmt": "yuv420p"},
                {"codec_type": "audio", "codec_name": "aac"}]})
        with patch("public_release_audit.subprocess.run", return_value=Result()):
            with self.assertRaisesRegex(AuditError, "no audio"):
                scan_mp4("demo.mp4", Path("unused"), b"ftypmoov")

    def test_gif_rejects_comment_extension(self):
        source = Path(__file__).resolve().parents[1] / "docs/media/demo.gif"
        data = source.read_bytes()
        scan_gif("demo.gif", data)
        with self.assertRaisesRegex(AuditError, "comments or metadata"):
            scan_gif("demo.gif", data[:-1] + b"\x21\xfe\x03bad\x00" + data[-1:])

    def test_canonical_fixture_hash_ignores_tiny_float_drift(self):
        import math
        import generate_public_media
        encode = generate_public_media.canonical_graph_bytes
        self.assertEqual(encode({"cost": sum([0.1] * 10)}),
                         encode({"cost": math.fsum([0.1] * 10)}))
        self.assertNotEqual(encode({"cost": 1.0}), encode({"cost": 1.000001}))
        with self.assertRaisesRegex(ValueError, "nonfinite"):
            encode({"cost": float("nan")})


if __name__ == "__main__":
    unittest.main()
