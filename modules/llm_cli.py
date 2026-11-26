"""
# NVIDIA NIM
$env:OPENAI_BASE_URL="[https://integrate.api.nvidia.com/v1](https://integrate.api.nvidia.com/v1)"
$env:OPENAI_API_KEY="nvapi-your-key-here"
$env:LLM_MODEL="mistralai/mistral-large-4-1t-a52b"

# Or switch to Groq in the same terminal
$env:OPENAI_BASE_URL="[https://api.groq.com/openai/v1](https://api.groq.com/openai/v1)"
$env:OPENAI_API_KEY="gsk_your-key-here"
$env:LLM_MODEL="llama-3.3-70b-versatile"
"""

import json
import os
from pathlib import Path

import duckdb
from openai import OpenAI
from rich.table import Table

from modules.llm_ui import StreamRenderer, console, print_help, print_result_table
from modules.pane_bridge import (
    clear_result_pane,
    close_result_pane,
    get_session_state_path,
    is_pane_alive,
    open_result_pane,
    publish_result,
)
from modules.tools import TOOL_SCHEMAS, execute_tool, list_tools
from modules.tools.duckdb_sql import execute_sql

VALID_LEVELS = ("low", "medium", "high", "off")
VALID_THINKING_MODES = ("collapse", "full", "off")
DEFAULT_REASONING = "medium"
DEFAULT_THINKING_MODE = "collapse"
MAX_TOOL_ROUNDS = 6
MAX_TRIAGE_CHARS = 6000


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


def _list_case_csvs(base_output: Path) -> list[str]:
    """Lists relative CSV paths in base_output so the LLM knows exact filenames for GUI handoff."""
    csv_lines = []
    for p in sorted(base_output.rglob("*.csv")):
        if p.stat().st_size > 0:
            size_mb = p.stat().st_size / (1024 * 1024)
            csv_lines.append(f"- `{p.relative_to(base_output).as_posix()}` ({size_mb:.2f} MB)")
    return csv_lines


def _build_system_prompt(schemas: dict[str, dict], base_output: Path) -> str:
    """Builds the DFIR system prompt grounded in live DuckDB schemas and optional triage output."""
    schema_lines = [
        f"- `{view}` ({info['rows']:,} rows): {', '.join(info['columns'])}"
        for view, info in schemas.items()
    ]
    csv_lines = _list_case_csvs(base_output)

    triage_block = ""
    summary_path = base_output / "triage_summary.json"
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
        "You are an expert Windows DFIR analyst assisting a human investigator.\n"
        "Use your registered tools to query DuckDB artifacts, decode obfuscated payloads, calculate offsets, "
        "or open Timeline Explorer when the analyst wants to inspect raw CSVs visually.\n\n"
        "### Mounted DuckDB Views & Columns\n"
        + "\n".join(schema_lines)
        + "\n\n### Parsed CSV Files on Disk\n"
        + ("\n".join(csv_lines) if csv_lines else "- (none)")
        + "\n\n### Critical DuckDB Query & Tool Rules\n"
        "1. Every view was mounted with `all_varchar=true` — ALL columns are VARCHAR strings.\n"
        "   - Compare IDs as strings (e.g., `EventId = '4624'`).\n"
        "   - Use `TRY_CAST(col AS BIGINT)` for numeric sorting/filtering.\n"
        "   - Use `ILIKE` for case-insensitive Windows path/filename matching.\n"
        "2. Always include `LIMIT 50` or `LIMIT 100` on non-aggregated queries (output caps at 100 rows).\n"
        "3. Use ONLY exact column names listed above. Never invent columns or views.\n"
        "4. Filter out forensic collection noise (KAPE, RECmd, PECmd, EvtxECmd artifacts) when analyzing.\n"
        "5. If the analyst declines a tool action, respect the decision and ask how they want to proceed.\n"
        f"6. You have a strict budget of {MAX_TOOL_ROUNDS} consecutive tool rounds per user prompt. "
        "Batch independent queries in parallel in a single turn when possible, and reserve your final round "
        "to synthesize findings rather than issuing another query."
        + triage_block
    )


def _stream_completion(
    client: OpenAI,
    model: str,
    messages: list[dict],
    state: dict,
    allow_tools: bool = True,
):
    """Streams one LLM turn, returning (reply_text, tool_calls_list, finish_reason)."""
    request_kwargs = {
        "model": model,
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": 4096,
        "stream": True,
    }
    if TOOL_SCHEMAS:
        request_kwargs["tools"] = TOOL_SCHEMAS
        request_kwargs["tool_choice"] = "auto" if allow_tools else "none"
    if state["reasoning"] != "off":
        request_kwargs["extra_body"] = {"reasoning_effort": state["reasoning"]}

    answer_chunks: list[str] = []
    tool_calls_acc: dict[int, dict] = {}
    finish_reason = None

    console.print()
    with StreamRenderer(state["reasoning"], state["thinking_mode"]) as renderer:
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
                            renderer.set_status(f"Preparing tool: {entry['function']['name']}...")
                        if tc.function.arguments:
                            entry["function"]["arguments"] += tc.function.arguments

    print()
    tool_calls = [tool_calls_acc[i] for i in sorted(tool_calls_acc)]
    return "".join(answer_chunks), tool_calls, finish_reason


def _handle_slash_command(user_input: str, ctx: dict, schemas: dict, messages: list[dict]) -> bool:
    """Handles local CLI commands. Returns True if input was consumed locally."""
    cmd, _, arg = user_input.partition(" ")
    cmd_lower = cmd.lower()
    arg = arg.strip()
    state = ctx["state"]
    pane_path = state["pane_path"]

    if cmd_lower in ("/help", "/?"):
        print_help()
        return True

    if cmd_lower == "/pane":
        if arg.lower() == "off":
            close_result_pane(pane_path)
            console.print("[cyan][*] Closed query viewer pane and switched back to inline mode.[/cyan]\n")
            return True

        latest = state["history"][-1] if state["history"] else None
        status = open_result_pane(pane_path, initial_entry=latest)
        if status == "opened":
            console.print("[cyan][*] Opened right-side query viewer pane (type '/pane off' to close).[/cyan]\n")
        elif status == "already_open":
            console.print("[cyan][*] Query viewer pane is already open.[/cyan]\n")
        else:
            console.print("[yellow][!] /pane requires Windows Terminal.[/yellow]\n")
        return True

    if cmd_lower == "/reasoning":
        if arg.lower() in VALID_LEVELS:
            state["reasoning"] = arg.lower()
            console.print(f"[cyan][*] Reasoning level set to: {state['reasoning']}[/cyan]\n")
        else:
            console.print(f"[yellow][!] Current: {state['reasoning']} | Usage: /reasoning <low|medium|high|off>[/yellow]\n")
        return True

    if cmd_lower == "/thinking":
        if arg.lower() in VALID_THINKING_MODES:
            state["thinking_mode"] = arg.lower()
            console.print(f"[cyan][*] Thinking display set to: {state['thinking_mode']}[/cyan]\n")
        else:
            console.print(f"[yellow][!] Current: {state['thinking_mode']} | Usage: /thinking <collapse|full|off>[/yellow]\n")
        return True

    if cmd_lower == "/show":
        history = state["history"]
        if not history:
            console.print("[yellow][!] No queries executed yet in this session.[/yellow]\n")
            return True
        idx = len(history) - 1
        if arg:
            if not arg.isdigit() or not (1 <= int(arg) <= len(history)):
                console.print(f"[yellow][!] Valid query range: 1..{len(history)}[/yellow]\n")
                return True
            idx = int(arg) - 1

        entry = history[idx]
        if is_pane_alive(pane_path):
            publish_result(pane_path, entry)
            qid = entry.get("id") or (idx + 1)
            console.print(f"[cyan][*] Displayed Query #{qid} in right pane.[/cyan]\n")
        else:
            print_result_table(entry)
            print()
        return True

    if cmd_lower == "/sql":
        if not arg:
            console.print("[yellow][!] Usage: /sql SELECT * FROM evtx LIMIT 5[/yellow]\n")
        else:
            pane_active = is_pane_alive(pane_path)
            prev_len = len(state["history"])
            execute_sql(ctx["con"], arg, state["history"], show_table=not pane_active)
            if pane_active and len(state["history"]) > prev_len:
                publish_result(pane_path, state["history"][-1])
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

    if cmd_lower == "/tools":
        console.print(f"[cyan][*] Loaded AI tools:[/cyan] {', '.join(list_tools())}\n")
        return True

    if cmd_lower == "/clear":
        del messages[1:]
        state["history"].clear()
        clear_result_pane(pane_path)
        console.print("[cyan][*] Conversation and query cache cleared.[/cyan]\n")
        return True

    return False


def start_cli(con, mounted_views: set[str], base_output: Path):
    """Interactive DFIR investigation CLI with DuckDB schema grounding and HITL tool execution."""
    api_key = os.environ.get("OPENAI_API_KEY")
    model = os.environ.get("LLM_MODEL")

    if not api_key or not model:
        console.print("[red][!] Missing config. Set OPENAI_API_KEY, OPENAI_BASE_URL, and LLM_MODEL.[/red]")
        return

    schemas = _get_schemas(con, mounted_views)
    if not schemas:
        console.print("[red][!] No queryable DuckDB schemas found.[/red]")
        return

    active_tools = list_tools()
    client = OpenAI()
    pane_path = get_session_state_path()
    ctx = {
        "con": con,
        "mounted_views": mounted_views,
        "base_output": Path(base_output),
        "state": {
            "reasoning": DEFAULT_REASONING,
            "thinking_mode": DEFAULT_THINKING_MODE,
            "history": [],
            "pane_path": pane_path,
        },
    }
    messages = [{"role": "system", "content": _build_system_prompt(schemas, Path(base_output))}]

    console.print("\n[bold cyan]=== LLM Investigation CLI ===[/bold cyan]")
    console.print(
        f"[dim]Model: {model} | Views: {', '.join(sorted(schemas))} | "
        f"Tools: {len(active_tools)} loaded | Type /pane for split viewer or /help for commands[/dim]\n"
    )

    try:
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
            if user_input.startswith("/") and _handle_slash_command(user_input, ctx, schemas, messages):
                continue

            turn_start_idx = len(messages)
            messages.append({"role": "user", "content": user_input})

            try:
                for round_idx in range(MAX_TOOL_ROUNDS):
                    # Reserve the final round for synthesis so the turn never ends without a summary
                    allow_tools = round_idx < (MAX_TOOL_ROUNDS - 1)
                    reply, tool_calls, finish_reason = _stream_completion(
                        client, model, messages, ctx["state"], allow_tools=allow_tools
                    )

                    if finish_reason == "length":
                        console.print("[yellow][!] Response hit max_tokens (4096) and was cut short.[/yellow]\n")

                    if not tool_calls:
                        if reply:
                            messages.append({"role": "assistant", "content": reply})
                        else:
                            del messages[turn_start_idx:]
                        break

                    messages.append({"role": "assistant", "content": reply or "", "tool_calls": tool_calls})

                    for tc in tool_calls:
                        prev_len = len(ctx["state"]["history"])
                        result = execute_tool(tc["function"]["name"], tc["function"]["arguments"], ctx)
                        if len(ctx["state"]["history"]) > prev_len:
                            publish_result(pane_path, ctx["state"]["history"][-1])
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc["id"],
                                "content": json.dumps(result, ensure_ascii=False),
                            }
                        )
                else:
                    console.print(f"[yellow][!] Reached {MAX_TOOL_ROUNDS} consecutive tool rounds. Stopping turn.[/yellow]\n")

            except KeyboardInterrupt:
                console.print("\n[yellow][-] Generation interrupted.[/yellow]\n")
                del messages[turn_start_idx:]
            except Exception as e:
                console.print(f"\n[red][!] API Error: {e}[/red]\n")
                del messages[turn_start_idx:]
    finally:
        close_result_pane(pane_path)