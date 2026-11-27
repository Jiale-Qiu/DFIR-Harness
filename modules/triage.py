from pathlib import Path

import duckdb
from rich.console import Console
from rich.table import Table

from modules import llm_cli
from modules.common import ARTIFACT_SOURCES

console = Console(highlight=False)


def _is_non_empty_file(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def mount_views(con, base_output: Path) -> set[str]:
    """Mounts available CSV folders as DuckDB views and returns the active view names."""
    table = Table(title="DuckDB Mounted Views")
    table.add_column("View Name", style="bold cyan")
    table.add_column("CSV Files", justify="right")
    table.add_column("Total Rows", justify="right")

    mounted_views: set[str] = set()

    for view_name, (sub, pattern) in ARTIFACT_SOURCES.items():
        folder = base_output / sub
        if not folder.is_dir():
            continue

        csv_files = [f for f in sorted(folder.glob(pattern)) if _is_non_empty_file(f)]
        if not csv_files:
            continue

        sql_paths = ", ".join(f"'{f.as_posix().replace(chr(39), chr(39) * 2)}'" for f in csv_files)

        try:
            con.execute(
                f"""
                CREATE VIEW {view_name} AS
                SELECT * FROM read_csv(
                    [{sql_paths}],
                    header=true,
                    union_by_name=true,
                    all_varchar=true,
                    ignore_errors=true
                )
                """
            )
            row = con.execute(f"SELECT COUNT(*) FROM {view_name}").fetchone()
            row_count = row[0] if row else 0
            table.add_row(view_name, str(len(csv_files)), f"{row_count:,}")
            mounted_views.add(view_name)
        except duckdb.Error as e:
            console.print(f"[red][!] Could not mount '{view_name}': {e}[/red]")

    if mounted_views:
        console.print(table)
    return mounted_views


def run_triage(base_output: Path, main_directory: Path | None = None) -> None:
    with duckdb.connect(":memory:") as con:
        con.execute("SET memory_limit = '2GB';")

        mounted_views = mount_views(con, base_output)
        if not mounted_views:
            console.print("[yellow][!] No valid CSVs found to mount in DuckDB.[/yellow]")
            return

        llm_cli.start_cli(con, mounted_views, base_output, main_directory=main_directory)