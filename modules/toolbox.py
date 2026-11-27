"""Persistent registry of analyst-provided tool directories/files, plus pip package discovery."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from functools import lru_cache
from importlib import metadata
from pathlib import Path

CONFIG_ENV_VAR = "DFIR_TOOLBOX_CONFIG"
_EXECUTABLE_SUFFIXES = (".exe", ".cmd", ".bat", ".com", ".ps1", ".py", ".pyw")
_MAX_LISTED_EXECUTABLES = 40
_MAX_LISTED_PACKAGES = 120
_IGNORED_PACKAGES = {"pip", "setuptools", "wheel", "pkg-resources", "distribute"}


def config_path() -> Path:
    """Returns the persisted toolbox config location (override with DFIR_TOOLBOX_CONFIG)."""
    override = os.environ.get(CONFIG_ENV_VAR)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".dfir-harness" / "toolbox.json"


def _read_raw() -> dict:
    try:
        raw = json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _str_list(raw: dict, key: str) -> list[str]:
    values = raw.get(key, [])
    if not isinstance(values, list):
        return []
    return [v for v in values if isinstance(v, str)]


def load_directories() -> list[str]:
    """Returns registered tool directories, tolerating a missing/corrupt config."""
    return _str_list(_read_raw(), "directories")


def load_files() -> list[str]:
    """Returns registered individual tool files, tolerating a missing/corrupt config."""
    return _str_list(_read_raw(), "files")


def _save(directories: list[str], files: list[str]) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    payload = {"directories": directories, "files": files}
    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    try:
        os.replace(tmp_path, path)
    finally:
        tmp_path.unlink(missing_ok=True)


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(os.path.expanduser(path)))


def add_path(raw: str) -> tuple[bool, str]:
    """Registers a tool directory (added to PATH) or a single tool file (path only)."""
    target = Path(os.path.expanduser(raw.strip().strip('"').strip("'")))
    if not target.exists():
        return False, f"'{raw}' is not an existing file or directory."

    resolved = str(target.resolve())

    if target.is_dir():
        directories = load_directories()
        if any(_norm(existing) == _norm(resolved) for existing in directories):
            return False, f"'{resolved}' is already registered."
        directories.append(resolved)
        try:
            _save(directories, load_files())
        except OSError as exc:
            return False, f"Could not save toolbox config: {exc}"
        apply_environment()
        return True, f"Registered tool directory: {resolved}"

    if target.is_file():
        files = load_files()
        if any(_norm(existing) == _norm(resolved) for existing in files):
            return False, f"'{resolved}' is already registered."
        files.append(resolved)
        try:
            _save(load_directories(), files)
        except OSError as exc:
            return False, f"Could not save toolbox config: {exc}"
        return True, f"Registered tool file: {resolved}"

    return False, f"'{raw}' is not a file or directory."


def remove_path(selector: str) -> tuple[bool, str]:
    """Removes a registered directory or file by path or 1-based index from the /tools listing."""
    directories = load_directories()
    files = load_files()
    selector = selector.strip()
    match: str | None = None

    if selector.isdigit():
        combined = [*directories, *files]
        index = int(selector) - 1
        if 0 <= index < len(combined):
            match = combined[index]
    else:
        match = next((p for p in (*directories, *files) if _norm(p) == _norm(selector)), None)

    if match is None:
        return False, f"No registered entry matches '{selector}'. Run /tools to list them."

    if match in directories:
        directories.remove(match)
    else:
        files.remove(match)

    try:
        _save(directories, files)
    except OSError as exc:
        return False, f"Could not save toolbox config: {exc}"

    apply_environment()
    return True, f"Removed: {match}"


def build_env() -> dict[str, str]:
    """Returns a copy of the environment with registered directories prepended to PATH."""
    env = os.environ.copy()
    directories = [d for d in load_directories() if os.path.isdir(d)]
    if not directories:
        return env

    entries = {_norm(p) for p in env.get("PATH", "").split(os.pathsep) if p}
    missing = [d for d in directories if _norm(d) not in entries]
    if missing:
        current = env.get("PATH", "")
        env["PATH"] = os.pathsep.join([*missing, current]) if current else os.pathsep.join(missing)
    return env


_BASE_PATH: str | None = None


def _base_path() -> str:
    """Returns the PATH captured before this process added any registered directories."""
    global _BASE_PATH
    if _BASE_PATH is None:
        _BASE_PATH = os.environ.get("PATH", "")
    return _BASE_PATH


def apply_environment() -> None:
    """Puts exactly the registered directories on this process PATH so children inherit them."""
    directories = [d for d in load_directories() if os.path.isdir(d)]
    base = _base_path()
    os.environ["PATH"] = os.pathsep.join([*directories, base]) if directories else base


def _list_executables(folder: Path) -> list[str]:
    names: list[str] = []
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                if len(names) >= _MAX_LISTED_EXECUTABLES:
                    break
                if entry.name.startswith(".") or not entry.is_file():
                    continue
                if entry.name.lower().endswith(_EXECUTABLE_SUFFIXES):
                    names.append(entry.name)
    except OSError:
        return []
    return sorted(names)


@lru_cache(maxsize=1)
def _scan_installed_packages() -> dict[str, str]:
    packages: dict[str, str] = {}
    try:
        for dist in metadata.distributions():
            name = dist.metadata["Name"]
            if not name or name.lower() in _IGNORED_PACKAGES:
                continue
            packages[name] = dist.version
    except Exception:
        return {}
    return dict(sorted(packages.items(), key=lambda item: item[0].lower()))


def installed_packages(refresh: bool = False) -> dict[str, str]:
    """Returns {distribution name: version}; refresh re-scans after external pip installs."""
    if refresh:
        _scan_installed_packages.cache_clear()
    return _scan_installed_packages()


def install_packages(raw_names: list[str]) -> tuple[bool, str]:
    """pip-installs packages into this interpreter (they are auto-detected afterwards)."""
    names = [name.strip() for name in raw_names if name.strip()]
    if not names:
        return False, "No package names given."

    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", *names],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"pip install failed: {exc}"

    output = (result.stdout or result.stderr or "").strip().splitlines()
    tail = " | ".join(output[-3:]) if output else ""
    if result.returncode != 0:
        return False, f"pip install exited {result.returncode}. {tail}"

    _scan_installed_packages.cache_clear()
    return True, f"Installed: {', '.join(names)}"


def describe_tools() -> str:
    """Builds the prompt block describing registered tools and installed packages."""
    directories = load_directories()
    files = load_files()
    lines: list[str] = []

    installed_dirs = [d for d in directories if os.path.isdir(d)]
    if installed_dirs:
        lines.append("Registered tool directories (prepended to PATH for every `run_terminal` call):")
        for directory in installed_dirs:
            names = _list_executables(Path(directory))
            detail = ", ".join(names) if names else "(no executables detected)"
            lines.append(f"- {directory}: {detail}")
        missing = [d for d in directories if not os.path.isdir(d)]
        if missing:
            lines.append(f"- Missing/unavailable directories: {', '.join(missing)}")

    present_files = [f for f in files if os.path.isfile(f)]
    if present_files:
        lines.append(
            "Registered tool files (individual executables/scripts, NOT on PATH - call them by absolute "
            "path and quote paths containing spaces, e.g. "
            '`& "C:\\Program Files\\7-Zip\\7z.exe" l archive.7z`):'
        )
        for file in present_files:
            hint = " (run with python)" if file.lower().endswith((".py", ".pyw")) else ""
            lines.append(f"- {file}{hint}")
        missing_files = [f for f in files if not os.path.isfile(f)]
        if missing_files:
            lines.append(f"- Missing/unavailable files: {', '.join(missing_files)}")

    packages = installed_packages()
    if packages:
        shown = list(packages.items())[:_MAX_LISTED_PACKAGES]
        listing = ", ".join(f"{name} {version}" for name, version in shown)
        if len(packages) > len(shown):
            listing += f", ... (+{len(packages) - len(shown)} more)"
        lines.append(f"Python packages installed for `{sys.executable}`: {listing}")

    return "\n".join(lines)
