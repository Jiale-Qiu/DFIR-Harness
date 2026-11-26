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
            self._flush_answer_tail(use_live=False)
        return False

    def _emit(self, renderable, use_live: bool = True) -> None:
        target = self._live.console if use_live else console
        target.print(renderable)

    def add_thinking(self, token: str) -> None:
        if self.thinking_mode == "off":
            with self._lock:
                self._thinking_chars += len(token)
            return

        if self.thinking_mode == "collapse":
            with self._lock:
                if self._thinking_chars == 0:
                    token = token.lstrip()
                    if not token:
                        return
                self._thinking_chars += len(token)
                self._thinking_buf += token
            return

        # thinking_mode == "full": stream permanently into scrollback
        with self._lock:
            is_first = self._thinking_chars == 0
            self._thinking_chars += len(token)

        if is_first:
            token = token.lstrip()
            if not token:
                return
            if not self._thinking_header_printed:
                self._emit(Text("[Thinking]", style="dim bold"))
                self._thinking_header_printed = True

        self._thinking_buf += token
        while "\n" in self._thinking_buf:
            line, self._thinking_buf = self._thinking_buf.split("\n", 1)
            self._emit(Text(line, style="dim italic"))

    def add_answer(self, token: str) -> None:
        self._finalize_thinking(use_live=True)

        self._answer_buf += token
        if not self._answer_started:
            self._answer_buf = self._answer_buf.lstrip("\n")
            if self._answer_buf:
                with self._lock:
                    self._answer_started = True

        with self._lock:
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
            self._emit(
                Text(
                    f"  [Thought process collapsed ({chars:,} chars — /thinking full to keep expanded)]",
                    style="dim italic",
                ),
                use_live=use_live,
            )
            self._emit(Text(""), use_live=use_live)
        elif self.thinking_mode == "full":
            if leftover.strip():
                self._emit(Text(leftover, style="dim italic"), use_live=use_live)
            self._emit(Text(""), use_live=use_live)

    def _flush_complete_blocks(self) -> None:
        lines = self._answer_buf.splitlines(keepends=True)
        if not lines:
            return

        if lines[-1].endswith("\n"):
            complete_lines, tail = lines, ""
        else:
            complete_lines, tail = lines[:-1], lines[-1]

        in_fence = False
        last_cut = 0
        has_content = False

        for i, line in enumerate(complete_lines):
            stripped = line.strip()
            if stripped.startswith("```") or stripped.startswith("~~~"):
                in_fence = not in_fence
                has_content = True
            elif stripped == "" and not in_fence and has_content:
                last_cut = i + 1
                has_content = False
            elif stripped != "":
                has_content = True

        if last_cut > 0:
            chunk = "".join(complete_lines[:last_cut])
            if chunk.strip():
                self._emit(Markdown(chunk, code_theme="monokai"))
            self._answer_buf = "".join(complete_lines[last_cut:]) + tail

    def _flush_answer_tail(self, use_live: bool = False) -> None:
        if self._answer_buf.strip():
            self._emit(Markdown(self._answer_buf, code_theme="monokai"), use_live=use_live)
        self._answer_buf = ""

    def __rich__(self):
        with self._lock:
            status_note = self._status_note
            thinking_chars = self._thinking_chars
            answer_chars = self._answer_chars
            answer_started = self._answer_started
            show_live_thinking = (
                self.thinking_mode == "collapse"
                and not self._thinking_finalized
                and bool(self._thinking_buf)
            )
            thinking_text = self._thinking_buf if show_live_thinking else ""

        if status_note:
            label = Text(status_note, style="dim cyan")
        elif answer_started:
            label = Text(f"Streaming response... ({answer_chars:,} chars)", style="dim")
        elif thinking_chars > 0:
            label = Text(f"Thinking... ({thinking_chars:,} chars)", style="dim")
        else:
            label = Text("Thinking...", style="dim")

        status_row = Columns([self._spinner, label], padding=(0, 1))
        if not thinking_text:
            return status_row

        inner_width = max(20, console.size.width - 6)
        max_visible_rows = max(4, console.size.height - 6)
        char_budget = max_visible_rows * inner_width + 500
        snippet = thinking_text[-char_budget:] if len(thinking_text) > char_budget else thinking_text
        wrapped = Text(snippet.replace("\r", ""), style="dim italic").wrap(console, inner_width)

        if len(wrapped) > max_visible_rows:
            visible = wrapped[-max_visible_rows:]
            title = f"[dim]Thinking ({thinking_chars:,} chars — live tail)[/dim]"
        else:
            visible = wrapped
            title = f"[dim]Thinking ({thinking_chars:,} chars)[/dim]"

        panel = Panel(
            Text("\n").join(visible),
            title=title,
            title_align="left",
            box=box.HORIZONTALS,
            border_style="dim",
            padding=(0, 1),
        )
        return Group(panel, status_row)


def print_help():
    """Prints available investigation CLI slash commands."""
    table = Table(box=box.SIMPLE, show_header=True, header_style="bold cyan", padding=(0, 2, 0, 0))
    table.add_column("Command", style="bold green")
    table.add_column("Description")

    commands = [
        ("/pane [off]", "Open right-side query viewer pane (or '/pane off' to close it)"),
        ("/show [n]", "Show the result table for query #n (defaults to latest)"),
        ("/sql <query>", "Execute a read-only DuckDB SQL query directly"),
        ("/schema", "List mounted DuckDB views, row counts, and columns"),
        ("/tools", "List loaded AI tools from modules/tools/"),
        ("/reasoning <level>", "Set reasoning effort: low, medium, high, or off"),
        ("/thinking <mode>", "Set thinking display: collapse (live->collapse), full, or off"),
        ("/clear", "Clear conversation history and cached query results"),
        ("/help", "Show this command reference"),
        ("exit", "Exit the investigation CLI"),
    ]
    for cmd, desc in commands:
        table.add_row(cmd, desc)

    console.print(table)


def print_result_table(entry: dict, max_rows: int = 100):
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