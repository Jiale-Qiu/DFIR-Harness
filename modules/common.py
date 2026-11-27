"""Shared constants and small utilities used across DFIR-Harness modules."""

from __future__ import annotations

ARTIFACT_SOURCES: dict[str, tuple[str, str]] = {
    "evtx": ("evtx", "*.csv"),
    "prefetch": ("prefetch", "*_PECmd_Output.csv"),
    "prefetch_timeline": ("prefetch", "*_PECmd_Output_Timeline.csv"),
    "registry": ("registry", "*.csv"),
    "mft": ("mft", "*.csv"),
    "usnjrnl": ("usnjrnl", "*.csv"),
}


def format_bytes(size: int | float) -> str:
    """Formats a byte count into a human-readable string."""
    size = max(0.0, float(size))
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def safe_int(value: object, default: int) -> int:
    """Parses an integer from tool arguments, falling back to default on invalid input."""
    if value is None or isinstance(value, bool):
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default
