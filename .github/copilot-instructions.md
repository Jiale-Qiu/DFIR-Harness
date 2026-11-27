# DFIR-Harness — Copilot Instructions

## Overview

Windows DFIR (digital forensics & incident response) harness. It scans an evidence
directory, shells out to Eric Zimmerman (EZ) Tools to parse artifacts into CSVs, mounts
those CSVs as DuckDB views, and runs an interactive LLM agent ("analyst in the loop")
that queries the views and calls a small toolbelt. Every SQL/terminal/read action that
touches evidence requires analyst approval.

Pipeline (see `main.py`):

1. `modules/preflight.py` — verifies `EvtxECmd.exe`, `RECmd.exe`, `PECmd.exe`,
   `MFTECmd.exe` are on PATH; can append a user-supplied folder to the session PATH.
2. `modules/scanner.py` — classifies files by magic bytes (`SIGNATURES`) plus filename
   heuristics (`$J` / UsnJrnl). Categories: `evtx`, `registry`, `prefetch`, `mft`, `usnjrnl`.
3. `modules/parsers/*.py` — thin per-artifact wrappers that build an EZ-Tools command
   line and call the shared runner `run_ez_command` in `modules/parsers/_common.py`.
   Output goes to `output/<case>/<category>/*.csv`.
4. `modules/triage.py` — mounts each CSV folder as a DuckDB view (`ARTIFACT_SOURCES` in
   `modules/common.py` defines folder + filename pattern) and hands control to the CLI.
5. `modules/llm_cli.py` — REPL: system prompt grounded in live view schemas, directory
   tree and host shell context; streams OpenAI-compatible completions; runs up to
   `MAX_TOOL_ROUNDS` tool calls per turn; handles `/help`, `/pane`, `/autosql`, reasoning
   level and thinking-mode slash commands.
6. `modules/llm_ui.py` — Rich rendering (streaming markdown/thinking), result tables, and
   the human-in-the-loop confirmation prompts (`confirm_sql`, `confirm_terminal`,
   `confirm_action`, `declined_payload`).
7. `modules/table_viewer.py` + `modules/pane_bridge.py` — optional right-hand Windows
   Terminal split pane that renders query results from a JSON state file; degrades
   gracefully when `WT_SESSION` is absent.
8. `modules/tools/` — auto-discovered tool plugins (`__init__.py` imports every module
   and reads its `REGISTER` list). Each module exports `REGISTER = [{"schema": ...,
   "handler": fn}]` where handlers take `(args, ctx)` and return a dict. Current tools:
   `query_duckdb`, `run_terminal`, `read_file`, `list_directory`, `calculate`,
   `decode_base64`, `decode_hex`, `xor_decode`, `open_timeline_explorer`.
9. `modules/toolbox.py` — persistent registry of analyst-supplied tool directories
   (`~/.dfir-harness/toolbox.json`, override with `DFIR_TOOLBOX_CONFIG`) plus pip package
   discovery. Registered directories are prepended to PATH (process-wide and per
   `run_terminal` subprocess); installed packages are auto-detected via
   `importlib.metadata`. The CLI manages it through `/tools list|add|remove|pip install`,
   and the system prompt lists what is available.

`ctx` passed to handlers: `con` (DuckDB connection), `mounted_views`, `base_output`
(parsed CSV root), `main_directory` (raw evidence root), `state` (reasoning, thinking
mode, `auto_sql`, `history`, `pane_path`).

Conventions worth preserving: camelCase for top-level pipeline functions
(`scanDirectory`, `testFileMagic`, `run_triage`, `mount_views`) and snake_case for
private helpers; type hints everywhere; `from __future__ import annotations` in newer
modules; Rich consoles via `Console(highlight=False)`; SQL results capped at
`MAX_SQL_ROWS = 50` and tool payloads trimmed before reaching the LLM.

## Run & test

No test suite, linter config, or dependency manifest exists yet.

```powershell
# Install runtime deps (rich, duckdb, openai are imported directly)
py -3 -m pip install rich duckdb openai

# Required environment (NVIDIA NIM, Groq, or any OpenAI-compatible endpoint)
$env:OPENAI_BASE_URL="https://integrate.api.nvidia.com/v1"
$env:OPENAI_API_KEY="nvapi-..."
$env:LLM_MODEL="mistralai/mistral-large-4-1t-a52b"

# Full pipeline: scan -> confirm parse -> DuckDB views -> LLM CLI
py -3 main.py "C:\cases\case01"

# Useful flags
py -3 main.py "C:\cases\case01" --dry-run      # skip tool checks, print commands only
py -3 main.py "C:\cases\case01" --clear        # wipe output/<case> first
py -3 main.py "C:\cases\case01" --skip-parse   # reuse existing CSVs in output/<case>

py -3 main.py --help

# Inside the investigation CLI: register extra CLI tools / pip packages (persisted)
#   /tools add C:\tools\hayabusa
#   /tools pip install yara-python

# Syntax/import smoke check (no tests exist)
py -3 -m compileall -q main.py modules
```

EZ Tools must be on PATH (or pasted at the preflight prompt). `RECmd.exe` parsing also
needs `BatchExamples\Kroll_Batch.reb` beside it. Output always lands in
`output/<evidence-folder-name>/`.

Verification is manual: run with `--dry-run` to inspect generated EZ-Tools commands, or
exercise the CLI end-to-end against a small case directory. Never invoke real parsers
against evidence to "test" a code change.

## Coding rules (strict)

- **Keep changes minimal.** Touch only the lines needed for the requested behavior. No
  drive-by refactors, reformatting, renaming, or reordering of unrelated code.
- **No over-engineering.** Do not add config systems, plugin layers, base classes,
  dependency-injection containers, retry frameworks, caching, async, or new
  abstractions unless the task explicitly requires them. A plain function in the
  existing module is the default answer.
- **No new files or dependencies without need.** Prefer editing existing modules; do not
  add libraries when the stdlib or an already-imported package (rich, duckdb, openai)
  suffices.
- **Match local style.** Private helpers are prefixed `_`, public pipeline functions keep
  their existing camelCase names, type hints on all new signatures, `Path` over string
  paths, Rich for all user-facing output.
- **Respect the safety model.** Do not bypass or weaken the HITL confirmations, the
  read-only SQL validation in `modules/tools/duckdb_sql.py`, or the path confinement in
  `modules/tools/file_reader.py` / `_resolve_cwd` in `terminal.py`. New tools must keep
  the `REGISTER` schema/handler contract and return a dict.
- **Handle failures defensively.** Subprocess/OS/DB failures are caught and surfaced with
  a short Rich message rather than raised to the top level, matching `run_ez_command` and
  `mount_views`. The same applies to `modules/toolbox.py`: a missing or corrupt config must
  load as empty, and PATH edits must never raise.
- **Keep persisted user config backward compatible.** `modules/toolbox.py` owns
  `~/.dfir-harness/toolbox.json`; read unknown/missing keys with defaults and stay
  stdlib-only there.
- **Bounds stay bounded.** Tool output fed back to the LLM must remain truncated by the
  existing `MAX_*` constants; do not raise caps without an explicit request.
- **No speculative tests or docs.** Only add tests/docs for behavior the change
  introduces, and only if the task asks for them.
