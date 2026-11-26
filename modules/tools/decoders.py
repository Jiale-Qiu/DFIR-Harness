import base64
from modules.llm_ui import console


def _decode_base64(args: dict, ctx: dict) -> dict:
    raw = "".join(args.get("data", "").split())
    if not raw:
        return {"error": "Empty Base64 input."}

    # Restore stripped '=' padding if needed
    pad = (-len(raw)) % 4
    padded = raw + ("=" * pad)

    try:
        decoded_bytes = base64.urlsafe_b64decode(padded)
    except Exception as e:
        return {"error": f"Base64 decode failed: {e}"}

    # PowerShell -EncodedCommand uses UTF-16LE (alternating null bytes on ASCII text)
    encoding_used = "utf-8"
    if len(decoded_bytes) >= 2 and decoded_bytes[1::2].count(0) > len(decoded_bytes) // 4:
        text = decoded_bytes.decode("utf-16-le", errors="replace")
        encoding_used = "utf-16-le (PowerShell)"
    else:
        text = decoded_bytes.decode("utf-8", errors="replace")

    console.print(f"[dim][tool] decode_base64 ({encoding_used}, {len(decoded_bytes)} bytes)[/dim]")
    return {
        "encoding": encoding_used,
        "byte_length": len(decoded_bytes),
        "decoded_text": text,
        "hex": decoded_bytes.hex(),
    }


def _decode_hex(args: dict, ctx: dict) -> dict:
    cleaned = (
        args.get("hex_string", "")
        .replace("0x", "")
        .replace("\\x", "")
        .replace(",", "")
    )
    cleaned = "".join(cleaned.split())
    try:
        raw = bytes.fromhex(cleaned)
    except ValueError as e:
        return {"error": f"Invalid hex string: {e}"}

    console.print(f"[dim][tool] decode_hex ({len(raw)} bytes)[/dim]")
    return {
        "byte_length": len(raw),
        "utf8": raw.decode("utf-8", errors="replace"),
        "utf16le": raw.decode("utf-16-le", errors="replace"),
    }


def _xor_bytes(args: dict, ctx: dict) -> dict:
    data_str = args.get("data", "")
    data_format = args.get("data_format", "hex").lower()
    key_str = args.get("key", "")
    key_format = args.get("key_format", "hex").lower()

    try:
        data_bytes = bytes.fromhex("".join(data_str.replace("0x", "").split())) if data_format == "hex" else data_str.encode("utf-8")
        key_bytes = bytes.fromhex("".join(key_str.replace("0x", "").split())) if key_format == "hex" else key_str.encode("utf-8")
    except ValueError as e:
        return {"error": f"Invalid hex input: {e}"}

    if not key_bytes:
        return {"error": "XOR key cannot be empty."}

    out = bytes(b ^ key_bytes[i % len(key_bytes)] for i, b in enumerate(data_bytes))
    console.print(f"[dim][tool] xor_decode ({len(out)} bytes)[/dim]")
    return {
        "hex": out.hex(),
        "ascii_preview": out.decode("utf-8", errors="replace"),
    }


REGISTER = [
    {
        "schema": {
            "type": "function",
            "function": {
                "name": "decode_base64",
                "description": "Decode Base64 strings (automatically detects UTF-8 vs PowerShell UTF-16LE).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "data": {"type": "string", "description": "Base64-encoded payload."},
                    },
                    "required": ["data"],
                },
            },
        },
        "handler": _decode_base64,
    },
    {
        "schema": {
            "type": "function",
            "function": {
                "name": "decode_hex",
                "description": "Convert a hexadecimal string into UTF-8 and UTF-16LE text.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "hex_string": {"type": "string", "description": "Hex string (spaces, 0x, or \\x allowed)."},
                    },
                    "required": ["hex_string"],
                },
            },
        },
        "handler": _decode_hex,
    },
    {
        "schema": {
            "type": "function",
            "function": {
                "name": "xor_decode",
                "description": "XOR-decode bytes using a single-byte or repeating multi-byte key.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "data": {"type": "string", "description": "Ciphertext as hex or UTF-8 string."},
                        "data_format": {"type": "string", "enum": ["hex", "text"], "default": "hex"},
                        "key": {"type": "string", "description": "XOR key as hex (e.g. '41' or 'deadbeef') or ASCII text."},
                        "key_format": {"type": "string", "enum": ["hex", "text"], "default": "hex"},
                    },
                    "required": ["data", "key"],
                },
            },
        },
        "handler": _xor_bytes,
    },
]