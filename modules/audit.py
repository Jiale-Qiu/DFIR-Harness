"""Append-only, hash-chained audit log (JSONL) for chain-of-custody."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

AUDIT_FILENAME = "audit.jsonl"
GENESIS_HASH = "0" * 64


def audit_path(ctx: dict) -> Path:
    """Returns the audit log path for the current case."""
    return Path(ctx["base_output"]) / AUDIT_FILENAME


def _canonical(record: dict) -> str:
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def record_hash(record: dict) -> str:
    """Hash of a record without its own `hash` field."""
    payload = {k: v for k, v in record.items() if k != "hash"}
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _records(path: Path) -> list[dict]:
    """Reads all well-formed records; a truncated tail line stops parsing."""
    lines, _truncated = _valid_lines(path)
    return [json.loads(line) for line in lines]


def _valid_lines(path: Path) -> tuple[list[str], bool]:
    """Returns (raw JSON lines that parse, whether a corrupt tail was dropped)."""
    if not path.is_file():
        return [], False
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return [], False

    valid: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            item = json.loads(stripped)
        except json.JSONDecodeError:
            return valid, True
        if not isinstance(item, dict):
            return valid, True
        valid.append(stripped)
    return valid, False


def append(ctx: dict, event: str, **fields: object) -> dict:
    """Appends one hash-chained event, repairing a crash-truncated tail first. Never raises."""
    path = audit_path(ctx)
    try:
        lines, repaired = _valid_lines(path)
        if repaired:
            # Drop the incomplete record so the chain stays verifiable.
            path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

        prev = json.loads(lines[-1]).get("hash", GENESIS_HASH) if lines else GENESIS_HASH
        record = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "event": event,
            "prev": prev,
            **{k: v for k, v in fields.items() if v is not None},
        }
        if repaired:
            record["repaired_truncated_tail"] = True
        record["hash"] = record_hash(record)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(_canonical(record) + "\n")
        return record
    except (OSError, ValueError):
        return {}


def read(ctx: dict, limit: int | None = None) -> list[dict]:
    """Returns audit records, newest last (optionally the last `limit`)."""
    records = _records(audit_path(ctx))
    return records[-limit:] if limit else records


def verify(ctx: dict) -> tuple[bool, str]:
    """Recomputes the hash chain; reports the first broken, modified or truncated record."""
    lines, truncated = _valid_lines(audit_path(ctx))

    prev = GENESIS_HASH
    for index, line in enumerate(lines, start=1):
        record = json.loads(line)
        if record.get("prev") != prev:
            return False, f"Line {index}: broken chain (`prev` does not match the previous record)."
        if record_hash(record) != record.get("hash"):
            return False, f"Line {index}: record content was modified after it was written."
        prev = record.get("hash", prev)

    if truncated:
        return False, f"Line {len(lines) + 1}: truncated or non-JSON record at the end of the log."

    return True, f"Chain intact across {len(lines)} record(s)."
