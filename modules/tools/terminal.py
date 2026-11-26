"""Last-resort HITL terminal command execution tool (never auto-accepted)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from modules.llm_ui import confirm_terminal

DEFAULT_TIMEOUT_SEC = 30
MAX_TIMEOUT_SEC = 120
MAX_STDOUT_CHARS = 10_000
MAX_STDERR_CHARS = 4_000


def _cap(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit] + f"\n... [truncated {len(text) - limit:,} chars]", True


def _run_terminal(args: dict, ctx: dict) -> dict:
    command = (args.get("command") or "").strip()
    reason = (args.get("reason") or "").strip()
    root = (args.get("root") or "output").strip().lower()
    if not command:
        return {"error": "Empty command."}

    timeout = max(5, min(MAX_TIMEOUT_SEC, int(args.get("timeout") or DEFAULT_TIMEOUT_SEC)))
    if root == "source":
        raw_main = ctx.get("main_directory")
        if not raw_main:
            return {"error": "Source investigation directory is not configured."}
        cwd = Path(raw_main).resolve()
    elif root == "output":
        cwd = Path(ctx["base_output"]).resolve()
    else:
        return {"error": f"Invalid root '{root}'. Use 'output' or 'source'."}

    # Always require explicit analyst approval — intentionally ignores auto_sql or any auto-accept flags.
    if not confirm_terminal(reason=reason, command=command, cwd=str(cwd)):
        return {"status": "declined", "message": "Analyst declined terminal command."}

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
        out, _ = _cap(exc.stdout or "", MAX_STDOUT_CHARS)
        err, _ = _cap(exc.stderr or "", MAX_STDERR_CHARS)
        return {
            "error": f"Command timed out after {timeout}s.",
            "stdout": out,
            "stderr": err,
        }
    except OSError as exc:
        return {"error": f"Failed to execute command: {exc}"}

    stdout, out_trunc = _cap(proc.stdout or "", MAX_STDOUT_CHARS)
    stderr, err_trunc = _cap(proc.stderr or "", MAX_STDERR_CHARS)
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
                    "STRICT LAST RESORT ONLY. Proposes a local shell command for explicit human approval. "
                    "Do NOT call this if DuckDB SQL, read_file, list_directory, or any other built-in tool "
                    "can accomplish the task. You must explain in 'reason' why no other tool can do it. "
                    "Prefer read-only, targeted commands with bounded output."
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
                                "Which directory to use as the working directory: 'output' for the parsed output "
                                "folder (default) or 'source' for the selected investigation directory."
                            ),
                            "default": "output",
                        },
                        "reason": {
                            "type": "string",
                            "description": "Why this command is necessary and why no other built-in tool suffices.",
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