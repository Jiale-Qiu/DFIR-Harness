"""
NVIDIA NIM (paste the exact model ID shown on the NIM page)
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
from rich.console import Console

console = Console(highlight=False)


def start_cli(con, mounted_views: set[str], base_output):
    """Minimal interactive chat loop configured purely via environment variables."""
    api_key = os.environ.get("OPENAI_API_KEY")
    model = os.environ.get("LLM_MODEL")

    if not api_key or not model:
        console.print("[red][!] Missing config. Set OPENAI_API_KEY, OPENAI_BASE_URL, and LLM_MODEL.[/red]")
        return

    # OpenAI() automatically reads OPENAI_API_KEY and OPENAI_BASE_URL from the environment
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

        try:
            with console.status("[cyan]Thinking...[/cyan]", spinner="dots"):
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=0.6,
                    max_tokens=1024,
                )

            reply = response.choices[0].message.content or "(Empty response)"
            messages.append({"role": "assistant", "content": reply})
            console.print(f"\n[bold green]AI >[/bold green] {reply}\n")

        except Exception as e:
            console.print(f"\n[red][!] API Error: {e}[/red]\n")
            messages.pop()