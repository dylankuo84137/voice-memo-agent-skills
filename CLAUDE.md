# CLAUDE.md

This file provides guidance to Claude Code in this repository.

## What This Repo Is

A Claude Code workspace for an automated voice memo → Obsidian note pipeline. It is a fork of the original by Tony Huang, modified to use **OpenRouter** instead of the OpenAI native audio API.

The pipeline has two stages, each driven by a Claude Code skill:

```
voice-memo-recordings/   (raw .m4a audio)
        ↓  /voice-memo-process
raw-transcript/                         (plain-text .md transcripts)
        ↓  /refine-memo-for-obsidian
Voice-Memo-Vault/    (structured Obsidian notes)
```

## Agent Skills

| Skill | Slash command | SKILL.md |
|---|---|---|
| Transcribe audio via OpenRouter | `/voice-memo-process` | `.claude/skills/voice-memo-process/SKILL.md` |
| Refine transcripts into Obsidian notes | `/refine-memo-for-obsidian` | `.claude/skills/refine-memo-for-obsidian/SKILL.md` |
| Verify a skill's script runs cleanly | `/prep-agent-skill` | `.claude/commands/prep-agent-skill.md` |

## Python Script (`voice_transcription_service`)

The transcription backend lives at `.claude/skills/voice-memo-process/script/voice_transcription_service/`.

### Install dependencies

```bash
cd .claude/skills/voice-memo-process
pip install -r script/voice_transcription_service/requirements.txt
```

### Key CLI commands

```bash
# Run from .claude/skills/voice-memo-process/

# Sync audio metadata from the last 7 days (dry-run preview)
python -m script.voice_transcription_service sync --days 7 --dry-run --verbose

# Transcribe all pending files from the last 7 days
python -m script.voice_transcription_service transcribe \
  --from-db --db-status pending --db-days 7 \
  --workers 4 --max-files 20 --yes

# Reprocess failed files
python -m script.voice_transcription_service transcribe \
  --from-db --db-status error --force --yes

# Show help
python -m script.voice_transcription_service --help
```

### Configuration

Config file: `.claude/skills/voice-memo-process/script/voice_transcription_service/config.yaml`

Key settings modified from the original fork:

```yaml
api:
  base_url: "https://openrouter.ai/api/v1"
  transcription_mode: "chat_completions"   # uses base64 audio, not /v1/audio/transcriptions
model:
  name: "google/gemini-2.5-flash"
  language: "zh"
```

Config priority: CLI args → env vars (`TRANSCRIBE_SKILL_*` / `OPENAI_API_KEY`) → `config.yaml` → code defaults.

`.env` at the repo root holds `OPENAI_API_KEY=<openrouter-key>` — the script auto-loads it.

### Two transcription modes

`transcription_mode` in `config.yaml` switches between the two backends:

| Mode | `transcription_mode` | Endpoint | Provider |
| --- | --- | --- | --- |
| OpenAI native | `audio_api` | `/v1/audio/transcriptions` | OpenAI |
| Chat completions | `chat_completions` | `/v1/chat/completions` (base64 audio) | OpenRouter (or any provider lacking the audio endpoint) |

Current repo default is `chat_completions` with `google/gemini-2.5-flash` via OpenRouter. To use OpenAI native, set `transcription_mode: "audio_api"`, clear `base_url`, and use an OpenAI model name (e.g. `gpt-4o-transcribe`).

### Fork differences vs. upstream

- `config.py` adds `base_url` and `transcription_mode` fields.
- `transcription.py` adds `_transcribe_via_chat()`: encodes audio as base64 and posts to `/v1/chat/completions`.

### Database

SQLite at `.claude/skills/voice-memo-process/script/audio_metadata.db`. Tracks each audio file through states: `pending → processing → completed` (or `error`). Status updates happen atomically alongside transcription.

## Obsidian Note Structure

Refined notes written to `~/Documents/mdNote/Voice-Memo-Vault/` follow this exact structure (no additional section headings):

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

Tags are defined in `note-property/tag-list.md`. Only use tags from that file.
