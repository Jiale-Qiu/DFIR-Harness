"""Lightweight right-pane Rich table viewer for Windows Terminal splits."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

MAX_CELL_CHARS = 160


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


def watch(state_path: Path, poll_interval: float = 0.2) -> None:
    console = Console(highlight=False)
    _render_waiting(console)

    last_mtime: int | None = None
    try:
        while True:
            if state_path.exists():
                mtime = state_path.stat().st_mtime_ns
                if mtime != last_mtime:
                    try:
                        data = json.loads(state_path.read_text(encoding="utf-8"))
                        render_payload(console, data)
                        last_mtime = mtime
                    except (json.JSONDecodeError, OSError):
                        pass
            time.sleep(poll_interval)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("Usage: python table_viewer.py <state_json_path>")
    watch(Path(sys.argv[1]))