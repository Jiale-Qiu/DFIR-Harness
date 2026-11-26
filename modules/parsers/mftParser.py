import subprocess
from rich.console import Console

console = Console(highlight=False)

def parse(file_list, out_dir, dry_run=False):
    out_dir.mkdir(parents=True, exist_ok=True)
    out_str = str(out_dir)
    failed = 0

    for idx, mft_file in enumerate(file_list, start=1):
        # Avoid '$' in CSV filenames so PowerShell doesn't treat it as a variable later
        csv_name = "MFT.csv" if len(file_list) == 1 else f"MFT_{idx}.csv"
        cmd = ["MFTECmd.exe", "-f", str(mft_file), "--csv", out_str, "--csvf", csv_name]

        if dry_run:
            console.print(f"    [DRY-RUN] {' '.join(cmd)}")
            continue

        with console.status(f"[cyan]Parsing {mft_file.name}...[/cyan]", spinner="aesthetic"):
            result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            failed += 1
            console.print(f"[red][!] Failed to parse {mft_file.name}[/red]")
            error_msg = (result.stderr or result.stdout).strip()
            if error_msg:
                console.print(f"    [red]{error_msg}[/red]")

    if not dry_run:
        parsed_count = len(file_list) - failed
        console.print(f"[green][+] Parsed {parsed_count}/{len(file_list)} MFT file(s) into {out_dir}[/green]")