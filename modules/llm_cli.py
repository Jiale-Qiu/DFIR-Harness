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
    """Interactive chat loop with token streaming."""
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

        try:
            stream = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.6,
                max_tokens=1024,
                stream=True,
            )

            console.print("\n[bold green]AI >[/bold green] ", end="")
            reply_chunks = []

            for chunk in stream:
                # Some providers send empty keep-alive or usage chunks at the end
                if not chunk.choices:
                    continue

                token = chunk.choices[0].delta.content
                if token:
                    print(token, end="", flush=True)
                    reply_chunks.append(token)

            print("\n")  # Clean spacing after the stream finishes

            full_reply = "".join(reply_chunks)
            if full_reply:
                messages.append({"role": "assistant", "content": full_reply})
            else:
                messages.pop()

        except Exception as e:
            console.print(f"\n[red][!] API Error: {e}[/red]\n")
            messages.pop()