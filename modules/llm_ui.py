import threading
from rich import box
from rich.columns import Columns
from rich.console import Console, Group
from rich.panel import Panel
from rich.spinner import Spinner
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

console = Console(highlight=False)


class StreamRenderer:
    """Thread-safe incremental Live renderer with auto-collapsing thinking stream."""

    def __init__(self, reasoning_level: str, thinking_mode: str):
        self.reasoning_level = reasoning_level
        self.thinking_mode = thinking_mode
        self.spinner: Spinner | None = Spinner("dots", style="bold cyan")
        self.status_note: str | None = None
        self._lock = threading.Lock()
        self._pending_thinking: list[str] = []
        self._pending_answer: list[str] = []
        self._thinking_text = Text(style="dim italic")
        self._answer_text = Text()
        self._thinking_chars = 0
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
                if self._thinking_chars == 0:
                    chunk = chunk.lstrip()
                if chunk:
                    self._thinking_text.append(chunk)
                    self._thinking_chars += len(chunk)

            if self._pending_answer:
                chunk = "".join(self._pending_answer)
                self._pending_answer.clear()
                self._answer_text.append(chunk)
                self._has_answer = True

            spinner = self.spinner
            status_note = self.status_note
            thinking_chars = self._thinking_chars
            has_answer = self._has_answer

        prefix = Text.assemble(("AI ", "bold green"), (f"[{self.reasoning_level}]", "cyan"))

        if spinner:
            header_items = [prefix, spinner, Text(">", style="bold green")]
            if status_note:
                header_items.append(Text(status_note, style="dim cyan"))
            elif thinking_chars == 0 and not has_answer:
                header_items.append(Text("Thinking...", style="dim"))
            header = Columns(header_items, padding=(0, 1))
        else:
            header = Text.assemble(prefix, (" >", "bold green"))

        if thinking_chars == 0 and not has_answer:
            return header

        parts = [header]
        if thinking_chars > 0 and self.thinking_mode != "off":
            if spinner is None and self.thinking_mode == "collapse":
                parts.append(
                    Text(
                        f"  [Thought process collapsed ({thinking_chars:,} chars — /thinking full to expand)]",
                        style="dim italic",
                    )
                )
            else:
                parts.append(Text("[Thinking]", style="dim bold"))
                parts.append(self._thinking_text)
            if has_answer:
                parts.append(Text(""))

        if has_answer:
            parts.append(self._answer_text)

        return Group(*parts)


def print_help():
    """Prints available investigation CLI slash commands."""
    table = Table(box=box.SIMPLE, show_header=True, header_style="bold cyan", padding=(0, 2, 0, 0))
    table.add_column("Command", style="bold green")
    table.add_column("Description")

    commands = [
        ("/show [n]", "Print the result table for query #n (defaults to latest)"),
        ("/sql <query>", "Execute a read-only DuckDB SQL query directly"),
        ("/schema", "List mounted DuckDB views, row counts, and columns"),
        ("/tools", "List loaded AI tools from modules/tools/"),
        ("/reasoning <level>", "Set reasoning effort: low, medium, high, or off"),
        ("/thinking <mode>", "Set thinking display: collapse, full, or off"),
        ("/clear", "Clear conversation history and cached query results"),
        ("/help", "Show this command reference"),
        ("exit", "Exit the investigation CLI"),
    ]
    for cmd, desc in commands:
        table.add_row(cmd, desc)

    console.print(table)


def print_result_table(entry: dict, max_rows: int = 50):
    """Renders a cached query result entry as a Rich Table."""
    rows = entry["rows"]
    columns = entry["columns"]
    truncated = entry["truncated"]
    qid = entry.get("id")

    if not rows:
        console.print("[dim][-] Query returned 0 rows.[/dim]")
        return

    prefix = f"Query #{qid} — " if qid else ""
    title = f"{prefix}{len(rows)}{'+' if truncated else ''} rows"
    table = Table(title=title)
    for col in columns:
        table.add_column(str(col), overflow="fold")
    for row in rows:
        table.add_row(*row)
    console.print(table)
    if truncated:
        console.print(f"[yellow][*] Output capped at {max_rows} rows.[/yellow]")


def confirm_sql(reason: str, sql: str, has_inner_semicolon: bool = False) -> str:
    """Displays a sleek, compact SQL proposal and returns 'yes', 'table', or 'no'."""
    content = Group(
        Text.assemble(("Rationale: ", "bold yellow"), (reason or "No reason given", "italic")),
        Text(""),
        Syntax(sql.strip(), "sql", theme="monokai", word_wrap=True),
    )
    console.print()
    console.print(
        Panel(
            content,
            title="[bold cyan]Proposed SQL[/bold cyan]",
            title_align="left",
            box=box.HORIZONTALS,
            border_style="cyan",
            padding=(0, 1),
        )
    )

    if has_inner_semicolon:
        console.print("[yellow][!] Note: Query contains an internal semicolon — inspect carefully.[/yellow]")

    while True:
        try:
            choice = input("Execute? [Y]es / [t]able / [n]o: ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            console.print("[yellow][-] Query declined.[/yellow]")
            return "no"

        if choice in ("", "y", "yes"):
            return "yes"
        if choice in ("t", "table"):
            return "table"
        if choice in ("n", "no"):
            console.print("[yellow][-] Query declined.[/yellow]")
            return "no"
        console.print("[red][!] Enter y (quiet), t (show table), or n (decline).[/red]")


def confirm_action(title: str, details: list[str], prompt: str = "Proceed? [Y]es / [n]o: ") -> bool:
    """Generic HITL approval prompt for side-effect tools like launching GUI viewers."""
    body = Group(*(Text(f"• {line}") for line in details))
    console.print()
    console.print(
        Panel(
            body,
            title=f"[bold cyan]{title}[/bold cyan]",
            title_align="left",
            box=box.HORIZONTALS,
            border_style="cyan",
            padding=(0, 1),
        )
    )

    while True:
        try:
            choice = input(prompt).strip().lower()
        except (KeyboardInterrupt, EOFError):
            console.print("[yellow][-] Action declined.[/yellow]")
            return False

        if choice in ("", "y", "yes"):
            return True
        if choice in ("n", "no"):
            console.print("[yellow][-] Action declined.[/yellow]")
            return False
        console.print("[red][!] Invalid choice. Press Enter for Yes, or enter n.[/red]")