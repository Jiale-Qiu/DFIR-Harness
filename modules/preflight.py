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

def find_missing_tools():
    missing = {}
    for category, exe in EZ_TOOLS.items():
        if shutil.which(exe) is None:
            missing[category] = exe
    return missing

def ensure_ez_tools():
    missing = find_missing_tools()
    if not missing:
        console.print("[green][+] All Eric Zimmerman tools found on PATH.[/green]\n")
        return True

    console.print("[yellow][!] Missing EZ Tools on PATH:[/yellow]")
    for category, exe in missing.items():
        console.print(f"    - {exe} ({category})")

    console.print("\nPaste the folder path containing your EZ Tools (or press Enter to exit and add them to Windows PATH manually):")

    while True:
        raw_path = input("> ").strip().strip('"').strip("'")
        if not raw_path:
            console.print("[red][!] Exiting. Add your EZ Tools directory to your Windows PATH and re-run.[/red]")
            return False

        ez_dir = Path(raw_path)
        if not ez_dir.is_dir():
            console.print(f"[red][!] '{raw_path}' is not a valid directory. Try again or press Enter to exit:[/red]")
            continue

        # Search recursively because Get-ZimmermanTools nests binaries in subfolders (e.g., net6/net9)
        added_dirs = set()
        for exe in missing.values():
            for match in ez_dir.rglob(exe):
                added_dirs.add(str(match.parent))

        if added_dirs:
            # Prepend found folders to PATH for this Python session and any subprocesses
            os.environ["PATH"] = os.pathsep.join(added_dirs) + os.pathsep + os.environ["PATH"]

        still_missing = find_missing_tools()
        if not still_missing:
            console.print("[green][+] EZ Tools loaded into session PATH.[/green]\n[red][!] Note: This change is temporary and will not persist after this session. To make it permanent, add the EZ Tools directory to your Windows PATH.[/red]\n")
            return True

        console.print("[red][!] Still missing: " + ", ".join(still_missing.values()) + "[/red]")
        console.print("Enter another folder path or press Enter to exit:")