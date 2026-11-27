from pathlib import Path

from modules.parsers._common import console, run_ez_command


def parse(file_list: list[Path], out_dir: Path, dry_run: bool = False) -> None:
    if not file_list:
        return

    out_dir.mkdir(parents=True, exist_ok=True)
    out_str = str(out_dir)
    target_dirs = sorted({pf.parent for pf in file_list})
    status_msg = f"[cyan]Parsing {len(file_list)} Prefetch files...[/cyan]"

    failed = sum(
        not run_ez_command(
            ["PECmd.exe", "-d", str(pf_dir), "--csv", out_str],
            fail_label=f"Prefetch directory {pf_dir}",
            dry_run=dry_run,
            status_msg=status_msg,
        )
        for pf_dir in target_dirs
    )

    if not dry_run:
        console.print(
            f"[green][+] Parsed {len(file_list)} Prefetch files across {len(target_dirs) - failed} folder(s) into {out_dir}[/green]"
        )
