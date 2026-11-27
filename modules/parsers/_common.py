"""Shared subprocess runner for Eric Zimmerman CLI parsers."""

from __future__ import annotations

import subprocess
from contextlib import nullcontext

from rich.console import Console

console = Console(highlight=False)


def run_ez_command(
    cmd: list[str],
    fail_label: str,
    dry_run: bool = False,
    status_msg: str | None = None,
) -> bool:
    """Runs an EZ-Tools command (or logs it in dry-run mode). Returns True on success."""
    if dry_run:
        console.print(f"    [DRY-RUN] {' '.join(cmd)}")
        return True

    ctx = console.status(status_msg, spinner="aesthetic") if status_msg else nullcontext()
    try:
        with ctx:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
    except OSError as exc:
        console.print(f"[red][!] Failed to run {cmd[0]} on {fail_label}: {exc}[/red]")
        return False

    if result.returncode == 0:
        return True

    console.print(f"[red][!] Failed to parse {fail_label}[/red]")
    error_msg = (result.stderr or result.stdout or "").strip()
    if error_msg:
        console.print(f"    [red]{error_msg}[/red]")
    return False
