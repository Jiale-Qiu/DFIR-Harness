import duckdb
from pathlib import Path
from rich.console import Console
from rich.table import Table
from modules import hunts, llm_cli

console = Console(highlight=False)

def mount_views(con, base_output: Path) -> set[str]:
    """Mounts available CSV folders as DuckDB views and returns the active view names."""
    sources = {
        "evtx": (base_output / "evtx", "*.csv"),
        "prefetch": (base_output / "prefetch", "*_PECmd_Output.csv"),
        "prefetch_timeline": (base_output / "prefetch", "*_PECmd_Output_Timeline.csv"),
        "registry": (base_output / "registry", "*.csv"),
        "mft": (base_output / "mft", "*.csv"),
    }

    table = Table(title="DuckDB Mounted Views")
    table.add_column("View Name", style="bold cyan")
    table.add_column("CSV Files", justify="right")
    table.add_column("Total Rows", justify="right")

    mounted_views = set()

    for view_name, (folder, pattern) in sources.items():
        csv_files = [f for f in folder.glob(pattern) if f.stat().st_size > 0]
        if not csv_files:
            continue

        sql_paths = [f"'{f.as_posix().replace(chr(39), chr(39)*2)}'" for f in csv_files]
        files_list = ", ".join(sql_paths)

        try:
            con.execute(f"""
                CREATE VIEW {view_name} AS
                SELECT * FROM read_csv(
                    [{files_list}],
                    header=true,
                    union_by_name=true,
                    all_varchar=true,
                    ignore_errors=true
                )
            """)
            row_count = con.execute(f"SELECT COUNT(*) FROM {view_name}").fetchone()[0]
            table.add_row(view_name, str(len(csv_files)), f"{row_count:,}")
            mounted_views.add(view_name)
        except duckdb.Error as e:
            console.print(f"[red][!] Could not mount '{view_name}': {e}[/red]")

    if mounted_views:
        console.print(table)
    return mounted_views


def llm_cli_placeholder(con, mounted_views: set[str], base_output: Path):
    """Placeholder for Milestone 4: Interactive LLM investigation loop."""
    console.print("\n[bold cyan]=== Entering LLM Investigation CLI (Placeholder) ===[/bold cyan]")
    console.print(f"Active DuckDB views ready for querying: {', '.join(sorted(mounted_views))}")
    console.print("[dim](Milestone 4 LLM loop will live here while DuckDB remains open.)[/dim]\n")


def run_triage(base_output: Path, main_directory: Path | None = None):
    with duckdb.connect(":memory:") as con:
        con.execute("SET memory_limit = '2GB';")

        mounted_views = mount_views(con, base_output)
        if not mounted_views:
            console.print("[yellow][!] No valid CSVs found to mount in DuckDB.[/yellow]")
            return

        console.print("\nRun initial triage (default baseline queries for available artifacts)?")
        while True:
            choice = input("Continue? [Y]es / [n]o: ").strip().lower()
            if choice in ("", "y", "yes"):
                hunts.run_all(con, mounted_views, base_output)
                break
            elif choice in ("n", "no"):
                console.print("[yellow][-] Skipping initial triage queries.[/yellow]")
                break
            else:
                console.print("[red][!] Invalid choice. Press Enter for Yes, or enter n.[/red]")

        llm_cli.start_cli(con, mounted_views, base_output, main_directory=main_directory)