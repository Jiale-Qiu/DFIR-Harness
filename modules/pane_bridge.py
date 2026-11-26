"""Bridge between the main CLI and the right-hand Windows Terminal pane."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def get_session_state_path() -> Path:
    return Path(tempfile.gettempdir()) / f"dfir_cli_result_{os.getpid()}.json"


def open_result_pane(state_path: Path, width_ratio: float = 0.45) -> bool:
    """Opens a vertical Windows Terminal split running table_viewer.py."""
    if not os.environ.get("WT_SESSION"):
        return False

    viewer_script = Path(__file__).with_name("table_viewer.py")
    cmd = [
        "wt.exe", "-w", "0",
        "split-pane", "-V",
        "-s", str(width_ratio),
        "-d", os.getcwd(),
        "--title", "Query Results",
        sys.executable, str(viewer_script), str(state_path),
        ";", "move-focus", "left",
    ]
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return True


def _write_json_atomic(state_path: Path, payload: dict) -> None:
    tmp_path = state_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    for _ in range(4):
        try:
            os.replace(tmp_path, state_path)
            return
        except PermissionError:
            time.sleep(0.03)


def publish_result(state_path: Path, entry: dict) -> None:
    """Writes a history entry dict so the right pane refreshes cleanly."""
    payload = {
        "id": entry.get("id"),
        "sql": entry.get("sql", ""),
        "columns": [str(c) for c in entry.get("columns", [])],
        "rows": [["" if v is None else str(v) for v in r] for r in entry.get("rows", [])],
        "truncated": bool(entry.get("truncated", False)),
    }
    _write_json_atomic(state_path, payload)


def clear_result_pane(state_path: Path) -> None:
    """Resets the right pane back to its waiting state."""
    _write_json_atomic(state_path, {"cleared": True})