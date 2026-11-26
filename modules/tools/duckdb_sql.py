"""Read-only DuckDB SQL execution tool with static safety validation and HITL/auto-sql support."""

from __future__ import annotations

import json
import re

import duckdb

from modules.llm_ui import confirm_sql, console, print_result_table

MAX_SQL_ROWS = 100
MAX_LLM_CELL_CHARS = 500
MAX_LLM_PAYLOAD_CHARS = 24_000

_ALLOWED_START = re.compile(
    r"^\s*(SELECT|WITH|DESCRIBE|SHOW|SUMMARIZE|EXPLAIN)\b",
    re.IGNORECASE,
)

_FORBIDDEN_KEYWORDS = re.compile(
    r"\b("
    r"INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE|REPLACE|MERGE|"
    r"COPY|ATTACH|DETACH|PRAGMA|INSTALL|LOAD|SET|RESET|CALL|"
    r"EXPORT|IMPORT|CHECKPOINT|VACUUM|EXECUTE|PREPARE"
    r")\b",
    re.IGNORECASE,
)

# Blocks local filesystem reads, env var access, secrets, and network functions inside SELECT
_FORBIDDEN_FUNCTIONS = re.compile(
    r"\b("
    r"read_csv|read_csv_auto|read_parquet|read_json|read_json_auto|read_ndjson|"
    r"read_text|read_blob|read_xlsx|parquet_scan|csv_scan|json_scan|sqlite_scan|"
    r"postgres_scan|mysql_scan|iceberg_scan|delta_scan|glob|getenv|current_setting|"
    r"duckdb_settings|duckdb_secrets|duckdb_extensions|http_get|http_post|shellfs"
    r")\s*\(",
    re.IGNORECASE,
)


def _strip_sql_comments_and_strings(sql: str) -> str:
    """Removes SQL comments and single-quoted string literals for safe static inspection."""
    no_block = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    no_line = re.sub(r"--[^\n]*", " ", no_block)
    return re.sub(r"'(?:''|[^'])*'", "''", no_line)


def validate_readonly_sql(sql: str) -> tuple[bool, str | None]:
    """Validates that a query is a single read-only statement without external/system functions."""
    cleaned = _strip_sql_comments_and_strings(sql).strip()
    if not cleaned:
        return False, "Empty SQL query."

    if ";" in cleaned.rstrip(";"):
        return False, "Multiple SQL statements are not permitted. Submit one read-only query at a time."

    if not _ALLOWED_START.match(cleaned):
        return False, "Only read-only SELECT/WITH/DESCRIBE/SHOW/SUMMARIZE/EXPLAIN queries are permitted."

    kw = _FORBIDDEN_KEYWORDS.search(cleaned)
    if kw:
        return False, f"Blocked forbidden SQL keyword: {kw.group(1).upper()}"

    fn = _FORBIDDEN_FUNCTIONS.search(cleaned)
    if fn:
        return False, f"Blocked external/system DuckDB function: {fn.group(1)}()"

    return True, None


def _stringify(val: object) -> str:
    return "" if val is None else str(val)


def _trim_for_llm(columns: list[str], rows: list[list[str]]) -> tuple[list[dict[str, str]], bool]:
    """Builds a context-bounded list of row dicts for the LLM without altering full viewer history."""
    llm_rows: list[dict[str, str]] = []
    char_budget = 0
    clipped = False

    for row in rows:
        record: dict[str, str] = {}
        for col, val in zip(columns, row):
            if val == "":
                continue
            if len(val) > MAX_LLM_CELL_CHARS:
                val = val[: MAX_LLM_CELL_CHARS - 1] + "…"
            record[col] = val

        serialized_len = len(json.dumps(record, ensure_ascii=False))
        if char_budget + serialized_len > MAX_LLM_PAYLOAD_CHARS and llm_rows:
            clipped = True
            break
        llm_rows.append(record)
        char_budget += serialized_len

    return llm_rows, clipped


def execute_sql(
    con,
    sql: str,
    history: list[dict],
    show_table: bool = False,
    max_rows: int = MAX_SQL_ROWS,
) -> dict:
    """Validates and executes a read-only DuckDB query, caching results in session history."""
    is_safe, err = validate_readonly_sql(sql)
    if not is_safe:
        console.print(f"[red][!] SQL Rejected: {err}[/red]")
        return {"error": err}

    clean_sql = sql.strip().rstrip(";")
    try:
        cursor = con.execute(clean_sql)
        columns = [desc[0] for desc in (cursor.description or [])]
        raw_rows = cursor.fetchmany(max_rows + 1)
    except duckdb.Error as exc:
        console.print(f"[red][!] DuckDB Error: {exc}[/red]")
        return {"error": f"DuckDB error: {exc}"}

    truncated = len(raw_rows) > max_rows
    if truncated:
        raw_rows = raw_rows[:max_rows]

    rows = [[_stringify(cell) for cell in row] for row in raw_rows]
    qid = len(history) + 1
    entry = {
        "id": qid,
        "sql": clean_sql,
        "columns": columns,
        "rows": rows,
        "truncated": truncated,
    }
    history.append(entry)

    if show_table:
        print_result_table(entry)
    else:
        suffix = "+" if truncated else ""
        console.print(f"[dim][+] Query #{qid}: {len(rows)}{suffix} rows returned (use /show {qid} to view).[/dim]")

    llm_rows, llm_clipped = _trim_for_llm(columns, rows)
    result = {
        "query_id": qid,
        "columns": columns,
        "row_count": len(rows),
        "truncated": truncated,
        "rows": llm_rows,
    }
    if llm_clipped:
        result["llm_context_clipped"] = True
        result["note"] = (
            f"Returned {len(llm_rows)} of {len(rows)} rows to stay within token budget; "
            "narrow SELECT columns or add stricter WHERE filters if you need remaining rows."
        )
    return result


def _query_duckdb(args: dict, ctx: dict) -> dict:
    sql = (args.get("sql") or "").strip()
    reason = (args.get("reason") or "Investigate forensic artifacts").strip()

    is_safe, err = validate_readonly_sql(sql)
    if not is_safe:
        return {"error": err}

    state = ctx["state"]
    if state.get("auto_sql"):
        short_sql = " ".join(sql.split())
        if len(short_sql) > 140:
            short_sql = short_sql[:139] + "…"
        console.print(f"[dim cyan][auto-sql] {short_sql}[/dim cyan]")
        show_table = False
    else:
        decision = confirm_sql(reason, sql)
        if decision in (False, "no", "n"):
            return {"status": "declined", "message": "Analyst declined SQL execution."}
        show_table = decision == "table"

    return execute_sql(ctx["con"], sql, state["history"], show_table=show_table)


REGISTER = [
    {
        "schema": {
            "type": "function",
            "function": {
                "name": "query_duckdb",
                "description": (
                    "Execute a read-only DuckDB SQL query against the mounted forensic artifact views. "
                    "Always select only the columns you need and include LIMIT 50 or LIMIT 100 on non-aggregated queries."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "sql": {
                            "type": "string",
                            "description": "Single read-only DuckDB SQL query (SELECT, WITH, DESCRIBE, SHOW, SUMMARIZE).",
                        },
                        "reason": {
                            "type": "string",
                            "description": "Short explanation of what forensic question this query answers.",
                        },
                    },
                    "required": ["sql", "reason"],
                },
            },
        },
        "handler": _query_duckdb,
    }
]