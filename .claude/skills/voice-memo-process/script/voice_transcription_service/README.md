# Voice Transcription Service

A unified voice transcription workflow service based on OpenAI API, providing audio file metadata management, batch transcription, and database status tracking.

## Table of Contents

- [Features](#features)
- [Architecture](#architecture)
- [Installation & Configuration](#installation--configuration)
- [Usage Guide](#usage-guide)
- [Configuration Details](#configuration-details)
- [Database Management](#database-management)
- [FAQ](#faq)

## Features

- **Unified Workflow**: Integrated audio file ingestion, transcription, and status management
- **Database Coordination**: SQLite database automatically tracks file status (pending/processing/completed/error)
- **Batch Processing**: Support for batch selection and processing based on database filter conditions
- **Concurrency Support**: Configurable multi-threaded concurrent transcription for improved efficiency
- **Safety Safeguards**: Built-in file count limits and confirmation mechanisms to prevent accidental operations
- **Flexible Configuration**: Support for both YAML configuration files and environment variables

## Architecture

### Core Modules

```
voice_transcription_service/
├── __init__.py          # Module initialization
├── __main__.py          # Program entry point
├── audio_downsize.py    # ffmpeg re-encode of an oversize memo (see Automatic Downsize)
├── cli.py               # Command-line interface implementation
├── config.py            # Configuration management (YAML + environment variables)
├── config.yaml          # Default configuration file
├── database.py          # SQLite database operations wrapper
├── transcription.py     # OpenAI API transcription service wrapper
└── workflow.py          # Workflow orchestration and safety safeguards
```

### Data Flow

```
Audio Files → Database Ingestion → Status Management → Transcription Processing → Result Output → Status Update
                   ↓                                                                    ↓
              pending status                                                      completed status
```

## Installation & Configuration

### 1. Environment Requirements

- Python 3.11+
- OpenAI API key

### 2. Install Dependencies

```bash
pip install openai pyyaml python-dotenv
```

### 3. Configure API Key

Create a `.env` file (recommended):

```bash
# .env
OPENAI_API_KEY=sk-your-openai-api-key-here

# Or use custom environment variable
OTHER_API_KEY=sk-your-other-api-key-for-voice-transcription
```

Or use command-line argument:

```bash
python -m voice_transcription_service --api-key sk-xxx transcribe ...
```

### 4. Configuration File

Uses `config.yaml` by default, customizable configuration available.

## Usage Guide

### Basic Command Format

```bash
python -m voice_transcription_service [global options] <command> [command options]
```

### Available Commands

#### 1. Sync Command - File Discovery and Database Ingestion

Discover and ingest audio files from a directory into the database:

```bash
# Sync files from default voice memo directory (last 30 days by default)
python -m voice_transcription_service sync

# Sync all files from a custom directory
python -m voice_transcription_service sync --path /path/to/audio

# Sync only files modified in the last 3 days
python -m voice_transcription_service sync --days 3

# Preview files that would be synced (dry-run)
python -m voice_transcription_service sync --dry-run --verbose
```

**Sync Command Options:**

| Option | Description | Default |
|--------|-------------|---------|
| `--path` | Override voice memo directory from config | `sources.voice_memo_directory` |
| `--days` | Only sync files whose *mtime* is within last N days (not recording time — cf. `--db-days`) | `sources.default_sync_days` (30) |
| `--dry-run` | Preview files without actually ingesting | false |
| `--verbose` | Print detailed sync information | false |

#### 2. Transcribe Command - Process Audio Files

##### Transcribe Specific Files

Directly specify file paths, files will be automatically ingested into database:

```bash
python -m voice_transcription_service transcribe \
  ./audio/recording1.m4a \
  ./audio/recording2.mp3 \
  --output-dir ./transcripts
```

##### Batch Process from Database (Recommended)

Process pending files from the last 7 days:

```bash
python -m voice_transcription_service transcribe \
  --from-db \
  --db-status pending \
  --db-days 7 \
  --output-dir ./transcripts \
  --max-files 10 \
  --yes
```

##### Preview Files to be Processed (Dry-Run)

Use `--dry-run` to view the list of files to be processed:

```bash
python -m voice_transcription_service transcribe \
  --from-db \
  --db-status pending \
  --db-days 7 \
  --dry-run
```

Output example:
```
Selected files:
  - /path/to/audio1.m4a (status=pending)
  - /path/to/audio2.mp3 (status=pending)

Skipped files:
  - /path/to/completed.m4a [already-completed]
```

##### Concurrent Processing for Improved Efficiency

Process with 4 concurrent threads:

```bash
python -m voice_transcription_service transcribe \
  --from-db \
  --db-status pending \
  --workers 4 \
  --max-files 20 \
  --yes \
  --output-dir ./transcripts
```

##### Reprocess Completed Files

Use `--force` to force reprocessing:

```bash
python -m voice_transcription_service transcribe \
  --from-db \
  --db-status completed \
  --force \
  --yes \
  --output-dir ./transcripts
```

##### Verbose Logging

Use `--verbose` to view detailed processing information:

```bash
python -m voice_transcription_service transcribe \
  --from-db \
  --db-status pending \
  --verbose \
  --yes
```

### Command-Line Arguments Reference

#### Global Options

| Argument | Description | Example |
|----------|-------------|---------|
| `--config` | Specify configuration file path | `--config /path/to/config.yaml` |
| `--api-key` | Override API key | `--api-key sk-xxx` |

#### Sync Command Options

| Argument | Description | Default |
|----------|-------------|---------|
| `--path` | Override voice memo directory path from config | `sources.voice_memo_directory` |
| `--days` | Only sync files whose *mtime* is within last N days (not recording time — cf. `--db-days`) | `sources.default_sync_days` (30) |
| `--dry-run` | Preview files without ingesting | false |
| `--verbose` | Print detailed sync information | false |

#### Transcribe Command Options

##### File Selection

| Argument | Description | Default |
|----------|-------------|---------|
| `files` | Explicit audio file paths (positional arguments) | - |
| `--from-db` | Select files via database filters | false |
| `--db-status` | Filter by status (pending/processing/completed/error) | - |
| `--db-days` | Only include files whose *recording start* is within the last N days | none — no time window |
| `--db-limit` | Maximum number of database records to fetch | - |
| `--db-offset` | Skip first N database records | 0 |

##### Processing Control

| Argument | Description | Default |
|----------|-------------|---------|
| `--output-dir` | Directory for transcription output | `output.directory` in config or `./transcripts` |
| `--workers` | Number of concurrent threads | 1 |
| `--max-files` | Maximum files allowed in this run | `safeguards.default_max_files` (10) |
| `--yes` | Auto-confirm (skip confirmation prompt) | false |
| `--dry-run` | Preview mode: no API call and no database write of any kind | false |
| `--force` | Force process completed files | false |
| `--verbose` | Verbose output mode | false |

### Workflow Examples

#### Typical Daily Processing Flow

```bash
# 1. Sync new voice memos from the last 7 days
python -m voice_transcription_service sync --days 7 --verbose

# 2. Preview pending files from the last 7 days
python -m voice_transcription_service transcribe \
  --from-db --db-status pending --db-days 7 --dry-run

# 3. Batch process with 4 concurrent workers
python -m voice_transcription_service transcribe \
  --from-db --db-status pending --db-days 7 \
  --output-dir ./transcripts \
  --workers 4 \
  --max-files 20 \
  --yes

# 4. View processing result summary
# Output will display:
# - Completed: X files succeeded
# - Failed: Y files failed
# - Skipped: Z files skipped
```

#### Batch Processing from Last 3 Days

```bash
# Preview files from the last 3 days
python -m voice_transcription_service transcribe \
  --from-db \
  --db-status pending \
  --db-days 3 \
  --dry-run

# Process files from the last 3 days with parallel workers
python -m voice_transcription_service transcribe \
  --from-db \
  --db-status pending \
  --db-days 3 \
  --output-dir ./transcripts \
  --workers 3 \
  --yes \
  --verbose
```

## Configuration Details

### Configuration Priority

Configuration loading follows this priority (high to low):

1. Command-line arguments (`--api-key`, `--output-dir`, etc.)
2. Environment variables (`TRANSCRIBE_SKILL_*` or `OPENAI_API_KEY`)
3. YAML configuration file (`config.yaml`)
4. Code defaults

### Environment Variables List

| Environment Variable | Config Path | Description |
|---------------------|-------------|-------------|
| `OPENAI_API_KEY` | `api_key` | OpenAI API key |
| `OTHER_API_KEY` | `api_key` | Custom API key (higher priority) |
| `TRANSCRIBE_SKILL_MODEL` | `model.name` | Transcription model name |
| `TRANSCRIBE_SKILL_LANGUAGE` | `model.language` | Audio language code |
| `TRANSCRIBE_SKILL_TEMPERATURE` | `model.temperature` | Model temperature parameter |
| `TRANSCRIBE_SKILL_PROMPT` | `model.prompt` | Transcription prompt |
| `TRANSCRIBE_SKILL_SUPPORTED_FORMATS` | `files.supported_formats` | Supported formats (comma-separated) |
| `TRANSCRIBE_SKILL_MAX_FILE_SIZE_MB` | `files.max_file_size_mb` | File size limit, `audio_api` mode only (default 25) |
| `TRANSCRIBE_SKILL_CHAT_MAX_FILE_SIZE_MB` | `files.chat_max_file_size_mb` | File size limit for `chat_completions` mode (default 14) |
| `TRANSCRIBE_SKILL_OUTPUT_DIR` | `output.directory` | Default output directory |
| `TRANSCRIBE_SKILL_OUTPUT_SUFFIX` | `output.suffix` | Output file suffix |
| `TRANSCRIBE_SKILL_VOICE_MEMO_DIR` | `sources.voice_memo_directory` | Voice memo default directory |
| `TRANSCRIBE_SKILL_DEFAULT_SYNC_DAYS` | `sources.default_sync_days` | Default database sync date range (days) |
| `TRANSCRIBE_SKILL_DATABASE_PATH` | `database.path` | Database file path |
| `TRANSCRIBE_SKILL_DATABASE_TIMEOUT` | `database.timeout` | Database connection timeout |
| `TRANSCRIBE_SKILL_MAX_FILES` | `safeguards.default_max_files` | Default max files |
| `TRANSCRIBE_SKILL_CONFIRM_THRESHOLD` | `safeguards.confirmation_threshold` | Confirmation threshold |
| `TRANSCRIBE_SKILL_SKIP_COMPLETED` | `safeguards.skip_completed_by_default` | Skip completed files |
| `TRANSCRIBE_SKILL_RETRY_MAX_ATTEMPTS` | `retry.max_attempts` | Total API attempts per file (incl. the first) |
| `TRANSCRIBE_SKILL_RETRY_BACKOFF_SECONDS` | `retry.backoff_seconds` | Base retry delay, doubled each attempt |
| `TRANSCRIBE_SKILL_DOWNSIZE` | `downsize.enabled` | Re-encode an oversize file instead of failing it (default true) |
| `TRANSCRIBE_SKILL_DOWNSIZE_BITRATE_KBPS` | `downsize.bitrate_kbps` | Bitrate of the compressed copy (default 32) |

### Safety Safeguards

System has three layers of safety safeguards:

1. **File Count Limit**: Default max 10 files (overridable with `--max-files`)
2. **Confirmation Threshold**: Requires `--yes` confirmation when > 3 files
3. **Auto-Skip Completed**: Default skips files with `completed` status (unless using `--force`)

### Retry Behaviour

A 200 response does not guarantee a transcript. Two shapes arrive empty, both
proven transient — replaying the identical payload immediately afterwards
returned a full transcript:

1. **Empty content** — `choices[0].message.content` is `""` or `None`.
2. **No `choices`** — the body omits the key, or carries `"choices": null`.
   Before this was handled, that surfaced as
   `TypeError: 'NoneType' object is not subscriptable`, which named nothing.

`TranscriptionService.transcribe` retries only these two shapes: up to
`retry.max_attempts` (default 3) total attempts, sleeping
`retry.backoff_seconds` doubled each time (2s, then 4s). Each retry prints to
stderr as it happens:

```
↻ 9月5日 21-59 週回顧.m4a: attempt 1 of 3 returned no transcript
  (model=google/gemini-2.5-pro; finish_reason='stop'; content='';
   tokens=prompt:38000/completion:0; reasoning_tokens=3589); retrying in 2s
```

When the bound is exhausted the file ends as `error` with that same diagnostic
recorded in `last_error` — `finish_reason`, whether `choices` was absent, any
`error` object in the body, and token usage including reasoning tokens.

**Cost.** Every attempt is a separately billed call on the full audio payload,
so `max_attempts` is a cost ceiling, not just a patience setting: worst case is
`max_attempts × per-call cost` (~$0.045–$0.075 per attempt for an 18 MB file on
`google/gemini-2.5-pro`). Reasoning models make shape 1 more likely — they can
spend their whole output budget on reasoning tokens before emitting content —
so exposure rises with the model choice. Set `max_attempts: 1` to disable
retrying entirely.

**Boundary with the SDK's own retries.** The `openai` client already retries
HTTP 408, 409, 429 and ≥500, plus connection errors and timeouts, with
`max_retries=2` (3 calls). Those are *not* counted here and are not
double-retried. Everything else — authentication failure, a 4xx that will never
succeed, an unsupported format — fails on the first attempt with no backoff
sleep. An oversize file is no longer in that list: see
[Automatic Downsize](#automatic-downsize).

### File size limits

The two transcription modes do not share a limit, because they do not share a
wire format:

| Mode | Setting | Default | Why |
|------|---------|---------|-----|
| `audio_api` | `files.max_file_size_mb` | 25 MB | OpenAI's documented per-file cap for a multipart upload |
| `chat_completions` | `files.chat_max_file_size_mb` | 14 MB | The audio is base64'd into one JSON body, and Gemini caps an inline request at 20 MB *total* |

The 14 MB figure is derived, not guessed. Measured base64+JSON expansion on
this exact request shape is 1.3334×, so 20 MiB ÷ 1.3336 = **14.997 MB** of raw
audio saturates the body exactly, leaving nothing for the prompt. 14 keeps
~1.4 MB of headroom.

One caveat, recorded honestly: whether OpenRouter forwards the audio inline (so
Gemini's cap binds) or stages it through Google's Files API (so it does not) is
**unverified** — no OpenRouter document addresses it. 14 MB is correct under
either reading, and is in any case far tighter than the 25 MB this mode used to
be allowed. Raising it is a one-line config change if a later probe settles the
question.

### Automatic Downsize

A file over the limit for its mode is not rejected. It is re-encoded to a
temporary speech-grade copy, and that copy is transcribed instead:

```bash
ffmpeg -v error -y -i <input> -ac 1 -ar 16000 -c:a aac -b:a 32k <temp>/<input name>
```

The headroom is real: iOS Voice Memos records 48 kHz stereo at ~98 kbps, about
three times what a transcription model needs to read speech. A measured 46.7-
minute memo went from **32.66 MB to 10.94 MB in 10.5 s**, with ffprobe
reporting the duration unchanged to within 0.04 s. That the result still
transcribes cleanly is field evidence, not a guess — an earlier memo compressed
by hand at exactly these settings produced a note with every proper noun intact.

It announces itself on stderr as it happens:

```
⤓ 3月27日08-29與施志恆和譜生討論智慧化氣候校園計畫.m4a: 32.66 MB exceeds the
  14 MB chat_completions limit; transcribing a 1-channel 32 kbps copy (10.94 MB)
```

The copy lives in a `voice-transcription-downsize-*` temp directory for exactly
the duration of one file's upload and is deleted afterwards, including on
Ctrl-C. Nothing is written next to the original, and the database records what
was actually sent in `sent_file_size` (NULL means the original went as-is).
A `--dry-run` never reaches this code: it spawns no `ffmpeg` and creates no
temp directory.

**Cost.** One successful API call in place of a failed file, plus ~10 s of local
CPU per oversize memo. There is no extra billed call — the downsize happens
before the first attempt, not after a rejection.

**Limits.** One pass, no splitting. At 32 kbps a 14 MB cap is roughly **61
minutes** of audio; a recording longer than that still fails, with an error
naming both the original and compressed sizes and pointing at
`downsize.bitrate_kbps`. Chunking a long recording across several requests is
deliberately not implemented. Set `downsize.enabled: false` (or
`TRANSCRIBE_SKILL_DOWNSIZE=false`) to restore the old fail-fast behaviour.

## Database Management

### Database Structure

`audio_files` table structure:

```sql
CREATE TABLE audio_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,                 -- Filename
    file_path TEXT NOT NULL UNIQUE,         -- Full file path (unique key)
    file_size INTEGER NOT NULL,             -- File size (bytes)
    created_at TEXT NOT NULL,               -- Recording start time (see below)
    modified_at TEXT NOT NULL,              -- File modification time
    status TEXT NOT NULL DEFAULT 'pending', -- Status: pending/processing/completed/error
    status_updated_at TEXT,                 -- Status update time
    output_path TEXT,                       -- Transcription output directory (see below)
    output_filename TEXT,                   -- Transcription output filename
    attempt_count INTEGER DEFAULT 0,        -- Attempt count
    last_attempt_at TEXT,                   -- Last attempt time
    last_error TEXT,                        -- Last error message
    synced_at TEXT,                         -- Sync time
    processing_owner TEXT,                  -- hostname:pid holding 'processing' (see below)
    sent_file_size INTEGER                  -- Bytes actually uploaded; NULL = original sent as-is
);
```

`sent_file_size` differs from `file_size` only when the memo was too large for
the mode's limit and was transcribed from a compressed copy — see
[Automatic Downsize](#automatic-downsize). It is cleared whenever a file
re-enters `processing`, so it always describes the run that produced the
current transcript.

Databases created before the key moved to `file_path` are migrated automatically
on the next run; keying on the bare filename made two same-named memos in
different folders collide. The rewrite runs inside one explicit transaction —
sqlite3 auto-commits bare DDL, so without it a crash partway through would leave
an empty new table that the next run reads as "already migrated".

Because the key is now the path, a memo that *moves* would look like a brand-new
file. `ingest_file` therefore adopts an existing row whose filename, size *and*
mtime match and whose recorded path no longer resolves, instead of inserting a
duplicate and transcribing it a second time. Name and size alone are too weak:
iOS reuses names, so a fresh recording that happened to match a deleted memo's
name and byte count would inherit that row's `completed` status and never be
transcribed. Matching mtime too makes adoption mean "the same bytes, at a new
path" — a move preserves mtime, a different recording does not.

`created_at` is the moment the recording *started*, resolved by
`recording_time.py` from the filename (`6月25日 22-48.m4a`), falling back to the
container metadata via `ffprobe`, then to the file's mtime. It is not the
filesystem creation time. The filename carries no year, so the year is inferred
from mtime — which only works while mtime still sits close behind the recording.
Past a week, the filename step abstains rather than guess (a memo re-downloaded
from cloud storage two years later would otherwise be stamped with this year),
and resolution falls through to `ffprobe`, which carries the real year. Rows written before this change stored the filesystem
timestamp, and sync never revisits a path it already knows — so every `sync`
first re-derives `created_at` from the filename (a regex, no I/O) and repairs any
row still on the old basis, keeping one clock across the table.

A row that already produced a transcript is the exception: its `created_at` is
baked into the note name on disk (`voice-memo_<YYYYMMDD_HHMMSS>_<title>.md`), so
moving it would leave the database unable to name a file it wrote. The repair
touches such a row only when the new timestamp is the one its own
`output_filename` already carries — pulling the row back into line with the disk,
never away from it. Rows with an output whose name cannot be checked (no
filename recorded) are left as they are.

Note this makes `--db-days` a window on *recording* time, not on when the file
reached this machine: a memo recorded months ago but synced today sits outside
any recent window. That is why `--db-days` now defaults to no window at all.
The two day-flags therefore read different clocks — `sync --days` filters on the
file's mtime (when it landed here), `transcribe --db-days` on `created_at` (when
it was recorded). The same N gives different sets.

The filename carries no year, so it is taken from the file's mtime, stepping back
one year if that lands in the future. A memo older than roughly a year — or one
copied here with a fresh mtime — therefore resolves to the wrong year silently;
`ffprobe` is not consulted, because the filename matched.

**Known limitation — `output_path` is written, never read.** This service records
where it wrote the transcript. The refine stage then consumes that transcript and
writes its note into `OBSIDIAN_VAULT_DIR` under the same filename, without
updating this column, so the value goes stale as soon as the transcript moves.
Nothing in the pipeline reads it back: the two stages are coupled by
`RAW_TRANSCRIPT_DIR`, not by this database. Treat it as a last-known-location
hint. (The existing rows were repaired by hand to point at each note's current
location; one row whose output no longer exists anywhere was set to NULL.)

### Status Flow

```
pending → processing → completed
                ↓
              error

processing → pending    (reclaimed, see below)
```

A run killed mid-flight leaves its row in `processing`, where `--db-status
pending` can no longer see it. The next run hands any row idle there for more
than `STALE_PROCESSING_MINUTES` (60) back to `pending` — unless the run that
claimed it is still alive. Rows in `error` stay put until you select them
explicitly with `--db-status error`.

Age alone does not mean abandoned: the memo directory is a FUSE mount, so a read
inside a transcription can block with no timeout, and a run genuinely still
working on a file can hold its row well past an hour. `mark_processing` therefore
stamps the row with `processing_owner` (`hostname:pid`), and the reclaim skips
any row whose owner process is still running on this host — otherwise a second
run started in another terminal would pay for the same audio again and write over
the transcript the first run is about to produce. Rows with no owner recorded
(claimed before this column existed, or claimed on another machine) fall back to
the time rule alone. The column is cleared when the file reaches `completed`,
`error`, or is reclaimed. Databases created before it are migrated in place with
a single `ALTER TABLE ... ADD COLUMN` on the next run.

`attempt_count` counts **runs, not network attempts**. Retries happen inside
`TranscriptionService.transcribe`, beneath the single `mark_processing` →
`mark_error` pair in `workflow.py`, so a file that only succeeded on its third
API call still shows `attempt_count = 1`, and a file that exhausted its retries
lands in `error` once with the full diagnostic in `last_error`. To count API
calls, read the `↻` lines on stderr.

`--dry-run` never writes to the database — but it has to *see* the writes the
real run performs first (the schema migration, the reclaim pass, the `created_at`
repair), or it would preview a different set of files than the run it is
previewing. So it performs them against a throwaway copy of the database file and
reports them as "Would reclaim" / "Would repair". The real file is left
byte-identical, and one that does not exist yet is not created at all; a
present-but-empty file is fine, the copy gets the schema. A file named explicitly
on the command line is still not ingested: it is listed as `not-yet-synced`
rather than registered as `pending`, since merely looking at a file must not
queue it for the next `--from-db` batch to transcribe.

The preview reports the safeguards rather than failing on them: it prints its
selection, then — if the batch exceeds `--max-files` or would need `--yes` — one
line saying a real run would stop there, and exits 0. Since `--db-days` defaults
to no window, the documented preview of every pending row routinely exceeds the
limit, and answering "what would this run?" with an error makes the preview
useless. The limits still refuse the real run.

- **pending**: Initial state, waiting for processing
- **processing**: Currently transcribing
- **completed**: Transcription successfully completed
- **error**: Transcription failed (error info recorded)

## FAQ

### Q1: How to handle API key errors?

**Error message**:
```
Error: OpenAI API key is required. Set OTHER_API_KEY or OPENAI_API_KEY, or provide --api-key via CLI.
```

**Solutions**:
1. Create `.env` file and add `OPENAI_API_KEY=sk-xxx`
2. Or set environment variable: `export OPENAI_API_KEY=sk-xxx`
3. Or use command-line argument: `--api-key sk-xxx`

### Q2: How to handle file count limit errors?

**Error message**:
```
RuntimeError: Refusing to process 15 files (limit 10). Use --max-files 15 to override explicitly.
```

**Solutions**:
```bash
# Method 1: Increase --max-files limit
python -m voice_transcription_service transcribe \
  --from-db --db-status pending \
  --max-files 15 --yes

# Method 2: Modify config file
# config.yaml
safeguards:
  default_max_files: 15
```

### Q3: How to handle confirmation prompts?

**Error message**:
```
RuntimeError: 8 files selected. Re-run with --yes to confirm this batch.
```

**Solution**:
Add `--yes` parameter to skip confirmation prompt:
```bash
python -m voice_transcription_service transcribe \
  --from-db --db-status pending --yes
```

### Q4: How to reprocess failed files?

```bash
# View failed files
python -m voice_transcription_service transcribe \
  --from-db --db-status error --dry-run

# Reprocess (will automatically reset status)
python -m voice_transcription_service transcribe \
  --from-db --db-status error \
  --force --yes
```

### Q5: How to batch process in chunks?

Use `--db-limit` and `--db-offset` for pagination:

```bash
# Process files 1-10
python -m voice_transcription_service transcribe \
  --from-db --db-status pending \
  --db-limit 10 --db-offset 0 --yes

# Process files 11-20
python -m voice_transcription_service transcribe \
  --from-db --db-status pending \
  --db-limit 10 --db-offset 10 --yes
```

### Q6: How to improve processing speed?

```bash
# Use concurrent processing (recommended 4-8 threads)
python -m voice_transcription_service transcribe \
  --from-db --db-status pending \
  --workers 8 \
  --max-files 50 \
  --yes
```

Note: Concurrency count depends on network bandwidth and API rate limits.

### Q7: What is the output file naming convention?

Output filename format: `voice-memo_YYYYMMDD_HHMMSS` + `output.suffix`

The system reads the actual creation time (`created_at` field) from the database and formats it as `YYYYMMDD_HHMMSS` to avoid collisions when multiple memos are created on the same day.

Example:
- Input: `recording_20250321.m4a` (created on 2025-03-21 14:30:45)
- Output: `voice-memo_20250321_143045.md`

Note: If the database has no creation time information (rare case), it falls back to using the file's modified time for `YYYYMMDD_HHMMSS`.

That stamp is minute-precision and usually derived from the filename, so two
same-named memos in different folders resolve to the *same* name — a collision
the old `UNIQUE(filename)` schema hid by never storing the second memo at all.
The first writer keeps the plain name and later ones get `-2`, `-3`; the name is
claimed both with an exclusive create and in `output_filename`, so parallel
workers cannot both take it. The database half of the claim matters once the
refine stage moves a transcript into the vault: the name is then free on disk,
but still spoken for. Re-transcribing a file reuses whatever name it was written
under before, so `--force` overwrites its own transcript rather than growing a
second copy.

### Q8: How does file synchronization work?

The `sync` command discovers audio files from a directory and registers them in the database:

```bash
# Sync from default voice memo directory (last 30 days)
python -m voice_transcription_service sync

# Sync only recent files (last 3 days)
python -m voice_transcription_service sync --days 3

# Preview sync without ingesting
python -m voice_transcription_service sync --dry-run --verbose
```

The sync process:
1. Recursively scans the directory for audio files
2. Filters by modification time (if `--days` specified)
3. Checks for supported formats (.m4a, .mp3, .wav, .mp4)
4. Avoids duplicates (skips files already in database)
5. Ingests new files with `pending` status

## Module Overview

### cli.py
Command-line interface implementation, responsible for:
- Argument parsing and validation
- Subcommand routing (`sync`, `transcribe`)
- User interaction and result display

### config.py
Configuration management, provides:
- YAML configuration file loading
- Environment variable parsing
- Configuration priority merging
- Configuration validation

### database.py
SQLite database wrapper, includes:
- Table structure management (`audio_files`)
- File ingestion (`ingest_file`)
- Status queries and updates
- Filtering and pagination queries

### transcription.py
OpenAI API wrapper, handles:
- Audio file validation (format, size)
- API calls (Whisper Transcription)
- Response parsing and error handling
- Bounded retry for transcript-less responses

### workflow.py
Workflow orchestration, coordinates:
- Target file preparation (`prepare_targets`)
- Safety safeguard checks (`enforce_safeguards`)
- File synchronization (`sync_files`)
- Concurrent execution management (`run`)
- Status updates and result summary

## Tech Stack

- **Language**: Python 3.11+
- **API**: OpenAI API
- **Database**: SQLite 3
- **Concurrency**: ThreadPoolExecutor
- **Configuration**: PyYAML + python-dotenv

## License

This project is community member use only.

## Changelog

### v2.3 (Current Version)
- Per-mode file size limits: `files.max_file_size_mb` now applies to `audio_api`
  only, and `chat_completions` gets `files.chat_max_file_size_mb` (default 14,
  derived from Gemini's 20 MB inline-request cap) — see
  [File size limits](#file-size-limits)
- An oversize file is re-encoded to a temporary mono/16 kHz/32 kbps copy and
  transcribed, instead of failing — see [Automatic Downsize](#automatic-downsize)
- New `downsize.*` config block with matching `TRANSCRIBE_SKILL_DOWNSIZE` and
  `TRANSCRIBE_SKILL_DOWNSIZE_BITRATE_KBPS` environment variables
- New `sent_file_size` column records the bytes actually uploaded; NULL means
  the original was sent unmodified
- The size-limit error now names the mode whose limit applied

### v2.2
- Bounded retry for 200 responses that carry no transcript (empty content, or a
  body with no `choices`) — see [Retry Behaviour](#retry-behaviour)
- New `retry.max_attempts` / `retry.backoff_seconds` config, with matching
  `TRANSCRIBE_SKILL_RETRY_*` environment variables
- Exhausted retries now report `finish_reason`, absent `choices`, the body's
  `error` object and token usage instead of `'NoneType' object is not subscriptable`
- Non-retryable errors (auth, 4xx, bad format) still fail fast. Oversize files
  did too until v2.3, which downsizes them instead
- Documented `attempt_count` as counting runs, not network attempts

### v2.1
- Added `sync` command for file discovery and database ingestion
- Support for `--days` filter using `default_sync_days` config
- Dry-run mode for sync preview
- Enhanced batch processing workflow

### v2.0
- Unified CLI interface (`voice_transcription_service`)
- Integrated database coordination and workflow orchestration
- Support for concurrent processing and batch operations
- Added safety safeguard mechanisms
- Improved error handling and status management

---

**Last Updated**: 2026-09-13
**author**: Tony Huang (https://www.youtube.com/@tonyhhq)
**date-updated**: 2026-09-13
