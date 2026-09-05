# Plan: Bounded retry for transcript-less OpenRouter responses

## Summary
`TranscriptionService.transcribe` makes exactly one API call per file, so a 200 response
that carries no transcript (empty `content`, or `choices` absent/`null`) is treated as a
terminal failure — the row goes to `error`, the call is billed, and recovery is manual.
This plan puts a **bounded, backed-off retry inside `TranscriptionService`**, wrapping only
the API call, so it covers both `chat_completions` and `audio_api` modes and leaves
`workflow.py`'s DB state machine (`mark_processing` → `mark_error`, `processing_owner`
reclaim) completely untouched. Only the transcript-less shape is retried here; HTTP-level
transients (429/5xx/connection/timeout) are already retried by the `openai` SDK and are
deliberately not double-retried, and every other error (auth, 4xx, bad format, oversize
file) propagates on the first attempt with no sleep. When retries are exhausted the raised
error names what was actually seen — `finish_reason`, whether `choices` was absent, any
`error` object in the body, and token usage including reasoning tokens.

## User Story
As the operator of the voice-memo pipeline, I want a transient transcript-less response to
be retried automatically within a cost-bounded budget, so that a 50%-per-call failure rate
on a reasoning model does not leave rows in `error` for me to find and re-run by hand.

## Metadata
| Field | Value |
|-------|-------|
| Type | BUG_FIX |
| Complexity | LOW–MEDIUM |
| Systems affected | `transcription.py` (retry + diagnostics), `config.py` / `config.yaml` (new `retry:` knobs), `README.md` (docs) |
| Issue | `.agents/issues/transcription-retry.md` |

---

## Assumptions & Risks

All probes below were run offline with `httpx.MockTransport` against the project venv
(`.claude/skills/voice-memo-process/.venv`, `openai==2.30.0`). **Zero API calls, zero cost.**

| Claim | Status | Evidence |
|---|---|---|
| A 200 body whose `choices` key is **absent** parses into `ChatCompletion` with `choices is None`; `response.choices[0]` then raises `TypeError: 'NoneType' object is not subscriptable` | **VERIFIED** | MockTransport returning `{"id","object","created","model","error":{...}}` → `type=ChatCompletion`, `choices=None`, `choices[0]` raised exactly that `TypeError`. Reproduces issue shape 2 offline. |
| Same for an explicit `"choices": null` | **VERIFIED** | Same probe, `choices-null` case → `choices=None`, same `TypeError`. |
| When the body carries a top-level `error` object, it survives on the model and is reachable as `getattr(response, "error", None)` | **VERIFIED** | Probe printed `model_extra keys: ['error']` and `.error attr: {'message': 'upstream hiccup', 'code': 502}`. On a body without it, `getattr(..., default)` returns the default (pydantic raises `AttributeError`, which `getattr` absorbs) — so **always use `getattr` with a default**, never `response.error`. |
| `usage` may be `None` even on a parsed 200, and `usage.completion_tokens_details.reasoning_tokens` is reachable when present | **VERIFIED** | `no-choices-key` probe → `usage: None`; `empty-content` probe → `CompletionUsage(prompt_tokens=38000, completion_tokens=0, total_tokens=41589, completion_tokens_details=CompletionTokensDetails(reasoning_tokens=3589, ...))`. Both must be `getattr`-guarded. |
| `message.content` can be `""` **or** `None` on an otherwise well-formed choice | **VERIFIED** | `empty-content` probe → `''` with `finish_reason='stop'`; `null-content` probe → `None` with `finish_reason='length'`. |
| The `openai` SDK already retries HTTP transients on its own: **408, 409, 429, ≥500**, plus connection errors and timeouts, `max_retries=2` by default (3 total calls) | **VERIFIED** | `inspect.getsource(BaseClient._should_retry)` lists 408/409/429/≥500 and honours `x-should-retry`. Probe: `http=429 calls=3 elapsed=1.40s`, `http=500 calls=3`, `http=502 calls=3`, `http=503 calls=3`; `httpx.ConnectError` → `APIConnectionError` after 1.17s (3 attempts). `openai._constants.DEFAULT_MAX_RETRIES == 2`. |
| The SDK does **not** retry a 200 with empty/absent `choices` — that gap is exactly what this change fills | **VERIFIED** | `_should_retry` only inspects `response.status_code`/headers; all four malformed-200 probes returned after **1** call. |
| Non-retryable statuses fail fast at the SDK with **one** call and no backoff: 400/401/403/404/413/422 | **VERIFIED** | Probe: each → `calls=1`, `elapsed≤0.08s`, raising `BadRequestError` / `AuthenticationError` / `PermissionDeniedError` / `NotFoundError` / `APIStatusError(413)` / `UnprocessableEntityError`. Satisfies AC6 provided our loop retries **only** the transcript-less exception type. |
| `--dry-run` reaches no code path that calls `transcribe()`, so retry cannot affect it | **VERIFIED** | `cli.py:342-355` returns `0` inside the `if args.dry_run:` block before `workflow.run(...)` at `cli.py:363`; the dry run additionally operates on a scratch DB copy (`cli.py:221-226`). Baseline `transcribe --from-db --db-status pending --dry-run` → `No files selected for processing.`, exit 0. |
| A retry inside `TranscriptionService` cannot re-churn DB state | **VERIFIED by code reading** | `mark_processing` is called once at `workflow.py:277`, before `self.service.transcribe(path)` at `workflow.py:282`; `mark_error` only at `workflow.py:330` in the outer `except`. Nothing between them re-enters the DB. |
| `attempt_count` currently counts **runs**, not network attempts | **VERIFIED by code reading** | Only writer is `database.py:461-480` (`mark_processing`, `attempt_count = attempt_count + 1`), called once per file per run. **Decision: keep that meaning.** Retries are logged, not persisted. |
| The E2E test file exists and is cheap | **VERIFIED** | `8月5日 22-37 用藥紀錄.m4a`, 2.09 MB, present under `VOICE_MEMO_DIR`. |
| SDK read/write timeout is 600 s, so a slow reasoning call (3 m observed) is not clipped by the client | **VERIFIED** | `openai._constants.DEFAULT_TIMEOUT` → `Timeout(connect=5.0, read=600, write=600, pool=600)`. |
| **Risk:** worst-case wall-clock and cost multiply by `max_attempts` | ASSUMED (arithmetic) | With `max_attempts: 3` on `google/gemini-2.5-pro` at ~$0.075 and ~3 min per failed call, an exhausted retry costs ~$0.225 and ~9 min for one file. Mitigation: default of 3 is the documented ceiling, the knob is operator-tunable in `config.yaml`, and each retry prints an "attempt N of M" line to stderr as it happens. |
| **Risk:** repeated base64 encoding of an 18 MB file on each retry | ACCEPTED | ~0.2 s per encode against a multi-minute API call. Not worth hoisting; keep `_transcribe_via_chat` self-contained. |

---

## Patterns to Follow

**Nested settings dataclass + the four-place config chain.** `retry:` must be plumbed the
same way `safeguards:` is — dataclass, `from_yaml`, `from_env`, and the env-override block
inside `load()`:

```python
@dataclass
class SafeguardSettings:
    """Safety knobs to prevent unintended large batch operations."""

    default_max_files: int = 5
    confirmation_threshold: int = 3
    skip_completed_by_default: bool = True
# SOURCE: config.py:22-28

    safeguards: SafeguardSettings = field(default_factory=SafeguardSettings)
# SOURCE: config.py:60

            safeguards=SafeguardSettings(
                default_max_files=int(safeguards_data.get("default_max_files", 5)),
                ...
            ),
# SOURCE: config.py:110-114  (from_yaml)

            safeguards=SafeguardSettings(
                default_max_files=int(os.getenv("TRANSCRIBE_SKILL_MAX_FILES", "5")),
                ...
            ),
# SOURCE: config.py:146-150  (from_env)

            safeguards = config.safeguards
            if env_values["safeguards_default_max_files"]:
                safeguards.default_max_files = int(env_values["safeguards_default_max_files"])
            ...
            config.safeguards = safeguards
# SOURCE: config.py:239-249  (load() override block)
```

**Validation raises `ValueError` with an actionable message:**

```python
        if not (0.0 <= self.temperature <= 1.0):
            raise ValueError("Temperature must be between 0.0 and 1.0")
# SOURCE: config.py:261-262
```

**Fail-fast guards live before the expensive work, and read from config:**

```python
        if not self.config.is_supported_format(audio_path.name):
            raise ValueError(
                f"Unsupported file format for {audio_path.name}. Supported: {self.config.supported_formats}"
            )
# SOURCE: transcription.py:47-50
```

**Comment style — a docstring/comment states the failure the code exists to prevent**
(this repo consistently explains *why*, not *what*):

```python
    def ensure_client(self) -> None:
        """Build the client up front, before any row is marked 'processing'.

        Without this the lazy property defers a bad base_url or proxy setting
        until inside the per-file loop, failing every target one by one.
        """
# SOURCE: transcription.py:31-36
```

**Operator-facing warnings go to stderr:**

```python
        print("Error: No voice memo directory specified. ...", file=sys.stderr)
# SOURCE: cli.py:280
```

**The client is injectable for probes** — `client` returns `self._client` when already set,
so a test can assign `service._client = OpenAI(..., http_client=<MockTransport>)`:

```python
    @property
    def client(self) -> OpenAI:
        """Built on first use so `sync` and `--dry-run` need no API key."""

        if self._client is None:
            ...
        return self._client
# SOURCE: transcription.py:20-29
```

---

## Files to Change

| File | Action | Purpose |
|------|--------|---------|
| `.claude/skills/voice-memo-process/script/voice_transcription_service/config.py` | UPDATE | `RetrySettings` dataclass + `retry` field; wire through `from_yaml`, `from_env`, `load()` overrides, `validate()` |
| `.claude/skills/voice-memo-process/script/voice_transcription_service/config.yaml` | UPDATE | New `retry:` block with cost commentary |
| `.claude/skills/voice-memo-process/script/voice_transcription_service/transcription.py` | UPDATE | `EmptyTranscriptionError`, `_describe_chat_response`, retry loop in `transcribe()`, raise-instead-of-`or ""` in both mode methods |
| `.claude/skills/voice-memo-process/script/voice_transcription_service/README.md` | UPDATE | Retry section, env-var rows, `attempt_count` semantics, module blurb, changelog |

Dependency order: **config.py → config.yaml → transcription.py → README.md**
(`transcription.py` reads `self.config.retry`, so the config must exist first).

`workflow.py` and `database.py` are **not modified.** `workflow.py:283-284`'s
`raise ValueError("Transcription returned empty text")` stays as a cheap belt-and-braces
guard against a whitespace-only return; it simply stops being the primary path.

---

## Tasks

### Task 1: Add `RetrySettings` to the config chain
- File: `config.py`
- Action: UPDATE
- Implement:
  1. After `SafeguardSettings` (`config.py:22-28`) add:
     ```python
     @dataclass
     class RetrySettings:
         """Bounded retry for a 200 that carries no transcript.

         Each attempt is a fresh, separately billed API call on a multi-megabyte
         payload, so the bound is a cost ceiling, not just a patience setting.
         Transport-level transients (429, 5xx, connection, timeout) are already
         retried by the openai client and are not counted here.
         """

         max_attempts: int = 3
         backoff_seconds: float = 2.0
     ```
  2. Add the field next to `safeguards` (`config.py:60`):
     `retry: RetrySettings = field(default_factory=RetrySettings)`
  3. `from_yaml`: alongside `safeguards_data = raw.get("safeguards", {})` (`config.py:90`)
     add `retry_data = raw.get("retry", {})`, and pass
     `retry=RetrySettings(max_attempts=int(retry_data.get("max_attempts", 3)), backoff_seconds=float(retry_data.get("backoff_seconds", 2.0)))`.
  4. `from_env`: pass
     `retry=RetrySettings(max_attempts=int(os.getenv("TRANSCRIBE_SKILL_RETRY_MAX_ATTEMPTS", "3")), backoff_seconds=float(os.getenv("TRANSCRIBE_SKILL_RETRY_BACKOFF_SECONDS", "2.0")))`.
  5. `load()`: add `"retry_max_attempts"` / `"retry_backoff_seconds"` entries to `env_values`
     and mirror the `safeguards` override block at `config.py:239-249`.
  6. `validate()` (`config.py:253-264`): append
     ```python
     if self.retry.max_attempts < 1:
         raise ValueError("retry.max_attempts must be at least 1")
     if self.retry.backoff_seconds < 0:
         raise ValueError("retry.backoff_seconds must not be negative")
     ```
- Mirror: `config.py:22-28`, `config.py:60`, `config.py:90`, `config.py:110-114`, `config.py:146-150`, `config.py:239-249`, `config.py:261-262`
- Validate:
  ```bash
  cd .claude/skills/voice-memo-process
  ./.venv/bin/python -c "from script.voice_transcription_service.config import SkillConfig; c=SkillConfig.load(); print(c.retry)"
  TRANSCRIBE_SKILL_RETRY_MAX_ATTEMPTS=5 ./.venv/bin/python -c "from script.voice_transcription_service.config import SkillConfig; print(SkillConfig.load().retry)"
  ```
  Expect `RetrySettings(max_attempts=3, backoff_seconds=2.0)` then `max_attempts=5`.

### Task 2: Add the `retry:` block to `config.yaml`
- File: `config.yaml`
- Action: UPDATE
- Implement: append after the `safeguards:` block (`config.yaml:132-146`), matching the
  file's banner-comment style:
  ```yaml
  # -------------------------------------------------------------------------
  # Retry Behaviour
  # -------------------------------------------------------------------------
  # Bounded retry for a 200 response that arrives with no transcript (empty
  # content, or a body with no "choices"). Observed transient on OpenRouter and
  # more likely on reasoning models, which may spend their whole budget on
  # reasoning tokens before emitting content.
  #
  # COST: every attempt is a separately billed call on the full audio payload
  # (~$0.045-$0.075 per attempt for an 18 MB file on google/gemini-2.5-pro), so
  # max_attempts is a cost ceiling: worst case = max_attempts x per-call cost.
  #
  # Not counted here: HTTP 408/409/429/5xx, connection errors and timeouts are
  # already retried twice by the openai client itself.
  retry:
    # Total API attempts per file, including the first. 1 disables retrying.
    max_attempts: 3

    # Base delay before a retry, doubling each time (2s, then 4s).
    backoff_seconds: 2.0
  ```
- Mirror: `config.yaml:128-146`
- Validate: same command as Task 1 — YAML values must load (a YAML parse failure is
  silently swallowed by `config.py:167-171` and falls back to env, so confirm the value
  actually changes when you edit it):
  ```bash
  ./.venv/bin/python -c "from script.voice_transcription_service.config import SkillConfig; print(SkillConfig.load().retry)"
  ```

### Task 3: Rich diagnostics + retryable error type in `transcription.py`
- File: `transcription.py`
- Action: UPDATE
- Implement:
  1. Imports: add `import sys`, `import time`.
  2. Module-level exception, above the class:
     ```python
     class EmptyTranscriptionError(RuntimeError):
         """The API answered 200 but carried no usable transcript.

         Proven transient in practice: the identical payload replayed straight
         away came back with a full transcript. Distinct from every other
         failure so the retry loop can catch this and nothing else — an auth
         failure or an oversize file must not buy three backoff sleeps.
         """
     ```
  3. `_describe_chat_response(self, response) -> str` — every access `getattr`-guarded,
     because a malformed 200 may have `choices is None`, `usage is None`, and an `error`
     key that only exists in `model_extra`:
     ```python
     def _describe_chat_response(self, response) -> str:
         """Summarise a transcript-less response for the operator.

         'NoneType' object is not subscriptable told nobody anything; diagnosing
         it took an out-of-band replay of the payload. Report what came back.
         """

         parts = [f"model={self.config.model}"]
         choices = getattr(response, "choices", None)
         if not choices:
             parts.append("no choices in response")
         else:
             first = choices[0]
             parts.append(f"finish_reason={getattr(first, 'finish_reason', None)!r}")
             message = getattr(first, "message", None)
             parts.append(f"content={getattr(message, 'content', None)!r}")
         error = getattr(response, "error", None)
         if error:
             parts.append(f"error={error}")
         usage = getattr(response, "usage", None)
         if usage:
             parts.append(
                 f"tokens=prompt:{getattr(usage, 'prompt_tokens', '?')}"
                 f"/completion:{getattr(usage, 'completion_tokens', '?')}"
             )
             details = getattr(usage, "completion_tokens_details", None)
             reasoning = getattr(details, "reasoning_tokens", None)
             if reasoning:
                 parts.append(f"reasoning_tokens={reasoning}")
         return "; ".join(parts)
     ```
  4. `_transcribe_via_chat` — replace the final `return response.choices[0].message.content or ""`
     (`transcription.py:112`) with:
     ```python
         choices = getattr(response, "choices", None)
         content = ""
         if choices:
             message = getattr(choices[0], "message", None)
             content = getattr(message, "content", None) or ""
         if not content.strip():
             raise EmptyTranscriptionError(self._describe_chat_response(response))
         return content
     ```
  5. `_transcribe_via_audio_api` — the existing return chain (`transcription.py:75-79`)
     becomes a `text` local, then:
     ```python
         if not text.strip():
             raise EmptyTranscriptionError(
                 f"model={self.config.model}; audio_api returned no text"
             )
         return text
     ```
- Mirror: `transcription.py:31-36` (docstring style), `transcription.py:47-50` (raise style)
- Validate:
  ```bash
  ./.venv/bin/python -c "import script.voice_transcription_service.transcription as t; print(t.EmptyTranscriptionError.__mro__[:2])"
  ```

### Task 4: The bounded retry loop in `transcribe()`
- File: `transcription.py`
- Action: UPDATE
- Implement: keep the three fail-fast guards (`transcription.py:43-57`) **outside** the
  loop — they are free, deterministic, and must never buy a sleep. Replace the mode
  dispatch (`transcription.py:59-61`) with:
  ```python
      attempts = max(self.config.retry.max_attempts, 1)
      last_error: EmptyTranscriptionError | None = None

      # The retry lives here, under workflow's mark_processing/mark_error pair,
      # so a retried file passes through the DB state machine exactly once.
      # attempt_count therefore keeps counting runs, not network attempts.
      for attempt in range(1, attempts + 1):
          try:
              if self.config.transcription_mode == "chat_completions":
                  return self._transcribe_via_chat(audio_path)
              return self._transcribe_via_audio_api(audio_path)
          except EmptyTranscriptionError as exc:
              last_error = exc
              if attempt == attempts:
                  break
              delay = self.config.retry.backoff_seconds * (2 ** (attempt - 1))
              print(
                  f"↻ {audio_path.name}: attempt {attempt} of {attempts} returned "
                  f"no transcript ({exc}); retrying in {delay:.0f}s",
                  file=sys.stderr,
                  flush=True,
              )
              time.sleep(delay)

      raise ValueError(
          f"No transcript after {attempts} attempt(s): {last_error}"
      )
  ```
  Notes for the implementer:
  - Only `EmptyTranscriptionError` is caught. `AuthenticationError`, `BadRequestError`,
    `APIStatusError(413)`, `FileNotFoundError`, `ValueError` from the format/size guards
    and every other exception propagate on the first attempt — **AC6**.
  - `flush=True` because the loop then sleeps; without it a threaded run under
    `--workers N` shows nothing until the process exits.
  - `ValueError` (not `EmptyTranscriptionError`) is what escapes, so `workflow.py:327`'s
    `except Exception` stores the full diagnostic via `mark_error` (truncated to 1000
    chars at `database.py:556`).
- Mirror: `transcription.py:40-61`
- Validate: the offline injected-client probe in **Validation** below.

### Task 5: Document the behaviour in `README.md`
- File: `README.md`
- Action: UPDATE
- Implement:
  1. Under `## Configuration Details`, after `### Safety Safeguards` (README:332-338), add
     `### Retry Behaviour`: the two shapes that are retried (empty content; body with no
     `choices`), the bound and backoff (3 attempts, 2 s then 4 s), the explicit cost
     ceiling (`max_attempts x per-call cost`, ~$0.045–$0.075 per attempt for an 18 MB file
     on `google/gemini-2.5-pro`), that reasoning models make the empty shape more likely,
     the stderr `↻ ... attempt N of M ...` line, and the boundary with the `openai`
     client's own retries (408/409/429/≥500 + connection/timeout, `max_retries=2`, not
     double-counted).
  2. `### Environment Variables List` (README:310-330): add rows for
     `TRANSCRIBE_SKILL_RETRY_MAX_ATTEMPTS` → `retry.max_attempts` and
     `TRANSCRIBE_SKILL_RETRY_BACKOFF_SECONDS` → `retry.backoff_seconds`.
  3. `### Status Flow` (README:422-...): one sentence stating that `attempt_count` counts
     **runs**, not network attempts — retries happen beneath `mark_processing`, so a file
     that succeeded on its third API call still shows `attempt_count = 1`, and a file that
     exhausted its retries lands in `error` once with the diagnostic in `last_error`.
  4. `### transcription.py` module blurb (README:630-634): add "Bounded retry for
     transcript-less responses".
  5. `## Changelog` (README:656): new `### v2.2 (Current Version)` entry above v2.1,
     demote v2.1's "(Current Version)" marker, and bump the `**Last Updated**` /
     `**date-updated**` footer to `2026-09-05`.
- Mirror: README:332-338 (section style), README:310-330 (table style)
- Validate: `grep -n "Retry Behaviour\|RETRY_MAX_ATTEMPTS\|attempt_count" script/voice_transcription_service/README.md`

---

## Validation

All commands from `.claude/skills/voice-memo-process/`.

**1. Project verify gate (must stay green, unchanged behaviour):**
```bash
./.venv/bin/python -m script.voice_transcription_service --help
./.venv/bin/python -m script.voice_transcription_service sync --days 7 --dry-run --verbose
./.venv/bin/python -m script.voice_transcription_service transcribe --from-db --db-status pending --dry-run
```
Baseline before the change: all exit 0; the third prints `No files selected for processing.`

**2. Offline unit probe — zero API calls, zero DB writes.** Write to the scratchpad (a
throwaway, not committed; this repo has no test suite). Inject a mock transport by
assigning `service._client` before the call — legal because `transcription.py:24` only
builds a client when `self._client is None`:

```python
# scratchpad/verify_retry.py
import time, httpx
from pathlib import Path
from openai import OpenAI
from script.voice_transcription_service.config import SkillConfig
from script.voice_transcription_service.transcription import TranscriptionService

GOOD = {"id":"x","object":"chat.completion","created":1,"model":"m",
        "choices":[{"index":0,"finish_reason":"stop",
                    "message":{"role":"assistant","content":"完整的逐字稿"}}]}
NO_CHOICES = {"id":"x","object":"chat.completion","created":1,"model":"m",
              "error":{"message":"upstream hiccup","code":502}}
EMPTY = {"id":"x","object":"chat.completion","created":1,"model":"m",
         "choices":[{"index":0,"finish_reason":"stop",
                     "message":{"role":"assistant","content":""}}],
         "usage":{"prompt_tokens":38000,"completion_tokens":0,"total_tokens":41589,
                  "completion_tokens_details":{"reasoning_tokens":3589}}}

def service(queue, status=200, **overrides):
    cfg = SkillConfig.load()
    cfg.transcription_mode = "chat_completions"
    cfg.retry.backoff_seconds = 0.1
    for k, v in overrides.items():
        setattr(cfg.retry, k, v)
    calls = {"n": 0}
    def handler(request):
        calls["n"] += 1
        return httpx.Response(status, json=queue[min(calls["n"] - 1, len(queue) - 1)])
    svc = TranscriptionService(cfg)
    svc._client = OpenAI(api_key="sk-test", base_url="https://example.invalid/v1",
                         http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    return svc, calls

AUDIO = Path("<any small real .m4a under VOICE_MEMO_DIR>")

# A: recovers on the third attempt, and actually backed off (0.1 + 0.2 s)
svc, calls = service([NO_CHOICES, EMPTY, GOOD])
t0 = time.time(); text = svc.transcribe(AUDIO); dt = time.time() - t0
assert text == "完整的逐字稿" and calls["n"] == 3 and dt >= 0.3, (text, calls, dt)

# B: exhausts the bound; the message names what was seen, not a TypeError
svc, calls = service([EMPTY])
try:
    svc.transcribe(AUDIO); assert False, "should have raised"
except ValueError as exc:
    msg = str(exc)
    assert calls["n"] == 3 and "3 attempt" in msg
    assert "finish_reason='stop'" in msg and "reasoning_tokens=3589" in msg, msg

# C: choices absent, exhausted -> names it, surfaces the body's error object
svc, calls = service([NO_CHOICES])
try:
    svc.transcribe(AUDIO); assert False
except ValueError as exc:
    assert "no choices in response" in str(exc) and "upstream hiccup" in str(exc)
except TypeError:
    assert False, "regression: NoneType still leaking"

# D: max_attempts=1 disables retrying
svc, calls = service([EMPTY], max_attempts=1)
try: svc.transcribe(AUDIO)
except ValueError: pass
assert calls["n"] == 1, calls

# E: AC6 — a 401 fails fast, one call, no backoff sleep
svc, calls = service([{"error":{"message":"bad key"}}], status=401)
t0 = time.time()
try: svc.transcribe(AUDIO)
except Exception as exc: assert type(exc).__name__ == "AuthenticationError", exc
assert calls["n"] == 1 and time.time() - t0 < 0.1, (calls, time.time() - t0)

print("all retry probes passed")
```
Run with `./.venv/bin/python <scratchpad>/verify_retry.py`. Then confirm the DB was
untouched: record `sha256sum script/audio_metadata.db` before and after — identical.

**3. AC6 regression at the CLI (no mock, still no billable call):**
```bash
time ./.venv/bin/python -m script.voice_transcription_service --api-key sk-definitely-invalid \
    transcribe --from-db --db-status pending --db-limit 1 --yes
```
Must fail in seconds with an authentication error, not after three backoffs.

**4. `--dry-run` fidelity (AC5):**
```bash
sha256sum script/audio_metadata.db
./.venv/bin/python -m script.voice_transcription_service transcribe --from-db --db-status pending --dry-run
sha256sum script/audio_metadata.db   # must be identical
```

**5. End-to-end, one real billable call (~$0.005):**
```bash
./.venv/bin/python -m script.voice_transcription_service transcribe \
    "$VOICE_MEMO_DIR/8月5日 22-37 用藥紀錄.m4a" --force --verbose
```
(2.09 MB, confirmed present.) Expect a transcript written to `RAW_TRANSCRIPT_DIR`, the row
`completed`, `last_error` NULL, and `attempt_count` incremented by exactly **1**:
```bash
./.venv/bin/python -c "
import sqlite3; c=sqlite3.connect('script/audio_metadata.db'); c.row_factory=sqlite3.Row
for r in c.execute(\"select filename,status,attempt_count,last_error from audio_files where filename like '%用藥紀錄%'\"):
    print(dict(r))"
```

---

## Acceptance Criteria
- [ ] All 5 tasks completed
- [ ] Verify gate (`--help`, `sync --dry-run`, `transcribe --dry-run`) exits 0 with output unchanged from baseline
- [ ] **AC1** — empty content and `choices is None` are retried, not failed (probes A/C)
- [ ] **AC2** — bound is enforced, backed off, printed as `attempt N of M` on stderr, and the cost ceiling is documented in `config.yaml` + README
- [ ] **AC3** — exhausted retries raise a message naming `finish_reason`, absent `choices`, the body's `error` object, and token usage — never `'NoneType' object is not subscriptable` (probes B/C)
- [ ] **AC4** — `workflow.py` and `database.py` untouched; `mark_processing`/`mark_error` run once per file per run; `attempt_count` documented as counting **runs** (E2E step 5 shows +1)
- [ ] **AC5** — `--dry-run` makes zero API calls and leaves `audio_metadata.db` byte-identical (step 4)
- [ ] **AC6** — a bad `--api-key` fails in one call with no backoff (probe E, step 3)
- [ ] Follows the existing config-chain, docstring, and stderr-warning patterns
- [ ] End-to-end real run succeeds and lands `completed` (step 5)
