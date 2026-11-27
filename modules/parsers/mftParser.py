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

def _find_matching_mft(j_file: Path, mft_files: list[Path] | None) -> Path | None:
    if not mft_files:
        return None
    if len(mft_files) == 1:
        return mft_files[0]

    j_parts = j_file.resolve().parts
    return max(
        mft_files,
        key=lambda m: sum(1 for a, b in zip(j_parts, m.resolve().parts) if a == b),
    )


def parse_usnjrnl(
    file_list: list[Path],
    out_dir: Path,
    mft_files: list[Path] | None = None,
    dry_run: bool = False,
) -> None:
    if not file_list:
        return

    out_dir.mkdir(parents=True, exist_ok=True)
    out_str = str(out_dir)
    failed = 0

    for idx, j_file in enumerate(file_list, start=1):
        csv_name = "UsnJrnl.csv" if len(file_list) == 1 else f"UsnJrnl_{idx}.csv"
        cmd = ["MFTECmd.exe", "-f", str(j_file), "--csv", out_str, "--csvf", csv_name]

        matched_mft = _find_matching_mft(j_file, mft_files)
        if matched_mft:
            cmd.extend(["-m", str(matched_mft)])
            status = f"[cyan]Parsing {j_file.name} (with {matched_mft.name})...[/cyan]"
        else:
            status = f"[cyan]Parsing {j_file.name} (no $MFT found for path resolution)...[/cyan]"

        if not run_ez_command(cmd, fail_label=j_file.name, dry_run=dry_run, status_msg=status):
            failed += 1

    if not dry_run:
        console.print(f"[green][+] Parsed {len(file_list) - failed}/{len(file_list)} UsnJrnl file(s) into {out_dir}[/green]")