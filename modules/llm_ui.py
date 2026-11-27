from dataclasses import dataclass
import os
import threading

from rich import box
from rich.columns import Columns
from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.spinner import Spinner
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

console = Console(highlight=False)


@dataclass(frozen=True)
class ConfirmDecision:
    """Result of an analyst HITL prompt; truthy when approved, falsy when declined."""

    approved: bool
    choice: str
    feedback: str = ""

    def __bool__(self) -> bool:
        return self.approved


def declined_payload(default_message: str, feedback: str = "") -> dict[str, str]:
    """Builds a standardized tool response so the LLM pivots on analyst feedback."""
    if not feedback:
        return {"status": "declined", "message": default_message}
    return {
        "status": "declined",
        "analyst_feedback": feedback,
        "message": (
            f"{default_message} Analyst guidance: {feedback} "
            "Follow this direction instead of repeating the declined request."
        ),
    }


class StreamRenderer:
    """Scroll-safe Markdown streamer with live-then-collapsing thinking display."""

    def __init__(self, reasoning_level: str, thinking_mode: str):
        self.reasoning_level = reasoning_level
        self.thinking_mode = thinking_mode
        self._lock = threading.Lock()
        self._spinner = Spinner("dots", style="bold cyan")
        self._status_note: str | None = None

        self._thinking_buf = ""
        self._thinking_chars = 0
        self._thinking_header_printed = False
        self._thinking_finalized = False

        self._answer_buf = ""
        self._answer_chars = 0
        self._answer_started = False

        self._live = Live(
            self,
            console=console,
            refresh_per_second=8,
            transient=True,
            vertical_overflow="ellipsis",
        )

    def __enter__(self):
        console.print(
            Text.assemble(("AI ", "bold green"), (f"[{self.reasoning_level}]", "cyan"), (" >", "bold green"))
        )
        self._live.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._live.stop()
        if exc_type is None:
            self._finalize_thinking(use_live=False)
            if self._answer_buf.strip():
                self._emit(Markdown(self._answer_buf, code_theme="monokai"), use_live=False)
            self._answer_buf = ""
        return False

    def _emit(self, renderable, use_live: bool = True) -> None:
        (self._live.console if use_live else console).print(renderable)

    def add_thinking(self, token: str) -> None:
        with self._lock:
            is_first = self._thinking_chars == 0
            if is_first:
                token = token.lstrip()
                if not token:
                    return
            self._thinking_chars += len(token)
            if self.thinking_mode == "collapse":
                self._thinking_buf += token
                return

        if self.thinking_mode != "full":
            return

        # Stream full reasoning lines permanently into terminal scrollback
        if is_first and not self._thinking_header_printed:
            self._emit(Text("[Thinking]", style="dim bold"))
            self._thinking_header_printed = True

        self._thinking_buf += token
        while "\n" in self._thinking_buf:
            line, self._thinking_buf = self._thinking_buf.split("\n", 1)
            self._emit(Text(line, style="dim italic"))

    def add_answer(self, token: str) -> None:
        self._finalize_thinking(use_live=True)
        self._answer_buf += token

        with self._lock:
            if not self._answer_started:
                self._answer_buf = self._answer_buf.lstrip("\n")
                self._answer_started = bool(self._answer_buf)
            self._answer_chars += len(token)

        self._flush_complete_blocks()

    def set_status(self, note: str | None) -> None:
        if note:
            self._finalize_thinking(use_live=True)
        with self._lock:
            self._status_note = note

    def _finalize_thinking(self, use_live: bool = True) -> None:
        with self._lock:
            if self._thinking_finalized:
                return
            self._thinking_finalized = True
            chars = self._thinking_chars
            leftover = self._thinking_buf if self.thinking_mode == "full" else ""
            self._thinking_buf = ""

        if chars == 0 or self.thinking_mode == "off":
            return

        if self.thinking_mode == "collapse":
            msg = f"  [Thought process collapsed ({chars:,} chars — /thinking full to keep expanded)]"
            self._emit(Text(msg, style="dim italic"), use_live=use_live)
        elif leftover.strip():
            self._emit(Text(leftover, style="dim italic"), use_live=use_live)
        self._emit(Text(""), use_live=use_live)

    def _flush_complete_blocks(self) -> None:
        """Flushes completed Markdown paragraphs/code blocks while keeping open fences buffered."""
        lines = self._answer_buf.splitlines(keepends=True)
        if not lines:
            return

        complete_lines, tail = (lines, "") if lines[-1].endswith("\n") else (lines[:-1], lines[-1])
        in_fence = False
        last_cut = 0
        has_content = False

        for i, line in enumerate(complete_lines):
            stripped = line.strip()
            if stripped.startswith(("```", "~~~")):
                in_fence = not in_fence
                has_content = True
            elif not stripped and not in_fence and has_content:
                last_cut = i + 1
                has_content = False
            elif stripped:
                has_content = True

        if last_cut > 0:
            chunk = "".join(complete_lines[:last_cut])
            if chunk.strip():
                self._emit(Markdown(chunk, code_theme="monokai"))
            self._answer_buf = "".join(complete_lines[last_cut:]) + tail

    def __rich__(self):
        with self._lock:
            note = self._status_note
            t_chars, a_chars, a_started = self._thinking_chars, self._answer_chars, self._answer_started
            live_thinking = (
                self._thinking_buf
                if self.thinking_mode == "collapse" and not self._thinking_finalized and self._thinking_buf
                else ""
            )

        if note:
            label = Text(note, style="dim cyan")
        elif a_started:
            label = Text(f"Streaming response... ({a_chars:,} chars)", style="dim")
        elif t_chars > 0:
            label = Text(f"Thinking... ({t_chars:,} chars)", style="dim")
        else:
            label = Text("Thinking...", style="dim")

        status_row = Columns([self._spinner, label], padding=(0, 1))
        if not live_thinking:
            return status_row

        # Render a bounded tail window of active reasoning inside the transient Live area
        width = max(20, console.size.width - 6)
        max_rows = max(4, console.size.height - 6)
        snippet = live_thinking[-(max_rows * width + 500) :]
        wrapped = Text(snippet.replace("\r", ""), style="dim italic").wrap(console, width)
        visible = wrapped[-max_rows:]
        title_suffix = " — live tail" if len(wrapped) > max_rows else ""

        panel = Panel(
            Text("\n").join(visible),
            title=f"[dim]Thinking ({t_chars:,} chars{title_suffix})[/dim]",
            title_align="left",
            box=box.HORIZONTALS,
            border_style="dim",
            padding=(0, 1),
        )
        return Group(panel, status_row)


def print_help() -> None:
    """Prints available investigation CLI slash commands."""
    table = Table(box=box.SIMPLE, show_header=True, header_style="bold cyan", padding=(0, 2, 0, 0))
    table.add_column("Command", style="bold green")
    table.add_column("Description")

    for cmd, desc in (
        ("/pane [off]", "Open right-side query viewer pane (or '/pane off' to close it)"),
        ("/show [n]", "Show the result table for query #n (defaults to latest)"),
        ("/sql <query>", "Execute a read-only DuckDB SQL query directly"),
        ("/autosql [on|off]", "Toggle auto-accept for validated read-only SQL (default: off)"),
        ("/schema", "List mounted DuckDB views, row counts, and columns"),
        ("/tools [list]", "List registered tool directories/files, pip packages, and AI tools"),
        ("/tools add <dir|file>", "Register a directory (added to PATH) or a single tool file (by path)"),
        ("/tools remove <dir|file|#>", "Unregister a tool directory or file by path or number"),
        ("/tools pip install <pkgs>", "pip install packages for this interpreter and record them"),
        ("/reasoning <level>", "Set reasoning effort: low, medium, high, or off"),
        ("/thinking <mode>", "Set thinking display: collapse (live->collapse), full, or off"),
        ("/clear", "Clear conversation history and cached query results"),
        ("/help", "Show this command reference"),
        ("exit", "Exit the investigation CLI"),
    ):
        table.add_row(cmd, desc)
    console.print(table)


def print_result_table(entry: dict, max_rows: int = 50) -> None:
    """Renders a cached query result entry as a Rich Table."""
    rows, columns = entry.get("rows") or [], entry.get("columns") or []
    if not rows:
        console.print("[dim][-] Query returned 0 rows.[/dim]")
        return

    truncated = bool(entry.get("truncated", False))
    prefix = f"Query #{entry['id']} — " if entry.get("id") else ""
    table = Table(title=f"{prefix}{len(rows)}{'+' if truncated else ''} rows")
    for col in columns:
        table.add_column(str(col), overflow="fold")
    for row in rows:
        table.add_row(*("" if val is None else str(val) for val in row))

    console.print(table)
    if truncated:
        console.print(f"[yellow][*] Output capped at {max_rows} rows.[/yellow]")


def _parse_choice(raw: str, choices: dict[str, str]) -> tuple[str, str]:
    """Splits input like 'n: try approach XYZ' into ('n', 'try approach XYZ')."""
    stripped = raw.strip()
    if not stripped:
        return "", ""
    parts = stripped.split(None, 1)
    token = parts[0].lower().rstrip(":,;-")
    rest = parts[1].strip() if len(parts) > 1 else ""
    return (token, rest) if token in choices else (stripped.lower(), "")


def _prompt_panel_choice(
    title: str,
    body,
    border_style: str,
    prompt_text: str,
    choices: dict[str, str],
    default_decline: str,
    decline_msg: str,
    invalid_msg: str,
    note: str | None = None,
) -> ConfirmDecision:
    """Shared HITL confirmation panel supporting inline ('n <reason>') or prompted decline feedback."""
    console.print()
    console.print(
        Panel(body, title=title, title_align="left", box=box.HORIZONTALS, border_style=border_style, padding=(0, 1))
    )
    if note:
        console.print(note)

    while True:
        try:
            raw = input(prompt_text)
        except (KeyboardInterrupt, EOFError):
            console.print(decline_msg)
            return ConfirmDecision(approved=False, choice=default_decline)

        token, inline_feedback = _parse_choice(raw, choices)
        if token not in choices:
            console.print(invalid_msg)
            continue

        choice = choices[token]
        is_decline = choice == default_decline

        # Reject trailing text on approvals so typos never silently approve
        if inline_feedback and not is_decline:
            console.print(invalid_msg)
            continue

        if is_decline:
            try:
                feedback = inline_feedback or input("Feedback for AI (Enter to skip): ").strip()
            except (KeyboardInterrupt, EOFError):
                feedback = ""
            suffix = " [dim](feedback sent to AI)[/dim]" if feedback else ""
            console.print(f"{decline_msg}{suffix}")
            return ConfirmDecision(approved=False, choice=choice, feedback=feedback)

        return ConfirmDecision(approved=True, choice=choice)


def confirm_sql(reason: str, sql: str, has_inner_semicolon: bool = False) -> ConfirmDecision:
    """Displays a compact SQL proposal and returns a ConfirmDecision ('yes', 'table', or 'no')."""
    content = Group(
        Text.assemble(("Rationale: ", "bold yellow"), (reason or "No reason given", "italic")),
        Text(""),
        Syntax(sql.strip(), "sql", theme="monokai", word_wrap=True),
    )
    note = (
        "[yellow][!] Note: Query contains an internal semicolon — inspect carefully.[/yellow]"
        if has_inner_semicolon
        else None
    )
    return _prompt_panel_choice(
        title="[bold cyan]Proposed SQL[/bold cyan]",
        body=content,
        border_style="cyan",
        prompt_text="Execute? [Y]es / [t]able / [n]o (or 'n <reason>'): ",
        choices={"": "yes", "y": "yes", "yes": "yes", "t": "table", "table": "table", "n": "no", "no": "no"},
        default_decline="no",
        decline_msg="[yellow][-] Query declined.[/yellow]",
        invalid_msg="[red][!] Enter y (quiet), t (show table), or n [reason] (decline).[/red]",
        note=note,
    )


def confirm_action(
    title: str,
    details: list[str],
    prompt: str = "Proceed? [Y]es / [n]o (or 'n <reason>'): ",
) -> ConfirmDecision:
    """Generic HITL approval prompt for file reads, hex dumps, or GUI viewers."""
    return _prompt_panel_choice(
        title=f"[bold cyan]{title}[/bold cyan]",
        body=Group(*(Text(f"• {line}") for line in details)),
        border_style="cyan",
        prompt_text=prompt,
        choices={"": "yes", "y": "yes", "yes": "yes", "n": "no", "no": "no"},
        default_decline="no",
        decline_msg="[yellow][-] Action declined.[/yellow]",
        invalid_msg="[red][!] Invalid choice. Press Enter for Yes, or enter n [reason].[/red]",
    )


def confirm_terminal(reason: str, command: str, cwd: str) -> ConfirmDecision:
    """High-visibility HITL prompt for last-resort shell commands. Defaults to NO on Enter."""
    lexer = "powershell" if os.name == "nt" else "bash"
    content = Group(
        Text.assemble(("Working Dir: ", "bold yellow"), (cwd, "dim")),
        Text.assemble(("Rationale:   ", "bold yellow"), (reason or "No reason given", "italic")),
        Text(""),
        Syntax(command.strip(), lexer, theme="monokai", word_wrap=True),
    )
    return _prompt_panel_choice(
        title="[bold red]Proposed Terminal Command[/bold red]",
        body=content,
        border_style="red",
        prompt_text="Execute shell command? [y]es / [N]o (or 'n <reason>'): ",
        choices={"y": "yes", "yes": "yes", "": "no", "n": "no", "no": "no"},
        default_decline="no",
        decline_msg="[yellow][-] Terminal command declined.[/yellow]",
        invalid_msg="[red][!] Enter y to execute, or press Enter / n [reason] to decline.[/red]",
    )