import os
import shutil
from pathlib import Path
from rich.console import Console

console = Console(highlight=False)

EZ_TOOLS = {
    "evtx": "EvtxECmd.exe",
    "registry": "RECmd.exe",
    "prefetch": "PECmd.exe",
    "mft": "MFTECmd.exe",
}


def find_missing_tools() -> dict[str, str]:
    return {category: exe for category, exe in EZ_TOOLS.items() if shutil.which(exe) is None}


def ensure_ez_tools() -> bool:
    missing = find_missing_tools()
    if not missing:
        console.print("[green][+] All Eric Zimmerman tools found on PATH.[/green]\n")
        return True

    console.print("[yellow][!] Missing EZ Tools on PATH:[/yellow]")
    for category, exe in missing.items():
        console.print(f"    - {exe} ({category})")

    console.print("\nPaste the folder path containing your EZ Tools (or press Enter to exit and add them to Windows PATH manually):")

    while True:
        try:
            raw_path = input("> ").strip().strip('"').strip("'")
        except (KeyboardInterrupt, EOFError):
            console.print("\n[red][!] Exiting.[/red]")
            return False

        if not raw_path:
            console.print("[red][!] Exiting. Add your EZ Tools directory to your Windows PATH and re-run.[/red]")
            return False

        ez_dir = Path(raw_path)
        if not ez_dir.is_dir():
            console.print(f"[red][!] '{raw_path}' is not a valid directory. Try again or press Enter to exit:[/red]")
            continue

        # Search recursively because Get-ZimmermanTools nests binaries in subfolders (e.g., net6/net9)
        added_dirs = {str(match.parent) for exe in missing.values() for match in ez_dir.rglob(exe)}
        if added_dirs:
            current_path = os.environ.get("PATH", "")
            prefix = os.pathsep.join(sorted(added_dirs))
            os.environ["PATH"] = f"{prefix}{os.pathsep}{current_path}" if current_path else prefix

        still_missing = find_missing_tools()
        if not still_missing:
            console.print(
                "[green][+] EZ Tools loaded into session PATH.[/green]\n"
                "[red][!] Note: This change is temporary and will not persist after this session. "
                "To make it permanent, add the EZ Tools directory to your Windows PATH.[/red]\n"
            )
            return True

        console.print(f"[red][!] Still missing: {', '.join(still_missing.values())}[/red]")
        console.print("Enter another folder path or press Enter to exit:")
