"""HITL terminal command execution tool (always requires explicit analyst approval)."""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

from modules import audit, guard, integrity, toolbox
from modules.common import safe_int
from modules.llm_ui import confirm_terminal, console, declined_payload

DEFAULT_TIMEOUT_SEC = 30
MAX_TIMEOUT_SEC = 120
MAX_STDOUT_CHARS = 10_000
MAX_STDERR_CHARS = 4_000
SCRATCH_DIRNAME = "_scratch"

_PROBE_TOOLS = (
    "pwsh", "powershell", "certutil", "findstr", "where", "tar", "wevtutil", "reg",
    "rg", "7z", "sqlite3", "bstrings", "strings", "strings64", "sigcheck", "sigcheck64",
    "MFTECmd", "EvtxECmd", "PECmd", "RECmd", "LECmd", "JLECmd",
    "AmcacheParser", "AppCompatCacheParser", "WxTCmd", "SBECmd",
)


def _get_windows_ps() -> str | None:
    """Returns the preferred PowerShell executable on Windows (PowerShell 7+ first, then 5.1)."""
    return shutil.which("pwsh") or shutil.which("powershell") if os.name == "nt" else None


def get_terminal_env_summary() -> str:
    """Builds a concise description of the host OS, target shell, and available CLI tools."""
    os_label = f"{platform.system()} {platform.release()} ({platform.machine()})"
    ps_bin = _get_windows_ps()

    if os.name == "nt":
        shell_info = (
            f"PowerShell ({Path(ps_bin).name})"
            if ps_bin
            else f"{Path(os.environ.get('COMSPEC', 'cmd.exe')).name} (PowerShell not found on PATH)"
        )
    else:
        shell_info = "/bin/sh (POSIX sh)"

    env = toolbox.build_env()
    found = [tool for tool in _PROBE_TOOLS if shutil.which(tool, path=env.get("PATH"))]
    found.append(f"python ({Path(sys.executable).name})")
    summary = f"OS: {os_label} | Shell: {shell_info} | Available CLI tools: {', '.join(found)}"

    registered = [d for d in toolbox.load_directories() if os.path.isdir(d)]
    if registered:
        summary += f" | Custom tool dirs on PATH: {', '.join(registered)}"
    return summary


_PS_BIN = _get_windows_ps()
_ENV_SUMMARY = get_terminal_env_summary()

# Put shell syntax rules right on the 'command' parameter so the LLM sees it while generating arguments
_COMMAND_DESC = (
    "Exact PowerShell command to run (e.g., Get-ChildItem, Select-String, Get-Content, Get-FileHash). "
    "Do NOT use bash/Linux commands (ls, grep, cat, find, xxd, 2>/dev/null). "
    "Use relative paths from the chosen root (e.g., .\\C\\Users\\... under root='source'), NEVER victim C:\\ paths."
    if _PS_BIN
    else f"Exact command formatted for {_ENV_SUMMARY.split('|')[1].strip()}. Use relative paths from the chosen root."
)


def _cap(raw: str | bytes | None, limit: int) -> tuple[str, bool]:
    """Decodes subprocess output safely and truncates it to fit LLM context limits."""
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else (raw or "")
    if len(text) <= limit:
        return text, False
    return text[:limit] + f"\n... [truncated {len(text) - limit:,} chars]", True


def scratch_dir(ctx: dict) -> Path:
    """Writable sandbox for extraction and scratch output (never evidence)."""
    return Path(ctx["base_output"]).resolve() / SCRATCH_DIRNAME


def _resolve_cwd(ctx: dict, root: str) -> tuple[Path | None, str | None]:
    """Resolves the working directory for 'scratch', 'output' or 'source' roots."""
    if root == "scratch":
        cwd = scratch_dir(ctx)
        try:
            cwd.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return None, f"Could not create scratch directory: {exc}"
    elif root == "output":
        cwd = Path(ctx["base_output"]).resolve()
    elif root == "source":
        raw_main = ctx.get("main_directory")
        if not raw_main:
            return None, "Source investigation directory is not configured."
        cwd = Path(raw_main).resolve()
    else:
        return None, f"Invalid root '{root}'. Use 'scratch', 'output', or 'source'."

    if not cwd.is_dir():
        return None, f"Working directory does not exist: {cwd}"
    return cwd, None


def _recheck_guard(ctx: dict) -> None:
    """Re-applies the deny ACE if it was removed, alerting the analyst and auditing it."""
    handle = ctx["state"].get("guard_handle")
    raw_main = ctx.get("main_directory")
    if handle is None or not handle.applied or not raw_main:
        return
    source = Path(raw_main)
    if guard.status(source, handle):
        return

    reapplied = guard.apply(source)
    audit.append(ctx, "guard_tampered", tool="run_terminal", path=str(source), reapplied=reapplied.applied)
    console.print(
        "[red][!] Evidence guard was missing and has been re-applied — review the audit log.[/red]\n"
        if reapplied.applied
        else f"[red][!] Evidence guard is missing and could not be re-applied. {guard.restore_command(source)}[/red]\n"
    )


def _check_evidence_changes(ctx: dict) -> list[str]:
    """Fast post-command check; returns changed evidence paths (empty when clean)."""
    raw_main = ctx.get("main_directory")
    baseline = ctx["state"].get("evidence_baseline")
    if not raw_main or baseline is None:
        return []
    source = Path(raw_main).resolve()
    if not source.is_dir():
        return []
    try:
        return integrity.changed_since(source, baseline)
    except OSError:
        return []


def _run_terminal(args: dict, ctx: dict) -> dict:
    command = str(args.get("command") or "").strip()
    if not command:
        return {"error": "Empty command."}

    reason = str(args.get("reason") or "Run forensic CLI inspection").strip()
    root = str(args.get("root") or "scratch").strip().lower()
    timeout = max(5, min(MAX_TIMEOUT_SEC, safe_int(args.get("timeout"), DEFAULT_TIMEOUT_SEC)))

    cwd, err = _resolve_cwd(ctx, root)
    if err or cwd is None:
        return {"error": err or "Could not resolve working directory."}

    audit.append(ctx, "proposed", tool="run_terminal", command=command, cwd=str(cwd), root=root)

    # Always require explicit analyst approval — intentionally ignores auto_sql or auto-accept flags.
    decision = confirm_terminal(reason=reason, command=command, cwd=str(cwd))
    if not decision:
        audit.append(ctx, "declined", tool="run_terminal", command=command, cwd=str(cwd), root=root)
        return declined_payload("Analyst declined terminal command.", decision.feedback)

    audit.append(ctx, "approved", tool="run_terminal", command=command, cwd=str(cwd), root=root)

    if _PS_BIN:
        exec_args: str | list[str] = [_PS_BIN, "-NoProfile", "-NonInteractive", "-Command", command]
        use_shell = False
        shell_name = Path(_PS_BIN).name
    else:
        exec_args = command
        use_shell = True
        shell_name = Path(os.environ.get("COMSPEC", "cmd.exe")).name if os.name == "nt" else "/bin/sh"

    try:
        proc = subprocess.run(
            exec_args,
            shell=use_shell,
            cwd=str(cwd),
            env=toolbox.build_env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        out, _ = _cap(exc.stdout, MAX_STDOUT_CHARS)
        err_out, _ = _cap(exc.stderr, MAX_STDERR_CHARS)
        audit.append(ctx, "timeout", tool="run_terminal", command=command, cwd=str(cwd), timeout=timeout)
        _recheck_guard(ctx)
        return {"error": f"Command timed out after {timeout}s.", "stdout": out, "stderr": err_out}
    except OSError as exc:
        audit.append(ctx, "error", tool="run_terminal", command=command, cwd=str(cwd), error=str(exc))
        return {"error": f"Failed to execute command: {exc}"}

    stdout, out_trunc = _cap(proc.stdout, MAX_STDOUT_CHARS)
    stderr, err_trunc = _cap(proc.stderr, MAX_STDERR_CHARS)

    audit.append(
        ctx,
        "executed",
        tool="run_terminal",
        command=command,
        cwd=str(cwd),
        root=root,
        exit_code=proc.returncode,
        stdout_sha256=hashlib.sha256((proc.stdout or "").encode("utf-8", "replace")).hexdigest(),
    )
    _recheck_guard(ctx)

    result: dict = {
        "shell": shell_name,
        "cwd": str(cwd),
        "exit_code": proc.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "truncated": out_trunc or err_trunc,
    }

    changed = _check_evidence_changes(ctx)
    if changed:
        listed = ", ".join(changed[:5]) + (f" (+{len(changed) - 5} more)" if len(changed) > 5 else "")
        audit.append(ctx, "evidence_modified", tool="run_terminal", command=command, paths=changed[:50])
        console.print(
            f"[red][!] Evidence changed during that command: {listed}[/red]\n"
            "[red]    Review the audit log and re-baseline with /integrity verify.[/red]\n"
        )
        result["evidence_modified"] = changed[:50]
        result["warning"] = (
            "Evidence files under root='source' changed while this command ran. Treat the affected "
            "evidence as potentially contaminated and report it to the analyst."
        )

    return result


REGISTER = [
    {
        "schema": {
            "type": "function",
            "function": {
                "name": "run_terminal",
                "description": (
                    "Propose a local shell command for explicit human approval inside the case directory. "
                    f"[{_ENV_SUMMARY}] "
                    "Use query_duckdb for mounted CSV views and read_file for simple text/hex slices, "
                    "but use run_terminal whenever CLI utilities are better suited—such as hashing files, "
                    "running strings/regex searches across artifacts, inspecting SQLite DBs/archives/PE headers, "
                    "or parsing unmounted evidence. Keep commands read-only and output-bounded. Write or extract "
                    "files only under root='scratch' (default cwd), never inside the evidence tree, and delete "
                    "what you created before finishing."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": _COMMAND_DESC,
                        },
                        "root": {
                            "type": "string",
                            "enum": ["scratch", "output", "source"],
                            "description": (
                                "Working directory: 'scratch' (default) is a writable sandbox for extraction and "
                                "any file you create; 'output' is the parsed CSV folder; 'source' is the raw "
                                "collected evidence, which is OS-enforced read-only."
                            ),
                            "default": "scratch",
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