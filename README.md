# Voice Memo Vault

An Obsidian vault that serves as the output destination for an automated voice memo processing pipeline. Audio recordings are transcribed and refined into structured Markdown notes using two Claude Code agent skills.

## How It Works

```
voice-memo-recordings/  ←  raw .m4a audio files (synced from device)
        ↓
  [voice-memo-process skill]
        ↓
raw-transcript/         ←  plain transcribed text as .md files
        ↓
  [refine-memo-for-obsidian skill]
        ↓
Voice-Memo-Vault/       ←  structured Obsidian notes with frontmatter & tags
```

## Directory Structure

| Path | Purpose |
|---|---|
| `raw-transcript/` | Raw transcription output (plain text `.md`) |
| `.env` | API key + machine-specific paths (gitignored) |
| `.env.example` | Template to copy to `.env` |
| `note-property/tag-list.md` | Canonical tag definitions for vault tagging |

## Agent Skills

### `voice-memo-process`

Transcribes audio files into raw text notes.

- Syncs audio metadata to a local SQLite database
- Transcribes audio via API (OpenRouter + Gemini, using `chat_completions` mode with base64-encoded audio)
- Writes output to `raw-transcript/`

### `refine-memo-for-obsidian`

Cleans up raw transcripts into Obsidian-ready notes.

- Adds YAML frontmatter (`title`, `recorded`, `created`, `description`, `tags`)
- Writes a `## Note Summary` section with key bullet points
- Outputs to `Voice-Memo-Vault/`

## Configuration

All machine-specific settings live in `.env` (gitignored). On a fresh clone,
copy the template and edit it:

```bash
cp .env.example .env
```

`.env` holds the API key and every path the pipeline uses (a leading `~`
expands to your home directory):

```env
OPENAI_API_KEY=<your-openrouter-api-key>
VOICE_MEMO_DIR=~/gdrive/Inbox/voice-memo-recordings   # raw .m4a recordings
RAW_TRANSCRIPT_DIR=~/Documents/voice-memo-agent-skills/raw-transcript
OBSIDIAN_VAULT_DIR=~/Documents/mdNote/Voice-Memo-Vault # refined notes destination
NOTE_PROPERTY_DIR=~/Documents/voice-memo-agent-skills/note-property
```

Because the paths come from `.env`, nothing in `config.yaml` or the skills is
hardcoded to a machine — running on a new computer only requires editing `.env`.

The transcription pipeline targets OpenRouter (`https://openrouter.ai/api/v1`) with `google/gemini-2.5-flash` by default.

## Tags

Tags are defined in `note-property/tag-list.md`:

| Tag | Use |
|---|---|
| `inbox` | Items to triage |
| `todo` | Actionable tasks |
| `question` | Unresolved questions |
| `self-reflection` | Personal reflections |
| `ai-dev` | AI development topics |
| `yt-content` | YouTube content ideas |
| `teaching` | Classroom observations and lesson reflections |

## Running the Skills

From within Claude Code, invoke the skills via the slash command interface:

- `/voice-memo-process` — transcribe new audio files
- `/refine-memo-for-obsidian` — refine raw transcripts into Obsidian notes

To verify a skill's Python script can run without side effects, see `.claude/commands/prep-agent-skill.md`.
