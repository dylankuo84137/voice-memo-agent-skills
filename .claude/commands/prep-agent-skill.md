---
description: ensure each Claude Agent skill’s `script` can run.
---

# Agent Skill Script Quick-Check Guide

Use this lean checklist to verify each Claude Agent skill’s `script` can run. Focus on help output and dry-run paths with no side effects.

## Objective

- Confirm a skill’s Python module under `script/<pkg>` imports and runs:
  - Help prints successfully
  - Safe subcommands run in `--dry-run` mode

## Scope

- Applies to skills under `.claude/skills/*` that include a `script/<pkg>` Python module.

## Fast Path Checklist

- Prerequisites
  - Python 3.11+ and `pip` available
  - Terminal starting in the skill directory that contains `script/`

- Install dependencies
  - If present: `pip install -r script/<pkg>/requirements.txt`
  - If missing: skip

- Environment secrets
  - Ensure a project‑root `.env` file exists with an `OPENAI_API_KEY` entry.
  - Do not print or paste the key in terminals, scripts, or commit history.
  - The script auto‑loads the nearest `.env` up the directory tree; no manual export needed.

- Working directory
  - Change into the skill root (the folder that contains `script/`)
  - Alternative: add that folder to `PYTHONPATH` before running

- Run basic checks (no side effects)
  - Help: `python -m script.<pkg> --help`
  - Dry-run, safe preview (pick what exists for the skill):
    - Example 1 (sync preview): `python -m script.<pkg> sync --dry-run --path .`
    - Example 2 (db selection preview): `python -m script.<pkg> transcribe --from-db --dry-run`

- Pass criteria
  - Commands exit with code 0
  - No `ImportError`/`ModuleNotFoundError`
  - Errors only occur when expected (e.g., nonexistent path without `--path`)

- Common fixes
  - Import errors → run from the correct skill directory or set `PYTHONPATH`
  - Missing packages → install via `requirements.txt`
  - Missing API key → create/update project‑root `.env` with `OPENAI_API_KEY`
  - Path errors → supply `--path` to a harmless existing directory for dry-run

---

## Example: Voice Memo Process Skill

- Location: `.claude/skills/voice-memo-process`
- Package: `script.voice_transcription_service`
- Requirements: `script/voice_transcription_service/requirements.txt`

Steps

1) Change directory
   - `cd .claude/skills/voice-memo-process`

2) Install dependencies
   - `pip install -r script/voice_transcription_service/requirements.txt`

3) Verify .env presence (no key exposure)
   - Ensure the project root has a `.env` file with `OPENAI_API_KEY=<redacted>`
   - Do not echo the key; avoid adding `.env` to version control.

4) Help output
   - `python -m script.voice_transcription_service --help`

5) Dry-run previews (no API calls or file writes)
   - Sync preview (scan a harmless directory):
     - `python -m script.voice_transcription_service sync --dry-run --path . --verbose`
   - Database selection preview:
     - `python -m script.voice_transcription_service transcribe --from-db --dry-run --verbose`

Passing if both commands run without crashes and print informative output.

