"""Bridge between the main CLI and the right-hand Windows Terminal pane."""

from __future__ import annotations

import atexit
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from modules.table_viewer import pid_is_alive

_LAUNCH_GRACE_SECONDS = 2.0
_launch_times: dict[Path, float] = {}
_registered_cleanup: set[Path] = set()


def get_session_state_path() -> Path:
    return Path(tempfile.gettempdir()) / f"dfir_cli_result_{os.getpid()}.json"


def _pid_path(state_path: Path) -> Path:
    return state_path.with_suffix(".pid")


def is_pane_alive(state_path: Path) -> bool:
    """Returns True if the viewer pane is running (or still starting up)."""
    pid_file = _pid_path(state_path)
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text(encoding="utf-8").strip())
            if pid_is_alive(pid):
                return True
        except (ValueError, OSError):
            pass
        # Stale pid file from a manually closed pane
        pid_file.unlink(missing_ok=True)
        _launch_times.pop(state_path, None)
        return False

    launched_at = _launch_times.get(state_path)
    if launched_at is not None:
        if (time.monotonic() - launched_at) < _LAUNCH_GRACE_SECONDS and state_path.exists():
            return True
        _launch_times.pop(state_path, None)

    return False


def _write_json_atomic(state_path: Path, payload: dict) -> None:
    tmp_path = state_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    for _ in range(4):
        try:
            os.replace(tmp_path, state_path)
            return
        except PermissionError:
            time.sleep(0.03)


def _format_entry(entry: dict) -> dict:
    return {
        "id": entry.get("id"),
        "sql": entry.get("sql", ""),
        "columns": [str(c) for c in entry.get("columns", [])],
        "rows": [["" if v is None else str(v) for v in r] for r in entry.get("rows", [])],
        "truncated": bool(entry.get("truncated", False)),
    }


def open_result_pane(
    state_path: Path,
    initial_entry: dict | None = None,
    width_ratio: float = 0.45,
) -> str:
    """
    Opens a vertical Windows Terminal split running table_viewer.py.
    Returns 'opened', 'already_open', or 'unsupported'.
    """
    if not os.environ.get("WT_SESSION"):
        return "unsupported"

    if is_pane_alive(state_path):
        if initial_entry is not None:
            publish_result(state_path, initial_entry)
        return "already_open"

    payload = _format_entry(initial_entry) if initial_entry is not None else {"cleared": True}
    _write_json_atomic(state_path, payload)

    if state_path not in _registered_cleanup:
        atexit.register(close_result_pane, state_path)
        _registered_cleanup.add(state_path)

    viewer_script = Path(__file__).with_name("table_viewer.py")
    cmd = [
        "wt.exe", "-w", "0",
        "split-pane", "-V",
        "-s", str(width_ratio),
        "-d", os.getcwd(),
        "--title", "Query Results",
        sys.executable, str(viewer_script), str(state_path), str(os.getpid()),
        ";", "move-focus", "left",
    ]
    _launch_times[state_path] = time.monotonic()
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return "opened"


def publish_result(state_path: Path, entry: dict) -> None:
    """Writes a history entry dict if the right pane is active."""
    if not is_pane_alive(state_path):
        return
    _write_json_atomic(state_path, _format_entry(entry))


def clear_result_pane(state_path: Path) -> None:
    """Resets the right pane back to its waiting state if open."""
    if not is_pane_alive(state_path):
        return
    _write_json_atomic(state_path, {"cleared": True})


def close_result_pane(state_path: Path) -> None:
    """Signals the right pane to exit cleanly and removes temp state files."""
    _launch_times.pop(state_path, None)
    state_path.unlink(missing_ok=True)
    state_path.with_suffix(".tmp").unlink(missing_ok=True)
    _pid_path(state_path).unlink(missing_ok=True)