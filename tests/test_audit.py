"""Unit tests for the hash-chained audit log (stdlib unittest only)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from modules import audit


def make_ctx(root: Path) -> dict:
    return {"base_output": root}


class AuditLogTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.ctx = make_ctx(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_append_chains_records(self) -> None:
        first = audit.append(self.ctx, "proposed", command="Get-ChildItem")
        second = audit.append(self.ctx, "executed", command="Get-ChildItem", exit_code=0)

        self.assertEqual(first["prev"], audit.GENESIS_HASH)
        self.assertEqual(second["prev"], first["hash"])
        self.assertEqual(len(audit.read(self.ctx)), 2)

    def test_verify_passes_for_clean_log(self) -> None:
        for event in ("proposed", "approved", "executed"):
            audit.append(self.ctx, event, command="whoami")
        ok, message = audit.verify(self.ctx)
        self.assertTrue(ok, message)

    def test_verify_detects_edited_record(self) -> None:
        audit.append(self.ctx, "proposed", command="cmd-a")
        audit.append(self.ctx, "executed", command="cmd-a", exit_code=0)
        audit.append(self.ctx, "proposed", command="cmd-b")

        path = audit.audit_path(self.ctx)
        lines = path.read_text(encoding="utf-8").splitlines()
        tampered = json.loads(lines[1])
        tampered["exit_code"] = 1
        lines[1] = json.dumps(tampered, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        ok, message = audit.verify(self.ctx)
        self.assertFalse(ok)
        self.assertIn("Line 2", message)

    def test_verify_detects_deleted_record(self) -> None:
        audit.append(self.ctx, "proposed", command="cmd-a")
        audit.append(self.ctx, "executed", command="cmd-a", exit_code=0)
        audit.append(self.ctx, "proposed", command="cmd-b")

        path = audit.audit_path(self.ctx)
        lines = path.read_text(encoding="utf-8").splitlines()
        path.write_text("\n".join([lines[0], lines[2]]) + "\n", encoding="utf-8")

        ok, message = audit.verify(self.ctx)
        self.assertFalse(ok)
        self.assertIn("Line 2", message)

    def test_truncated_tail_is_tolerated_and_reported(self) -> None:
        audit.append(self.ctx, "proposed", command="cmd-a")
        path = audit.audit_path(self.ctx)
        with path.open("a", encoding="utf-8") as fh:
            fh.write('{"ts": "2026-01-01T00:00:00Z", "event": "execu')

        self.assertEqual(len(audit.read(self.ctx)), 1)
        audit.append(self.ctx, "executed", command="cmd-b", exit_code=0)
        ok, _ = audit.verify(self.ctx)
        self.assertTrue(ok)

    def test_append_failure_does_not_raise(self) -> None:
        bad = {"base_output": Path("\x00invalid")}
        self.assertEqual(audit.append(bad, "proposed", command="x"), {})


if __name__ == "__main__":
    unittest.main()
