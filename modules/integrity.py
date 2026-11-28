"""Evidence integrity: sha256 manifest plus a fast path/size/mtime fingerprint guard."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

MANIFEST_FILENAME = "evidence_manifest.json"
MAX_LISTED = 20


def manifest_path(ctx: dict) -> Path:
    return Path(ctx["base_output"]) / MANIFEST_FILENAME


def _iter_files(root: Path):
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            path = Path(dirpath) / name
            try:
                if path.is_symlink() or not path.is_file():
                    continue
            except OSError:
                continue
            yield path


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def relative_key(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def fingerprint(root: Path) -> dict[str, tuple[int, int]]:
    """Cheap path -> (size, mtime_ns) map used to detect changes without hashing."""
    out: dict[str, tuple[int, int]] = {}
    for path in _iter_files(root):
        try:
            st = path.stat()
        except OSError:
            continue
        out[relative_key(root, path)] = (st.st_size, st.st_mtime_ns)
    return out


def snapshot(root: Path, on_progress=None) -> dict[str, dict]:
    """Hashes every file under `root`; unreadable entries are reported, not fatal."""
    manifest: dict[str, dict] = {}
    files = list(_iter_files(root))
    for index, path in enumerate(files, start=1):
        if on_progress is not None:
            on_progress(index, len(files), path.name)
        try:
            st = path.stat()
            entry = {
                "size": st.st_size,
                "mtime_ns": st.st_mtime_ns,
                "sha256": _hash_file(path),
            }
        except OSError as exc:
            entry = {"size": 0, "mtime_ns": 0, "sha256": None, "error": str(exc)}
        manifest[relative_key(root, path)] = entry
    return manifest


def save(ctx: dict, manifest: dict[str, dict], root: Path) -> Path:
    path = manifest_path(ctx)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"source": str(root), "files": manifest}
    path.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    return path


def load(ctx: dict) -> tuple[dict[str, dict], Path | None]:
    path = manifest_path(ctx)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}, None
    if not isinstance(payload, dict):
        return {}, None
    files = payload.get("files")
    source = payload.get("source")
    return (files if isinstance(files, dict) else {}), (Path(source) if isinstance(source, str) else None)


def diff(root: Path, manifest: dict[str, dict], rehash_changed: bool = True) -> dict[str, list[str]]:
    """Compares the tree to a manifest and returns added/removed/modified (content-aware)."""
    current = fingerprint(root)
    added = sorted(set(current) - set(manifest))
    removed = sorted(set(manifest) - set(current))
    modified: list[str] = []

    for key in sorted(set(current) & set(manifest)):
        old = manifest[key]
        size, mtime_ns = current[key]
        if size != old.get("size") or mtime_ns != old.get("mtime_ns"):
            if not rehash_changed:
                modified.append(key)
                continue
            try:
                if _hash_file(root / key) != old.get("sha256"):
                    modified.append(key)
            except OSError:
                modified.append(key)

    return {"added": added, "removed": removed, "modified": modified}


def changed_since(root: Path, baseline: dict[str, tuple[int, int]]) -> list[str]:
    """Fast check used after every command: compares size/mtime only."""
    current = fingerprint(root)
    changed = [key for key, value in current.items() if baseline.get(key) != value]
    changed += [key for key in baseline if key not in current]
    return sorted(set(changed))


def summarize(diff_result: dict[str, list[str]]) -> str:
    """Short human-readable summary used by commands and alerts."""
    added, removed, modified = (
        len(diff_result["added"]),
        len(diff_result["removed"]),
        len(diff_result["modified"]),
    )
    if not (added or removed or modified):
        return "No changes."

    parts = [f"{added} added", f"{removed} removed", f"{modified} modified"]
    lines = [", ".join(parts) + "."]
    for label, items in (("added", diff_result["added"]), ("removed", diff_result["removed"]), ("modified", diff_result["modified"])):
        for item in items[:MAX_LISTED]:
            lines.append(f"  {label}: {item}")
        if len(items) > MAX_LISTED:
            lines.append(f"  ... (+{len(items) - MAX_LISTED} more {label})")
    return "\n".join(lines)
