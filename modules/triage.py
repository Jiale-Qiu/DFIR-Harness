import duckdb
from pathlib import Path
from rich.console import Console
from rich.table import Table

console = Console(highlight=False)

def run_triage(base_output: Path):
    # 1. Start DuckDB in memory and cap its RAM
    con = duckdb.connect(":memory:")
    con.execute("SET memory_limit = '2GB';")

    # 2. Map each SQL view name to its folder and filename pattern
    sources = {
        "evtx": (base_output / "evtx", "*.csv"),
        "prefetch": (base_output / "prefetch", "*_PECmd_Output.csv"),
        "prefetch_timeline": (base_output / "prefetch", "*_PECmd_Output_Timeline.csv"),
        "registry": (base_output / "registry", "*.csv"),
        "mft": (base_output / "mft", "*.csv"),
    }

    # 3. Set up the Rich summary table
    table = Table(title="DuckDB Mounted Views")
    table.add_column("View Name", style="bold cyan")
    table.add_column("CSV Files", justify="right")
    table.add_column("Total Rows", justify="right")

    # 4. Find the CSVs, mount each view, and count the rows
    for view_name, (folder, pattern) in sources.items():
        csv_files = [f for f in folder.glob(pattern) if f.stat().st_size > 0]
        if not csv_files:
            continue

        # Convert Path objects into a SQL list of file paths: ['path1', 'path2']
        sql_paths = []
        for f in csv_files:
            clean_path = f.as_posix().replace("'", "''")
            sql_paths.append(f"'{clean_path}'")
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
        except duckdb.Error as e:
            console.print(f"[red][!] Could not mount '{view_name}': {e}[/red]")

    console.print(table)
    con.close()