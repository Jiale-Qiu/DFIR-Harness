import json
import duckdb
from pathlib import Path
from rich.console import Console
from rich.table import Table

console = Console(highlight=False)

# Add new baseline queries here. Each hunt specifies which mounted view(s) it needs.
HUNTS = [
    {
        "title": "EVTX High-Signal Security & System Events",
        "requires": ["evtx"],
        "sql": """
            SELECT
                EventId,
                Channel,
                MapDescription,
                COUNT(*) AS Hits,
                MIN(TimeCreated) AS FirstSeen,
                MAX(TimeCreated) AS LastSeen
            FROM evtx
            WHERE EventId IN ('1102', '104', '4625', '4720', '4732', '4698', '7045', '4104')
            GROUP BY EventId, Channel, MapDescription
            ORDER BY Hits DESC
        """,
    },
    {
        "title": "Prefetch LOLBins & Recon Execution",
        "requires": ["prefetch"],
        "sql": """
            SELECT
                ExecutableName,
                RunCount,
                LastRun,
                SourceFilename
            FROM prefetch
            WHERE UPPER(ExecutableName) IN (
                'POWERSHELL.EXE', 'PWSH.EXE', 'CMD.EXE', 'CERTUTIL.EXE',
                'MSHTA.EXE', 'RUNDLL32.EXE', 'REGSVR32.EXE', 'BITSADMIN.EXE',
                'WHOAMI.EXE', 'NET.EXE', 'NET1.EXE', 'NLTEST.EXE',
                'CURL.EXE', 'WGET.EXE', '7Z.EXE', 'RAR.EXE', 'RCLONE.EXE'
            )
            ORDER BY LastRun DESC
            LIMIT 25
        """,
    },
    {
        "title": "Registry Kroll Batch Summary (Top Categories)",
        "requires": ["registry"],
        "sql": """
            SELECT
                Category,
                Description,
                COUNT(*) AS Hits
            FROM registry
            WHERE Category IS NOT NULL AND Category != ''
            GROUP BY Category, Description
            ORDER BY Hits DESC
            LIMIT 20
        """,
    }
]


def _render_table(title: str, columns: list[str], rows: list[tuple]):
    table = Table(title=title)
    for col in columns:
        table.add_column(str(col), overflow="fold")
    for row in rows:
        table.add_row(*["" if val is None else str(val) for val in row])
    console.print()
    console.print(table)


def run_all(con, mounted_views: set[str], base_output: Path):
    """Executes all registered hunts whose required views exist in DuckDB."""
    findings = {}

    for hunt in HUNTS:
        # Skip query if any required artifact view is missing
        if not all(view in mounted_views for view in hunt["requires"]):
            continue

        try:
            cursor = con.execute(hunt["sql"])
            columns = [desc[0] for desc in cursor.description]
            rows = cursor.fetchall()
        except duckdb.Error as e:
            console.print(f"[red][!] Query failed ({hunt['title']}): {e}[/red]")
            continue

        findings[hunt["title"]] = [dict(zip(columns, row)) for row in rows]

        if rows:
            _render_table(hunt["title"], columns, rows)
        else:
            console.print(f"[dim][-] {hunt['title']}: 0 hits[/dim]")

    summary_path = base_output / "triage_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(findings, f, indent=2)
    console.print(f"\n[green][+] Initial triage saved to: {summary_path.resolve()}[/green]")