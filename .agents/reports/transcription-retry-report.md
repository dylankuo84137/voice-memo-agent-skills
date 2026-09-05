# Implementation Report: Bounded retry for transcript-less OpenRouter responses

**Plan**: `.agents/plans/transcription-retry.plan.md`
**Issue**: `.agents/issues/transcription-retry.md` (file-based, no tracker number)
**Branch**: `feat/transcription-retry`
**Worktree**: `../voice-memo-agent-skills-feat-transcription-retry`
**Commits**: `9988bbb` (plan seed) · `ad4d3f2` (implementation)
**Date**: 2026-09-06

---

## Tasks completed

| # | Task | Status | Validation |
|---|------|--------|-----------|
| 1 | `RetrySettings` through the config chain (`config.py`) | ✅ | Defaults, YAML, env override, `from_env`, and both `validate()` guards exercised |
| 2 | `retry:` block in `config.yaml` | ✅ | Value changed 3→9→3 and observed through `SkillConfig.load()`, proving YAML is read rather than silently falling back |
| 3 | `EmptyTranscriptionError` + `_describe_chat_response` + raise-instead-of-`or ""` | ✅ | MRO check; diagnostics asserted in probes B/C/C2 |
| 4 | Bounded retry loop in `transcribe()` | ✅ | Probes A–H |
| 5 | `README.md` documentation | ✅ | `grep` for all five insertion points |

## Validation results

**Verify gate** — green before and after, output unchanged from baseline:

| Command | Result |
|---|---|
| `--help` | exit 0 |
| `sync --days 7 --dry-run --verbose` | exit 0 |
| `transcribe --from-db --db-status pending --dry-run` | exit 0, `No files selected for processing.` |

**Offline probes** — zero API calls, zero DB writes, DB sha256 byte-identical across the run:

| Probe | Asserts |
|---|---|
| A | Recovers on attempt 3 after `no choices` then `empty content` |
| B | Exhausts the bound; message carries `finish_reason='stop'`, `reasoning_tokens=3589`, `prompt:38000` |
| C | `choices` key absent → "no choices in response" + the body's `error` object; no `TypeError` |
| C2 | Explicit `"choices": null` → same, no `TypeError` |
| C3 | `content: null` (`finish_reason='length'`) is retried, recovers on attempt 2 |
| D | `max_attempts: 1` disables retrying — exactly 1 call |
| E | HTTP 401 → `AuthenticationError`, 1 call, 0.024s, no backoff |
| F | HTTP 400 / 413 → fail fast, 1 call each |
| G | Missing file and unsupported format → 0 calls, no sleep |
| H | Backoff sequence recorded directly as `[2.0, 4.0, 8.0]` for `max_attempts: 4`; no sleep after the final attempt |
| I1/I2 | Same retry + exhaustion behaviour on the `audio_api` path |

**AC6 at the CLI** — `--api-key sk-definitely-invalid` on a real file failed with
`Error code: 401 - Missing Authentication header`, **not** `No transcript after 3
attempt(s)`, confirming the retry never engaged. `attempt_count` incremented by exactly 1.

**`--dry-run` fidelity (AC5)** — `audio_metadata.db` sha256 identical before and after.

**End-to-end (one real billable call)** — `8月5日 22-37 用藥紀錄.m4a` transcribed
successfully in 31s: status `completed`, `last_error` NULL, `attempt_count` +1, a
~300-character Chinese transcript written.

## Acceptance criteria

| AC | Status | Evidence |
|---|---|---|
| All 5 tasks | ✅ | table above |
| Verify gate unchanged | ✅ | 3 commands, exit 0, baseline output |
| AC1 — empty content and `choices is None` retried | ✅ | probes A, C, C2, C3, I1 |
| AC2 — bounded, backed off, printed, cost documented | ✅ | probes D, H; `↻` stderr line; cost ceiling in `config.yaml` + README |
| AC3 — exhaustion names what was seen | ✅ | probes B, C, C2, I2 |
| AC4 — `workflow.py`/`database.py` untouched, `attempt_count` counts runs | ✅ | diffstat lists 4 files, neither of them; DB showed +1 per run on both the failed and successful E2E runs |
| AC5 — `--dry-run` zero calls, DB byte-identical | ✅ | sha256 before/after |
| AC6 — bad key fails in one call, no backoff | ✅ | probes E, F, G + CLI run |
| Follows existing config-chain / docstring / stderr patterns | ✅ | mirrors `SafeguardSettings` and `ensure_client` docstring style |
| E2E real run lands `completed` | ✅ | above |

## Files changed

| File | Lines | Purpose |
|---|---|---|
| `script/voice_transcription_service/transcription.py` | +97 −12 | `EmptyTranscriptionError`, `_describe_chat_response`, retry loop, raise-instead-of-empty in both modes |
| `script/voice_transcription_service/config.py` | +38 | `RetrySettings` through dataclass / `from_yaml` / `from_env` / `load()` / `validate()` |
| `script/voice_transcription_service/config.yaml` | +23 −1 | `retry:` block with cost commentary; carries the `google/gemini-2.5-pro` model switch |
| `script/voice_transcription_service/README.md` | +67 | Retry Behaviour section, 2 env-var rows, `attempt_count` semantics, module blurb, v2.2 changelog |
| `.agents/probes/*` | new | Committed offline probes (see deviation 2) |

## Deviations from plan

1. **The uncommitted `google/gemini-2.5-pro` model switch was carried onto the branch**
   rather than stashed away. The plan did not anticipate a dirty working tree; the issue
   names that switch as the reason exposure to this bug rose, so shipping it with the
   retry that makes it safe keeps the two together. Confirmed with the user before
   branching.

2. **Probes are committed to `.agents/probes/`, not left in the scratchpad.** The plan
   called them throwaway. They are the only executable evidence for AC1/AC3/AC6, cost
   nothing to run, and `/validate` needs to re-run them. They live under `.agents/`, not
   inside the shipped skill, so the skill's surface is unchanged. Made self-bootstrapping:
   they cache the sample `.m4a` to the system temp dir on first run (the audio itself is
   gitignored).

3. **The plan's AC6 CLI command selected nothing.** It used
   `--from-db --db-status pending --db-limit 1`, but every row in the DB is `completed`,
   so the run exited before making a call. Replaced with a direct file path plus `--force`,
   which exercises the same path and additionally proves the DB lands in `error`.

4. **The E2E wrote to a scratch `--output-dir`** instead of the live `RAW_TRANSCRIPT_DIR`,
   so the user's transcript directory (and the refine stage downstream of it) is untouched.
   The API call, transcript content and DB transition were all still exercised for real.

5. **Validation ran against a copy of `audio_metadata.db`**, seeded into the worktree
   (the DB is gitignored, so the worktree had none). The live DB in the main repo is
   byte-identical to its pre-session sha256, verified after the E2E.

6. **Probe A's wall-clock assertion was replaced by a recorded-sleep assertion (probe H).**
   The first run took 52.8s, not the expected 0.3s. Cause: `VOICE_MEMO_DIR` is an
   `fuse.rclone` mount and each retry re-reads the audio, at ~17s cold per read. This
   refines the plan's risk table, which assumed "~0.2s per encode" — on this mount the
   per-retry re-read is two orders of magnitude slower, though still negligible beside a
   multi-minute API call, so the plan's decision to keep `_transcribe_via_chat`
   self-contained stands. Probes now use a local cached copy and assert the backoff
   sequence directly.

7. **Footer date is 2026-09-06**, not the 2026-09-05 the plan specified — the date rolled
   over mid-session.

## Tests written

No test framework exists in this repo. Twelve offline probes were written across two
committed scripts (`.agents/probes/verify_retry.py`, `.agents/probes/verify_audio_api.py`),
covering both transcription modes, all four transcript-less response shapes, the bound,
the backoff sequence, and six fail-fast paths. Both run in under a second with no API
calls and no DB writes; `.agents/probes/README.md` documents how to run them.

## Not done

- No PR opened — that follows `/validate`.
- `workflow.py:283-284`'s `raise ValueError("Transcription returned empty text")` left in
  place as the plan specified: a cheap belt-and-braces guard, no longer the primary path.

## Next step

`/validate .agents/plans/transcription-retry.plan.md` in a fresh session.
