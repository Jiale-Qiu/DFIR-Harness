import argparse
import shutil
import time
from pathlib import Path

from rich.console import Console

from modules import preflight, scanner, triage
from modules.parsers import evtxParser, mftParser, prefetchParser, registryParser

console = Console(highlight=False)

PARSERS = {
    "evtx": evtxParser.parse,
    "registry": registryParser.parse,
    "prefetch": prefetchParser.parse,
    "mft": mftParser.parse,
    "usnjrnl": mftParser.parse_usnjrnl,
}


def getValidDirectory(cli_path: str | None = None) -> Path | None:
    raw_path = cli_path

    while True:
        if not raw_path:
            try:
                raw_path = input("Enter the full path of your investigation directory: ")
            except (KeyboardInterrupt, EOFError):
                console.print("\n[yellow][-] Cancelled.[/yellow]")
                return None

        cleaned = raw_path.strip().strip('"').strip("'")
        if cleaned:
            try:
                target = Path(cleaned)
                if target.is_dir():
                    return target
            except OSError:
                pass

        console.print(f"[red][!] Error: '{cleaned}' is not a valid directory. Try again.\n")
        raw_path = None


def _prompt_parse_decision(category: str, file_count: int) -> str:
    """Prompts the analyst whether to parse a category ('yes', 'no', or 'all')."""
    console.print(f"\nThe program will now parse the following file type(s): [bold]{category.upper()}[/bold] ({file_count} files).")
    while True:
        try:
            choice = input("Continue? [Y]es / [n]o / [a]ll: ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            console.print(f"\n[yellow][-] Skipping {category.upper()}.[/yellow]")
            return "no"

        if choice in ("", "y", "yes"):
            return "yes"
        if choice in ("n", "no"):
            console.print(f"[yellow][-] Skipping {category.upper()}.[/yellow]")
            return "no"
        if choice in ("a", "all"):
            return "all"
        console.print("[red][!] Invalid choice. Press Enter for Yes, or enter n / a.[/red]")


def main() -> None:
    console.print("\ncleb's DFIR harness v1.0\n")

    parser = argparse.ArgumentParser(description="AI-Assisted Windows DFIR Harness")
    parser.add_argument("directory", nargs="?", help="Path to the investigation directory")
    parser.add_argument("--dry-run", action="store_true", help="Skip tool checks and simulate execution")
    parser.add_argument("--clear", action="store_true", help="Clear existing output for this case before starting")
    parser.add_argument("--skip-parse", action="store_true", help="Skip scanning/parsing and use existing output CSVs")
    args = parser.parse_args()

    if args.skip_parse and args.clear:
        console.print("[red][!] Error: --skip-parse and --clear cannot be used together.[/red]")
        return

    if not args.dry_run and not args.skip_parse and not preflight.ensure_ez_tools():
        return

    main_directory = getValidDirectory(args.directory)
    if main_directory is None:
        return
    console.print(f"Main directory set to: {main_directory}\n")

    base_output = Path("output") / (main_directory.resolve().name or "case")

    if args.skip_parse:
        if not base_output.is_dir() or not any(base_output.rglob("*.csv")):
            console.print(f"[red][!] Error: No existing parsed CSVs found at '{base_output.resolve()}'.[/red]")
            console.print("    Run without --skip-parse first to generate the CSVs.")
            return
        console.print(f"[cyan][*] Skipping parse. Using existing CSVs in: {base_output.resolve()}[/cyan]\n")
    else:
        if args.clear and base_output.exists():
            if args.dry_run:
                console.print(f"[yellow][DRY-RUN] Would delete existing output at: {base_output.resolve()}[/yellow]\n")
            else:
                shutil.rmtree(base_output)
                console.print(f"[yellow][-] Cleared existing output at: {base_output.resolve()}[/yellow]\n")

        console.print("Analyzing main directory...\n")
        files = scanner.scanDirectory(main_directory)

        for category, items in files.items():
            console.print(f"{category.upper()}: {len(items)} files")
            time.sleep(0.05)

        parse_all = False
        for category, file_list in files.items():
            if not file_list or category not in PARSERS:
                continue

            if not parse_all:
                decision = _prompt_parse_decision(category, len(file_list))
                if decision == "no":
                    continue
                if decision == "all":
                    parse_all = True

            console.print(f"[cyan][*] Running {category.upper()} parser...[/cyan]")
            if category == "usnjrnl":
                mftParser.parse_usnjrnl(
                    file_list,
                    base_output / category,
                    mft_files=files.get("mft"),
                    dry_run=args.dry_run,
                )
            else:
                PARSERS[category](file_list, base_output / category, dry_run=args.dry_run)

    if not args.dry_run:
        triage.run_triage(base_output, main_directory=main_directory)


if __name__ == "__main__":
    main()