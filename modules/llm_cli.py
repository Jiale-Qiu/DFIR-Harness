"""
# NVIDIA NIM (paste the exact model ID shown on the NIM page)
$env:OPENAI_BASE_URL="https://integrate.api.nvidia.com/v1"
$env:OPENAI_API_KEY="nvapi-your-key-here"
$env:LLM_MODEL="mistralai/mistral-large-4-1t-a52b"

# Or switch to Groq in the same terminal
$env:OPENAI_BASE_URL="https://api.groq.com/openai/v1"
$env:OPENAI_API_KEY="gsk_your-key-here"
$env:LLM_MODEL="llama-3.3-70b-versatile"
"""

import json
import os
import re
import threading
from pathlib import Path

import duckdb
from openai import OpenAI
from rich.columns import Columns
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.spinner import Spinner
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

console = Console(highlight=False)

VALID_LEVELS = ("low", "medium", "high", "off")
DEFAULT_REASONING = "medium"
MAX_RESULT_ROWS = 50
MAX_TOOL_ROUNDS = 6
MAX_TRIAGE_CHARS = 6000

_LEADING_COMMENTS_RE = re.compile(r"^\s*(?:--[^\n]*\n|/\*.*?\*/|\s)*", re.DOTALL)
READ_ONLY_KEYWORDS = {"SELECT", "WITH", "DESCRIBE", "SHOW", "SUMMARIZE", "EXPLAIN"}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "run_duckdb_sql",
            "description": (
                "Execute a read-only DuckDB SQL query against the mounted forensic artifact views. "
                "The analyst will review and approve every query before it runs."
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
    }
]


class _StreamRenderer:
    """Thread-safe, incremental Live renderer (avoids O(N^2) string joins per token)."""

    def __init__(self, reasoning_level: str):
        self.reasoning_level = reasoning_level
        self.spinner: Spinner | None = Spinner("dots", style="bold cyan")
        self.status_note: str | None = None
        self._lock = threading.Lock()
        self._pending_thinking: list[str] = []
        self._pending_answer: list[str] = []
        self._thinking_text = Text(style="dim italic")
        self._answer_text = Text()
        self._has_thinking = False
        self._has_answer = False

    def add_thinking(self, token: str):
        with self._lock:
            self._pending_thinking.append(token)

    def add_answer(self, token: str):
        with self._lock:
            self._pending_answer.append(token)

    def set_status(self, note: str | None):
        with self._lock:
            self.status_note = note

    def stop_spinner(self):
        with self._lock:
            self.spinner = None
            self.status_note = None

    def __rich__(self):
        with self._lock:
            if self._pending_thinking:
                chunk = "".join(self._pending_thinking)
                self._pending_thinking.clear()
                if not self._has_thinking:
                    chunk = chunk.lstrip()
                if chunk:
                    self._thinking_text.append(chunk)
                    self._has_thinking = True

            if self._pending_answer:
                chunk = "".join(self._pending_answer)
                self._pending_answer.clear()
                self._answer_text.append(chunk)
                self._has_answer = True

            spinner = self.spinner
            status_note = self.status_note
            has_thinking = self._has_thinking
            has_answer = self._has_answer

        prefix = Text.assemble(("AI ", "bold green"), (f"[{self.reasoning_level}]", "cyan"))

        if spinner:
            header_items = [prefix, spinner, Text(">", style="bold green")]
            if status_note:
                header_items.append(Text(status_note, style="dim cyan"))
            elif not has_thinking and not has_answer:
                header_items.append(Text("Thinking...", style="dim"))
            header = Columns(header_items, padding=(0, 1))
        else:
            header = Text.assemble(prefix, (" >", "bold green"))

        if not has_thinking and not has_answer:
            return header

        parts = [header]
        if has_thinking:
            parts.append(Text("[Thinking]", style="dim bold"))
            parts.append(self._thinking_text)
            if has_answer:
                parts.append(Text(""))
        if has_answer:
            parts.append(self._answer_text)

        return Group(*parts)


def _get_schemas(con, mounted_views: set[str]) -> dict[str, dict]:
    """Extracts row counts and column names for each mounted DuckDB view."""
    schemas = {}
    for view in sorted(mounted_views):
        try:
            row_count = con.execute(f'SELECT COUNT(*) FROM "{view}"').fetchone()[0]
            cols = [row[0] for row in con.execute(f'DESCRIBE "{view}"').fetchall()]
            schemas[view] = {"rows": row_count, "columns": cols}
        except duckdb.Error:
            continue
    return schemas


def _build_system_prompt(schemas: dict[str, dict], base_output: Path) -> str:
    """Builds the DFIR system prompt grounded in live DuckDB schemas and optional triage output."""
    schema_lines = [
        f"- `{view}` ({info['rows']:,} rows): {', '.join(info['columns'])}"
        for view, info in schemas.items()
    ]

    triage_block = ""
    summary_path = Path(base_output) / "triage_summary.json"
    if summary_path.exists():
        try:
            raw = summary_path.read_text(encoding="utf-8").strip()
            if raw and raw != "{}":
                if len(raw) > MAX_TRIAGE_CHARS:
                    raw = raw[:MAX_TRIAGE_CHARS] + "\n... (truncated)"
                triage_block = f"\n\n### Initial Triage Summary (already executed)\n```json\n{raw}\n```"
        except OSError:
            pass

    return (
        "You are an expert Windows DFIR analyst assisting a human investigator via DuckDB.\n"
        "Use the `run_duckdb_sql` tool whenever evidence from the case artifacts is needed.\n\n"
        "### Mounted DuckDB Views & Columns\n"
        + "\n".join(schema_lines)
        + "\n\n### Critical DuckDB Query Rules\n"
        "1. Every view was mounted with `all_varchar=true` — ALL columns are VARCHAR strings.\n"
        "   - Compare IDs as strings (e.g., `EventId = '4624'`).\n"
        "   - Use `TRY_CAST(col AS BIGINT)` for numeric sorting/filtering.\n"
        "   - Use `ILIKE` for case-insensitive Windows path/filename matching.\n"
        "2. Always include `LIMIT 25` or `LIMIT 50` on non-aggregated queries.\n"
        "3. Use ONLY exact column names listed above. Never invent columns or views.\n"
        "4. Account for forensic collection noise (e.g., KAPE, RECmd, PECmd, EvtxECmd artifacts) and filter it out when analyzing.\n"
        "5. If the analyst declines a query, respect the decision and ask how they want to proceed."
        + triage_block
    )


def _is_read_only(sql: str) -> tuple[bool, str]:
    """Verifies the SQL statement begins with a read-only DuckDB keyword."""
    cleaned = _LEADING_COMMENTS_RE.sub("", sql).strip()
    if not cleaned:
        return False, "Empty SQL query."
    first_word = cleaned.split(None, 1)[0].upper()
    if first_word not in READ_ONLY_KEYWORDS:
        return False, f"Blocked non-read-only statement starting with '{first_word}'."
    return True, ""


def _render_and_pack_results(columns: list[str], rows: list[tuple], truncated: bool) -> dict:
    """Prints a Rich table to the terminal and returns a compact JSON-serializable payload."""
    # Allow larger cell previews when returning a small deep-dive result set
    cell_limit = 1500 if len(rows) <= 5 else 300

    def _clip(val) -> str:
        s = "" if val is None else str(val)
        return s if len(s) <= cell_limit else s[:cell_limit] + "... [truncated]"

    if rows:
        title = f"Query Results ({len(rows)}{'+' if truncated else ''} rows)"
        table = Table(title=title)
        for col in columns:
            table.add_column(str(col), overflow="fold")
        for row in rows:
            table.add_row(*(_clip(v) for v in row))
        console.print()
        console.print(table)
        if truncated:
            console.print(
                f"[yellow][*] Output capped at {MAX_RESULT_ROWS} rows. Refine WHERE/LIMIT to narrow results.[/yellow]"
            )
    else:
        console.print("[dim][-] Query returned 0 rows.[/dim]")

    return {
        "row_count": len(rows),
        "truncated": truncated,
        "columns": columns,
        "rows": [dict(zip(columns, (_clip(v) for v in row))) for row in rows],
    }


def _execute_sql(con, sql: str) -> dict:
    """Safely executes a read-only DuckDB query with capped row fetching."""
    allowed, err = _is_read_only(sql)
    if not allowed:
        console.print(f"[red][!] {err}[/red]")
        return {"error": err}

    try:
        cursor = con.execute(sql)
        if not cursor.description:
            return {"row_count": 0, "columns": [], "rows": []}
        columns = [desc[0] for desc in cursor.description]
        # Fetch MAX_RESULT_ROWS + 1 instead of fetchall() so unbounded MFT/EVTX queries stay O(1) RAM
        raw_rows = cursor.fetchmany(MAX_RESULT_ROWS + 1)
        truncated = len(raw_rows) > MAX_RESULT_ROWS
        return _render_and_pack_results(columns, raw_rows[:MAX_RESULT_ROWS], truncated)
    except duckdb.Error as e:
        console.print(f"[red][!] DuckDB Error: {e}[/red]")
        return {"error": str(e)}


def _approve_tool_call(reason: str, sql: str) -> bool:
    """Displays the proposed SQL query and waits for explicit HITL confirmation."""
    body = Group(
        Text.assemble(("Reason: ", "bold yellow"), (reason or "Not provided", "")),
        Text(""),
        Syntax(sql.strip(), "sql", theme="monokai", word_wrap=True),
    )
    console.print()
    console.print(Panel(body, title="[bold cyan]Proposed DuckDB Query[/bold cyan]", border_style="cyan"))

    cleaned = _LEADING_COMMENTS_RE.sub("", sql).strip().rstrip(";")
    if ";" in cleaned:
        console.print("[yellow][!] Note: Query contains an internal semicolon — inspect carefully.[/yellow]")

    while True:
        try:
            choice = input("Execute query? [Y]es / [n]o: ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[yellow][-] Query declined.[/yellow]")
            return False

        if choice in ("", "y", "yes"):
            return True
        if choice in ("n", "no"):
            console.print("[yellow][-] Query declined.[/yellow]")
            return False
        console.print("[red][!] Invalid choice. Press Enter for Yes, or enter n.[/red]")


def _stream_completion(client: OpenAI, model: str, messages: list[dict], reasoning_level: str):
    """Streams one LLM turn, returning (reply_text, tool_calls_list, finish_reason)."""
    request_kwargs = {
        "model": model,
        "messages": messages,
        "tools": TOOLS,
        "tool_choice": "auto",
        "temperature": 0.3,
        "max_tokens": 4096,
        "stream": True,
    }
    if reasoning_level != "off":
        request_kwargs["extra_body"] = {"reasoning_effort": reasoning_level}

    renderer = _StreamRenderer(reasoning_level)
    answer_chunks: list[str] = []
    tool_calls_acc: dict[int, dict] = {}
    finish_reason = None

    console.print()
    with Live(renderer, console=console, refresh_per_second=12, vertical_overflow="visible") as live:
        stream = client.chat.completions.create(**request_kwargs)
        for chunk in stream:
            if not chunk.choices:
                continue

            choice = chunk.choices[0]
            if choice.finish_reason:
                finish_reason = choice.finish_reason

            delta = choice.delta

            reasoning_token = getattr(delta, "reasoning_content", None)
            if reasoning_token:
                renderer.add_thinking(reasoning_token)

            if delta.content:
                answer_chunks.append(delta.content)
                renderer.add_answer(delta.content)

            if delta.tool_calls:
                renderer.set_status("Drafting SQL query...")
                for tc in delta.tool_calls:
                    entry = tool_calls_acc.setdefault(
                        tc.index,
                        {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
                    )
                    if tc.id:
                        entry["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            entry["function"]["name"] += tc.function.name
                        if tc.function.arguments:
                            entry["function"]["arguments"] += tc.function.arguments

        renderer.stop_spinner()
        live.refresh()

    print()
    tool_calls = [tool_calls_acc[i] for i in sorted(tool_calls_acc)]
    return "".join(answer_chunks), tool_calls, finish_reason


def _handle_slash_command(
    user_input: str,
    state: dict,
    con,
    schemas: dict[str, dict],
    messages: list[dict],
) -> bool:
    """Handles local CLI commands. Returns True if input was consumed locally."""
    cmd, _, arg = user_input.partition(" ")
    cmd_lower = cmd.lower()
    arg = arg.strip()

    if cmd_lower == "/reasoning":
        if arg.lower() in VALID_LEVELS:
            state["reasoning"] = arg.lower()
            console.print(f"[cyan][*] Reasoning level set to: {state['reasoning']}[/cyan]\n")
        else:
            console.print(
                f"[yellow][!] Current: {state['reasoning']} | Usage: /reasoning <low|medium|high|off>[/yellow]\n"
            )
        return True

    if cmd_lower == "/sql":
        if not arg:
            console.print("[yellow][!] Usage: /sql SELECT * FROM evtx LIMIT 5[/yellow]\n")
        else:
            _execute_sql(con, arg)
            print()
        return True

    if cmd_lower == "/schema":
        table = Table(title="Mounted DuckDB Schemas")
        table.add_column("View", style="bold cyan")
        table.add_column("Rows", justify="right")
        table.add_column("Columns", overflow="fold")
        for view, info in schemas.items():
            table.add_row(view, f"{info['rows']:,}", ", ".join(info["columns"]))
        console.print(table)
        print()
        return True

    if cmd_lower == "/clear":
        del messages[1:]
        console.print("[cyan][*] Conversation history cleared (schemas retained).[/cyan]\n")
        return True

    return False


def start_cli(con, mounted_views: set[str], base_output: Path):
    """Interactive DFIR investigation CLI with DuckDB schema grounding and HITL SQL execution."""
    api_key = os.environ.get("OPENAI_API_KEY")
    model = os.environ.get("LLM_MODEL")

    if not api_key or not model:
        console.print("[red][!] Missing config. Set OPENAI_API_KEY, OPENAI_BASE_URL, and LLM_MODEL.[/red]")
        return

    schemas = _get_schemas(con, mounted_views)
    if not schemas:
        console.print("[red][!] No queryable DuckDB schemas found.[/red]")
        return

    client = OpenAI()
    state = {"reasoning": DEFAULT_REASONING}
    messages = [{"role": "system", "content": _build_system_prompt(schemas, base_output)}]

    console.print("\n[bold cyan]=== LLM Investigation CLI ===[/bold cyan]")
    console.print(f"[dim]Model: {model} | Views: {', '.join(sorted(schemas))}[/dim]")
    console.print("[dim]Commands: /reasoning <low|medium|high|off>, /sql <query>, /schema, /clear, exit[/dim]\n")

    while True:
        try:
            user_input = input("You > ").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[yellow][-] Exiting LLM CLI.[/yellow]")
            break

        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit", "/q"):
            console.print("[yellow][-] Exiting LLM CLI.[/yellow]")
            break
        if user_input.startswith("/") and _handle_slash_command(user_input, state, con, schemas, messages):
            continue

        turn_start_idx = len(messages)
        messages.append({"role": "user", "content": user_input})

        try:
            for _ in range(MAX_TOOL_ROUNDS):
                reply, tool_calls, finish_reason = _stream_completion(
                    client, model, messages, state["reasoning"]
                )

                if finish_reason == "length":
                    console.print("[yellow][!] Response hit max_tokens (4096) and was cut short.[/yellow]\n")

                if not tool_calls:
                    if reply:
                        messages.append({"role": "assistant", "content": reply})
                    else:
                        del messages[turn_start_idx:]
                    break

                # Record the assistant's tool call request before executing tools
                messages.append({"role": "assistant", "content": reply or "", "tool_calls": tool_calls})

                for tc in tool_calls:
                    fn_name = tc["function"]["name"]
                    raw_args = tc["function"]["arguments"]

                    if fn_name != "run_duckdb_sql":
                        result = {"error": f"Unknown tool: {fn_name}"}
                    else:
                        try:
                            args = json.loads(raw_args)
                            reason = args.get("reason", "")
                            sql = args.get("sql", "")
                        except json.JSONDecodeError as e:
                            result = {"error": f"Malformed tool JSON: {e}"}
                        else:
                            if _approve_tool_call(reason, sql):
                                result = _execute_sql(con, sql)
                            else:
                                result = {
                                    "status": "rejected_by_analyst",
                                    "message": "Analyst declined to execute this query.",
                                }

                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": json.dumps(result, ensure_ascii=False),
                        }
                    )
            else:
                console.print(f"[yellow][!] Reached {MAX_TOOL_ROUNDS} consecutive query rounds. Stopping turn.[/yellow]\n")

        except KeyboardInterrupt:
            console.print("\n[yellow][-] Generation interrupted.[/yellow]\n")
            del messages[turn_start_idx:]
        except Exception as e:
            console.print(f"\n[red][!] API Error: {e}[/red]\n")
            del messages[turn_start_idx:]