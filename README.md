# DFIR-Harness

**Goal:** Automate artifact discovery, parsing, and structured querying while keeping the analyst strictly in control—requiring human verification and exposing clear, verifiable reasoning at every step.

Still a work in progress

Necessary Environment Variables:
```
# NVIDIA NIM (paste the exact model ID shown on the NIM page)
$env:OPENAI_BASE_URL="https://integrate.api.nvidia.com/v1"
$env:OPENAI_API_KEY="nvapi-your-key-here"
$env:LLM_MODEL="mistralai/mistral-large-4-1t-a52b"

# Or switch to Groq in the same terminal
$env:OPENAI_BASE_URL="https://api.groq.com/openai/v1"
$env:OPENAI_API_KEY="gsk_your-key-here"
$env:LLM_MODEL="llama-3.3-70b-versatile"
```

External Tools (CLI + pip)
---------------------------
Anything already on PATH (or importable) is usable through `run_terminal`. To give the LLM
extra tools, register directories and install packages from inside the CLI — these persist
across sessions in `~/.dfir-harness/toolbox.json` (override with `DFIR_TOOLBOX_CONFIG`):

```
/tools                                   # show registered dirs, pip packages, AI tools
/tools add C:\tools\hayabusa             # dir is prepended to PATH for every command
/tools remove 1                          # or /tools remove C:\tools\hayabusa
/tools pip install yara-python pefile    # installs into this interpreter (auto-detected after)
```

Registered directories are put on PATH for both the process and each `run_terminal`
subprocess, and the system prompt lists their executables plus every installed pip package
so the model knows what it may call (e.g., `python -m pefile`, `hayabusa.exe ...`).
Installed packages are auto-detected via `importlib.metadata`, so packages added with a
normal `pip install` outside the harness are picked up automatically.

Template Tool:
```python
def _my_handler(args: dict, ctx: dict) -> dict:
    # ctx gives you access to ctx["con"], ctx["base_output"], ctx["mounted_views"], ctx["state"]
    return {"converted": "..."}

REGISTER = [
    {
        "schema": {
            "type": "function",
            "function": {
                "name": "convert_filetime",
                "description": "Convert a 64-bit Windows FILETIME integer into UTC ISO-8601.",
                "parameters": {
                    "type": "object",
                    "properties": {"filetime": {"type": "string"}},
                    "required": ["filetime"],
                },
            },
        },
        "handler": _my_handler,
    }
]
