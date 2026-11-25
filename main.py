import argparse
from pathlib import Path
from modules import scanner, preflight
from rich.console import Console
import time

console = Console(highlight=False)

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
    args = parser.parse_args()

    if not args.dry_run:
        if not preflight.ensure_ez_tools():
            return

    main_directory = getValidDirectory(args.directory)
    console.print(f"Main directory set to: {main_directory}\n")

    console.print("Analyzing main directory...\n")
    files = scanner.scanDirectory(main_directory)

    for category, items in files.items():
        console.print(f"{category.upper()}: {len(items)} files")
        time.sleep(0.05)

if __name__ == "__main__":
    main()