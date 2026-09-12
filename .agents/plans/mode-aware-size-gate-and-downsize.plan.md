# Plan: Mode-aware size gate + automatic mono-32k downsize

## Summary
The transcription service enforces a single flat 25 MB file-size gate
(`transcription.py:64-69`) that describes only the OpenAI native audio API. The
service's actual default mode is `chat_completions` against OpenRouter, whose
downstream Gemini inline-data cap is 20 MB **per whole request** — measured
base64+JSON expansion of 1.3334x puts the real raw-byte ceiling at **14.997 MB**,
i.e. roughly *half* of what the gate currently allows. This plan (1) splits the
cap per mode — `audio_api` keeps 25 MB, `chat_completions` gets 14 MB — and
(2) replaces the hard failure on an oversize file with an automatic ffmpeg
downsize to mono/16 kHz/32 kbps, transcribing the temp copy instead. The
downsize is not speculative: a previous session already did it by hand for the
2026-09-12 memo and the resulting note is good, so this promotes a proven manual
step into the pipeline. Chunking/splitting is explicitly out of scope.

## User Story
As the operator of the voice-memo pipeline I want long recordings to transcribe
without me hand-running ffmpeg, and I want the size gate to reflect the limit
that actually applies to the mode I run, so that a 47-minute memo is processed on
the first attempt instead of landing in `error` (or silently burning retries on a
payload the provider will not accept).

## Metadata
| Field | Value |
|-------|-------|
| Type | ENHANCEMENT (+ latent BUG_FIX: the chat-mode cap is ~11 MB too loose) |
| Complexity | MEDIUM |
| Systems affected | `config.py`, `config.yaml`, `transcription.py`, `workflow.py`, `database.py`, new `audio_downsize.py`, `README.md`, `.env.example` |
| Issue | N/A |

## Assumptions & Risks

| Claim | VERIFIED / ASSUMED | Evidence (probe command + result) or mitigation |
|-------|--------------------|--------------------------------------------------|
| `ffmpeg -ac 1 -ar 16000 -c:a aac -b:a 32k` brings both real oversize memos well under 14 MB, fast, without losing audio | **VERIFIED** | `ffmpeg -v error -y -i <file> -ac 1 -ar 16000 -c:a aac -b:a 32k out.m4a`: 32.7 MB / 2799.70 s -> **10.94 MB in 10.5 s**, ffprobe duration 2799.659 s (-0.04 s, container rounding); 25.8 MB / 2199.65 s -> **8.58 MB**. |
| Base64+JSON expansion is 1.3334x, so a 20 MB request body corresponds to **14.997 MB** of raw audio | **VERIFIED** | Built the exact `_transcribe_via_chat` body shape (`input_audio.data` + 691-byte text prompt) over both probe files and measured `len(json.dumps(body).encode())`: 10.94 MB -> 14.59 MB body (ratio 1.3334), 8.58 MB -> 11.45 MB (1.3334). Derived ceiling `20 MiB / 1.3336 = 14.997 MB`. **Therefore the cap is 14, not 15** — 15 MB raw saturates the 20 MB body exactly, leaving zero room for the prompt and JSON scaffolding. |
| The real prompt is small enough to ignore in the budget | **VERIFIED** | `TRANSCRIBE_SKILL_PROMPT` in `.env` is 417 chars / **691 UTF-8 bytes**; `config.yaml`'s fallback `model.prompt` is 174 bytes. Audio dominates by four orders of magnitude. |
| 32 kbps / 16 kHz mono is good enough for Traditional-Chinese transcription of these memos | **VERIFIED (field evidence, not a fresh probe)** | The 2026-09-12 row's `last_error` reads `transcribed from a mono 32kbps copy: original 25.77 MB exceeds max_file_size_mb 25`, and its output note `voice-memo_20260912_214400_一週回顧….md` exists at 14,982 bytes with correct proper nouns (Robin, 佳芳, 雅瑜, Phoebe, 仕揚科技, Turing Tumble). A 36.7-minute memo survived this exact transcode end to end. |
| ffmpeg fails loudly and leaves no partial output on bad input | **VERIFIED** | Fed it a text file with an `.m4a` suffix: `moov atom not found` / `Invalid data found when processing input`, **exit 183**, and `bad-out.m4a` was never created. So `returncode != 0` is a reliable signal and there is nothing to clean up on that path. |
| ffmpeg/ffprobe are present | **VERIFIED** | `command -v ffmpeg` -> `/usr/bin/ffmpeg`, `ffprobe` -> `/usr/bin/ffprobe`. Still handled as missing-tolerant: `_from_ffprobe`'s `except (OSError, subprocess.SubprocessError)` idiom is mirrored, and a missing binary yields a clear oversize+ffmpeg-unavailable error rather than a traceback. |
| `workflow.run()` is unreachable under `--dry-run`, so a transcode placed there creates no temp files in a preview | **VERIFIED (by reading the control flow)** | `cli.py:355` `return 0` inside `if args.dry_run:` returns before `workflow.run()` is called at `cli.py:363`; `run()` takes no `dry_run` parameter at all. Additionally a dry run operates on a `tempfile.TemporaryDirectory` snapshot DB (`cli.py:221-226`), so DB writes are already sandboxed. **Task 8 still asserts this empirically** rather than resting on the reading. |
| Gemini's documented 20 MB inline-request cap is the limit that actually binds through OpenRouter | **ASSUMED** | Documented for the Gemini API ("Maximum request size is 20 MB total (including prompts and all files)", https://ai.google.dev/gemini-api/docs/audio). **Unverified**: whether OpenRouter forwards inline (cap binds) or transparently stages via Google's Files API (cap does not bind); no OpenRouter doc addresses it, and its own audio docs state no size limit at all. Probing the edge with an invalid key accepted bodies up to 200 MB with a 401, which proves transport only, not post-auth validation. **Does NOT block this plan**: 14 MB is correct under *either* reading — it is strictly more conservative than today's 25 MB, and every real file lands far below it. If a later authenticated bisect shows the cap does not bind, raising `chat_max_file_size_mb` is a one-line config change with no code impact. |
| Oversize payloads currently fail as an *empty transcript* that the bounded retry masks, rather than a 413 | **ASSUMED (weak, non-load-bearing)** | Correlation only: the largest payload ever to complete through the current chat path is 17.8 MB raw / 23.7 MB body and it took **6 attempts**, while every payload <= 11.5 MB body took 1-2. The 32.7 MB `completed` row is **not** evidence — it is dated 2026-03-29, before this repo's initial commit (2026-05-17 per `git log`), so it did not go through this code path; and the 25.8 MB row went through as a compressed copy. Nothing in the plan depends on this being true; it is recorded so a future probe knows what to look for. |
| No payload above 20 MB of *body* has ever verifiably succeeded through `chat_completions` | **VERIFIED (from the DB)** | `sqlite3` over `audio_metadata.db`: sizes/attempts are 32.7 MB (att=1, 2026-03-29, pre-repo), 25.8 MB (att=1, transcoded copy), 17.8 MB (att=6), then 8.6/7.4/7.3/5.2/3.5/2.0/0.4 MB (att=1-2). |
| Adding a DB column is safe on the existing database | **ASSUMED — de-risk first** | `ensure_schema` runs hand-rolled idempotent migrations (`_add_processing_owner`, `database.py:181-196`) and is called on every run including dry runs. Risk: `_rekey_on_file_path` copies only the columns named in `_COLUMNS`, so a new column omitted there is silently dropped for any legacy DB that still hits the rekey path. Mitigation: Task 5 adds the column to `_CREATE_TABLE`, to `_COLUMNS`, **and** as an `_add_*` migration, and Task 8 verifies against a copy of the real DB before the real one is touched. |
| A downsized file can still exceed the cap for very long recordings | **VERIFIED (arithmetic)** | At 32 kbps, 14 MB of raw audio is ~61 minutes. The longest existing memo is 46.7 min -> 10.94 MB, so nothing in the vault is affected today, but a >~61-minute recording will still fail. Handled explicitly: one downsize pass only, then a clear terminal error naming both sizes and pointing at `downsize.bitrate_kbps` / raising the cap. Chunking stays out of scope by decision. |

## Design decisions (the three the brief asked to settle)

**1. Is the downsized copy kept or discarded? — Discarded.**
It lives in a `tempfile.TemporaryDirectory(prefix="voice-transcription-downsize-")`
for exactly the duration of one file's transcription. Reasons: it is derivable in
10 s from the original; keeping it would silently double storage in a Google-Drive
synced folder (`VOICE_MEMO_DIR` is under `~/gdrive/`); and a cached copy would
need its own staleness/invalidation rule against the original. The context
manager also cleans up on `BaseException`, so a Ctrl-C mid-run leaves no
multi-megabyte orphan — the same concern `workflow.py:311-317` documents for
`.partial`.

**2. How does the DB record that a compressed copy was sent? — A new nullable column, `sent_file_size`.**
The ad-hoc run wrote this note into `last_error` on a `completed` row, which is
dishonest twice over: the run did not error, and both `mark_processing`
(`database.py:475`) and `mark_completed` (`database.py:530`) reset `last_error`
to NULL, so the note would not have survived the next touch. No existing column
fits — `file_size` is the source size and is overwritten on every re-ingest
(`database.py:221`), `output_*` is the transcript location and `output_filename`
is a uniqueness key. So: `sent_file_size INTEGER` (NULL = the original was sent
as-is), written by `mark_completed`, cleared by `mark_processing`. A human-facing
stderr line is emitted at transcode time as well, mirroring the `↻` retry line's
precedent (`transcription.py:88-93`).

**3. How does `--dry-run` stay faithful? — By placement, then asserted.**
The transcode goes inside `workflow.run()`'s `_process`, which a dry run never
reaches (`cli.py:355` returns first). Nothing is added to `prepare_targets`,
which *does* run in a preview. A preview therefore spawns no ffmpeg and creates
no temp directory. Task 8 asserts this rather than trusting it. Deliberately
**not** done: teaching the preview to print "would compress", which would mean a
size check in `prepare_targets` and a second place for the cap arithmetic to
drift — reconsider only if the operator asks for it.

**4. Where does the transcode live? — `workflow.py`, not `transcription.py`.**
The size gate stays in `transcription.py` as a backstop, but the orchestration
(temp dir, DB bookkeeping, per-thread uniqueness) belongs to `workflow._process`,
which already owns exactly those concerns and already funnels every exception to
`mark_error` (`workflow.py:327-334`) — so a failed transcode is recorded with no
new error-handling code. The subprocess itself goes in a new single-purpose
module `audio_downsize.py`, mirroring how `recording_time.py` isolates the
ffprobe call.

## Patterns to Follow

```python
# SOURCE: config.py:34-54  (nested settings block + WHY docstring)
@dataclass
class RetrySettings:
    """Bounded retry for a 200 that carries no transcript.

    Each attempt is a fresh, separately billed API call on a multi-megabyte
    payload, so the bound is a cost ceiling, not just a patience setting.
    """

    max_attempts: int = 3
    backoff_seconds: float = 2.0
```

```python
# SOURCE: config.py:117-146  (from_yaml: pull the raw dict into a local, then construct inline)
        retry_data = raw.get("retry", {})
        return cls(
            ...
            max_file_size_mb=int(raw.get("files", {}).get("max_file_size_mb", 25)),
            retry=RetrySettings(
                max_attempts=int(retry_data.get("max_attempts", 3)),
                backoff_seconds=float(retry_data.get("backoff_seconds", 2.0)),
            ),
        )
```

```python
# SOURCE: config.py:183-186  (from_env: defaults are strings, cast on the way out)
            retry=RetrySettings(
                max_attempts=int(os.getenv("TRANSCRIBE_SKILL_RETRY_MAX_ATTEMPTS", "3")),
                backoff_seconds=float(os.getenv("TRANSCRIBE_SKILL_RETRY_BACKOFF_SECONDS", "2.0")),
            ),
```

```python
# SOURCE: config.py:289-295  (load: alias -> mutate under a truthiness guard -> reassign)
            retry = config.retry
            if env_values["retry_max_attempts"]:
                retry.max_attempts = int(env_values["retry_max_attempts"])
            config.retry = retry
```

```python
# SOURCE: config.py:331-335  (the only @property; the seam for mode-awareness)
    @property
    def max_file_size_bytes(self) -> int:
        """Return the maximum file size in bytes."""

        return int(self.max_file_size_mb * 1024 * 1024)
```

```python
# SOURCE: recording_time.py:16,84-104  (the subprocess convention to mirror for ffmpeg)
_FFPROBE_TIMEOUT = 120
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", ..., str(file_path)],
            capture_output=True,
            text=True,
            timeout=_FFPROBE_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None  # ffprobe missing, or the read timed out

    if proc.returncode != 0:
        return None
```

```python
# SOURCE: cli.py:221-226  (the only tempfile precedent: descriptive prefix, with-block cleanup)
    with tempfile.TemporaryDirectory(prefix="voice-transcription-preview-") as tmp_dir:
        scratch = Path(tmp_dir) / "preview.db"
```

```python
# SOURCE: workflow.py:302-317  (provisional artefact + BaseException cleanup, and WHY)
                staging_path = output_path.with_name(output_path.name + ".partial")
                # BaseException, not Exception: a Ctrl-C between the stub claim
                # and a finished write would otherwise leave the empty .md this
                # cleanup exists to remove. The bare raise still aborts the run.
                try:
                    ...
                except BaseException:
                    staging_path.unlink(missing_ok=True)
                    raise
```

```python
# SOURCE: database.py:181-196  (the additive-column migration to copy verbatim in shape)
    @staticmethod
    def _add_processing_owner(conn: sqlite3.Connection) -> None:
        """Add the processing_owner column to a database created without it."""

        cursor = conn.cursor()
        columns = {
            row[1] for row in cursor.execute("PRAGMA table_info(audio_files)").fetchall()
        }
        if "processing_owner" in columns:
            return
        cursor.execute("ALTER TABLE audio_files ADD COLUMN processing_owner TEXT")
```

```python
# SOURCE: transcription.py:88-93  (operator-facing progress line: symbol, name, flush)
                print(
                    f"↻ {audio_path.name}: attempt {attempt} of {attempts} returned "
                    f"no transcript ({exc}); retrying in {delay:.0f}s",
                    file=sys.stderr,
                    flush=True,
                )
```

```yaml
# SOURCE: config.yaml:169-188  (block style: rule sandwich, WHY prose, ALL-CAPS callout, per-key comment)
# -------------------------------------------------------------------------
# Retry Behaviour
# -------------------------------------------------------------------------
# Bounded retry for a 200 response that arrives with no transcript ...
#
# COST: every attempt is a separately billed call on the full audio payload ...
retry:
  # Total API attempts per file, including the first. 1 disables retrying.
  max_attempts: 3
```

## Files to Change

| File | Action | Purpose |
|------|--------|---------|
| `script/voice_transcription_service/audio_downsize.py` | CREATE | Single-purpose ffmpeg transcode + `DownsizeError`, mirroring `recording_time.py`'s isolation |
| `script/voice_transcription_service/config.py` | UPDATE | `chat_max_file_size_mb`, mode-aware `max_file_size_bytes`, `DownsizeSettings`, env/YAML/merge plumbing, `validate()` |
| `script/voice_transcription_service/config.yaml` | UPDATE | Per-mode caps under `files:` with the real arithmetic; new `downsize:` block; kill the stale "OpenAI API limit is 25MB" comment |
| `script/voice_transcription_service/transcription.py` | UPDATE | Gate reads the mode-aware limit and names it correctly in the error |
| `script/voice_transcription_service/workflow.py` | UPDATE | Downsize hook in `_process`, temp dir, stderr notice, `sent_file_size` through to `mark_completed` |
| `script/voice_transcription_service/database.py` | UPDATE | `sent_file_size` column + migration + `_COLUMNS`; `mark_completed` param; `mark_processing` clears it |
| `script/voice_transcription_service/README.md` | UPDATE | Env-var table, CLI table, new Downsize section, per-mode caps, changelog, footer dates |
| `.env.example` | UPDATE | Document the two new env vars (commented, with defaults) |

Dependency order: `audio_downsize.py` -> `config.py` -> `config.yaml` -> `database.py` -> `transcription.py` -> `workflow.py` -> docs.

## Tasks

### Task 1: New module `audio_downsize.py`
- File: `script/voice_transcription_service/audio_downsize.py`
- Action: CREATE
- Implement:
  - Module docstring stating why this exists: the chat-completions payload is
    base64 audio inside one JSON body, so the binding limit is bytes on the
    wire, and voice memos arrive as 48 kHz stereo ~98 kbps AAC — roughly 3x more
    than speech transcription needs. Cite the measured 32.7 MB -> 10.94 MB.
  - `_FFMPEG_TIMEOUT = 600` module constant (measured 10.5 s for 47 min; 600 s
    covers a multi-hour file on a slow FUSE mount without hanging forever).
  - `class DownsizeError(RuntimeError)` with a docstring noting it is raised (not
    degraded to `None` like `_from_ffprobe`) because the caller has already
    failed the size gate and has no other path forward.
  - `def downsize(src: Path, dest_dir: Path, *, channels: int, sample_rate: int,
    bitrate_kbps: int) -> Path`: builds `dest_dir / src.name` — keep the
    **original suffix**, because `_transcribe_via_chat` derives the API `format`
    field from it (`transcription.py:126`) and `.m4a` + `-c:a aac` is the
    combination already proven in production; run
    `["ffmpeg", "-v", "error", "-y", "-i", str(src), "-ac", str(channels),
    "-ar", str(sample_rate), "-c:a", "aac", "-b:a", f"{bitrate_kbps}k", str(dest)]`
    with `capture_output=True, text=True, timeout=_FFMPEG_TIMEOUT`.
  - Failure handling: `except (OSError, subprocess.SubprocessError) as exc:
    raise DownsizeError(...)` (names ffmpeg-missing vs timed-out); then
    `if proc.returncode != 0: raise DownsizeError(f"ffmpeg exited
    {proc.returncode}: {proc.stderr.strip()[:400]}")` — the 400-char clamp is
    deliberate, `mark_error` truncates the whole message at 1000
    (`database.py:557`) and the caller prepends context.
  - Also raise `DownsizeError` if the output is missing or zero bytes.
- Mirror: `recording_time.py:16,81-104` for the subprocess shape; keep the
  module as small and single-purpose as `recording_time.py`.
- Validate: `./.venv/bin/python -c "from script.voice_transcription_service.audio_downsize import downsize, DownsizeError"`
  from `.claude/skills/voice-memo-process/`.

### Task 2: Mode-aware cap + `DownsizeSettings` in `config.py`
- File: `script/voice_transcription_service/config.py`
- Action: UPDATE
- Implement:
  - Module constants next to `DEFAULT_SUPPORTED_FORMATS` (`config.py:19`), with
    the arithmetic as the comment (this is the load-bearing WHY, do not lose it):
    `DEFAULT_MAX_FILE_SIZE_MB = 25` (OpenAI native audio API) and
    `DEFAULT_CHAT_MAX_FILE_SIZE_MB = 14` — derived, not guessed: Gemini caps an
    inline request at 20 MB *total*, measured base64+JSON expansion is 1.3334x,
    so 20 MiB / 1.3336 = 14.997 MB of raw audio saturates the body exactly; 14
    leaves ~1.4 MB for the prompt and scaffolding.
  - `SkillConfig` field beside `max_file_size_mb` (`config.py:73`):
    `chat_max_file_size_mb: int = DEFAULT_CHAT_MAX_FILE_SIZE_MB`.
  - `@dataclass class DownsizeSettings` near `RetrySettings` (`config.py:44-54`),
    fields `enabled: bool = True`, `channels: int = 1`, `sample_rate: int =
    16000`, `bitrate_kbps: int = 32`; docstring carries the measured result and
    the field-evidence that 32 kbps mono transcribes fine.
  - Attach `downsize: DownsizeSettings = field(default_factory=DownsizeSettings)`
    after `retry` (`config.py:87`).
  - New property `effective_max_file_size_mb` returning
    `self.chat_max_file_size_mb if self.transcription_mode == "chat_completions"
    else self.max_file_size_mb`, and rewrite `max_file_size_bytes`
    (`config.py:331-335`) to derive from it. Docstring must say the mode branch
    matches `transcription.py`'s `if/else` fallthrough — an unrecognised mode
    means `audio_api`, exactly as dispatch behaves (`transcription.py:79-81`).
  - Plumbing in all five places the pattern requires: `from_yaml`
    (`files.chat_max_file_size_mb` + `downsize_data` local + inline
    `DownsizeSettings(...)`), `from_env`
    (`TRANSCRIBE_SKILL_CHAT_MAX_FILE_SIZE_MB`, `TRANSCRIBE_SKILL_DOWNSIZE`,
    `TRANSCRIBE_SKILL_DOWNSIZE_BITRATE_KBPS`), and `load`'s `env_values` dict +
    the alias/mutate/reassign stanza. Booleans use the house idiom
    `os.getenv(..., "true").lower() == "true"`.
  - `validate()` (`config.py:299-314`): `max_file_size_mb` and
    `chat_max_file_size_mb` must be >= 1; `downsize.bitrate_kbps` >= 1;
    `downsize.channels` in (1, 2); `downsize.sample_rate` >= 8000. Mirror the
    dotted-path message style of the existing `retry.*` checks.
  - Leave `transcription_mode` unvalidated — pre-existing, out of scope; note it
    in the report, do not fix it here.
- Mirror: `config.py:34-54`, `117-146`, `183-186`, `289-295`, `331-335`.
- Validate: `./.venv/bin/python -m script.voice_transcription_service --help`
  then the Task 8 probe's config assertions.

### Task 3: `config.yaml` — per-mode caps and the `downsize:` block
- File: `script/voice_transcription_service/config.yaml`
- Action: UPDATE
- Implement:
  - Replace the stale comment at lines 95-98. `max_file_size_mb: 25` keeps its
    value but its comment must now say it applies to `transcription_mode:
    "audio_api"` only, matching the `NOTE:` precedent used for `response_format`
    (`config.yaml:52-53`).
  - Add `chat_max_file_size_mb: 14` with the derivation in prose: Gemini's 20 MB
    total inline request, the measured 1.3334x base64+JSON expansion, the
    resulting 14.997 MB ceiling, and one sentence marking the open question —
    whether OpenRouter forwards inline at all — as unverified, so a future reader
    knows the number is conservative by choice.
  - New `downsize:` block after `retry:` using that block's exact shape (rule
    sandwich, prose preamble with measured numbers, an ALL-CAPS callout, per-key
    comments): keys `enabled: true`, `channels: 1`, `sample_rate: 16000`,
    `bitrate_kbps: 32`. The callout should be `NOTE:` about the ~61-minute
    ceiling at 32 kbps under a 14 MB cap, and that splitting is deliberately not
    implemented.
- Mirror: `config.yaml:169-188` (block style), `config.yaml:52-53` (mode-specific NOTE).
- Validate: `./.venv/bin/python -c "import yaml; yaml.safe_load(open('script/voice_transcription_service/config.yaml'))"`
  and confirm the loaded config reports 14 for chat mode.

### Task 4: Gate reads the mode-aware limit
- File: `script/voice_transcription_service/transcription.py`
- Action: UPDATE
- Implement: at `transcription.py:64-69`, keep the gate where it is (above the
  retry loop, so it cannot be re-run per attempt) and keep it a bare `ValueError`
  so `EmptyTranscriptionError`'s retry does not swallow it — but change the
  message to name `self.config.effective_max_file_size_mb` **and** the mode, so
  the error says which limit applied. Add a short comment noting this is now a
  backstop: `workflow` downsizes before calling, so in the normal pipeline an
  oversize file no longer reaches this raise; it still guards direct API use.
- Mirror: existing gate; `EmptyTranscriptionError` docstring at
  `transcription.py:15-22` explains why the exception type must stay `ValueError`.
- Validate: Task 8 probe case that calls `transcribe()` directly on an oversize
  file with a stub client and asserts the message names 14 MB and the mode.

### Task 5: `sent_file_size` column
- File: `script/voice_transcription_service/database.py`
- Action: UPDATE
- Implement:
  - Add `sent_file_size INTEGER` to `_CREATE_TABLE` (`database.py:28-46`) **and**
    to `_COLUMNS` (`database.py:48-52`) — the latter is not optional:
    `_rekey_on_file_path` copies only the columns it names, so omitting it drops
    the column for any legacy DB taking the rekey path.
  - Add `_add_sent_file_size(conn)` following `_add_processing_owner`
    (`database.py:181-196`) verbatim in shape, and call it from `ensure_schema`
    (`database.py:125-143`) after `_add_processing_owner`, before the
    `CREATE INDEX`.
  - `mark_completed(self, file_path, output_path, sent_file_size: Optional[int] = None)`
    (`database.py:517-540`): add `sent_file_size = ?` to the SET list. Default
    `None` means "sent as-is", which is also what every pre-existing row reads as.
  - `mark_processing` (`database.py:461-480`): add `sent_file_size = NULL` to the
    SET list, next to the existing `last_error = NULL`, so a re-run that no
    longer needs a downsize does not inherit a stale value. Comment why.
- Mirror: `database.py:181-196`, `461-480`, `517-540`.
- Validate: Task 8 probe runs `ensure_schema()` twice against a **copy** of the
  real `audio_metadata.db` and asserts the column appears once, is NULL for all
  10 existing rows, and that a second call is a no-op.

### Task 6: The downsize hook in `workflow._process`
- File: `script/voice_transcription_service/workflow.py`
- Action: UPDATE
- Implement:
  - Imports: `tempfile`, and `from .audio_downsize import DownsizeError, downsize`.
  - Inside `_process` (`workflow.py:274`), after `mark_processing` and inside the
    existing `try:` at line 281 — placement matters: being inside that try means
    a `DownsizeError` is recorded by the existing `mark_error` funnel
    (`workflow.py:327-334`) with no new error path.
  - Logic: `sent_bytes = None`; stat the source; if
    `size > self.config.max_file_size_bytes` and `self.config.downsize.enabled`,
    open `tempfile.TemporaryDirectory(prefix="voice-transcription-downsize-")`
    and transcode into it, print an operator line to stderr mirroring the `↻`
    style (e.g. `⤓ <name>: 32.68 MB exceeds the 14 MB chat_completions limit;
    transcribing a mono 32 kbps copy (10.94 MB)`, `flush=True`), set
    `sent_bytes = reduced.stat().st_size`, and transcribe the reduced path. If
    the reduced file is *still* over the limit, raise a `ValueError` naming both
    sizes and pointing at `downsize.bitrate_kbps` — one pass only, no loop.
  - Keep the temp dir alive across the whole transcribe-and-write block: the
    `with` must wrap the `transcribe()` call (and `_transcribe_via_audio_api`
    holds an open handle, `transcription.py:99-122`). Simplest shape that avoids
    duplicating the write path: an `ExitStack`, or hoist the existing body into a
    local closure called from both branches — implementer's choice, but do not
    duplicate the `.partial`/`os.replace` block.
  - Thread the value through: `self.database.mark_completed(key, output_path,
    sent_file_size=sent_bytes)` (`workflow.py:319`).
  - Do **not** touch `prepare_targets` or `sync_files` — they run under
    `--dry-run`.
  - Note for the implementer: under `--workers N` this means N concurrent ffmpeg
    processes; the per-file `TemporaryDirectory` keeps paths unique (unlike an
    input-derived name), which is the collision property `_claim_output_path`
    (`workflow.py:225-258`) exists to provide for outputs.
- Mirror: `workflow.py:281-334` (the try/except funnel), `cli.py:221-226`
  (tempfile idiom), `transcription.py:88-93` (stderr line).
- Validate: Task 8 end-to-end case against the 32.7 MB file with a stubbed
  `transcribe` that records the path it was handed.

### Task 7: Docs
- File: `script/voice_transcription_service/README.md`, `.env.example`
- Action: UPDATE
- Implement:
  - Env-var table (`README.md:312-332`): amend the `TRANSCRIBE_SKILL_MAX_FILE_SIZE_MB`
    row (line 321) to say audio_api-only, add rows for
    `TRANSCRIBE_SKILL_CHAT_MAX_FILE_SIZE_MB`, `TRANSCRIBE_SKILL_DOWNSIZE`,
    `TRANSCRIBE_SKILL_DOWNSIZE_BITRATE_KBPS`, following the `retry` rows'
    phrasing (`README.md:331-332`).
  - New `### Automatic downsize` section modelled on Retry Behaviour
    (`README.md:342-381`): when it triggers, the exact ffmpeg command, the
    measured 32.7 MB -> 10.94 MB / 10.5 s, a verbatim stderr sample in a fence, a
    bolded **Cost.** paragraph (one API call instead of a failure; ~10 s of CPU),
    and the ~61-minute ceiling with splitting named as out of scope.
  - Fix the two statements that downsizing makes false: `README.md:380` and
    `README.md:716` both describe an oversize file as a fast, non-retryable
    failure. It is now recoverable in the default configuration.
  - Cross-link the new section from the error-handling section the way
    `README.md:711` links Retry Behaviour.
  - Changelog (`README.md:707-717`): a v2.3 entry naming the new keys and env
    vars, following the v2.2 bullet's shape. Update the footer stamps
    (`README.md:734-736`) to today's date.
  - `.env.example`: add the two new vars commented out with their defaults and a
    one-line why, matching the file's existing comment style. (Check whether
    `TRANSCRIBE_SKILL_MAX_FILE_SIZE_MB` is even listed there; if not, do not add
    it — stay surgical.)
- Mirror: `README.md:342-381`, `README.md:707-717`.
- Validate: read back; confirm no remaining claim that 25 MB applies to chat mode
  (`grep -rn "25 ?MB" README.md config.yaml`).

### Task 8: Verification probe (scratchpad, discarded)
- File: `<scratchpad>/verify_downsize.py`
- Action: CREATE (throwaway — must not land in the repo)
- Implement a script that asserts, printing PASS/FAIL per case:
  1. `chat_completions` config reports `effective_max_file_size_mb == 14` and
     `max_file_size_bytes == 14*1024*1024`; `audio_api` reports 25. An
     unrecognised mode string reports 25 (matches dispatch fallthrough).
  2. Env override wins: `TRANSCRIBE_SKILL_CHAT_MAX_FILE_SIZE_MB=9` -> 9.
     `TRANSCRIBE_SKILL_DOWNSIZE=false` -> `downsize.enabled is False`.
  3. `validate()` rejects `chat_max_file_size_mb=0` and
     `downsize.bitrate_kbps=0` with dotted-path messages.
  4. `downsize()` on the real 32.7 MB memo -> output under 14 MB, suffix `.m4a`,
     ffprobe duration within 1 s of 2799.70, and the file lands inside the passed
     temp dir.
  5. `downsize()` on a corrupt `.m4a` -> `DownsizeError` whose message carries
     ffmpeg's stderr; and on a bogus binary name (monkeypatched) -> `DownsizeError`
     naming ffmpeg-unavailable, not a traceback.
  6. Schema migration against a **copy** of the real DB: `ensure_schema()` twice
     -> `sent_file_size` present exactly once, NULL for all 10 rows.
  7. `_process` end-to-end with `transcribe` stubbed to return fixed text and
     record its argument: for the 32.7 MB file it is handed a path inside a
     `voice-transcription-downsize-*` dir, the row ends `completed` with
     `sent_file_size` ~10.9e6; for a small file it is handed the original and
     `sent_file_size IS NULL`. After the call, assert the temp dir is gone.
  8. Dry-run fidelity: snapshot the set of `voice-transcription-downsize-*` dirs
     under the temp root, run `transcribe --dry-run` over the oversize file,
     assert the set is unchanged, that no `ffmpeg` child was spawned (stub
     `audio_downsize.downsize` with a raising sentinel — a dry run must never
     call it), and that the real DB's `mtime` is untouched.
- Validate: every case PASS; then delete the script.

## Validation

```bash
cd /home/dylan/Documents/voice-memo-agent-skills/.claude/skills/voice-memo-process/

# project verify gate (per CLAUDE.md — no test suite exists)
./.venv/bin/python -m script.voice_transcription_service --help
./.venv/bin/python -m script.voice_transcription_service sync --days 7 --dry-run --verbose

# dry-run fidelity on the file that used to be rejected
./.venv/bin/python -m script.voice_transcription_service transcribe \
  "$HOME/gdrive/Inbox/voice-memo-recordings/3月27日08-29與施志恆和譜生討論智慧化氣候校園計畫.m4a" \
  --dry-run --verbose
# expect: listed as a target, no ffmpeg, no temp dir, DB untouched

# the probe from Task 8
./.venv/bin/python <scratchpad>/verify_downsize.py

# /prep-agent-skill to confirm the skill's script still runs without side effects
```

**E2E (costs money, ~$0.05-0.08, run only with the operator's go-ahead):** one
real transcription of the 32.7 MB memo. Expect: the `⤓` line on stderr, one API
attempt (`attempt_count` +1, no `↻` lines), a transcript in
`RAW_TRANSCRIPT_DIR`, row `completed` with `sent_file_size` ~10.9e6 and
`last_error` NULL, and the temp dir gone afterwards. Spot-check the transcript
for Traditional Chinese and correct proper nouns, as the 2026-09-12 note
demonstrated is achievable at this bitrate.

**Deliberately not validated here:** whether the 20 MB inline cap binds through
OpenRouter. It would need an authenticated bisect with truncated payloads
(15/20/25 MB) recording 413 vs 400 vs an upstream Google error. Out of scope for
this plan; 14 MB is safe either way. Worth its own session if the operator wants
the cap raised.

## Acceptance Criteria
- [ ] All tasks completed
- [ ] `--help` and `sync --dry-run --verbose` run clean
- [ ] `chat_completions` enforces 14 MB, `audio_api` still 25 MB, both overridable by env
- [ ] An oversize file transcribes via an automatic mono-32k copy instead of erroring
- [ ] `sent_file_size` records the compressed size; `last_error` is never used for this
- [ ] `--dry-run` spawns no ffmpeg, creates no temp dir, writes nothing to the real DB
- [ ] Temp copy is gone after the run, including on Ctrl-C
- [ ] `config.yaml` and `README.md` no longer claim 25 MB applies to chat mode
- [ ] Follows existing patterns (subprocess convention, migration shape, YAML block style)
- [ ] Every external-behavior claim is VERIFIED by a probe, or carried as an explicit risk
