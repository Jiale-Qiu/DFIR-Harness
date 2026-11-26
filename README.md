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
