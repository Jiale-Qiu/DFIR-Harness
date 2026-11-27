"""HITL terminal command execution tool (always requires explicit analyst approval)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from modules.common import safe_int
from modules.llm_ui import confirm_terminal, declined_payload

DEFAULT_TIMEOUT_SEC = 30
MAX_TIMEOUT_SEC = 120
MAX_STDOUT_CHARS = 10_000
MAX_STDERR_CHARS = 4_000


def _cap(raw: str | bytes | None, limit: int) -> tuple[str, bool]:
    """Decodes subprocess output safely and truncates it to fit LLM context limits."""
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else (raw or "")
    if len(text) <= limit:
        return text, False
    return text[:limit] + f"\n... [truncated {len(text) - limit:,} chars]", True


def _resolve_cwd(ctx: dict, root: str) -> tuple[Path | None, str | None]:
    """Resolves the working directory for 'output' or 'source' roots."""
    if root == "output":
        cwd = Path(ctx["base_output"]).resolve()
    elif root == "source":
        raw_main = ctx.get("main_directory")
        if not raw_main:
            return None, "Source investigation directory is not configured."
        cwd = Path(raw_main).resolve()
    else:
        return None, f"Invalid root '{root}'. Use 'output' or 'source'."

    if not cwd.is_dir():
        return None, f"Working directory does not exist: {cwd}"
    return cwd, None


def _run_terminal(args: dict, ctx: dict) -> dict:
    command = str(args.get("command") or "").strip()
    if not command:
        return {"error": "Empty command."}

    reason = str(args.get("reason") or "Run forensic CLI inspection").strip()
    root = str(args.get("root") or "output").strip().lower()
    timeout = max(5, min(MAX_TIMEOUT_SEC, safe_int(args.get("timeout"), DEFAULT_TIMEOUT_SEC)))

    cwd, err = _resolve_cwd(ctx, root)
    if err or cwd is None:
        return {"error": err or "Could not resolve working directory."}

    # Always require explicit analyst approval — intentionally ignores auto_sql or auto-accept flags.
    decision = confirm_terminal(reason=reason, command=command, cwd=str(cwd))
    if not decision:
        return declined_payload("Analyst declined terminal command.", decision.feedback)

    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        out, _ = _cap(exc.stdout, MAX_STDOUT_CHARS)
        err_out, _ = _cap(exc.stderr, MAX_STDERR_CHARS)
        return {"error": f"Command timed out after {timeout}s.", "stdout": out, "stderr": err_out}
    except OSError as exc:
        return {"error": f"Failed to execute command: {exc}"}

    stdout, out_trunc = _cap(proc.stdout, MAX_STDOUT_CHARS)
    stderr, err_trunc = _cap(proc.stderr, MAX_STDERR_CHARS)
    return {
        "exit_code": proc.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "truncated": out_trunc or err_trunc,
    }


REGISTER = [
    {
        "schema": {
            "type": "function",
            "function": {
                "name": "run_terminal",
                "description": (
                    "Propose a local shell command for explicit human approval inside the case directory. "
                    "Use query_duckdb for mounted CSV views and read_file for simple text/hex slices, "
                    "but use run_terminal whenever CLI utilities are better suited—such as hashing files, "
                    "running strings/regex searches across artifacts, inspecting SQLite DBs/archives/PE headers, "
                    "or parsing unmounted evidence. Keep commands read-only and output-bounded."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "The exact shell command to run inside the selected case directory.",
                        },
                        "root": {
                            "type": "string",
                            "enum": ["output", "source"],
                            "description": (
                                "Working directory: 'output' for the parsed output folder (default) "
                                "or 'source' for the raw investigation directory."
                            ),
                            "default": "output",
                        },
                        "reason": {
                            "type": "string",
                            "description": "Short explanation of what forensic question this command answers.",
                        },
                        "timeout": {
                            "type": "integer",
                            "description": f"Timeout in seconds (default: {DEFAULT_TIMEOUT_SEC}, max: {MAX_TIMEOUT_SEC}).",
                            "default": DEFAULT_TIMEOUT_SEC,
                        },
                    },
                    "required": ["command", "reason"],
                },
            },
        },
        "handler": _run_terminal,
    }
]