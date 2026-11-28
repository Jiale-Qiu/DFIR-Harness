"""Unit tests for the kernel evidence guard (Windows, non-elevated only)."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from modules import guard

_WINDOWS = os.name == "nt"


@unittest.skipUnless(_WINDOWS, "evidence guard is Windows-only")
class GuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "evidence"
        (self.root / "sub" / "deep").mkdir(parents=True)
        (self.root / "sub" / "deep" / "hive.dat").write_text("original", encoding="utf-8")
        self.handle = None

    def tearDown(self) -> None:
        guard.restore(self.handle)
        self.handle = None
        self._tmp.cleanup()

    def test_filesystem_detection(self) -> None:
        filesystem = guard.filesystem_of(self.root)
        if filesystem is not None:
            self.assertIn(filesystem.upper(), ("NTFS", "REFS", "FAT", "FAT32", "EXFAT"))

    def test_apply_denies_writes_and_allows_reads(self) -> None:
        self.handle = guard.apply(self.root)
        if not self.handle.applied:
            self.skipTest(f"guard unavailable here: {self.handle.reason}")

        self.assertTrue(guard.status(self.root, self.handle))

        with self.assertRaises(PermissionError):
            (self.root / "new.txt").write_text("x", encoding="utf-8")
        with self.assertRaises(PermissionError):
            (self.root / "sub" / "deep" / "hive.dat").write_text("tampered", encoding="utf-8")

        # Reads still work and content is untouched.
        self.assertEqual((self.root / "sub" / "deep" / "hive.dat").read_text(encoding="utf-8"), "original")

    def test_restore_returns_full_access(self) -> None:
        self.handle = guard.apply(self.root)
        if not self.handle.applied:
            self.skipTest(f"guard unavailable here: {self.handle.reason}")

        self.assertTrue(guard.restore(self.handle))
        self.handle = None

        (self.root / "after.txt").write_text("ok", encoding="utf-8")
        self.assertEqual((self.root / "after.txt").read_text(encoding="utf-8"), "ok")
        self.assertFalse(guard.status(self.root))

    def test_status_detects_removed_ace(self) -> None:
        self.handle = guard.apply(self.root)
        if not self.handle.applied:
            self.skipTest(f"guard unavailable here: {self.handle.reason}")

        subprocess.run(
            ["icacls", str(self.root), "/remove:d", guard.current_user()],
            capture_output=True,
            check=False,
        )
        self.assertFalse(guard.status(self.root, self.handle))

    def test_restore_command_is_actionable(self) -> None:
        command = guard.restore_command(self.root)
        self.assertIn(str(self.root), command)
        self.assertIn("/remove:d", command)


if __name__ == "__main__":
    unittest.main()
