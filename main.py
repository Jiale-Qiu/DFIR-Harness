import argparse
from pathlib import Path
from modules import scanner
from rich import print

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

        print(f"[red][!] Error: '{cleaned}' is not a valid directory. Try again.\n")
        raw_path = None  # Reset so the loop prompts the user

def main():
    print("cleb's DFIR harness v1.0 - Initializing...")

    parser = argparse.ArgumentParser(description="AI-Assisted Windows DFIR Harness")
    parser.add_argument("directory", nargs="?", help="Path to the investigation directory")
    args = parser.parse_args()

    main_directory = getValidDirectory(args.directory)
    print(f"Main directory set to: {main_directory}\n")

    print("Analyzing main directory...")
    files = scanner.scanDirectory(main_directory)
    print(files)

if __name__ == "__main__":
    main()