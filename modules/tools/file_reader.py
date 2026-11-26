"""Safe, context-bounded file inspection tools confined to the case output directory."""

from __future__ import annotations

from pathlib import Path

from modules.llm_ui import confirm_action

MAX_LINES_CAP = 200
DEFAULT_MAX_LINES = 80
MAX_RETURN_CHARS = 12_000
MAX_SINGLE_LINE_CHARS = 500
MAX_LIST_ENTRIES = 100
LARGE_CSV_WARN_BYTES = 256 * 1024  # 256 KB


def _format_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{size} B"
        size /= 1024
    return f"{size:.1f} GB"


def _resolve_confined(base_output: Path, rel_path: str) -> tuple[Path | None, str | None]:
    base_resolved = base_output.resolve()
    raw = (rel_path or ".").strip()
    candidate = Path(raw)
    if candidate.is_absolute():
        target = candidate.resolve()
    else:
        target = (base_resolved / candidate).resolve()

    if not target.is_relative_to(base_resolved):
        return None, "Access denied: path escapes the case directory."
    return target, None


def _is_binary_file(path: Path) -> bool:
    try:
        sample = path.open("rb").read(4096)
    except OSError:
        return True
    if not sample:
        return False
    if b"\x00" in sample:
        return True
    try:
        sample.decode("utf-8")
        return False
    except UnicodeDecodeError:
        non_text = sum(1 for b in sample if b < 9 or (13 < b < 32))
        return (non_text / len(sample)) > 0.2


READ_FILE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": (
            "Request analyst approval to safely read a bounded slice of a text file inside the case directory. "
            "Large files are strictly paginated and capped to protect the context window. "
            "For large CSVs already mounted in DuckDB, prefer SQL queries instead of read_file."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path from the case directory (as shown in the directory tree).",
                },
                "reason": {
                    "type": "string",
                    "description": "Why you need to read this file.",
                },
                "start_line": {
                    "type": "integer",
                    "description": "1-based starting line number (default: 1).",
                    "default": 1,
                },
                "max_lines": {
                    "type": "integer",
                    "description": f"Maximum lines to return (default: {DEFAULT_MAX_LINES}, hard cap: {MAX_LINES_CAP}).",
                    "default": DEFAULT_MAX_LINES,
                },
                "search": {
                    "type": "string",
                    "description": "Optional case-insensitive substring filter; only matching lines are returned.",
                },
            },
            "required": ["path", "reason"],
        },
    },
}


LIST_DIR_SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_directory",
        "description": (
            "List files and subdirectories inside a specific folder of the case directory "
            "(useful when the initial directory tree was truncated)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative directory path inside the case folder (default: '.').",
                    "default": ".",
                }
            },
        },
    },
}


def run_read_file(args: dict, ctx: dict) -> dict:
    base_output = Path(ctx["base_output"])
    rel_path = args.get("path", "")
    reason = args.get("reason", "Inspect file contents")
    start_line = max(1, int(args.get("start_line") or 1))
    max_lines = max(1, min(MAX_LINES_CAP, int(args.get("max_lines") or DEFAULT_MAX_LINES)))
    search = (args.get("search") or "").strip()

    target, err = _resolve_confined(base_output, rel_path)
    if err:
        return {"error": err}
    if not target.exists():
        return {"error": f"File not found: {rel_path}"}
    if not target.is_file():
        return {"error": f"Path is not a file: {rel_path}"}

    size_bytes = target.stat().st_size
    if _is_binary_file(target):
        return {
            "error": (
                f"'{rel_path}' appears to be a binary file ({_format_size(size_bytes)}). "
                "read_file only supports text files."
            )
        }

    display_rel = target.relative_to(base_output.resolve()).as_posix()
    details = [
        f"File: {display_rel} ({_format_size(size_bytes)})",
        f"Slice: lines {start_line}..{start_line + max_lines - 1} (cap: {MAX_RETURN_CHARS:,} chars)",
        f"Reason: {reason}",
    ]
    if search:
        details.insert(2, f"Filter: contains {search!r} (case-insensitive)")

    if not confirm_action("Proposed File Read", details):
        return {"status": "declined", "message": "Analyst declined file read request."}

    collected: list[str] = []
    char_count = 0
    truncated_by_budget = False
    has_more = False
    last_line_seen = start_line - 1
    needle = search.lower()

    try:
        with target.open("r", encoding="utf-8", errors="replace") as fh:
            for line_no, raw_line in enumerate(fh, start=1):
                if line_no < start_line:
                    continue
                if needle and needle not in raw_line.lower():
                    continue

                if len(collected) >= max_lines:
                    has_more = True
                    break

                line = raw_line.rstrip("\r\n")
                if len(line) > MAX_SINGLE_LINE_CHARS:
                    line = line[: MAX_SINGLE_LINE_CHARS - 1] + "… [line truncated]"

                formatted = f"{line_no}: {line}"
                if char_count + len(formatted) + 1 > MAX_RETURN_CHARS:
                    truncated_by_budget = True
                    has_more = True
                    break

                collected.append(formatted)
                char_count += len(formatted) + 1
                last_line_seen = line_no
    except OSError as e:
        return {"error": f"Failed to read file: {e}"}

    result = {
        "path": display_rel,
        "size": _format_size(size_bytes),
        "start_line": start_line,
        "last_line_returned": last_line_seen if collected else None,
        "lines_returned": len(collected),
        "truncated_by_char_budget": truncated_by_budget,
        "has_more": has_more,
        "content": "\n".join(collected),
    }
    if has_more and collected:
        result["next_start_line"] = last_line_seen + 1
    if target.suffix.lower() == ".csv" and size_bytes > LARGE_CSV_WARN_BYTES:
        result["note"] = "Large CSV file — prefer DuckDB SQL if this artifact is mounted as a view."
    return result


def run_list_directory(args: dict, ctx: dict) -> dict:
    base_output = Path(ctx["base_output"])
    rel_path = args.get("path", ".")
    target, err = _resolve_confined(base_output, rel_path)
    if err:
        return {"error": err}
    if not target.exists() or not target.is_dir():
        return {"error": f"Directory not found: {rel_path}"}

    entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    items = []
    for entry in entries[:MAX_LIST_ENTRIES]:
        if entry.is_dir():
            items.append({"name": entry.name + "/", "type": "dir"})
        else:
            try:
                sz = _format_size(entry.stat().st_size)
            except OSError:
                sz = "?"
            items.append({"name": entry.name, "type": "file", "size": sz})

    return {
        "path": target.relative_to(base_output.resolve()).as_posix(),
        "total_entries": len(entries),
        "truncated": len(entries) > MAX_LIST_ENTRIES,
        "entries": items,
    }