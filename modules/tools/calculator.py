import ast
import operator
from modules.llm_ui import console

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.LShift: operator.lshift,
    ast.RShift: operator.rshift,
    ast.BitAnd: operator.and_,
    ast.BitOr: operator.or_,
    ast.BitXor: operator.xor,
}

_UNARY_OPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
    ast.Invert: operator.invert,
}


def _eval_node(node):
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        return _BIN_OPS[type(node.op)](_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_eval_node(node.operand))
    raise ValueError(f"Unsupported expression syntax: {ast.dump(node)}")


def _handle_calc(args: dict, ctx: dict) -> dict:
    expr = args.get("expression", "").strip()
    if not expr:
        return {"error": "Empty expression."}

    tree = ast.parse(expr, mode="eval")
    val = _eval_node(tree)
    console.print(f"[dim][tool] calculate: {expr} = {val}[/dim]")

    out = {"expression": expr, "result": val}
    if isinstance(val, int):
        out["hex"] = hex(val)
    return out


REGISTER = [
    {
        "schema": {
            "type": "function",
            "function": {
                "name": "calculate",
                "description": (
                    "Safely evaluate arithmetic, hex literals (0x...), byte sizes, offsets, "
                    "and bitwise flag masks."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "expression": {
                            "type": "string",
                            "description": "Math/bitwise expression, e.g. '(0x1a40 + 512) / 1024' or '0x200 & 0x200'.",
                        },
                    },
                    "required": ["expression"],
                },
            },
        },
        "handler": _handle_calc,
    }
]