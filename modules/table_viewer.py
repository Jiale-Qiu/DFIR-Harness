"""Lightweight right-pane Rich table viewer for Windows Terminal splits."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

MAX_TABLE_CELL_CHARS = 160
MAX_CARD_CELL_CHARS = 500
MIN_TABLE_COL_WIDTH = 14


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


def _truncate(val: object, limit: int) -> str:
    text = "" if val is None else str(val).replace("\r\n", " ").replace("\n", " ")
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


def _filter_empty_columns(columns: list[str], rows: list[list]) -> tuple[list[str], list[list], list[str]]:
    """Drops columns that are empty across every row so wide EVTX queries stay compact."""
    keep_idx = []
    hidden = []
    for i, col in enumerate(columns):
        has_val = any(i < len(r) and r[i] is not None and str(r[i]).strip() != "" for r in rows)
        if has_val:
            keep_idx.append(i)
        else:
            hidden.append(str(col))

    if not keep_idx:
        return [str(c) for c in columns], rows, []

    vis_cols = [str(columns[i]) for i in keep_idx]
    vis_rows = [[r[i] if i < len(r) else "" for i in keep_idx] for r in rows]
    return vis_cols, vis_rows, hidden


def _render_card_view(console: Console, columns: list[str], rows: list[list]) -> None:
    """Renders wide rows as full-width Key: Value cards so borders never wrap."""
    key_width = min(max((len(c) for c in columns), default=10), max(12, console.size.width // 3))

    for idx, row in enumerate(rows, start=1):
        console.print(Rule(f"[bold cyan]Row {idx}[/bold cyan]", align="left", style="dim"))
        grid = Table.grid(padding=(0, 1), expand=True)
        grid.add_column(style="bold cyan", width=key_width, overflow="fold")
        grid.add_column(overflow="fold", ratio=1)

        shown_any = False
        for col, val in zip(columns, row):
            text = _truncate(val, MAX_CARD_CELL_CHARS).strip()
            if text:
                grid.add_row(col, text)
                shown_any = True
        if not shown_any:
            grid.add_row("(empty)", "[dim]all values NULL/blank[/dim]")
        console.print(grid)


def _render_table_view(console: Console, columns: list[str], rows: list[list]) -> None:
    """Renders a clean, borderless horizontal-header table when columns fit the pane."""
    table = Table(
        box=box.SIMPLE_HEAD,
        show_header=True,
        header_style="bold cyan",
        pad_edge=False,
        padding=(0, 1),
        expand=True,
    )
    for col in columns:
        table.add_column(col, overflow="fold")
    for row in rows:
        table.add_row(*(_truncate(v, MAX_TABLE_CELL_CHARS) for v in row))
    console.print(table)


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

    vis_cols, vis_rows, hidden_cols = _filter_empty_columns(columns, rows) if rows else (columns, rows, [])
    pane_width = max(30, console.size.width)
    ncols = max(1, len(vis_cols))
    avg_col_width = (pane_width - 2 * max(0, ncols - 1)) // ncols
    use_cards = ncols > 1 and avg_col_width < MIN_TABLE_COL_WIDTH

    prefix = f"Query #{qid} — " if qid else ""
    mode_tag = " [card view]" if (rows and use_cards) else ""
    title = f"{prefix}{len(rows)}{'+' if truncated else ''} rows{mode_tag}"

    if sql:
        short_sql = sql if len(sql) <= 240 else sql[:239] + "…"
        console.print(
            Panel(
                Text(short_sql, overflow="fold"),
                title=title,
                title_align="left",
                box=box.HORIZONTALS,
                border_style="cyan",
                padding=(0, 1),
            )
        )

    if not rows:
        console.print("[dim][-] Query returned 0 rows.[/dim]")
        return

    if use_cards:
        _render_card_view(console, vis_cols, vis_rows)
    else:
        _render_table_view(console, vis_cols, vis_rows)

    if hidden_cols:
        console.print(f"[dim]Hidden empty columns: {', '.join(hidden_cols)}[/dim]")
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
    last_data: dict | None = None
    last_size = console.size
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
                    last_data = data
                    last_mtime = mtime
                    last_size = console.size
                    render_payload(console, data)
                except (json.JSONDecodeError, OSError):
                    pass
            elif last_data is not None and console.size != last_size:
                last_size = console.size
                render_payload(console, last_data)

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