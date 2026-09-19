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
                                  media_files, scan_mp4, scan_png, scan_text, text_digest)


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
            screenshot = media / "overview.png"
            video = media / "demo.mp4"
            generator = root / "scripts/generate_public_media.py"
            generator.parent.mkdir()
            generator.write_text("def fabricated_graph():\n    return {'title': 'Synthetic'}\n")
            graph_source = root / "src/claude_insight/graph.py"
            graph_source.parent.mkdir(parents=True)
            graph_source.write_text("# synthetic test\n")
            template = graph_source.with_name("graph_view.html")
            template.write_text("<p>synthetic</p>\n")
            screenshot.write_bytes(b"synthetic image for manifest check")
            video.write_bytes(b"synthetic video for manifest check")
            assets = [{"path": f"docs/media/{file.name}", "sha256": hashlib.sha256(file.read_bytes()).hexdigest()}
                      for file in (screenshot, video)]
            inputs = [{"path": file.relative_to(root).as_posix(), "sha256": text_digest(file)}
                      for file in (generator, graph_source, template)]
            graph_json = json.dumps({"title": "Synthetic"}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            inputs.append({"path": "synthetic-graph-json", "sha256": hashlib.sha256(graph_json.encode()).hexdigest()})
            (media / "manifest.json").write_text(json.dumps({"provenance": "synthetic", "hash_algorithm": "sha256",
                                                           "input_canonicalization": "lf", "inputs": inputs, "assets": assets}))
            self.assertEqual(media_files(root), {asset["path"] for asset in assets})
            screenshot.write_bytes(b"changed")
            with self.assertRaisesRegex(AuditError, "hash mismatch"):
                media_files(root)
            screenshot.write_bytes(b"synthetic image for manifest check")
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


if __name__ == "__main__":
    unittest.main()
