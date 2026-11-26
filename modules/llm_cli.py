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

import os
from openai import OpenAI
from rich.columns import Columns
from rich.console import Console, Group
from rich.live import Live
from rich.spinner import Spinner
from rich.text import Text

console = Console(highlight=False)

VALID_LEVELS = ("low", "medium", "high", "off")
DEFAULT_REASONING = "medium"


def _build_display(thinking: str, answer: str, reasoning_level: str, spinner: Spinner | None):
    """Renders the AI [level] > header (with optional spinner) and streamed text."""
    prefix = Text.assemble(
        ("AI ", "bold green"),
        (f"[{reasoning_level}]", "cyan"),
    )

    if spinner:
        header_items = [prefix, spinner, Text(">", style="bold green")]
        if not thinking and not answer:
            header_items.append(Text("Thinking...", style="dim"))
        header = Columns(header_items, padding=(0, 1))
    else:
        header = Text.assemble(prefix, (" >", "bold green"))

    if not thinking and not answer:
        return header

    body = Text()
    if thinking:
        body.append("[Thinking]\n", style="dim bold")
        body.append(thinking.strip(), style="dim italic")
        if answer:
            body.append("\n\n")

    if answer:
        body.append(answer)

    return Group(header, body)


def start_cli(con, mounted_views: set[str], base_output):
    """Interactive chat loop with live spinner, reasoning stream, and local /reasoning command."""
    api_key = os.environ.get("OPENAI_API_KEY")
    model = os.environ.get("LLM_MODEL")

    if not api_key or not model:
        console.print("[red][!] Missing config. Set OPENAI_API_KEY, OPENAI_BASE_URL, and LLM_MODEL.[/red]")
        return

    client = OpenAI()
    reasoning_level = DEFAULT_REASONING

    messages = [
        {
            "role": "system",
            "content": "You are a helpful Windows DFIR assistant. Keep responses clear and concise.",
        }
    ]

    console.print("\n[bold cyan]=== LLM Investigation CLI ===[/bold cyan]")
    console.print(f"[dim]Model: {model} | Commands: /reasoning <low|medium|high|off>, exit[/dim]\n")

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

        # Intercept /reasoning locally without sending to the LLM
        if user_input.lower().startswith("/reasoning"):
            parts = user_input.split()
            if len(parts) == 2 and parts[1].lower() in VALID_LEVELS:
                reasoning_level = parts[1].lower()
                console.print(f"[cyan][*] Reasoning level set to: {reasoning_level}[/cyan]\n")
            else:
                console.print(
                    f"[yellow][!] Current: {reasoning_level} | Usage: /reasoning <low|medium|high|off>[/yellow]\n"
                )
            continue

        messages.append({"role": "user", "content": user_input})
        console.print()

        try:
            thinking_chunks = []
            answer_chunks = []
            finish_reason = None
            spinner = Spinner("dots", style="bold cyan")

            request_kwargs = {
                "model": model,
                "messages": messages,
                "temperature": 0.6,
                "max_tokens": 4096,
                "stream": True,
            }
            if reasoning_level != "off":
                request_kwargs["extra_body"] = {"reasoning_effort": reasoning_level}

            with Live(
                _build_display("", "", reasoning_level, spinner),
                console=console,
                refresh_per_second=12,
                vertical_overflow="visible",
            ) as live:
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
                        thinking_chunks.append(reasoning_token)

                    content_token = delta.content
                    if content_token:
                        answer_chunks.append(content_token)

                    live.update(
                        _build_display(
                            "".join(thinking_chunks),
                            "".join(answer_chunks),
                            reasoning_level,
                            spinner,
                        )
                    )

                live.update(
                    _build_display(
                        "".join(thinking_chunks),
                        "".join(answer_chunks),
                        reasoning_level,
                        None,
                    )
                )

            print()

            if finish_reason == "length":
                console.print("[yellow][!] Response hit max_tokens (4096) and was cut short.[/yellow]\n")

            full_reply = "".join(answer_chunks)
            if full_reply:
                messages.append({"role": "assistant", "content": full_reply})
            else:
                messages.pop()

        except Exception as e:
            console.print(f"\n[red][!] API Error: {e}[/red]\n")
            messages.pop()