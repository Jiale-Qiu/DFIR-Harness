"""Persistent registry of analyst-provided tool directories, plus pip package discovery."""

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


def load() -> list[str]:
    """Loads registered tool directories, tolerating a missing/corrupt config."""
    try:
        raw = json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    if not isinstance(raw, dict):
        return []
    return [d for d in raw.get("directories", []) if isinstance(d, str)]


def _save(directories: list[str]) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps({"directories": directories}, indent=2), encoding="utf-8")
    try:
        os.replace(tmp_path, path)
    finally:
        tmp_path.unlink(missing_ok=True)


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(os.path.expanduser(path)))


def add_directory(raw: str) -> tuple[bool, str]:
    """Registers a tool directory and adds it to this session's PATH."""
    target = Path(os.path.expanduser(raw.strip().strip('"').strip("'")))
    if not target.is_dir():
        return False, f"'{raw}' is not a directory."

    config = load()
    resolved = str(target.resolve())
    if any(_norm(existing) == _norm(resolved) for existing in config):
        return False, f"'{resolved}' is already registered."

    config.append(resolved)
    try:
        _save(config)
    except OSError as exc:
        return False, f"Could not save toolbox config: {exc}"

    apply_environment()
    return True, f"Registered tool directory: {resolved}"


def remove_directory(selector: str) -> tuple[bool, str]:
    """Removes a registered directory by path or 1-based index from the /tools listing."""
    config = load()
    selector = selector.strip()
    match: str | None = None

    if selector.isdigit():
        index = int(selector) - 1
        if 0 <= index < len(config):
            match = config[index]
    else:
        match = next((d for d in config if _norm(d) == _norm(selector)), None)

    if match is None:
        return False, f"No registered directory matches '{selector}'. Run /tools to list them."

    config.remove(match)
    try:
        _save(config)
    except OSError as exc:
        return False, f"Could not save toolbox config: {exc}"

    apply_environment()
    return True, f"Removed tool directory: {match}"


def build_env() -> dict[str, str]:
    """Returns a copy of the environment with registered directories prepended to PATH."""
    env = os.environ.copy()
    directories = [d for d in load() if os.path.isdir(d)]
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
    directories = [d for d in load() if os.path.isdir(d)]
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
    """Builds the prompt block describing registered directories and installed packages."""
    directories = load()
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
            lines.append(f"- Missing/unavailable: {', '.join(missing)}")

    packages = installed_packages()
    if packages:
        shown = list(packages.items())[:_MAX_LISTED_PACKAGES]
        listing = ", ".join(f"{name} {version}" for name, version in shown)
        if len(packages) > len(shown):
            listing += f", ... (+{len(packages) - len(shown)} more)"
        lines.append(f"Python packages installed for `{sys.executable}`: {listing}")

    return "\n".join(lines)
