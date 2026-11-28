"""CER-style findings log: claim, direct evidence, reasoning chain, per major finding."""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path

FINDINGS_FILENAME = "findings.md"
VALID_CONFIDENCE = ("high", "medium", "low")
MAX_EVIDENCE_ITEMS = 12

_HEADER = (
    "# Case Findings Log\n"
    "\n"
    "CER-formatted findings recorded by the DFIR harness agent (claim, direct evidence, reasoning).\n"
    "Every entry is agent-generated and must be verified by the analyst before it is treated as fact.\n"
)


def findings_path(ctx: dict) -> Path:
    """Returns the case findings log path (lives beside the parsed CSVs)."""
    return Path(ctx["base_output"]) / FINDINGS_FILENAME


def _next_index(text: str) -> int:
    ids = [int(m) for m in re.findall(r"^## F-(\d+)", text, re.MULTILINE)]
    return (max(ids) + 1) if ids else 1


def count_findings(path: Path) -> int:
    """Returns how many findings are already recorded in the log (0 if missing/unreadable)."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return 0
    return len(re.findall(r"^## F-\d+", text, re.MULTILINE))


def _one_line(value: object) -> str:
    return " ".join(str(value or "").split())


def _parse_evidence(raw: object) -> tuple[list[tuple[str, str]], str | None]:
    if not isinstance(raw, list) or not raw:
        return [], "evidence must be a non-empty list of {source, detail} objects."

    evidence: list[tuple[str, str]] = []
    for item in raw[:MAX_EVIDENCE_ITEMS]:
        if isinstance(item, dict):
            source = _one_line(item.get("source"))
            detail = _one_line(item.get("detail"))
        else:
            source, detail = "", _one_line(item)
        if not source or not detail:
            return [], "each evidence item needs both 'source' (artifact/query/file) and 'detail' (observed value)."
        evidence.append((source, detail))
    return evidence, None


def _format_block(
    finding_id: str,
    claim: str,
    confidence: str,
    evidence: list[tuple[str, str]],
    reasoning: str,
    alternatives: str,
    caveats: str,
    next_steps: str,
    case_name: str,
    recorded: str,
) -> str:
    lines = [
        f"## {finding_id} - {claim}",
        "",
        f"- **Case:** {case_name}",
        f"- **Recorded (UTC):** {recorded}",
        f"- **Confidence:** {confidence}",
        "- **Status:** agent-recorded, pending analyst verification",
        "",
        "**Direct evidence**",
    ]
    for idx, (source, detail) in enumerate(evidence, start=1):
        lines.append(f"{idx}. `{source}` - {detail}")

    lines += ["", "**Reasoning chain**", reasoning]
    if alternatives:
        lines += ["", "**Alternatives considered**", alternatives]
    if caveats:
        lines += ["", "**Caveats / limits**", caveats]
    if next_steps:
        lines += ["", "**Open verification steps**", next_steps]

    return "\n".join(lines) + "\n"


def _handle_record_finding(args: dict, ctx: dict) -> dict:
    claim = _one_line(args.get("claim"))
    reasoning = str(args.get("reasoning") or "").strip()
    confidence = _one_line(args.get("confidence")).lower() or "medium"
    alternatives = str(args.get("alternatives") or "").strip()
    caveats = str(args.get("caveats") or "").strip()
    next_steps = str(args.get("next_steps") or "").strip()

    if not claim:
        return {"error": "claim is required: one falsifiable sentence."}
    if confidence not in VALID_CONFIDENCE:
        return {"error": f"confidence must be one of: {', '.join(VALID_CONFIDENCE)}."}
    if not reasoning:
        return {"error": "reasoning is required: explain how the evidence supports the claim."}

    evidence, evidence_error = _parse_evidence(args.get("evidence"))
    if evidence_error:
        return {"error": evidence_error}

    path = findings_path(ctx)
    try:
        existing = path.read_text(encoding="utf-8") if path.is_file() else _HEADER
    except OSError as exc:
        return {"error": f"Could not read findings log: {exc}"}

    finding_id = f"F-{_next_index(existing):03d}"
    block = _format_block(
        finding_id=finding_id,
        claim=claim,
        confidence=confidence,
        evidence=evidence,
        reasoning=reasoning,
        alternatives=alternatives,
        caveats=caveats,
        next_steps=next_steps,
        case_name=path.parent.name or "case",
        recorded=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )

    tmp_path = path.with_suffix(".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.write_text(existing.rstrip() + "\n\n" + block, encoding="utf-8")
        os.replace(tmp_path, path)
    except OSError as exc:
        return {"error": f"Could not write findings log: {exc}"}
    finally:
        tmp_path.unlink(missing_ok=True)

    total = len(re.findall(r"^## F-\d+", existing, re.MULTILINE)) + 1
    return {
        "status": "recorded",
        "finding_id": finding_id,
        "file": str(path),
        "total_findings": total,
    }


REGISTER = [
    {
        "schema": {
            "type": "function",
            "function": {
                "name": "record_finding",
                "description": (
                    "Record a MAJOR, defensible DFIR finding in the case findings log (CER format: claim, "
                    "direct evidence, reasoning chain). Use once per significant conclusion, never for routine "
                    "steps. Evidence must be direct - exact file paths, hashes, row values, or raw command "
                    "output, not a paraphrase of your own summary - and reasoning must link each item to the "
                    "claim while explaining why alternatives were rejected."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "claim": {
                            "type": "string",
                            "description": "One falsifiable sentence stating the finding.",
                        },
                        "confidence": {
                            "type": "string",
                            "description": "high, medium, or low - based on evidence directness and completeness.",
                            "enum": list(VALID_CONFIDENCE),
                        },
                        "evidence": {
                            "type": "array",
                            "description": "Direct evidence items backing the claim.",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "source": {
                                        "type": "string",
                                        "description": "Artifact, file path, DuckDB view/query, or command that produced it.",
                                    },
                                    "detail": {
                                        "type": "string",
                                        "description": "The exact value observed (quotable: hash, timestamp, path, row).",
                                    },
                                },
                                "required": ["source", "detail"],
                            },
                        },
                        "reasoning": {
                            "type": "string",
                            "description": "Reasoning chain: how the evidence supports the claim, step by step.",
                        },
                        "alternatives": {
                            "type": "string",
                            "description": "Alternative explanations considered and why they were rejected.",
                        },
                        "caveats": {
                            "type": "string",
                            "description": "Limits, missing data, or what could invalidate the finding.",
                        },
                        "next_steps": {
                            "type": "string",
                            "description": "Verification still required before this is treated as fact.",
                        },
                    },
                    "required": ["claim", "confidence", "evidence", "reasoning"],
                },
            },
        },
        "handler": _handle_record_finding,
    }
]
