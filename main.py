import argparse
from pathlib import Path
from modules import scanner
from rich import print
from rich.console import Console
import time

console = Console(highlight=False)

def getValidDirectory(cli_path=None):
    raw_path = cli_path

    while True:
        if not raw_path:
            raw_path = input("Enter the full path of your investigation directory: ")

        # Strip accidental quotes
        cleaned = raw_path.strip().strip('"').strip("'")
        target = Path(cleaned)

        if target.is_dir():
            return target

        console.print(f"[red][!] Error: '{cleaned}' is not a valid directory. Try again.\n")
        raw_path = None  # Reset so the loop prompts the user

def main():
    console.print("cleb's DFIR harness v1.0")

    parser = argparse.ArgumentParser(description="AI-Assisted Windows DFIR Harness")
    parser.add_argument("directory", nargs="?", help="Path to the investigation directory")
    args = parser.parse_args()

    time.sleep(0.1)

    main_directory = getValidDirectory(args.directory)
    console.print(f"Main directory set to: {main_directory}\n")

    console.print("Analyzing main directory...")
    files = scanner.scanDirectory(main_directory)

    time.sleep(0.1)

    for category, items in files.items():
        console.print(f"{category.upper()}: {len(items)} files")
        time.sleep(0.1)

if __name__ == "__main__":
    main()