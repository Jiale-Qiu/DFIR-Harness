import os
from openai import OpenAI
from rich.console import Console

console = Console(highlight=False)

MODEL = "moonshotai/kimi-k3.1-pro"
BASE_URL = "https://integrate.api.nvidia.com/v1"


def start_cli(con, mounted_views: set[str], base_output):
    """Minimal interactive chat loop with NVIDIA NIM."""
    api_key = os.environ.get("NVIDIA_API_KEY")
    if not api_key:
        console.print("[red][!] NVIDIA_API_KEY not found in environment variables.[/red]")
        console.print("    If you just ran setx, close and reopen your terminal first.")
        return

    client = OpenAI(base_url=BASE_URL, api_key=api_key)

    # Conversation history sent with each request so the model remembers context
    messages = [
        {
            "role": "system",
            "content": "You are a helpful Windows DFIR assistant. Keep responses clear and concise.",
        }
    ]

    console.print("\n[bold cyan]=== LLM Investigation CLI ===[/bold cyan]")
    console.print(f"[dim]Model: {MODEL} | Type 'exit' or 'quit' to leave.[/dim]\n")

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
                    model=MODEL,
                    messages=messages,
                    temperature=0.6,
                    max_tokens=1024,
                )

            reply = response.choices[0].message.content or "(Empty response)"
            messages.append({"role": "assistant", "content": reply})
            console.print(f"\n[bold green]AI >[/bold green] {reply}\n")

        except Exception as e:
            console.print(f"\n[red][!] API Error: {e}[/red]\n")
            messages.pop()  # Drop the failed prompt so message history stays clean