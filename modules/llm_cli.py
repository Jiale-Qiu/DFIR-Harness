"""
NVIDIA NIM (paste the exact model ID shown on the NIM page)

$env:OPENAI_BASE_URL="https://integrate.api.nvidia.com/v1"
$env:OPENAI_API_KEY="nvapi-your-key-here"
$env:LLM_MODEL="mistralai/mistral-large-4-1t-a52b"

Or switch to Groq in the same terminal

$env:OPENAI_BASE_URL="https://api.groq.com/openai/v1"
$env:OPENAI_API_KEY="gsk_your-key-here"
$env:LLM_MODEL="llama-3.3-70b-versatile"
"""

import os
import time
from openai import OpenAI
from rich.console import Console
from rich.live import Live
from rich.text import Text

console = Console(highlight=False)

SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


def _build_display(thinking: str, answer: str, frame: str | None) -> Text:
    """Builds the live terminal renderable with an optional inline spinner."""
    display = Text()
    display.append("AI ", style="bold green")
    if frame:
        display.append(f"{frame} ", style="bold cyan")
    display.append("> ", style="bold green")

    if not thinking and not answer:
        display.append("Thinking...", style="dim")
        return display

    if thinking:
        display.append("[Thinking]\n", style="dim bold")
        display.append(thinking.strip(), style="dim italic")
        if answer:
            display.append("\n\n")

    if answer:
        display.append(answer)

    return display


def start_cli(con, mounted_views: set[str], base_output):
    """Interactive chat loop with live spinner, reasoning stream, and answer stream."""
    api_key = os.environ.get("OPENAI_API_KEY")
    model = os.environ.get("LLM_MODEL")

    if not api_key or not model:
        console.print("[red][!] Missing config. Set OPENAI_API_KEY, OPENAI_BASE_URL, and LLM_MODEL.[/red]")
        return

    client = OpenAI()

    messages = [
        {
            "role": "system",
            "content": "You are a helpful Windows DFIR assistant. Keep responses clear and concise.",
        }
    ]

    console.print("\n[bold cyan]=== LLM Investigation CLI ===[/bold cyan]")
    console.print(f"[dim]Model: {model} | Type 'exit' to leave.[/dim]\n")

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

        messages.append({"role": "user", "content": user_input})
        console.print()

        try:
            thinking_chunks = []
            answer_chunks = []
            finish_reason = None
            start_time = time.monotonic()

            with Live(
                _build_display("", "", SPINNER_FRAMES[0]),
                console=console,
                refresh_per_second=12,
                vertical_overflow="visible",
            ) as live:
                stream = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=0.6,
                    max_tokens=4096,
                    stream=True,
                )

                for chunk in stream:
                    if not chunk.choices:
                        continue

                    choice = chunk.choices[0]
                    if choice.finish_reason:
                        finish_reason = choice.finish_reason

                    delta = choice.delta

                    # Stream reasoning tokens if the model emits them
                    reasoning_token = getattr(delta, "reasoning_content", None)
                    if reasoning_token:
                        thinking_chunks.append(reasoning_token)

                    # Stream final response tokens
                    content_token = delta.content
                    if content_token:
                        answer_chunks.append(content_token)

                    frame = SPINNER_FRAMES[int((time.monotonic() - start_time) * 12) % len(SPINNER_FRAMES)]
                    live.update(
                        _build_display("".join(thinking_chunks), "".join(answer_chunks), frame)
                    )

                # Final render with spinner removed once streaming completes
                live.update(_build_display("".join(thinking_chunks), "".join(answer_chunks), None))

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