"""Unit tests for the evidence integrity manifest and fingerprint guard."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from modules import integrity


class IntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "evidence"
        (self.root / "sub").mkdir(parents=True)
        (self.root / "a.txt").write_text("alpha", encoding="utf-8")
        (self.root / "sub" / "b.txt").write_text("bravo", encoding="utf-8")
        self.ctx = {"base_output": Path(self._tmp.name) / "output"}

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_snapshot_and_clean_diff(self) -> None:
        manifest = integrity.snapshot(self.root)
        self.assertEqual(set(manifest), {"a.txt", "sub/b.txt"})
        self.assertTrue(all(entry["sha256"] for entry in manifest.values()))

        result = integrity.diff(self.root, manifest)
        self.assertEqual(result, {"added": [], "removed": [], "modified": []})
        self.assertEqual(integrity.summarize(result), "No changes.")

    def test_diff_detects_added_removed_modified(self) -> None:
        manifest = integrity.snapshot(self.root)

        (self.root / "sub" / "b.txt").write_text("bravo-changed", encoding="utf-8")
        (self.root / "c.txt").write_text("charlie", encoding="utf-8")
        (self.root / "a.txt").unlink()

        result = integrity.diff(self.root, manifest)
        self.assertEqual(result["added"], ["c.txt"])
        self.assertEqual(result["removed"], ["a.txt"])
        self.assertEqual(result["modified"], ["sub/b.txt"])

    def test_unchanged_content_is_not_flagged_after_touch(self) -> None:
        manifest = integrity.snapshot(self.root)
        target = self.root / "a.txt"
        stat = target.stat()

        # Same size + same content, but a different mtime: the content hash decides.
        os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
        result = integrity.diff(self.root, manifest)
        self.assertEqual(result["modified"], [])

    def test_fingerprint_flags_out_of_band_change(self) -> None:
        baseline = integrity.fingerprint(self.root)
        time.sleep(0.01)
        (self.root / "sub" / "b.txt").write_text("tampered!", encoding="utf-8")

        changed = integrity.changed_since(self.root, baseline)
        self.assertEqual(changed, ["sub/b.txt"])

    def test_missing_baseline_is_empty(self) -> None:
        manifest, source = integrity.load(self.ctx)
        self.assertEqual(manifest, {})
        self.assertIsNone(source)

    def test_save_and_load_round_trip(self) -> None:
        manifest = integrity.snapshot(self.root)
        integrity.save(self.ctx, manifest, self.root)
        loaded, source = integrity.load(self.ctx)
        self.assertEqual(loaded, manifest)
        self.assertEqual(source, self.root)


if __name__ == "__main__":
    unittest.main()
