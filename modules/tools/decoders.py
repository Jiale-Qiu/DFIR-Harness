import base64
import binascii

from modules.llm_ui import console


def _clean_hex(raw: str) -> str:
    cleaned = raw.replace("0x", "").replace("0X", "").replace("\\x", "").replace(",", "")
    return "".join(cleaned.split())


def _decode_base64(args: dict, ctx: dict) -> dict:
    raw = "".join(str(args.get("data") or "").split())
    if not raw:
        return {"error": "Empty Base64 input."}

    # Normalize URL-safe alphabet and restore stripped '=' padding while still validating characters
    normalized = raw.replace("-", "+").replace("_", "/")
    padded = normalized + ("=" * ((-len(normalized)) % 4))

    try:
        decoded_bytes = base64.b64decode(padded, validate=True)
    except (binascii.Error, ValueError) as e:
        return {"error": f"Base64 decode failed: {e}"}

    # PowerShell -EncodedCommand uses UTF-16LE (alternating null bytes on ASCII text)
    if len(decoded_bytes) >= 2 and decoded_bytes[1::2].count(0) > len(decoded_bytes) // 4:
        encoding_used = "utf-16-le (PowerShell)"
        text = decoded_bytes.decode("utf-16-le", errors="replace")
    else:
        encoding_used = "utf-8"
        text = decoded_bytes.decode("utf-8", errors="replace")

    console.print(f"[dim][tool] decode_base64 ({encoding_used}, {len(decoded_bytes)} bytes)[/dim]")
    return {
        "encoding": encoding_used,
        "byte_length": len(decoded_bytes),
        "decoded_text": text,
        "hex": decoded_bytes.hex(),
    }


def _decode_hex(args: dict, ctx: dict) -> dict:
    cleaned = _clean_hex(str(args.get("hex_string") or ""))
    if not cleaned:
        return {"error": "Empty hex string."}

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


def _parse_xor_input(raw: str, fmt: str, label: str) -> bytes:
    if fmt == "hex":
        cleaned = _clean_hex(raw)
        if not cleaned:
            raise ValueError(f"Empty {label} hex input.")
        return bytes.fromhex(cleaned)
    if fmt == "text":
        return raw.encode("utf-8")
    raise ValueError(f"Invalid {label}_format '{fmt}'. Use 'hex' or 'text'.")


def _xor_bytes(args: dict, ctx: dict) -> dict:
    data_str = str(args.get("data") or "")
    data_format = str(args.get("data_format") or "hex").lower()
    key_str = str(args.get("key") or "")
    key_format = str(args.get("key_format") or "hex").lower()

    try:
        data_bytes = _parse_xor_input(data_str, data_format, "data")
        key_bytes = _parse_xor_input(key_str, key_format, "key")
    except ValueError as e:
        return {"error": str(e)}

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
