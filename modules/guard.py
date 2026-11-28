"""Kernel-enforced evidence guard using a deny ACE on the evidence root."""

from __future__ import annotations

import getpass
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

_ACL_RIGHTS = "(OI)(CI)(DE,WD,AD,WEA,WA)"
_GRANTED_FS = ("NTFS", "REFS")
_MISSING = "not-applied"


@dataclass(frozen=True)
class GuardHandle:
    """Result of applying the deny ACE; `applied` is False when the guard was skipped."""

    path: str
    applied: bool
    reason: str


def current_user() -> str:
    """Returns DOMAIN\\user for the account running the harness."""
    try:
        name = getpass.getuser()
        domain = os.environ.get("USERDOMAIN", "")
        return f"{domain}\\{name}" if domain else name
    except Exception:
        return os.environ.get("USERNAME", "")


def filesystem_of(path: Path) -> str | None:
    """Returns the volume filesystem name (e.g. NTFS) or None when unavailable."""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", "(Get-Item -LiteralPath $args[0]).PSDrive.FileSystem", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        value = (result.stdout or "").strip()
        return value or None
    except (OSError, subprocess.SubprocessError):
        return None


def is_elevated() -> bool:
    """True when the current process can bypass ACLs (admin token)."""
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _run_icacls(args: list[str]) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["icacls", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    output = (result.stdout or result.stderr or "").strip()
    return result.returncode == 0, output


def restore_command(path: Path) -> str:
    """Exact command an analyst can run by hand to unlock the evidence tree."""
    return f'icacls "{path}" /remove:d "{current_user()}"'


def apply(path: Path) -> GuardHandle:
    """Applies the recursive deny ACE to `path` (writes only, reads stay allowed)."""
    if os.name != "nt":
        return GuardHandle(str(path), False, "Evidence guard requires Windows")
    if not path.is_dir():
        return GuardHandle(str(path), False, f"Evidence path is not a directory: {path}")

    filesystem = filesystem_of(path)
    if filesystem and filesystem.upper() not in _GRANTED_FS:
        return GuardHandle(str(path), False, f"Guard unavailable on {filesystem} volume (needs NTFS/ReFS)")

    user = current_user()
    ok, output = _run_icacls([str(path), "/deny", f"{user}:{_ACL_RIGHTS}"])
    if not ok:
        return GuardHandle(str(path), False, f"icacls failed: {output.splitlines()[-1] if output else 'unknown error'}")
    return GuardHandle(str(path), True, "evidence write-protected (deny ACE applied)")


def restore(handle: GuardHandle | None) -> bool:
    """Removes the deny ACE; idempotent and safe to call when nothing was applied."""
    if handle is None or not handle.applied or os.name != "nt":
        return False
    ok, _ = _run_icacls([handle.path, "/remove:d", current_user()])
    return ok


def status(path: Path, handle: GuardHandle | None = None) -> bool:
    """True when the deny ACE is still present on the evidence root."""
    if handle is not None and not handle.applied:
        return False
    if os.name != "nt":
        return False
    ok, output = _run_icacls([str(path)])
    if not ok:
        return False
    user = current_user().split("\\")[-1].lower()
    for line in output.splitlines():
        if user in line.lower() and "(DENY)" in line.upper():
            return True
    return False
