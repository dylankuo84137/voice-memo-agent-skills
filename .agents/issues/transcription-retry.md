# BUG_FIX — Transcription has no retry; transient OpenRouter failures poison DB state

**Type**: BUG_FIX · **Complexity**: LOW–MEDIUM
**Filed**: 2026-09-05, from a model-comparison session (evidence below is from that session and is not cheap to reproduce — each probe costs a real API call on an 18 MB audio file).

## Problem

`TranscriptionService` issues exactly one OpenRouter request per file. Any transient
failure is treated as terminal: the row is marked `error`, the API call is paid for, and
the operator must notice and re-run by hand.

Observed rate in one sitting: **2 failures in 4 real runs (50%)**, on the same audio file,
with an unchanged payload.

## Evidence

Audio: `9月5日 21-59 週回顧.m4a` (18 MB, ~38k audio prompt tokens).

| # | Model | Result | Surfaced as |
|---|---|---|---|
| 1 | `google/gemini-2.5-pro` | FAIL after 3m04s | `Transcription returned empty text` |
| 2 | `google/gemini-2.5-pro` | OK, 1m47s, 4,123 chars | — |
| 3 | `google/gemini-2.5-flash` | FAIL after 41s | `'NoneType' object is not subscriptable` |
| 4 | `google/gemini-2.5-flash` | OK, 41s, 3,933 chars | — |

Both failures were proven transient by replaying the **identical payload** directly
against OpenRouter (bypassing the service) immediately afterwards — both returned
`finish_reason: "stop"` with full transcripts (4,600 and 3,790 chars). Nothing about the
file, model, or request shape is at fault.

Two distinct failure shapes, and the code handles neither:

1. **Empty content** — `response.choices[0].message.content` is `""`/`None`.
   `transcription.py:112` collapses it with `or ""`, then
   `workflow.py:283-284` raises `ValueError("Transcription returned empty text")`.
   Note this response is still **billed** (a confirmed empty-ish pro call cost $0.075).
2. **`choices` is `None`** — OpenRouter returned a body with no `choices` array, so
   `transcription.py:112`'s `response.choices[0]` raises
   `TypeError: 'NoneType' object is not subscriptable`. The message is opaque; nothing
   logs the actual response body, so diagnosing it required an out-of-band probe.

## Why it matters here

- A failed row goes to `error`, which `--db-status pending` cannot see. Recovery requires
  knowing to re-select with `--db-status error --force`.
- Reasoning models make shape 1 more likely: the pro calls spent 3,589 reasoning tokens
  before content on this file; flash spent 0. `config.yaml` now defaults to
  `google/gemini-2.5-pro`, so exposure went **up** with the recent model switch.
- Cost is real per attempt (~$0.045–$0.075 for this file), so retry must be bounded, not
  optimistic.

## Files in scope

| File | Why |
|---|---|
| `.claude/skills/voice-memo-process/script/voice_transcription_service/transcription.py:81-112` | `_transcribe_via_chat` — where both shapes originate |
| `.claude/skills/voice-memo-process/script/voice_transcription_service/workflow.py:273-331` | `_process` — marks `processing` / `error`; where a retry must NOT re-churn DB state |
| `.claude/skills/voice-memo-process/script/voice_transcription_service/config.py` | If retry knobs become configurable, they follow the CLI → env → YAML → default chain |
| `.claude/skills/voice-memo-process/script/voice_transcription_service/config.yaml` | New `retry:` block, if any |
| `.claude/skills/voice-memo-process/script/voice_transcription_service/README.md` | Documents behaviour + config; must stay in sync |

## Acceptance criteria

1. A transient empty response or a `choices is None` response is retried, not failed.
2. Retries are bounded and backed off; the bound is visible to the operator (log line
   naming attempt N of M) and the cost implication is documented.
3. A genuinely empty transcript after exhausting retries still ends as `error` with a
   message that names what was actually seen (finish_reason, whether choices was absent,
   token usage) — not `'NoneType' object is not subscriptable`.
4. Retrying does not re-run the `mark_processing` → `mark_error` cycle per attempt, and
   does not inflate `attempt_count` once per network attempt if that column is meant to
   count *runs*. (Decide and state which; the column's current semantics are ambiguous.)
5. `--dry-run` still performs zero API calls and zero DB writes.
6. Non-retryable errors (auth failure, file too large, unsupported format, 4xx that will
   never succeed) fail fast — no backoff sleep on a bad API key.

## Open question for the Plan session

**Which layer owns the retry?** Recommendation: inside `TranscriptionService` (wrap the
API call), so it covers both `chat_completions` and `audio_api` modes and leaves
`workflow.py`'s DB state machine untouched. The alternative — retrying in
`_process` — re-enters the DB status transitions and interacts badly with the
`processing_owner` reclaim logic. Confirm against the code before committing.

Secondary: does `openai`'s client already retry some of this (`max_retries` defaults to 2
for connection-level errors)? A 200 response with empty/absent `choices` is *not* covered
by that, but the plan should state the boundary rather than duplicate it.

## Verification strategy

No test suite exists in this repo. Verification is by probe:
- Unit-level: inject a fake client returning (a) `choices=None`, (b) empty content, then a
  good response; assert one transcript out and the expected attempt count — no API calls.
- End-to-end: `--dry-run` unchanged; then one real `--force` run on a small file
  (`8月5日 22-37 用藥紀錄.m4a`, 2 MB, ~$0.005) confirming success and DB `completed`.
- Regression: bad `--api-key` must fail immediately, not after 3 backoffs.
