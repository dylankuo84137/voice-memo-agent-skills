# Implementation Report: Mode-aware size gate + automatic mono-32k downsize

**Plan**: `.agents/plans/mode-aware-size-gate-and-downsize.plan.md`
**Branch**: `feat/mode-aware-size-gate-and-downsize`
**Worktree**: `../voice-memo-agent-skills-feat-mode-aware-size-gate-and-downsize`
**Issue**: N/A
**Date**: 2026-09-13

## Summary

The `chat_completions` size cap is now 14 MB (was sharing `audio_api`'s 25 MB),
and a file over the cap for its mode is re-encoded to a temporary
mono/16 kHz/32 kbps copy and transcribed, instead of failing. The bytes actually
uploaded are recorded in a new `sent_file_size` column.

## Tasks completed

| # | Task | Status | Validation |
|---|------|--------|------------|
| 1 | New module `audio_downsize.py` | ✅ | Imports clean; probe cases 4-5 |
| 2 | Mode-aware cap + `DownsizeSettings` in `config.py` | ✅ | `--help` clean; probe cases 1-3 |
| 3 | `config.yaml` per-mode caps + `downsize:` block | ✅ | YAML parses; `from_yaml` reports 14 for chat |
| 4 | Gate reads the mode-aware limit | ✅ | Message names 14 MB *and* the mode |
| 5 | `sent_file_size` column | ✅ | Probe case 6 + a legacy-rekey case |
| 6 | Downsize hook in `workflow._process` | ✅ | Probe case 7; verify gate green |
| 7 | Docs (`README.md`, `.env.example`) | ✅ | No surviving claim that 25 MB binds chat mode |
| 8 | Verification probe | ✅ | 8/8 PASS; script discarded |

## Validation results

Project verify gate (per CLAUDE.md — no test suite exists), all from
`.claude/skills/voice-memo-process/`:

```
./.venv/bin/python -m script.voice_transcription_service --help              → rc=0
./.venv/bin/python -m script.voice_transcription_service sync --days 7 \
    --dry-run --verbose                                                      → rc=0
./.venv/bin/python -m script.voice_transcription_service transcribe \
    "<the 32.7 MB memo>" --dry-run --verbose                                 → rc=0,
    listed as a target, no ffmpeg, no temp dir, real DB byte-identical
```

`/prep-agent-skill` checks: `--help`, `sync --dry-run --path .`, and
`transcribe --from-db --dry-run` all exit 0 with no import errors.

Verification probe — 8 cases, all PASS:

1. Mode-aware caps: chat → 14 MB / 14680064 B, `audio_api` → 25 MB, an
   unrecognised mode → 25 MB (matching dispatch fallthrough).
2. Env overrides win, through both `load()` and `from_env()`:
   `CHAT_MAX_FILE_SIZE_MB=9` → 9, `DOWNSIZE=false` → disabled,
   `DOWNSIZE_BITRATE_KBPS=24` → 24.
3. `validate()` rejects `chat_max_file_size_mb=0`, `bitrate_kbps=0`,
   `channels=3`, `sample_rate=4000`, each with a dotted-path message; the happy
   path still passes.
4. `downsize()` on the real memo: **34.24 MB → 11.47 MB** (32.66 → 10.94 MiB),
   suffix preserved, output inside the passed temp dir, ffprobe duration
   2799.66 s vs the original's 2799.70 s.
5. `DownsizeError` carrying ffmpeg's stderr on corrupt input; a clear
   "ffmpeg is not available" on a missing binary, not a traceback.
6. Schema migration against a **copy** of the real DB: `ensure_schema()` twice →
   `sent_file_size` present exactly once, NULL for all 10 existing rows.
7. `_process` end-to-end with `transcribe` stubbed: the oversize memo is handed
   a path inside a `voice-transcription-downsize-*` dir and ends `completed`
   with `sent_file_size` ≈ 11.47e6 and `last_error` NULL; a small file is handed
   the original with `sent_file_size` NULL; the temp dir and copy are gone after
   the run. With `downsize.enabled=false` the oversize file fails, and the error
   names both 14 MB and `chat_completions`.
8. Dry-run fidelity: `downsize` replaced with a raising sentinel in a subprocess
   — a `--dry-run` over the oversize file never calls it, leaves the set of
   `voice-transcription-downsize-*` dirs unchanged, and leaves the real DB
   identical in both mtime and bytes.

**Not run**: the E2E real transcription (~$0.05–0.08). It needs the operator's
go-ahead and belongs to `/validate`.

## Files changed

| File | Action | Notes |
|------|--------|-------|
| `script/voice_transcription_service/audio_downsize.py` | CREATE | ffmpeg transcode + `DownsizeError`, 600 s timeout |
| `script/voice_transcription_service/config.py` | UPDATE | `chat_max_file_size_mb`, `DownsizeSettings`, `effective_max_file_size_mb`, env/YAML/merge plumbing, 4 new `validate()` checks |
| `script/voice_transcription_service/config.yaml` | UPDATE | Per-mode caps with the derivation in prose; new `downsize:` block |
| `script/voice_transcription_service/transcription.py` | UPDATE | Gate names the effective limit and the mode; comment marks it a backstop |
| `script/voice_transcription_service/workflow.py` | UPDATE | New `_prepare_upload`; `ExitStack` around the transcribe; `sent_file_size` to `mark_completed` |
| `script/voice_transcription_service/database.py` | UPDATE | `sent_file_size` column + `_add_sent_file_size` migration; `mark_completed` param; `mark_processing` clears it |
| `script/voice_transcription_service/README.md` | UPDATE | File-size-limits table, Automatic Downsize section, schema, module tree, v2.3 changelog, footer dates |
| `.env.example` | UPDATE | Two downsize vars, commented with defaults |

## Deviations from plan

1. **`_COLUMNS` — plan was wrong, not followed.** Task 5 called adding
   `sent_file_size` to `_COLUMNS` "not optional". A probe proved it breaks the
   legacy rekey outright (`OperationalError: no such column: sent_file_size`),
   because `_rekey_on_file_path` SELECTs that list *from the legacy table*. The
   new table comes from `_CREATE_TABLE`, so the column exists regardless and
   arrives NULL — its correct "sent as-is" value. This is why `processing_owner`
   is also absent from `_COLUMNS`; the plan mistook a correct existing decision
   for an oversight. `_COLUMNS` now carries a comment explaining it, and a probe
   case covers the legacy-rekey path end to end. Recorded in the plan file too.

2. **Exception handling in `downsize()` split three ways.** The plan specified
   one `except (OSError, subprocess.SubprocessError)`. Implemented as
   `FileNotFoundError` → "ffmpeg is not available", `TimeoutExpired` → names the
   timeout, then the general pair — which is what the plan's own wording
   ("names ffmpeg-missing vs timed-out") asked for. Ordering is correct:
   both specific types are subclasses of the general pair.

3. **`_prepare_upload` extracted as a method**, with `ExitStack` in `_process`.
   The plan left the shape to the implementer ("ExitStack, or hoist the body
   into a closure — but do not duplicate the `.partial`/`os.replace` block").
   `ExitStack` scopes the temp dir to exactly the transcribe call, leaving the
   write path untouched.

4. **`DownsizeError` not imported into `workflow.py`.** It is a `RuntimeError`,
   so the existing `except Exception` funnel records it via `mark_error` with no
   new code. Importing an unused name would be dead code.

5. **Two docs additions the plan did not list**: `audio_downsize.py` added to the
   README module tree, and `sent_file_size` documented in the Database Structure
   SQL block. Both are lists my change would otherwise leave incomplete.

6. **`.env.example` got the two downsize vars only**, not
   `TRANSCRIBE_SKILL_CHAT_MAX_FILE_SIZE_MB` — consistent with the plan's
   instruction to stay surgical, since `TRANSCRIBE_SKILL_MAX_FILE_SIZE_MB` is
   not listed there either. Both caps are documented in the README table.

## Tests written

No test suite exists in this project (CLAUDE.md states this explicitly), so the
verification is the 8-case probe described above, run from the scratchpad and
discarded per Task 8. Its cases cover the public interface (config properties,
`validate()`, `downsize()`, `mark_completed`), error paths (corrupt audio,
missing ffmpeg, still-too-large, downsize disabled), and cross-boundary
behaviour (`_process` end to end, the schema migration against real data,
dry-run fidelity in a real subprocess).

If a test suite is ever added, cases 1-3, 5 and 6 port directly as unit tests;
4, 7 and 8 need the real audio fixture.

## Notes for `/validate`

- **E2E is unrun and costs money** (~$0.05–0.08): one real transcription of the
  32.7 MB memo. Expect the `⤓` line on stderr, one API attempt (no `↻` lines), a
  transcript in `RAW_TRANSCRIPT_DIR`, the row `completed` with `sent_file_size`
  ≈ 11.5e6 and `last_error` NULL, and no temp dir afterwards. Spot-check the
  transcript for Traditional Chinese and proper nouns.
- **Pre-existing, left alone** (out of scope, flagged not fixed):
  `transcription_mode` is never validated — an unrecognised value silently
  falls through to `audio_api`. The new `effective_max_file_size_mb` deliberately
  mirrors that fallthrough rather than diverging from it.
  `recording_time.py` is missing from the README's module tree.
- **Still assumed, not verified**: whether Gemini's 20 MB inline cap actually
  binds through OpenRouter. 14 MB is conservative under either answer. Settling
  it needs an authenticated bisect and deserves its own session.
- The real DB at `script/audio_metadata.db` was never written to — every probe
  ran against a copy.
