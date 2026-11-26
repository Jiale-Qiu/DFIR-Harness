import shutil
import subprocess
from pathlib import Path
from rich.console import Console
from rich.progress import track

console = Console(highlight=False)

def get_kroll_batch():
    recmd_path = shutil.which("RECmd.exe")
    if not recmd_path:
        return None
    batch_file = Path(recmd_path).parent / "BatchExamples" / "Kroll_Batch.reb"
    return str(batch_file) if batch_file.is_file() else None

def parse(file_list, out_dir, dry_run=False):
    batch_file = get_kroll_batch()
    if not batch_file and not dry_run:
        console.print("[red][!] Error: Could not find BatchExamples\\Kroll_Batch.reb next to RECmd.exe.[/red]")
        return

    out_dir.mkdir(parents=True, exist_ok=True)
    out_str = str(out_dir)
    failed = 0

    items = file_list if dry_run else track(file_list, description="[cyan]Parsing Registry hives...[/cyan]", console=console)

    for hive in items:
        # Prefix with parent folder (e.g., config_SYSTEM.csv, Administrator_NTUSER.DAT.csv)
        csv_name = f"{hive.parent.name}_{hive.name}.csv"
        cmd = ["RECmd.exe", "-f", str(hive), "--csv", out_str, "--csvf", csv_name]
        if batch_file:
            cmd.extend(["--bn", batch_file])

        if dry_run:
            console.print(f"    [DRY-RUN] {' '.join(cmd)}")
            continue

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            failed += 1
            console.print(f"[red][!] Failed to parse {hive.name}[/red]")
            error_msg = (result.stderr or result.stdout).strip()
            if error_msg:
                console.print(f"    [red]{error_msg}[/red]")

    if not dry_run:
        parsed_count = len(file_list) - failed
        console.print(f"[green][+] Parsed {parsed_count}/{len(file_list)} Registry hives into {out_dir}[/green]")