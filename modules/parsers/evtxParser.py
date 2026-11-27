from pathlib import Path
from rich.progress import track

from modules.parsers._common import console, run_ez_command


def parse(file_list: list[Path], out_dir: Path, dry_run: bool = False) -> None:
    if not file_list:
        return

    out_dir.mkdir(parents=True, exist_ok=True)
    out_str = str(out_dir)
    items = file_list if dry_run else track(file_list, description="[cyan]Parsing EVTX logs...[/cyan]", console=console)

    failed = sum(
        not run_ez_command(
            ["EvtxECmd.exe", "-f", str(evtx_file), "--csv", out_str, "--csvf", f"{evtx_file.stem}.csv"],
            fail_label=evtx_file.name,
            dry_run=dry_run,
        )
        for evtx_file in items
    )

    if not dry_run:
        console.print(f"[green][+] Parsed {len(file_list) - failed}/{len(file_list)} EVTX files into {out_dir}[/green]")
