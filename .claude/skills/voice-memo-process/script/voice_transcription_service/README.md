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
| `--days` | Only sync files modified within last N days | `sources.default_sync_days` (30) |
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
| `--days` | Only sync files modified within last N days | `sources.default_sync_days` (30) |
| `--dry-run` | Preview files without ingesting | false |
| `--verbose` | Print detailed sync information | false |

#### Transcribe Command Options

##### File Selection

| Argument | Description | Default |
|----------|-------------|---------|
| `files` | Explicit audio file paths (positional arguments) | - |
| `--from-db` | Select files via database filters | false |
| `--db-status` | Filter by status (pending/processing/completed/error) | - |
| `--db-days` | Only include files created within last N days | `sources.default_sync_days` (30) |
| `--db-limit` | Maximum number of database records to fetch | - |
| `--db-offset` | Skip first N database records | 0 |

##### Processing Control

| Argument | Description | Default |
|----------|-------------|---------|
| `--output-dir` | Directory for transcription output | `output.directory` in config or `./transcripts` |
| `--workers` | Number of concurrent threads | 1 |
| `--max-files` | Maximum files allowed in this run | `safeguards.default_max_files` (10) |
| `--yes` | Auto-confirm (skip confirmation prompt) | false |
| `--dry-run` | Preview mode (don't actually call API) | false |
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
| `TRANSCRIBE_SKILL_MAX_FILE_SIZE_MB` | `files.max_file_size_mb` | File size limit |
| `TRANSCRIBE_SKILL_OUTPUT_DIR` | `output.directory` | Default output directory |
| `TRANSCRIBE_SKILL_OUTPUT_SUFFIX` | `output.suffix` | Output file suffix |
| `TRANSCRIBE_SKILL_VOICE_MEMO_DIR` | `sources.voice_memo_directory` | Voice memo default directory |
| `TRANSCRIBE_SKILL_DEFAULT_SYNC_DAYS` | `sources.default_sync_days` | Default database sync date range (days) |
| `TRANSCRIBE_SKILL_DATABASE_PATH` | `database.path` | Database file path |
| `TRANSCRIBE_SKILL_DATABASE_TIMEOUT` | `database.timeout` | Database connection timeout |
| `TRANSCRIBE_SKILL_MAX_FILES` | `safeguards.default_max_files` | Default max files |
| `TRANSCRIBE_SKILL_CONFIRM_THRESHOLD` | `safeguards.confirmation_threshold` | Confirmation threshold |
| `TRANSCRIBE_SKILL_SKIP_COMPLETED` | `safeguards.skip_completed_by_default` | Skip completed files |

### Safety Safeguards

System has three layers of safety safeguards:

1. **File Count Limit**: Default max 10 files (overridable with `--max-files`)
2. **Confirmation Threshold**: Requires `--yes` confirmation when > 3 files
3. **Auto-Skip Completed**: Default skips files with `completed` status (unless using `--force`)

## Database Management

### Database Structure

`audio_files` table structure:

```sql
CREATE TABLE audio_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL UNIQUE,          -- Filename (unique)
    file_path TEXT NOT NULL,                -- Full file path
    file_size INTEGER NOT NULL,             -- File size (bytes)
    created_at TEXT NOT NULL,               -- File creation time
    modified_at TEXT NOT NULL,              -- File modification time
    status TEXT NOT NULL DEFAULT 'pending', -- Status: pending/processing/completed/error
    status_updated_at TEXT,                 -- Status update time
    output_path TEXT,                       -- Transcription output path
    output_filename TEXT,                   -- Transcription output filename
    attempt_count INTEGER DEFAULT 0,        -- Attempt count
    last_attempt_at TEXT,                   -- Last attempt time
    last_error TEXT,                        -- Last error message
    synced_at TEXT                          -- Sync time
);
```

### Status Flow

```
pending → processing → completed
                ↓
              error
```

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

### v2.1 (Current Version)
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

**Last Updated**: 2025-10-24
**author**: Tony Huang (https://www.youtube.com/@tonyhhq)
**date-updated**: 2025-10-24
