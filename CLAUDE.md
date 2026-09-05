# CLAUDE.md

This file is the **AI Layer** for this project: the always-loaded contract that tells Claude how to think, work, and verify here. Keep it lean; a bloated system prompt starts every session already degraded. Deep reference lives in `README.md` and each skill's `SKILL.md` — link to it, don't inline it.

---

## Smart Zone

LLMs decay over a long context and forget across sessions. The working rules that follow:

- **One job per session.** Plan, implement, and review are separate sessions (see [PIV Loop](#piv-loop)). Don't review code in the same session that wrote it.
- **Prefer `/clear` over compaction.** Clearing returns you to a known baseline. Re-prime from files instead.
- **Offload research to sub-agents.** Exploring the codebase or a transcript can burn huge context. Delegate it; pull back only the summary.
- **Files are the only durable memory.** The handoff between sessions is a written artifact (a plan, a report), never recollection.

## PIV Loop

The per-task inner loop: **Plan → Implement → Validate**. Each phase is a *fresh session*; the plan file is the only interface between Plan and Implement.

1. **Plan** (`/plan`) — New session. Load the task plus the relevant slice of the code, explore (delegate heavy research), and emit a context-rich plan. No code is written.
2. **Implement** (`/implement`) — Reopen a fresh session. Read the plan, verify its assumptions against the real code, execute task by task, and run the project's checks after every task before moving on.
3. **Validate** (`/validate`) — Own fresh session. Run the verify gate plus any E2E checklist, then hand to human review. Pass → merge. Problem → drop into System Evolution via `/retroactive`.

Anytime you prompt the same thing more than three times, promote it to a command or skill.

## System Evolution

The outer loop. When a PIV Loop surfaces a bug or a miss, don't just patch the surface code — treat it as a signal that the **AI Layer itself** is incomplete. Run a *retroactive session* (`/retroactive`) and look in four places: the commands, on-demand context, the global rules in this file, and the plan/PRD templates.

## Communication

When reporting information, be extremely concise and sacrifice grammar for the sake of concision.

## Conventions

Behavioral guardrails for every change. These bias toward caution over speed; for trivial tasks, use judgment.

**Think before coding.** State assumptions explicitly; if uncertain, ask. If multiple interpretations exist, surface them all — don't silently pick one.

**Simplicity first.** Write the minimum code that solves the problem. No speculative features, no abstractions for single-use code.

**Surgical changes.** Touch only what the task requires. Match existing style. Remove only the orphans *your* change created; leave pre-existing dead code (mention it instead).

**Verification-led.** Define how you'll verify work *before* doing it. A claim about how an external component behaves (an API's response shape, a config override) is not grounded until a probe has run it — assert it only after the experiment, never from docs alone.

---

### Project specifics

A Claude Code workspace for an automated voice memo → Obsidian note pipeline. A fork of the original by Tony Huang, modified to use **OpenRouter** (base64 audio via `/v1/chat/completions`) instead of the OpenAI native audio API.

**Stack**: Python 3 transcription backend (`voice_transcription_service`, run as a module) · SQLite metadata DB · OpenRouter + `google/gemini-2.5-pro` for audio→text · two Claude Code skills drive the pipeline · Obsidian Markdown output.

**Architecture** — two stages, one skill each:

```
voice-memo-recordings/   (raw .m4a audio)
        ↓  /voice-memo-process      → transcribe via OpenRouter
raw-transcript/          (plain-text .md transcripts)
        ↓  /refine-memo-for-obsidian → structure + tag
Voice-Memo-Vault/        (Obsidian notes with frontmatter)
```

| Skill / command | Purpose | Reference |
|---|---|---|
| `/voice-memo-process` | Sync metadata + transcribe audio | `.claude/skills/voice-memo-process/SKILL.md` |
| `/refine-memo-for-obsidian` | Refine transcripts into Obsidian notes | `.claude/skills/refine-memo-for-obsidian/SKILL.md` |
| `/prep-agent-skill` | Verify a skill's script runs cleanly | `.claude/commands/prep-agent-skill.md` |

- **Transcription backend**: `.claude/skills/voice-memo-process/script/voice_transcription_service/`. SQLite at `script/audio_metadata.db` tracks each file `pending → processing → completed` (or `error`). Config in `config.yaml`; `transcription_mode` switches `chat_completions` (OpenRouter, base64) vs `audio_api` (OpenAI native). Config priority: CLI args → env vars → `config.yaml` → defaults.
- **All machine-specific paths and the API key live in `.env`** (gitignored, auto-loaded). A fresh clone only edits `.env` (copy from `.env.example`); a leading `~` expands to home. Vars: `OPENAI_API_KEY`, `VOICE_MEMO_DIR`, `RAW_TRANSCRIPT_DIR`, `OBSIDIAN_VAULT_DIR`, `NOTE_PROPERTY_DIR`.

**Verify commands** (no build/test suite):

```bash
# from .claude/skills/voice-memo-process/

# one-time setup (fresh clone has no .venv — it's gitignored):
python3 -m venv .venv
./.venv/bin/pip install -r script/voice_transcription_service/requirements.txt

# verify:
./.venv/bin/python -m script.voice_transcription_service sync --days 7 --dry-run --verbose
./.venv/bin/python -m script.voice_transcription_service --help
```
Run via `./.venv/bin/python -m script.voice_transcription_service ...`, not bare `python`/`python3` — the deps (`python-dotenv`, etc.) live in `.venv`, not system Python.
Run `/prep-agent-skill` to confirm a skill's Python script runs without side effects.

**Refined note structure** — written to `OBSIDIAN_VAULT_DIR` as
`voice-memo_<YYYYMMDD_HHMMSS>_<title>.md` (timestamp prefix from the transcript,
title from frontmatter), exactly this shape, no extra headings:

```markdown
---
title: "..."
recorded: YYYY-MM-DD
created: YYYY-MM-DD
description: "one sentence"
tags:
  - "tag-name"
---

## Note Summary

<bullet-point outline>

<body — oral style converted to writing style, paragraphed, no sub-headings>
```

**Do-not**:
- Never hardcode machine paths — read them from `.env`. The pipeline reads no hardcoded paths.
- Only use tags defined in `note-property/tag-list.md`; never invent new ones.
- Never commit `.env` (it holds the API key); keep `.env.example` in sync when adding a variable.
- Preserve the refined-note structure above exactly — frontmatter keys, `## Note Summary`, no other section headings.
