"""Safe, context-bounded file inspection tools (text & hex dump) confined to case directories."""

from __future__ import annotations

from pathlib import Path

from modules.common import format_bytes, safe_int
from modules.llm_ui import confirm_action, declined_payload

MAX_LINES_CAP = 200
DEFAULT_MAX_LINES = 80
MAX_RETURN_CHARS = 12_000
MAX_SINGLE_LINE_CHARS = 500
DEFAULT_HEX_BYTES = 512
MAX_HEX_BYTES_CAP = 2048
MAX_LIST_ENTRIES = 100
LARGE_CSV_WARN_BYTES = 256 * 1024  # 256 KB
VALID_ROOTS = ("auto", "scratch", "output", "source")
SCRATCH_DIRNAME = "_scratch"


def _resolve_confined(root_dir: Path, rel_path: str) -> tuple[Path | None, str | None]:
    """Resolves a path inside root_dir and blocks directory traversal outside it."""
    base = root_dir.resolve()
    candidate = Path((rel_path or ".").strip())
    target = candidate.resolve() if candidate.is_absolute() else (base / candidate).resolve()
    if not target.is_relative_to(base):
        return None, "Access denied: path escapes the case directory."
    return target, None


def _resolve_target(
    ctx: dict, rel_path: str, root: str | None = None
) -> tuple[Path | None, str | None, Path | None, str | None, str | None]:
    """Resolves a target path under 'scratch', 'output', 'source', or 'auto'."""
    req = (root or "auto").strip().lower()
    if req not in VALID_ROOTS:
        return None, None, None, None, f"Invalid root '{root}'. Use 'scratch', 'output', 'source', or 'auto'."

    base_out = Path(ctx["base_output"]).resolve()
    main_dir = Path(ctx["main_directory"]).resolve() if ctx.get("main_directory") else None

    if req == "scratch":
        scratch = base_out / SCRATCH_DIRNAME
        target, err = _resolve_confined(scratch, rel_path)
        return target, "scratch", scratch, None, err

    if req == "output":
        target, err = _resolve_confined(base_out, rel_path)
        return target, "output", base_out, None, err

    if req == "source":
        if main_dir is None:
            return None, None, None, None, "Source investigation directory is not configured."
        target, err = _resolve_confined(main_dir, rel_path)
        return target, "source", main_dir, None, err

    # 'auto': match absolute path to either root, or probe 'output' before 'source'
    raw = (rel_path or ".").strip()
    if Path(raw).is_absolute():
        resolved = Path(raw).resolve()
        for label, base in (("output", base_out), ("source", main_dir), ("scratch", base_out / SCRATCH_DIRNAME)):
            if base is not None and resolved.is_relative_to(base):
                return resolved, label, base, None, None
        return None, None, None, None, "Access denied: path escapes the case directories."

    out_target, out_err = _resolve_confined(base_out, raw)
    if out_err:
        return None, None, None, None, out_err

    if main_dir is not None:
        src_target, src_err = _resolve_confined(main_dir, raw)
        if src_err:
            return None, None, None, None, src_err

        out_exists = bool(out_target and out_target.exists())
        src_exists = bool(src_target and src_target.exists())
        if out_exists and src_exists and out_target != src_target:
            note = "A matching path also exists in root='source'. Pass root='source' to inspect it."
            return out_target, "output", base_out, note, None
        if not out_exists and src_exists:
            return src_target, "source", main_dir, None, None

    return out_target, "output", base_out, None, None


def _is_binary_file(path: Path) -> bool:
    """Heuristically detects binary files via null bytes, UTF-8 decoding, and control-char ratio."""
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


def _format_hex_dump(data: bytes, base_offset: int) -> str:
    """Formats bytes as canonical 16-byte offset + hex + ASCII rows."""
    lines = []
    for i in range(0, len(data), 16):
        row = data[i : i + 16]
        left = " ".join(f"{b:02x}" for b in row[:8])
        right = " ".join(f"{b:02x}" for b in row[8:])
        ascii_col = "".join(chr(b) if 32 <= b <= 126 else "." for b in row)
        lines.append(f"{base_offset + i:08x}  {left:<23}  {right:<23}  |{ascii_col}|")
    return "\n".join(lines)


def _read_hex(
    target: Path,
    root_name: str,
    display_rel: str,
    root_note: str | None,
    size_bytes: int,
    reason: str,
    offset: int,
    length: int,
) -> dict:
    """Prompts for approval and returns a bounded hex + ASCII slice."""
    span = max(0, min(size_bytes, offset + length) - offset)
    details = [
        f"File: [{root_name}] {display_rel} ({format_bytes(size_bytes)})",
        f"Mode: hex dump (offset {offset} / 0x{offset:x}, {span} bytes)",
        f"Reason: {reason}",
    ]
    decision = confirm_action("Proposed File Hex Dump", details)
    if not decision:
        return declined_payload("Analyst declined file hex dump request.", decision.feedback)

    try:
        with target.open("rb") as fh:
            fh.seek(offset)
            chunk = fh.read(length)
    except OSError as e:
        return {"error": f"Failed to read file: {e}"}

    next_offset = offset + len(chunk)
    has_more = next_offset < size_bytes
    result: dict = {
        "root": root_name,
        "path": display_rel,
        "mode": "hex",
        "size": format_bytes(size_bytes),
        "size_bytes": size_bytes,
        "offset": offset,
        "bytes_returned": len(chunk),
        "has_more": has_more,
        "content": _format_hex_dump(chunk, offset),
    }
    if has_more:
        result["next_offset"] = next_offset
    if root_note:
        result["note"] = root_note
    return result


def _read_file(args: dict, ctx: dict) -> dict:
    rel_path = str(args.get("path") or "")
    reason = (args.get("reason") or "Inspect file contents").strip()
    raw_hex = args.get("hex")
    hex_mode = raw_hex if isinstance(raw_hex, bool) else str(raw_hex or "").strip().lower() in ("true", "1", "yes")

    target, root_name, root_dir, root_note, err = _resolve_target(ctx, rel_path, args.get("root"))
    if err or target is None or root_dir is None or root_name is None:
        return {"error": err or "Could not resolve path."}
    if not target.exists():
        return {"error": f"File not found: {rel_path}"}
    if not target.is_file():
        return {"error": f"Path is not a file: {rel_path}"}

    size_bytes = target.stat().st_size
    display_rel = target.relative_to(root_dir).as_posix()

    if hex_mode:
        offset = max(0, safe_int(args.get("offset"), 0))
        length = max(1, min(MAX_HEX_BYTES_CAP, safe_int(args.get("length"), DEFAULT_HEX_BYTES)))
        return _read_hex(target, root_name, display_rel, root_note, size_bytes, reason, offset, length)

    if _is_binary_file(target):
        return {
            "error": (
                f"'{rel_path}' appears to be a binary file ({format_bytes(size_bytes)}). "
                "Re-call read_file with hex=True (and optional offset/length) to inspect a hex dump."
            )
        }

    start_line = max(1, safe_int(args.get("start_line"), 1))
    max_lines = max(1, min(MAX_LINES_CAP, safe_int(args.get("max_lines"), DEFAULT_MAX_LINES)))
    search = str(args.get("search") or "").strip()

    details = [
        f"File: [{root_name}] {display_rel} ({format_bytes(size_bytes)})",
        f"Slice: lines {start_line}..{start_line + max_lines - 1} (cap: {MAX_RETURN_CHARS:,} chars)",
        f"Reason: {reason}",
    ]
    if search:
        details.insert(2, f"Filter: contains {search!r} (case-insensitive)")

    decision = confirm_action("Proposed File Read", details)
    if not decision:
        return declined_payload("Analyst declined file read request.", decision.feedback)

    collected: list[str] = []
    char_count = 0
    truncated_by_budget = False
    has_more = False
    last_line_seen = start_line - 1
    needle = search.lower()

    try:
        with target.open("r", encoding="utf-8", errors="replace") as fh:
            for line_no, raw_line in enumerate(fh, start=1):
                if line_no < start_line or (needle and needle not in raw_line.lower()):
                    continue
                if len(collected) >= max_lines:
                    has_more = True
                    break

                line = raw_line.rstrip("\r\n")
                if len(line) > MAX_SINGLE_LINE_CHARS:
                    line = line[: MAX_SINGLE_LINE_CHARS - 1] + "â€¦ [line truncated]"

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
        "mode": "text",
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

    csv_note = (
        "Large CSV file â€” prefer DuckDB SQL if this artifact is mounted as a view."
        if target.suffix.lower() == ".csv" and size_bytes > LARGE_CSV_WARN_BYTES
        else None
    )
    if notes := [n for n in (root_note, csv_note) if n]:
        result["note"] = " ".join(notes)
    return result


def _list_directory(args: dict, ctx: dict) -> dict:
    rel_path = str(args.get("path") or ".")
    target, root_name, root_dir, root_note, err = _resolve_target(ctx, rel_path, args.get("root"))
    if err or target is None or root_dir is None or root_name is None:
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
                    "Request analyst approval to safely read a bounded slice of a file inside either the "
                    "source investigation directory (root='source') or the parsed output directory (root='output'). "
                    "Reads text lines by default, or dumps a canonical hex+ASCII view when hex=True (for binary files, "
                    "magic headers, or raw artifacts). For large CSVs already mounted in DuckDB, prefer SQL queries."
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
                            "enum": ["auto", "scratch", "output", "source"],
                            "description": (
                                "Which directory tree to read from: 'source' for the selected investigation directory, "
                                "'scratch' for your writable sandbox (_scratch), 'output' for the parsed output "
                                "directory, or 'auto' (default) to check 'output' then 'source'."
                            ),
                            "default": "auto",
                        },
                        "reason": {"type": "string", "description": "Why you need to read this file."},
                        "hex": {
                            "type": "boolean",
                            "description": "Set to true to dump raw bytes as a hex + ASCII view instead of UTF-8 text.",
                            "default": False,
                        },
                        "offset": {
                            "type": "integer",
                            "description": "0-based byte offset for hex=True mode (default: 0).",
                            "default": 0,
                        },
                        "length": {
                            "type": "integer",
                            "description": (
                                f"Number of bytes to dump in hex=True mode "
                                f"(default: {DEFAULT_HEX_BYTES}, hard cap: {MAX_HEX_BYTES_CAP})."
                            ),
                            "default": DEFAULT_HEX_BYTES,
                        },
                        "start_line": {
                            "type": "integer",
                            "description": "1-based starting line number for text mode (default: 1).",
                            "default": 1,
                        },
                        "max_lines": {
                            "type": "integer",
                            "description": (
                                f"Maximum lines to return in text mode "
                                f"(default: {DEFAULT_MAX_LINES}, hard cap: {MAX_LINES_CAP})."
                            ),
                            "default": DEFAULT_MAX_LINES,
                        },
                        "search": {
                            "type": "string",
                            "description": "Optional case-insensitive substring filter for text mode; only matching lines are returned.",
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
                            "enum": ["auto", "scratch", "output", "source"],
                            "description": (
                                "Which directory tree to list: 'source' for the selected investigation directory, "
                                "'scratch' for your writable sandbox (_scratch), 'output' for the parsed output "
                                "directory, or 'auto' (default)."
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
