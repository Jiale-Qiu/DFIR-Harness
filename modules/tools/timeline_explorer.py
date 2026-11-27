import os
import shutil
import subprocess
from pathlib import Path

from modules.common import ARTIFACT_SOURCES
from modules.llm_ui import confirm_action, console


def _find_timeline_explorer() -> Path | None:
    """Finds TimelineExplorer.exe via env var, PATH, or local ./tools directory."""
    env_path = os.environ.get("TIMELINE_EXPLORER_PATH")
    if env_path and Path(env_path).is_file():
        return Path(env_path)

    if which_path := shutil.which("TimelineExplorer.exe"):
        return Path(which_path)

    tools_dir = Path("tools")
    if tools_dir.is_dir():
        return next(tools_dir.rglob("TimelineExplorer.exe"), None)
    return None


def _resolve_csv_files(
    base_output: Path,
    artifacts: list[str],
    files: list[str],
    main_directory: Path | None = None,
) -> list[Path]:
    """Resolves requested artifact categories and/or specific CSV filenames inside base_output or main_directory."""
    resolved: list[Path] = []
    seen: set[Path] = set()
    base_resolved = base_output.resolve()
    main_resolved = main_directory.resolve() if main_directory is not None else None
    search_roots = [base_resolved] + ([main_resolved] if main_resolved and main_resolved != base_resolved else [])

    def _add(p: Path) -> bool:
        try:
            p_res = p.resolve()
            is_valid = (
                any(p_res.is_relative_to(root) for root in search_roots)
                and p_res.is_file()
                and p_res.suffix.lower() == ".csv"
                and p_res not in seen
            )
        except OSError:
            return False
        if is_valid:
            seen.add(p_res)
            resolved.append(p_res)
            return True
        return False

    for name in artifacts:
        key = str(name).strip().lower()
        if key in ARTIFACT_SOURCES:
            sub, pattern = ARTIFACT_SOURCES[key]
            for f in sorted((base_resolved / sub).glob(pattern)):
                try:
                    if f.stat().st_size > 0:
                        _add(f)
                except OSError:
                    continue

    for rel in files:
        candidate = Path(str(rel))
        if candidate.is_absolute():
            _add(candidate)
            continue

        if not any(_add(root / candidate) for root in search_roots):
            for root in search_roots:
                for match in sorted(root.rglob(candidate.name)):
                    _add(match)

    return resolved


def _handle_open_gui(args: dict, ctx: dict) -> dict:
    base_output = Path(ctx["base_output"])
    raw_main = ctx.get("main_directory")
    main_directory = Path(raw_main) if raw_main else None
    artifacts = args.get("artifacts") or []
    files = args.get("files") or []
    reason = str(args.get("reason") or "Open parsed CSVs for manual analyst review")

    exe = _find_timeline_explorer()
    if not exe:
        msg = (
            "TimelineExplorer.exe not found. Put it in ./tools/, add it to PATH, "
            "or set TIMELINE_EXPLORER_PATH."
        )
        console.print(f"[red][!] {msg}[/red]")
        return {"error": msg}

    targets = _resolve_csv_files(base_output, artifacts, files, main_directory=main_directory)
    if not targets:
        return {"error": "No matching non-empty CSV files found in case directories."}

    base_resolved = base_output.resolve()
    main_resolved = main_directory.resolve() if main_directory else None
    preview_lines = [f"Reason: {reason}"]
    for p in targets:
        try:
            size_mb = p.stat().st_size / (1024 * 1024)
        except OSError:
            size_mb = 0.0

        if p.is_relative_to(base_resolved):
            rel_display = f"[output] {p.relative_to(base_resolved).as_posix()}"
        elif main_resolved and p.is_relative_to(main_resolved):
            rel_display = f"[source] {p.relative_to(main_resolved).as_posix()}"
        else:
            rel_display = str(p)
        warn = " [LARGE - may use heavy RAM]" if size_mb > 150 else ""
        preview_lines.append(f"- {rel_display} ({size_mb:.2f} MB){warn}")

    if not confirm_action("Open Timeline Explorer GUI", preview_lines, "Launch GUI? [Y]es / [n]o: "):
        return {"status": "rejected_by_analyst", "message": "Analyst declined to open Timeline Explorer."}

    # Non-blocking launch so the CLI remains responsive while you inspect in the GUI
    try:
        subprocess.Popen([str(exe), *(str(t) for t in targets)])
    except OSError as exc:
        return {"error": f"Failed to launch TimelineExplorer.exe: {exc}"}

    console.print(f"[green][+] Launched TimelineExplorer.exe with {len(targets)} file(s).[/green]")
    return {
        "status": "launched",
        "opened_files": [t.name for t in targets],
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
                            "description": (
                                "Optional specific CSV filenames or relative paths inside the parsed output folder "
                                "or source investigation directory."
                            ),
                        },
                    },
                    "required": ["reason"],
                },
            },
        },
        "handler": _handle_open_gui,
    }
]
