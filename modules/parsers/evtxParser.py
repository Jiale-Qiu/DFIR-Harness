import subprocess
from rich.console import Console
from rich.progress import track

console = Console(highlight=False)

def parse(file_list, out_dir, dry_run=False):
    out_dir.mkdir(parents=True, exist_ok=True)
    out_str = str(out_dir)
    failed = 0

    items = file_list if dry_run else track(file_list, description="[cyan]Parsing EVTX logs...[/cyan]", console=console)

    for evtx_file in items:
        csv_name = f"{evtx_file.stem}.csv"
        cmd = ["EvtxECmd.exe", "-f", str(evtx_file), "--csv", out_str, "--csvf", csv_name]

        if dry_run:
            console.print(f"    [DRY-RUN] {' '.join(cmd)}")
            continue

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            failed += 1
            console.print(f"[red][!] Failed to parse {evtx_file.name}[/red]")
            error_msg = (result.stderr or result.stdout).strip()
            if error_msg:
                console.print(f"    [red]{error_msg}[/red]")

    if not dry_run:
        parsed_count = len(file_list) - failed
        console.print(f"[green][+] Parsed {parsed_count}/{len(file_list)} EVTX files into {out_dir}[/green]")