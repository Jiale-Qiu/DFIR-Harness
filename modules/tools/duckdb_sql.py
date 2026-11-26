import re
import duckdb
from modules.llm_ui import confirm_sql, console, print_result_table

MAX_RESULT_ROWS = 50
_LEADING_COMMENTS_RE = re.compile(r"^\s*(?:--[^\n]*\n|/\*.*?\*/|\s)*", re.DOTALL)
READ_ONLY_KEYWORDS = {"SELECT", "WITH", "DESCRIBE", "SHOW", "SUMMARIZE", "EXPLAIN"}


def _is_read_only(sql: str) -> tuple[bool, str]:
    cleaned = _LEADING_COMMENTS_RE.sub("", sql).strip()
    if not cleaned:
        return False, "Empty SQL query."
    first_word = cleaned.split(None, 1)[0].upper()
    if first_word not in READ_ONLY_KEYWORDS:
        return False, f"Blocked non-read-only statement starting with '{first_word}'."
    return True, ""


def execute_sql(con, sql: str, history: list[dict], show_table: bool = False) -> dict:
    """Executes a read-only DuckDB query, caches results, and optionally prints the table."""
    allowed, err = _is_read_only(sql)
    if not allowed:
        console.print(f"[red][!] {err}[/red]")
        return {"error": err}

    try:
        cursor = con.execute(sql)
        if not cursor.description:
            return {"row_count": 0, "columns": [], "rows": []}

        columns = [desc[0] for desc in cursor.description]
        raw_rows = cursor.fetchmany(MAX_RESULT_ROWS + 1)
        truncated = len(raw_rows) > MAX_RESULT_ROWS
        capped_rows = raw_rows[:MAX_RESULT_ROWS]

        cell_limit = 1500 if len(capped_rows) <= 5 else 300

        def _clip(val) -> str:
            s = "" if val is None else str(val)
            return s if len(s) <= cell_limit else s[:cell_limit] + "... [truncated]"

        formatted_rows = [tuple(_clip(v) for v in row) for row in capped_rows]
        qid = len(history) + 1
        entry = {
            "id": qid,
            "sql": sql.strip(),
            "columns": columns,
            "rows": formatted_rows,
            "truncated": truncated,
        }
        history.append(entry)

        if show_table:
            print_result_table(entry, MAX_RESULT_ROWS)
        else:
            row_label = f"{len(formatted_rows)}{'+' if truncated else ''} rows"
            console.print(
                f"[green][+] Query #{qid} -> {row_label}[/green] [dim](type /show {qid} to view table)[/dim]"
            )

        return {
            "row_count": len(formatted_rows),
            "truncated": truncated,
            "columns": columns,
            "rows": [dict(zip(columns, row)) for row in formatted_rows],
        }
    except duckdb.Error as e:
        console.print(f"[red][!] DuckDB Error: {e}[/red]")
        return {"error": str(e)}


def _handle_tool(args: dict, ctx: dict) -> dict:
    reason = args.get("reason", "")
    sql = args.get("sql", "")
    cleaned = _LEADING_COMMENTS_RE.sub("", sql).strip().rstrip(";")
    decision = confirm_sql(reason, sql, has_inner_semicolon=(";" in cleaned))

    if decision == "no":
        return {"status": "rejected_by_analyst", "message": "Analyst declined to execute this query."}

    state = ctx["state"]
    show = (decision == "table") or state["auto_tables"]
    return execute_sql(ctx["con"], sql, state["history"], show_table=show)


REGISTER = [
    {
        "schema": {
            "type": "function",
            "function": {
                "name": "run_duckdb_sql",
                "description": (
                    "Execute a read-only DuckDB SQL query against the mounted forensic artifact views. "
                    "The analyst reviews and approves every query before execution."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reason": {
                            "type": "string",
                            "description": "Brief 1-sentence forensic rationale explaining why this query helps.",
                        },
                        "sql": {
                            "type": "string",
                            "description": "Valid read-only DuckDB SQL query (always include LIMIT unless aggregating).",
                        },
                    },
                    "required": ["reason", "sql"],
                },
            },
        },
        "handler": _handle_tool,
    }
]