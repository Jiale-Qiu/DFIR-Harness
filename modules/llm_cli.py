"""Interactive DFIR investigation CLI powered by OpenAI-compatible APIs and DuckDB."""

import json
import os
from pathlib import Path

import duckdb
from openai import OpenAI
from rich.table import Table

from modules.common import format_bytes
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
MAX_TOOL_ROUNDS = 10
MAX_TOKENS = 16384


def _get_schemas(con, mounted_views: set[str]) -> dict[str, dict]:
    """Extracts row counts and column names for each mounted DuckDB view."""
    schemas: dict[str, dict] = {}
    for view in sorted(mounted_views):
        try:
            row = con.execute(f'SELECT COUNT(*) FROM "{view}"').fetchone()
            row_count = row[0] if row else 0
            cols = [desc[0] for desc in con.execute(f'DESCRIBE "{view}"').fetchall()]
            schemas[view] = {"rows": row_count, "columns": cols}
        except duckdb.Error:
            continue
    return schemas


def _build_directory_tree(root_dir: Path, max_entries: int = 120, max_depth: int = 4) -> str:
    """Builds a bounded indented tree of all files/folders in root_dir."""
    lines: list[str] = [f"{root_dir.name}/"]
    count = 0
    truncated = False

    def _walk(directory: Path, depth: int) -> None:
        nonlocal count, truncated
        if depth > max_depth or truncated:
            return
        try:
            entries = sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError:
            return

        indent = "  " * depth
        for entry in entries:
            if count >= max_entries:
                truncated = True
                return
            count += 1
            if entry.is_dir():
                lines.append(f"{indent}{entry.name}/")
                if depth == max_depth:
                    lines.append(f"{indent}  … (use list_directory to inspect deeper)")
                else:
                    _walk(entry, depth + 1)
            else:
                try:
                    sz = format_bytes(entry.stat().st_size)
                except OSError:
                    sz = "?"
                lines.append(f"{indent}{entry.name} ({sz})")

    _walk(root_dir, 1)
    if truncated:
        lines.append(f"... (tree capped at {max_entries} entries — use list_directory for specific folders)")
    return "\n".join(lines)


def _build_system_prompt(
    schemas: dict[str, dict],
    base_output: Path,
    main_directory: Path | None = None,
) -> str:
    """Builds the DFIR system prompt grounded in live DuckDB schemas, file trees, and triage output."""
    schema_lines = [
        f"- `{view}` ({info['rows']:,} rows): {', '.join(info['columns'])}"
        for view, info in schemas.items()
    ]
    base_resolved = base_output.resolve()
    output_tree = _build_directory_tree(base_resolved)

    source_section = ""
    if main_directory is not None:
        main_resolved = main_directory.resolve()
        if main_resolved == base_resolved:
            source_section = (
                f'### Source Investigation Directory (`root="source"`)\n'
                f"Same as the parsed output directory (`{main_resolved}`).\n\n"
            )
        elif main_resolved.is_dir():
            source_tree = _build_directory_tree(main_resolved)
            source_section = (
                f'### Source Investigation Directory Tree (`root="source"`, {main_resolved})\n'
                f"Raw/unparsed case artifacts in the selected investigation directory:\n```\n"
                f"{source_tree}\n```\n\n"
            )

    return (
        "You are an expert Windows DFIR analyst assisting a human investigator.\n"
        "Use your registered tools to query DuckDB artifacts, inspect case files, decode obfuscated payloads, "
        "or open Timeline Explorer when the analyst wants to inspect raw CSVs visually.\n\n"
        "### Mounted DuckDB Views & Columns\n"
        + "\n".join(schema_lines)
        + "\n\n"
        + source_section
        + f'### Parsed Output Directory Tree (`root="output"`, {base_resolved})\n```\n'
        + output_tree
        + "\n```\n\n### Critical Tool & Query Rules\n"
        "1. Every DuckDB view was mounted with `all_varchar=true` — ALL columns are VARCHAR strings.\n"
        "   - Compare IDs as strings (e.g., `EventId = '4624'`).\n"
        "   - Use `TRY_CAST(col AS BIGINT)` for numeric sorting/filtering.\n"
        "   - Use `ILIKE` for case-insensitive Windows path/filename matching.\n"
        "2. Always include `LIMIT 25` or `LIMIT 50` on non-aggregated queries (output caps at 50 rows).\n"
        "3. Use ONLY exact column names listed above. Never invent columns or views.\n"
        "4. Filter out forensic collection noise (KAPE, RECmd, PECmd, EvtxECmd artifacts) when analyzing.\n"
        "5. File Inspection (`read_file` / `list_directory`): Use `read_file` to inspect logs, scripts, JSON, "
        'or unmounted text artifacts in either the source investigation directory (`root="source"`) or the '
        'parsed output directory (`root="output"`). Prefer specifying `root` explicitly when a folder name '
        "exists in both trees. For large files, ALWAYS use `search` or small `start_line`/`max_lines` "
        "slices rather than trying to read whole files. Never use `read_file` on large CSVs that are already "
        "mounted in DuckDB.\n"
        "6. Terminal Execution (`run_terminal`): Prefer `query_duckdb` for mounted views and `read_file` for "
        "simple text/hex slices, but use `run_terminal` when CLI tools are more effective (e.g., file hashing, "
        "`strings`, multi-file regex search, SQLite/archive/PE inspection, or unparsed artifacts). Set `root` "
        '(`"output"` or `"source"`) appropriately and keep commands read-only and output-bounded.\n'
        "7. If the analyst declines a tool action, respect the decision and ask how they want to proceed.\n"
        f"8. You have a strict budget of {MAX_TOOL_ROUNDS} consecutive tool rounds per user prompt. "
        "Batch independent queries in parallel in a single turn when possible, and reserve your final round "
        "to synthesize findings rather than issuing another query."
    )


def _stream_completion(
    client: OpenAI,
    model: str,
    messages: list[dict],
    state: dict,
    allow_tools: bool = True,
    reasoning_override: str | None = None,
) -> tuple[str, str, list[dict], str | None]:
    """Streams one LLM turn, returning (reply_text, reasoning_text, tool_calls_list, finish_reason)."""
    effective_reasoning = reasoning_override if reasoning_override is not None else state["reasoning"]
    request_kwargs: dict = {
        "model": model,
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": MAX_TOKENS,
        "stream": True,
    }
    if TOOL_SCHEMAS and allow_tools:
        request_kwargs["tools"] = TOOL_SCHEMAS
        request_kwargs["tool_choice"] = "auto"
    if effective_reasoning != "off":
        request_kwargs["extra_body"] = {"reasoning_effort": effective_reasoning}

    answer_chunks: list[str] = []
    reasoning_chunks: list[str] = []
    tool_calls_acc: dict[int, dict] = {}
    finish_reason = None

    console.print()
    with StreamRenderer(effective_reasoning, state["thinking_mode"]) as renderer:
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
                reasoning_chunks.append(reasoning_token)
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
    return "".join(answer_chunks), "".join(reasoning_chunks), tool_calls, finish_reason


def _set_enum_option(state: dict, key: str, label: str, arg: str, valid: tuple[str, ...]) -> None:
    val = arg.lower()
    if val in valid:
        state[key] = val
        console.print(f"[cyan][*] {label} set to: {val}[/cyan]\n")
    else:
        console.print(f"[yellow][!] Current: {state[key]} | Usage: /{key.replace('_mode', '')} <{'|'.join(valid)}>[/yellow]\n")


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
        msgs = {
            "opened": "[cyan][*] Opened right-side query viewer pane (type '/pane off' to close).[/cyan]\n",
            "already_open": "[cyan][*] Query viewer pane is already open.[/cyan]\n",
        }
        console.print(msgs.get(status, "[yellow][!] /pane requires Windows Terminal.[/yellow]\n"))
        return True

    if cmd_lower == "/autosql":
        arg_lower = arg.lower()
        toggle_map = {"on": True, "true": True, "1": True, "off": False, "false": False, "0": False}
        if not arg_lower:
            state["auto_sql"] = not state["auto_sql"]
        elif arg_lower in toggle_map:
            state["auto_sql"] = toggle_map[arg_lower]
        else:
            console.print("[yellow][!] Usage: /autosql [on|off][/yellow]\n")
            return True

        status = (
            "ON (validated read-only SQL auto-runs; terminal commands still require approval)"
            if state["auto_sql"]
            else "OFF (manual approval required)"
        )
        style = "yellow" if state["auto_sql"] else "cyan"
        console.print(f"[{style}][*] Auto-accept SQL: {status}[/{style}]\n")
        return True

    if cmd_lower == "/reasoning":
        _set_enum_option(state, "reasoning", "Reasoning level", arg, VALID_LEVELS)
        return True

    if cmd_lower == "/thinking":
        _set_enum_option(state, "thinking_mode", "Thinking display", arg, VALID_THINKING_MODES)
        return True

    if cmd_lower == "/show":
        history = state["history"]
        if not history:
            console.print("[yellow][!] No queries executed yet in this session.[/yellow]\n")
            return True
        idx = len(history) - 1
        if arg:
            try:
                idx = int(arg) - 1
            except ValueError:
                idx = -1
            if not (0 <= idx < len(history)):
                console.print(f"[yellow][!] Valid query range: 1..{len(history)}[/yellow]\n")
                return True

        entry = history[idx]
        if is_pane_alive(pane_path):
            publish_result(pane_path, entry)
            console.print(f"[cyan][*] Displayed Query #{entry.get('id') or (idx + 1)} in right pane.[/cyan]\n")
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


def _build_retry_messages(messages: list[dict], last_reasoning: str) -> list[dict]:
    """Appends a synthesis-only nudge when a model emits reasoning without a final answer."""
    nudge = (
        "Tool calling is disabled. Do not output chain-of-thought reasoning or "
        "attempt tool calls. Write the final DFIR response for the investigator "
        "now based on the evidence gathered so far."
    )
    trimmed = last_reasoning.strip()[-6000:]
    if trimmed:
        nudge += (
            "\n\nSynthesize your prior analysis below into a clear final answer:\n"
            f"<prior_analysis>\n{trimmed}\n</prior_analysis>"
        )
    return [*messages, {"role": "user", "content": nudge}]


def _run_agent_turn(client: OpenAI, model: str, messages: list[dict], ctx: dict, turn_start_idx: int) -> None:
    """Executes up to MAX_TOOL_ROUNDS of LLM calls and tool dispatches for one user prompt."""
    state = ctx["state"]
    pane_path = state["pane_path"]
    empty_retries = 0
    last_reasoning = ""
    round_idx = 0

    while round_idx < MAX_TOOL_ROUNDS:
        # Reserve the final round (and empty-reply retries) for synthesis
        allow_tools = (round_idx < (MAX_TOOL_ROUNDS - 1)) and (empty_retries == 0)
        call_messages = _build_retry_messages(messages, last_reasoning) if empty_retries > 0 else messages

        reply, reasoning, tool_calls, finish_reason = _stream_completion(
            client,
            model,
            call_messages,
            state,
            allow_tools=allow_tools,
            reasoning_override="off" if empty_retries > 0 else None,
        )

        if finish_reason == "length":
            console.print(f"[yellow][!] Response hit max_tokens ({MAX_TOKENS}) and was cut short.[/yellow]\n")

        if not tool_calls:
            if reply.strip():
                messages.append({"role": "assistant", "content": reply})
                return

            if empty_retries < 1:
                empty_retries += 1
                last_reasoning = reasoning
                console.print(
                    f"[yellow][*] Model returned no final answer "
                    f"(finish_reason={finish_reason or 'unknown'}) — retrying synthesis...[/yellow]"
                )
                continue

            console.print(
                f"[yellow][!] Model still returned no final answer "
                f"(finish_reason={finish_reason or 'unknown'}).[/yellow]"
            )
            fallback = reasoning.strip() or last_reasoning.strip()
            if fallback and state["thinking_mode"] != "full":
                console.print("[dim]--- Captured reasoning (no final reply emitted) ---[/dim]")
                console.print(fallback, style="dim")
            print()

            if round_idx > 0:
                # Keep executed tool results in context with a valid assistant turn
                messages.append({"role": "assistant", "content": fallback or "[No final summary returned]"})
            else:
                del messages[turn_start_idx:]
            return

        empty_retries = 0
        last_reasoning = ""
        messages.append({"role": "assistant", "content": reply or "", "tool_calls": tool_calls})

        for tc in tool_calls:
            prev_len = len(state["history"])
            result = execute_tool(tc["function"]["name"], tc["function"]["arguments"], ctx)
            if len(state["history"]) > prev_len:
                publish_result(pane_path, state["history"][-1])
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )
        round_idx += 1

    console.print(f"[yellow][!] Reached {MAX_TOOL_ROUNDS} consecutive tool rounds. Stopping turn.[/yellow]\n")


def start_cli(con, mounted_views: set[str], base_output: Path, main_directory: Path | None = None) -> None:
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
    base_path = Path(base_output)
    main_path = Path(main_directory) if main_directory is not None else None
    ctx = {
        "con": con,
        "mounted_views": mounted_views,
        "base_output": base_path,
        "main_directory": main_path,
        "state": {
            "reasoning": DEFAULT_REASONING,
            "thinking_mode": DEFAULT_THINKING_MODE,
            "auto_sql": False,
            "history": [],
            "pane_path": pane_path,
        },
    }
    messages = [{"role": "system", "content": _build_system_prompt(schemas, base_path, main_path)}]

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
                _run_agent_turn(client, model, messages, ctx, turn_start_idx)
            except KeyboardInterrupt:
                console.print("\n[yellow][-] Generation interrupted.[/yellow]\n")
                del messages[turn_start_idx:]
            except Exception as e:
                console.print(f"\n[red][!] API Error: {e}[/red]\n")
                del messages[turn_start_idx:]
    finally:
        close_result_pane(pane_path)
