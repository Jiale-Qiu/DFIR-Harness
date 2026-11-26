# DFIR-Harness

**Goal:** Automate artifact discovery, parsing, and structured querying while keeping the analyst strictly in control—requiring human verification and exposing clear, verifiable reasoning at every step.

Still a work in progress

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
```
