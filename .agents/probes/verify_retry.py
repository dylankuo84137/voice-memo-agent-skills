import os, shutil, tempfile
from pathlib import Path


def sample_audio() -> Path:
    """A small real .m4a, cached on local disk.

    VOICE_MEMO_DIR is an fuse.rclone mount: a cold re-read of even a 2 MB file
    costs ~17s, and each retry re-reads, which drowns the timing assertions.
    """

    cached = Path(tempfile.gettempdir()) / "voice-memo-retry-probe-sample.m4a"
    if not cached.exists():
        src = Path(os.path.expanduser(
            os.environ.get("VOICE_MEMO_DIR", "~/gdrive/Inbox/voice-memo-recordings")
        )) / "8月5日 22-37 用藥紀錄.m4a"
        assert src.exists(), f"sample audio not found: {src}"
        shutil.copy2(src, cached)
    return cached


import os, time, httpx
from pathlib import Path
from openai import OpenAI
from script.voice_transcription_service.config import SkillConfig
from script.voice_transcription_service.transcription import TranscriptionService

GOOD = {"id":"x","object":"chat.completion","created":1,"model":"m",
        "choices":[{"index":0,"finish_reason":"stop",
                    "message":{"role":"assistant","content":"完整的逐字稿"}}]}
NO_CHOICES = {"id":"x","object":"chat.completion","created":1,"model":"m",
              "error":{"message":"upstream hiccup","code":502}}
NULL_CHOICES = {"id":"x","object":"chat.completion","created":1,"model":"m","choices":None}
EMPTY = {"id":"x","object":"chat.completion","created":1,"model":"m",
         "choices":[{"index":0,"finish_reason":"stop",
                     "message":{"role":"assistant","content":""}}],
         "usage":{"prompt_tokens":38000,"completion_tokens":0,"total_tokens":41589,
                  "completion_tokens_details":{"reasoning_tokens":3589}}}
NULL_CONTENT = {"id":"x","object":"chat.completion","created":1,"model":"m",
                "choices":[{"index":0,"finish_reason":"length",
                            "message":{"role":"assistant","content":None}}]}

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

AUDIO = sample_audio()

# A: recovers on the third attempt, and actually backed off (0.1 + 0.2 s)
svc, calls = service([NO_CHOICES, EMPTY, GOOD])
t0 = time.time(); text = svc.transcribe(AUDIO); dt = time.time() - t0
assert text == "完整的逐字稿" and calls["n"] == 3 and dt >= 0.3, (text, calls, dt)
print(f"A ok: recovered on attempt 3, backed off {dt:.2f}s")

# B: exhausts the bound; the message names what was seen, not a TypeError
svc, calls = service([EMPTY])
try:
    svc.transcribe(AUDIO); assert False, "should have raised"
except ValueError as exc:
    msg = str(exc)
    assert calls["n"] == 3 and "3 attempt" in msg, (calls, msg)
    assert "finish_reason='stop'" in msg and "reasoning_tokens=3589" in msg, msg
    assert "prompt:38000" in msg, msg
print(f"B ok: {msg}")

# C: choices absent, exhausted -> names it, surfaces the body's error object
svc, calls = service([NO_CHOICES])
try:
    svc.transcribe(AUDIO); assert False
except ValueError as exc:
    seen = str(exc)
    assert "no choices in response" in seen and "upstream hiccup" in seen, seen
except TypeError:
    assert False, "regression: NoneType still leaking"
print(f"C ok: {seen}")

# C2: explicit "choices": null -- the second shape from the issue
svc, calls = service([NULL_CHOICES])
try:
    svc.transcribe(AUDIO); assert False
except ValueError as exc:
    seen = str(exc)
    assert "no choices in response" in seen, seen
except TypeError:
    assert False, "regression: NoneType still leaking"
print(f"C2 ok: {seen}")

# C3: content is null (finish_reason=length) is retried too
svc, calls = service([NULL_CONTENT, GOOD])
assert svc.transcribe(AUDIO) == "完整的逐字稿" and calls["n"] == 2, calls
print("C3 ok: null content retried, recovered on attempt 2")

# D: max_attempts=1 disables retrying
svc, calls = service([EMPTY], max_attempts=1)
try: svc.transcribe(AUDIO)
except ValueError: pass
assert calls["n"] == 1, calls
print("D ok: max_attempts=1 -> 1 call")

# E: AC6 -- a 401 fails fast, one call, no backoff sleep
svc, calls = service([{"error":{"message":"bad key"}}], status=401)
t0 = time.time()
try: svc.transcribe(AUDIO)
except Exception as exc: assert type(exc).__name__ == "AuthenticationError", exc
el = time.time() - t0
assert calls["n"] == 1 and el < 0.1, (calls, el)
print(f"E ok: 401 -> 1 call, {el:.3f}s, no backoff")

# F: AC6 -- 400 and 413 also fail fast with one call
for status, expected in ((400, "BadRequestError"), (413, "APIStatusError")):
    svc, calls = service([{"error":{"message":"nope"}}], status=status)
    t0 = time.time()
    try:
        svc.transcribe(AUDIO); assert False
    except Exception as exc:
        assert type(exc).__name__ == expected, (status, type(exc).__name__)
    assert calls["n"] == 1 and time.time() - t0 < 0.1, (status, calls)
print("F ok: 400/413 fail fast, 1 call each")

# G: AC6 -- local guards raise before any call and before any sleep
svc, calls = service([EMPTY])
try: svc.transcribe(Path("/nonexistent/x.m4a")); assert False
except FileNotFoundError: pass
assert calls["n"] == 0, calls
svc, calls = service([EMPTY])
try: svc.transcribe(Path(__file__)); assert False
except ValueError as exc: assert "Unsupported file format" in str(exc), exc
assert calls["n"] == 0, calls
print("G ok: fail-fast guards make 0 calls, no sleep")

# H: the backoff sequence itself -- record sleeps instead of trusting wall clock
import script.voice_transcription_service.transcription as tmod
slept = []
real_sleep = tmod.time.sleep
tmod.time.sleep = lambda d: slept.append(d)
try:
    svc, calls = service([EMPTY], max_attempts=4)
    svc.config.retry.backoff_seconds = 2.0
    try: svc.transcribe(AUDIO)
    except ValueError: pass
finally:
    tmod.time.sleep = real_sleep
assert slept == [2.0, 4.0, 8.0], slept
assert calls["n"] == 4, calls
print(f"H ok: 4 attempts, exponential backoff {slept} (no sleep after the last)")

print("all retry probes passed")
