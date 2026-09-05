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


import httpx
from pathlib import Path
from openai import OpenAI
from script.voice_transcription_service.config import SkillConfig
from script.voice_transcription_service.transcription import TranscriptionService

AUDIO = sample_audio()

def service(bodies, **ov):
    cfg = SkillConfig.load()
    cfg.transcription_mode = "audio_api"      # the other mode
    cfg.response_format = "text"
    cfg.retry.backoff_seconds = 0.0
    for k, v in ov.items(): setattr(cfg.retry, k, v)
    calls = {"n": 0}
    def h(req):
        calls["n"] += 1
        return httpx.Response(200, text=bodies[min(calls["n"]-1, len(bodies)-1)],
                              headers={"content-type": "text/plain"})
    svc = TranscriptionService(cfg)
    svc._client = OpenAI(api_key="sk-test", base_url="https://example.invalid/v1",
                         http_client=httpx.Client(transport=httpx.MockTransport(h)))
    return svc, calls

# I1: empty text from audio_api is retried, then recovers
svc, calls = service(["", "   ", "完整的逐字稿"])
assert svc.transcribe(AUDIO) == "完整的逐字稿" and calls["n"] == 3, calls
print("I1 ok: audio_api empty/whitespace retried, recovered on attempt 3")

# I2: exhausted -> ValueError naming the mode, not a silent empty string
svc, calls = service([""])
try:
    svc.transcribe(AUDIO); assert False, "should have raised"
except ValueError as exc:
    seen = str(exc)
assert calls["n"] == 3 and "audio_api returned no text" in seen and "3 attempt" in seen, (calls, seen)
print(f"I2 ok: {seen}")
print("audio_api probes passed")
