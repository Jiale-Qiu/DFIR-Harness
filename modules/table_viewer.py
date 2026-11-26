"""Lightweight right-pane Rich table viewer for Windows Terminal splits."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

MAX_CELL_CHARS = 160


def pid_is_alive(pid: int) -> bool:
    """Returns True if a process with the given PID is currently running."""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return ctypes.get_last_error() == 5  # ACCESS_DENIED -> process exists
        try:
            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)

    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _truncate(val: object, limit: int = MAX_CELL_CHARS) -> str:
    text = "" if val is None else str(val)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _render_waiting(console: Console) -> None:
    console.clear()
    console.print(
        Panel(
            "Waiting for query results...\n"
            "[dim]Run a query or /show [n] in the left pane.[/dim]",
            title="Query Viewer",
            border_style="dim",
        )
    )


def render_payload(console: Console, data: dict) -> None:
    if data.get("cleared"):
        _render_waiting(console)
        return

    console.clear()

    qid = data.get("id")
    sql = (data.get("sql") or "").strip()
    columns = data.get("columns") or []
    rows = data.get("rows") or []
    truncated = bool(data.get("truncated", False))

    prefix = f"Query #{qid} — " if qid else ""
    title = f"{prefix}{len(rows)}{'+' if truncated else ''} rows"

    if sql:
        short_sql = sql if len(sql) <= 240 else sql[:239] + "…"
        console.print(Panel(short_sql, title=title, border_style="cyan"))

    if not rows:
        console.print("[dim][-] Query returned 0 rows.[/dim]")
        return

    table = Table(
        title=None if sql else title,
        show_header=True,
        header_style="bold cyan",
        expand=True,
    )
    for col in columns:
        table.add_column(str(col), overflow="fold")
    for row in rows:
        table.add_row(*(_truncate(v) for v in row))

    console.print(table)
    if truncated:
        console.print("[yellow][*] Output capped at max row limit.[/yellow]")


def _cleanup_own_pid(pid_path: Path) -> None:
    try:
        if pid_path.exists() and pid_path.read_text(encoding="utf-8").strip() == str(os.getpid()):
            pid_path.unlink(missing_ok=True)
    except OSError:
        pass


def watch(state_path: Path, parent_pid: int | None = None, poll_interval: float = 0.2) -> None:
    pid_path = state_path.with_suffix(".pid")
    try:
        pid_path.write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        pass

    console = Console(highlight=False)
    _render_waiting(console)

    last_mtime: int | None = None
    parent_died = False

    try:
        while True:
            if parent_pid and not pid_is_alive(parent_pid):
                parent_died = True
                break
            if not state_path.exists():
                break

            try:
                mtime = state_path.stat().st_mtime_ns
            except OSError:
                break

            if mtime != last_mtime:
                try:
                    data = json.loads(state_path.read_text(encoding="utf-8"))
                    if data.get("close"):
                        break
                    render_payload(console, data)
                    last_mtime = mtime
                except (json.JSONDecodeError, OSError):
                    pass

            time.sleep(poll_interval)
    except KeyboardInterrupt:
        pass
    finally:
        _cleanup_own_pid(pid_path)
        if parent_died:
            state_path.unlink(missing_ok=True)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("Usage: python table_viewer.py <state_json_path> [parent_pid]")
    target = Path(sys.argv[1])
    parent = int(sys.argv[2]) if len(sys.argv) >= 3 and sys.argv[2].isdigit() else None
    watch(target, parent_pid=parent)