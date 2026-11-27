import shutil
from pathlib import Path
from rich.progress import track

from modules.parsers._common import console, run_ez_command


def get_kroll_batch() -> str | None:
    recmd_path = shutil.which("RECmd.exe")
    if not recmd_path:
        return None
    batch_file = Path(recmd_path).parent / "BatchExamples" / "Kroll_Batch.reb"
    return str(batch_file) if batch_file.is_file() else None


def parse(file_list: list[Path], out_dir: Path, dry_run: bool = False) -> None:
    if not file_list:
        return

    batch_file = get_kroll_batch()
    if not batch_file and not dry_run:
        console.print("[red][!] Error: Could not find BatchExamples\\Kroll_Batch.reb next to RECmd.exe.[/red]")
        return

    out_dir.mkdir(parents=True, exist_ok=True)
    out_str = str(out_dir)
    items = file_list if dry_run else track(file_list, description="[cyan]Parsing Registry hives...[/cyan]", console=console)
    failed = 0

    for hive in items:
        # Prefix with parent folder (e.g., config_SYSTEM.csv, Administrator_NTUSER.DAT.csv)
        parent_prefix = hive.parent.name or "root"
        cmd = ["RECmd.exe", "-f", str(hive), "--csv", out_str, "--csvf", f"{parent_prefix}_{hive.name}.csv"]
        if batch_file:
            cmd.extend(["--bn", batch_file])
        if not run_ez_command(cmd, fail_label=hive.name, dry_run=dry_run):
            failed += 1

    if not dry_run:
        console.print(f"[green][+] Parsed {len(file_list) - failed}/{len(file_list)} Registry hives into {out_dir}[/green]")
