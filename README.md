# DFIR-Harness

**Goal:** Automate artifact discovery, parsing, and structured querying while keeping the analyst strictly in control—requiring human verification and exposing clear, verifiable reasoning at every step.

Still a work in progress

Necessary Environment Variables:
```
# NVIDIA NIM (paste the exact model ID shown on the NIM page)
$env:OPENAI_BASE_URL="https://integrate.api.nvidia.com/v1"
$env:OPENAI_API_KEY="nvapi-your-key-here"
$env:LLM_MODEL="mistralai/mistral-large-4-1t-a52b"

# Or switch to Groq in the same terminal
$env:OPENAI_BASE_URL="https://api.groq.com/openai/v1"
$env:OPENAI_API_KEY="gsk_your-key-here"
$env:LLM_MODEL="llama-3.3-70b-versatile"
```

External Tools (CLI + pip)
---------------------------
Anything already on PATH (or importable) is usable through `run_terminal`. To give the LLM
extra tools, register paths and install packages from inside the CLI — these persist across
sessions in `~/.dfir-harness/toolbox.json` (override with `DFIR_TOOLBOX_CONFIG`):

```
/tools                                       # show registered dirs/files, pip packages, AI tools
/tools add C:\tools\hayabusa                 # a directory: prepended to PATH for every command
/tools add "C:\Program Files\7-Zip\7z.exe"   # a single file: exposed by absolute path only
/tools remove 2                              # or /tools remove <path>
/tools pip install yara-python pefile        # installs into this interpreter (auto-detected after)
```

Registered directories are put on PATH for both the process and each `run_terminal`
subprocess. Registered files (standalone exes living in shared folders, e.g. 7-Zip) are not
added to PATH — the system prompt lists their absolute paths so the model calls them
directly (`& "C:\Program Files\7-Zip\7z.exe" l archive.7z`). Every installed pip package is
listed too, auto-detected via `importlib.metadata`, so packages added with a normal
`pip install` outside the harness are picked up automatically.

Analyst Check-ins
-----------------
The agent pauses every few tool rounds to report what it has established and the single
next step it plans to take, then waits for you (default: every 3 rounds):

```
+- Analyst Check-in (round 3) ------------------------------------------------+
| Findings                                                                    |
| target.exe has an alternate data stream + a suspicious Run key value        |
|                                                                             |
| Next step                                                                   |
| dump and hash the ADS payload, then check Autoruns for persistence          |
|                                                                             |
| Not verified yet                                                            |
| payload not extracted; no reputation check                                  |
+-----------------------------------------------------------------------------+
Continue? [Y]es / [n]o (or 'n <redirection>'): _
```

Enter/y continues; `n <direction>` (or `n` plus the feedback prompt) redirects the model
instead, and a bare `n` stops the turn. Adjust the cadence in-session with
`/checkin <rounds|on|off>` — `off` gives the model its full 10-round budget uninterrupted.

Findings Log (CER)
------------------
Whenever the agent reaches a major conclusion it records it with the `record_finding` tool, which
appends a CER entry to `output/<case>/findings.md` (claim, direct evidence, reasoning chain, plus
alternatives, caveats and open verification steps). Routine lookups are not logged. The check-in
prompt also reports how many findings are already recorded, so nothing major is left implicit.

```
## F-001 - target.exe executed the ADS payload

- **Recorded (UTC):** 2026-11-27T02:40:11Z
- **Confidence:** high
- **Status:** agent-recorded, pending analyst verification

**Direct evidence**
1. `evtx view / Security.evtx` - EventId 4688: ProcessName=C:\Users\bob\Downloads\target.exe ...
2. `mft view` - target.exe:payload ADS size=48KB, modified 2026-11-27T02:13:52Z

**Reasoning chain**
4688 shows the process start; the ADS predates it by 8s, so the stream is the executed payload.

**Alternatives considered** ...   **Caveats / limits** ...   **Open verification steps** ...
```

Evidence items are deliberately strict — every one needs a `source` (artifact, view/query, or
command) and a quotable `detail` (path, hash, timestamp, row value), so each claim can be traced
back to raw data. Every write prints a transcript confirmation (id, confidence, evidence count,
total, path) or a rejection reason, and `/findings` displays the whole log any time.

Safety Layers
-------------
The harness assumes it runs inside a VM; on top of that, the evidence tree and the model's
side effects are guarded:

- **Kernel write protection** — on start the evidence directory gets a deny ACE
  (`icacls … /deny "<user>:(OI)(CI)(DE,WD,AD,WEA,WA)"`), so any create/modify/rename/delete
  beneath it fails at the OS level, regardless of the command. Reads are unaffected. The ACE is
  removed on exit (also via a crash handler). There is no switch to disable it; `/guard` shows
  the state plus the manual `icacls` restore command (needed only if a crash leaves it behind).
- **Tamper detection** — after every `run_terminal`, the ACE is re-checked; if it was removed it
  is re-applied and audited as `guard_tampered`. A cheap path/size/mtime fingerprint of the
  evidence tree runs too, so any change (including one made after removing the ACE in the same
  command) is flagged, audited as `evidence_modified`, and reported to the model.
- **Scratch confinement** — `run_terminal` defaults to `root="scratch"`
  (`output/<case>/_scratch`), so relative-path writes land outside the case and evidence.
  `/scratch` shows usage, `/scratch clean` wipes it; it is cleaned automatically on exit.
- **Tamper-evident audit log** — `output/<case>/audit.jsonl`, hash-chained (`prev` + `hash`):
  proposed / approved / declined / executed (+ exit code and stdout hash) / timeout / error /
  evidence_modified / guard_applied / guard_tampered / guard_restored / finding / cleanup.
  `/audit [n]` shows recent events, `/audit verify` detects edits, deletions or reordering.
- **Evidence integrity baseline** — sha256 manifest of the evidence tree at start
  (`output/<case>/evidence_manifest.json`); `/integrity verify` reports added/removed/modified and
  `/integrity snapshot` re-baselines (e.g. after an accepted, documented change). The baseline
  itself always runs.

Limits (deliberate): the model runs as your account, so it *can* deliberately remove the deny
ACE — that is re-applied, alerted and audited, and evidence changes are still detected; elevated
processes bypass ACLs; FAT/exFAT and network shares cannot be guarded (warned at start); writes
to unrelated system paths are audited but not blocked. The VM remains the real containment
boundary for detonating malware.

Tests: `python -m unittest discover -s tests` (guard, audit, integrity).

Template Tool:
```python
def _my_handler(args: dict, ctx: dict) -> dict:
    # ctx gives you access to ctx["con"], ctx["base_output"], ctx["mounted_views"], ctx["state"]
    return {"converted": "..."}

REGISTER = [
    {
        "schema": {
            "type": "function",
            "function": {
                "name": "convert_filetime",
                "description": "Convert a 64-bit Windows FILETIME integer into UTC ISO-8601.",
                "parameters": {
                    "type": "object",
                    "properties": {"filetime": {"type": "string"}},
                    "required": ["filetime"],
                },
            },
        },
        "handler": _my_handler,
    }
]
