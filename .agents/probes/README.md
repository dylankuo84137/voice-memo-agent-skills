# Probes

Offline verification scripts. This repo has no test suite; these stand in for one
where a change needs executable evidence.

They make **zero API calls** (an injected `httpx.MockTransport` answers every
request) and **zero database writes**. The only real I/O is one read of a small
`.m4a`, cached to the system temp dir on first run.

Run them from the skill directory, with the package on the path:

```bash
cd .claude/skills/voice-memo-process
PYTHONPATH=. ./.venv/bin/python ../../../.agents/probes/verify_retry.py
PYTHONPATH=. ./.venv/bin/python ../../../.agents/probes/verify_audio_api.py
```

Each prints one line per probe and `all retry probes passed` /
`audio_api probes passed` at the end; a failure raises `AssertionError`.

| Script | Covers |
|---|---|
| `verify_retry.py` | `chat_completions` mode: retry on empty content, absent `choices`, `"choices": null`, and null content; exhaustion diagnostics; `max_attempts: 1`; exponential backoff sequence; fail-fast on 400/401/413 and on the local format/size/existence guards |
| `verify_audio_api.py` | `audio_api` mode: the same retry and exhaustion behaviour on the other transcription path |
