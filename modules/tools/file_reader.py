"""Safe, context-bounded file inspection tools confined to case directories."""

from __future__ import annotations

from pathlib import Path

from modules.common import format_bytes, safe_int
from modules.llm_ui import confirm_action

MAX_LINES_CAP = 200
DEFAULT_MAX_LINES = 80
MAX_RETURN_CHARS = 12_000
MAX_SINGLE_LINE_CHARS = 500
MAX_LIST_ENTRIES = 100
LARGE_CSV_WARN_BYTES = 256 * 1024  # 256 KB
VALID_ROOTS = ("auto", "output", "source")


def _resolve_confined(root_dir: Path, rel_path: str) -> tuple[Path | None, str | None]:
    base_resolved = root_dir.resolve()
    raw = (rel_path or ".").strip()
    candidate = Path(raw)
    target = candidate.resolve() if candidate.is_absolute() else (base_resolved / candidate).resolve()

    if not target.is_relative_to(base_resolved):
        return None, "Access denied: path escapes the case directory."
    return target, None


def _resolve_target(
    ctx: dict, rel_path: str, root: str | None = None
) -> tuple[Path | None, str | None, Path | None, str | None, str | None]:
    """Resolves a path inside the parsed output ('output') or source investigation ('source') root."""
    requested_root = (root or "auto").strip().lower()
    if requested_root not in VALID_ROOTS:
        return None, None, None, None, f"Invalid root '{root}'. Use 'output', 'source', or 'auto'."

    base_output = Path(ctx["base_output"]).resolve()
    raw_main = ctx.get("main_directory")
    main_dir = Path(raw_main).resolve() if raw_main else None

    if requested_root == "output":
        target, err = _resolve_confined(base_output, rel_path)
        return target, "output", base_output, None, err

    if requested_root == "source":
        if main_dir is None:
            return None, None, None, None, "Source investigation directory is not configured."
        target, err = _resolve_confined(main_dir, rel_path)
        return target, "source", main_dir, None, err

    raw = (rel_path or ".").strip()
    candidate = Path(raw)
    if candidate.is_absolute():
        resolved = candidate.resolve()
        if resolved.is_relative_to(base_output):
            return resolved, "output", base_output, None, None
        if main_dir is not None and resolved.is_relative_to(main_dir):
            return resolved, "source", main_dir, None, None
        return None, None, None, None, "Access denied: path escapes the case directories."

    out_target, out_err = _resolve_confined(base_output, raw)
    if out_err:
        return None, None, None, None, out_err

    if main_dir is not None:
        src_target, src_err = _resolve_confined(main_dir, raw)
        if src_err:
            return None, None, None, None, src_err

        out_exists = out_target is not None and out_target.exists()
        src_exists = src_target is not None and src_target.exists()
        if out_exists and src_exists and out_target != src_target:
            note = (
                "A matching path also exists in root='source'. "
                "Pass root='source' to inspect the source investigation directory."
            )
            return out_target, "output", base_output, note, None
        if not out_exists and src_exists:
            return src_target, "source", main_dir, None, None

    return out_target, "output", base_output, None, None


def _is_binary_file(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            sample = fh.read(4096)
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


def _read_file(args: dict, ctx: dict) -> dict:
    rel_path = str(args.get("path") or "")
    root = args.get("root")
    reason = (args.get("reason") or "Inspect file contents").strip()
    start_line = max(1, safe_int(args.get("start_line"), 1))
    max_lines = max(1, min(MAX_LINES_CAP, safe_int(args.get("max_lines"), DEFAULT_MAX_LINES)))
    search = str(args.get("search") or "").strip()

    target, root_name, root_dir, root_note, err = _resolve_target(ctx, rel_path, root)
    if err or target is None or root_dir is None:
        return {"error": err or "Could not resolve path."}
    if not target.exists():
        return {"error": f"File not found: {rel_path}"}
    if not target.is_file():
        return {"error": f"Path is not a file: {rel_path}"}

    size_bytes = target.stat().st_size
    if _is_binary_file(target):
        return {
            "error": (
                f"'{rel_path}' appears to be a binary file ({format_bytes(size_bytes)}). "
                "read_file only supports text files."
            )
        }

    display_rel = target.relative_to(root_dir).as_posix()
    details = [
        f"File: [{root_name}] {display_rel} ({format_bytes(size_bytes)})",
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

    result: dict = {
        "root": root_name,
        "path": display_rel,
        "size": format_bytes(size_bytes),
        "start_line": start_line,
        "last_line_returned": last_line_seen if collected else None,
        "lines_returned": len(collected),
        "truncated_by_char_budget": truncated_by_budget,
        "has_more": has_more,
        "content": "\n".join(collected),
    }
    if has_more and collected:
        result["next_start_line"] = last_line_seen + 1

    notes = [n for n in (root_note, "Large CSV file — prefer DuckDB SQL if this artifact is mounted as a view." if target.suffix.lower() == ".csv" and size_bytes > LARGE_CSV_WARN_BYTES else None) if n]
    if notes:
        result["note"] = " ".join(notes)
    return result


def _list_directory(args: dict, ctx: dict) -> dict:
    rel_path = str(args.get("path") or ".")
    root = args.get("root")
    target, root_name, root_dir, root_note, err = _resolve_target(ctx, rel_path, root)
    if err or target is None or root_dir is None:
        return {"error": err or "Could not resolve path."}
    if not target.exists() or not target.is_dir():
        return {"error": f"Directory not found: {rel_path}"}

    try:
        entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError as e:
        return {"error": f"Failed to list directory: {e}"}

    items = []
    for entry in entries[:MAX_LIST_ENTRIES]:
        if entry.is_dir():
            items.append({"name": entry.name + "/", "type": "dir"})
        else:
            try:
                sz = format_bytes(entry.stat().st_size)
            except OSError:
                sz = "?"
            items.append({"name": entry.name, "type": "file", "size": sz})

    result: dict = {
        "root": root_name,
        "path": target.relative_to(root_dir).as_posix(),
        "total_entries": len(entries),
        "truncated": len(entries) > MAX_LIST_ENTRIES,
        "entries": items,
    }
    if root_note:
        result["note"] = root_note
    return result


REGISTER = [
    {
        "schema": {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": (
                    "Request analyst approval to safely read a bounded slice of a text file inside either the "
                    "source investigation directory (root='source') or the parsed output directory (root='output'). "
                    "Large files are strictly paginated and capped to protect the context window. "
                    "For large CSVs already mounted in DuckDB, prefer SQL queries instead of read_file."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Relative path from the chosen root directory (as shown in the directory tree).",
                        },
                        "root": {
                            "type": "string",
                            "enum": ["auto", "output", "source"],
                            "description": (
                                "Which directory tree to read from: 'source' for the selected investigation directory, "
                                "'output' for the parsed output directory, or 'auto' (default) to check 'output' then 'source'."
                            ),
                            "default": "auto",
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
        },
        "handler": _read_file,
    },
    {
        "schema": {
            "type": "function",
            "function": {
                "name": "list_directory",
                "description": (
                    "List files and subdirectories inside a specific folder of either the source investigation "
                    "directory (root='source') or the parsed output directory (root='output')."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Relative directory path inside the chosen root folder (default: '.').",
                            "default": ".",
                        },
                        "root": {
                            "type": "string",
                            "enum": ["auto", "output", "source"],
                            "description": (
                                "Which directory tree to list: 'source' for the selected investigation directory, "
                                "'output' for the parsed output directory, or 'auto' (default)."
                            ),
                            "default": "auto",
                        },
                    },
                },
            },
        },
        "handler": _list_directory,
    },
]
