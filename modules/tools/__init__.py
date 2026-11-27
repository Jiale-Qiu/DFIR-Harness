import importlib
import json
import pkgutil
from pathlib import Path
from typing import Any, Callable

_REGISTRY: dict[str, Callable[[dict, dict], Any]] = {}
TOOL_SCHEMAS: list[dict] = []
_DISCOVERED = False


def _discover_tools() -> None:
    """Auto-imports every .py module in modules/tools/ and registers its REGISTER entries."""
    global _DISCOVERED
    if _DISCOVERED:
        return
    _DISCOVERED = True

    package_dir = Path(__file__).resolve().parent
    for mod_info in pkgutil.iter_modules([str(package_dir)]):
        if mod_info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{__name__}.{mod_info.name}")
        for entry in getattr(module, "REGISTER", []):
            schema = entry["schema"]
            name = schema["function"]["name"]
            _REGISTRY[name] = entry["handler"]
            TOOL_SCHEMAS.append(schema)


def list_tools() -> list[str]:
    _discover_tools()
    return sorted(_REGISTRY.keys())


def execute_tool(name: str, raw_args: str, ctx: dict) -> dict:
    """Parses JSON arguments and dispatches to the matching drop-in tool handler."""
    _discover_tools()
    handler = _REGISTRY.get(name)
    if not handler:
        return {"error": f"Unknown tool: {name}"}

    try:
        args = json.loads(raw_args) if raw_args else {}
    except json.JSONDecodeError as e:
        return {"error": f"Malformed tool JSON: {e}"}

    if not isinstance(args, dict):
        return {"error": "Tool arguments must be a JSON object."}

    try:
        result = handler(args, ctx)
        return result if isinstance(result, dict) else {"result": result}
    except Exception as e:
        return {"error": f"Tool '{name}' failed: {e}"}
