import os
import shutil
import subprocess
from pathlib import Path
from modules.llm_ui import confirm_action, console

ARTIFACT_SOURCES = {
    "evtx": ("evtx", "*.csv"),
    "prefetch": ("prefetch", "*_PECmd_Output.csv"),
    "prefetch_timeline": ("prefetch", "*_PECmd_Output_Timeline.csv"),
    "registry": ("registry", "*.csv"),
    "mft": ("mft", "*.csv"),
}


def _find_timeline_explorer() -> Path | None:
    """Finds TimelineExplorer.exe via env var, PATH, or local ./tools directory."""
    env_path = os.environ.get("TIMELINE_EXPLORER_PATH")
    if env_path and Path(env_path).is_file():
        return Path(env_path)

    which_path = shutil.which("TimelineExplorer.exe")
    if which_path:
        return Path(which_path)

    tools_dir = Path("tools")
    if tools_dir.is_dir():
        for match in tools_dir.rglob("TimelineExplorer.exe"):
            return match
    return None


def _resolve_csv_files(base_output: Path, artifacts: list[str], files: list[str]) -> list[Path]:
    """Resolves requested artifact categories and/or specific CSV filenames inside base_output."""
    resolved: list[Path] = []
    seen: set[Path] = set()

    def _add(p: Path):
        p_res = p.resolve()
        if p_res.is_file() and p_res.suffix.lower() == ".csv" and p_res not in seen:
            seen.add(p_res)
            resolved.append(p_res)

    for name in artifacts:
        key = name.strip().lower()
        if key in ARTIFACT_SOURCES:
            sub, pattern = ARTIFACT_SOURCES[key]
            for f in sorted((base_output / sub).glob(pattern)):
                if f.stat().st_size > 0:
                    _add(f)

    for rel in files:
        candidate = Path(rel)
        if not candidate.is_absolute():
            candidate = base_output / rel
        if candidate.is_file():
            _add(candidate)
        else:
            # Fallback: match by basename anywhere under base_output
            for match in sorted(base_output.rglob(Path(rel).name)):
                _add(match)

    return resolved


def _handle_open_gui(args: dict, ctx: dict) -> dict:
    base_output = Path(ctx["base_output"])
    artifacts = args.get("artifacts") or []
    files = args.get("files") or []
    reason = args.get("reason", "Open parsed CSVs for manual analyst review")

    exe = _find_timeline_explorer()
    if not exe:
        msg = (
            "TimelineExplorer.exe not found. Put it in ./tools/, add it to PATH, "
            "or set TIMELINE_EXPLORER_PATH."
        )
        console.print(f"[red][!] {msg}[/red]")
        return {"error": msg}

    targets = _resolve_csv_files(base_output, artifacts, files)
    if not targets:
        return {"error": f"No matching non-empty CSV files found in {base_output}."}

    preview_lines = [f"Reason: {reason}"]
    for p in targets:
        size_mb = p.stat().st_size / (1024 * 1024)
        rel_display = p.relative_to(base_output.resolve()) if p.is_relative_to(base_output.resolve()) else p
        warn = " [LARGE - may use heavy RAM]" if size_mb > 150 else ""
        preview_lines.append(f"- {rel_display} ({size_mb:.2f} MB){warn}")

    if not confirm_action("Open Timeline Explorer GUI", preview_lines, "Launch GUI? [Y]es / [n]o: "):
        return {"status": "rejected_by_analyst", "message": "Analyst declined to open Timeline Explorer."}

    # Non-blocking launch so the CLI remains responsive while you inspect in the GUI
    subprocess.Popen([str(exe), *[str(t) for t in targets]])
    console.print(f"[green][+] Launched TimelineExplorer.exe with {len(targets)} file(s).[/green]")
    return {
        "status": "launched",
        "opened_files": [str(t.name) for t in targets],
    }


REGISTER = [
    {
        "schema": {
            "type": "function",
            "function": {
                "name": "open_timeline_explorer",
                "description": (
                    "Launch Eric Zimmerman's TimelineExplorer.exe GUI with relevant parsed case CSVs pre-loaded "
                    "when the analyst asks to inspect the raw artifact files visually (e.g., 'show me in timeline explorer')."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reason": {
                            "type": "string",
                            "description": "Why these CSV files are relevant for manual review.",
                        },
                        "artifacts": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "enum": ["evtx", "prefetch", "prefetch_timeline", "registry", "mft"],
                            },
                            "description": "Artifact categories to open (opens all CSVs in those folders).",
                        },
                        "files": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional specific CSV filenames or relative paths inside the parsed output folder.",
                        },
                    },
                    "required": ["reason"],
                },
            },
        },
        "handler": _handle_open_gui,
    }
]