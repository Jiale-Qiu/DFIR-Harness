from pathlib import Path

from modules.parsers._common import console, run_ez_command


def parse(file_list: list[Path], out_dir: Path, dry_run: bool = False) -> None:
    if not file_list:
        return

    out_dir.mkdir(parents=True, exist_ok=True)
    out_str = str(out_dir)
    failed = 0

    for idx, mft_file in enumerate(file_list, start=1):
        # Avoid '$' in CSV filenames so PowerShell doesn't treat it as a variable later
        csv_name = "MFT.csv" if len(file_list) == 1 else f"MFT_{idx}.csv"
        cmd = ["MFTECmd.exe", "-f", str(mft_file), "--csv", out_str, "--csvf", csv_name]
        if not run_ez_command(cmd, fail_label=mft_file.name, dry_run=dry_run, status_msg=f"[cyan]Parsing {mft_file.name}...[/cyan]"):
            failed += 1

    if not dry_run:
        console.print(f"[green][+] Parsed {len(file_list) - failed}/{len(file_list)} MFT file(s) into {out_dir}[/green]")
