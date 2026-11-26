import argparse
import shutil
from pathlib import Path
from modules import scanner, preflight
from modules.parsers import evtxParser, registryParser, prefetchParser, mftParser
from rich.console import Console
import time

console = Console(highlight=False)

PARSERS = {
    "evtx": evtxParser.parse,
    "registry": registryParser.parse,
    "prefetch": prefetchParser.parse,
    "mft": mftParser.parse,
}

def getValidDirectory(cli_path=None):
    raw_path = cli_path

    while True:
        if not raw_path:
            raw_path = input("Enter the full path of your investigation directory: ")

        cleaned = raw_path.strip().strip('"').strip("'")
        target = Path(cleaned)

        if target.is_dir():
            return target

        console.print(f"[red][!] Error: '{cleaned}' is not a valid directory. Try again.\n")
        raw_path = None

def main():
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

    if not args.dry_run and not args.skip_parse:
        if not preflight.ensure_ez_tools():
            return

    main_directory = getValidDirectory(args.directory)
    console.print(f"Main directory set to: {main_directory}\n")

    base_output = Path("output") / main_directory.name

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

        # HITL Parsing Loop
        parse_all = False

        for category, file_list in files.items():
            if not file_list or category not in PARSERS:
                continue

            if not parse_all:
                console.print(f"\nThe program will now parse the following file type(s): [bold]{category.upper()}[/bold] ({len(file_list)} files).")
                skip_category = False

                while True:
                    choice = input("Continue? [Y]es / [n]o / [a]ll: ").strip().lower()
                    if choice in ("", "y", "yes"):
                        break
                    elif choice in ("n", "no"):
                        console.print(f"[yellow][-] Skipping {category.upper()}.[/yellow]")
                        skip_category = True
                        break
                    elif choice in ("a", "all"):
                        parse_all = True
                        break
                    else:
                        console.print("[red][!] Invalid choice. Press Enter for Yes, or enter n / a.[/red]")

                if skip_category:
                    continue

            category_out = base_output / category
            console.print(f"[cyan][*] Running {category.upper()} parser...[/cyan]")
            PARSERS[category](file_list, category_out, dry_run=args.dry_run)

    # Milestone 3 hook: everything below runs regardless of whether CSVs were just parsed or loaded via --skip-parse
    console.print(f"\n[green][+] Ready for DuckDB triage against: {base_output.resolve()}[/green]")

if __name__ == "__main__":
    main()