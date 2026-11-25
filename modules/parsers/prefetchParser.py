import subprocess
from rich.console import Console

console = Console(highlight=False)

def parse(file_list, out_dir, dry_run=False):
    out_dir.mkdir(parents=True, exist_ok=True)
    out_str = str(out_dir)
    failed = 0

    for pf_file in file_list:
        cmd = ["PECmd.exe", "-f", str(pf_file), "--csv", out_str]

        if dry_run:
            console.print(f"    [DRY-RUN] {' '.join(cmd)}")
            continue

        # Capture output so EZ Tools banners stay hidden unless an error occurs
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            failed += 1
            console.print(f"[red][!] Failed to parse {pf_file.name}[/red]")
            # EZ Tools typically write error messages to stdout rather than stderr
            error_msg = (result.stderr or result.stdout).strip()
            if error_msg:
                console.print(f"    [red]{error_msg}[/red]")

    if not dry_run:
        parsed_count = len(file_list) - failed
        console.print(f"[green][+] Parsed {parsed_count}/{len(file_list)} Prefetch files into {out_dir}[/green]")