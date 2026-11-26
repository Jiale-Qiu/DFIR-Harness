import subprocess
from rich.console import Console

console = Console(highlight=False)

def parse(file_list, out_dir, dry_run=False):
    out_dir.mkdir(parents=True, exist_ok=True)
    out_str = str(out_dir)
    target_dirs = sorted({pf.parent for pf in file_list})
    failed = 0

    for pf_dir in target_dirs:
        cmd = ["PECmd.exe", "-d", str(pf_dir), "--csv", out_str]

        if dry_run:
            console.print(f"    [DRY-RUN] {' '.join(cmd)}")
            continue

        # Plays the bouncing bar animation until subprocess.run() finishes
        with console.status(f"[cyan]Parsing {len(file_list)} Prefetch files...[/cyan]", spinner="aesthetic"):
            result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            failed += 1
            console.print(f"[red][!] Failed to parse Prefetch directory {pf_dir}[/red]")
            error_msg = (result.stderr or result.stdout).strip()
            if error_msg:
                console.print(f"    [red]{error_msg}[/red]")

    if not dry_run:
        parsed_dirs = len(target_dirs) - failed
        console.print(f"[green][+] Parsed {len(file_list)} Prefetch files across {parsed_dirs} folder(s) into {out_dir}[/green]")