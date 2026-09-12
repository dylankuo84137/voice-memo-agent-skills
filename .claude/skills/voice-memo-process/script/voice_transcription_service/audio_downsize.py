"""Shrink an oversize memo into a speech-grade copy fit for one API request.

Why this exists: in `chat_completions` mode the audio is base64'd into a single
JSON body, so the binding limit is bytes on the wire, not minutes of speech.
iOS Voice Memos hands us 48 kHz stereo at ~98 kbps — roughly three times what a
transcription model needs to read speech. Re-encoding to mono / 16 kHz / 32 kbps
buys back that margin: a measured 32.68 MB / 46.7-minute memo came out at
10.94 MB in 10.5 s, with ffprobe reporting the duration unchanged to within
0.04 s (container rounding).

Kept separate from `transcription.py` for the same reason `recording_time.py`
is: one subprocess, one job, testable without an API key.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

# Measured 10.5 s for a 46.7-minute memo. 600 s covers a multi-hour recording
# read over a slow FUSE mount (VOICE_MEMO_DIR lives under a synced drive)
# without letting a wedged ffmpeg hang the run forever.
_FFMPEG_TIMEOUT = 600


class DownsizeError(RuntimeError):
    """The transcode did not produce a usable file.

    Raised rather than degraded to None the way `_from_ffprobe` handles a
    missing ffprobe: the caller only reaches this code because the file already
    failed the size gate, so there is no smaller path forward to fall back to.
    The workflow's existing except-funnel turns it into a row-level error.
    """


def downsize(
    src: Path,
    dest_dir: Path,
    *,
    channels: int,
    sample_rate: int,
    bitrate_kbps: int,
) -> Path:
    """Re-encode `src` into `dest_dir` at speech bitrate; return the new path.

    The copy keeps the original suffix on purpose: `_transcribe_via_chat`
    derives the API's `format` field from it (`transcription.py:126`), and
    `.m4a` paired with `-c:a aac` is the combination already proven in
    production on a real memo.
    """

    dest = dest_dir / src.name
    try:
        proc = subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-i",
                str(src),
                "-ac",
                str(channels),
                "-ar",
                str(sample_rate),
                "-c:a",
                "aac",
                "-b:a",
                f"{bitrate_kbps}k",
                str(dest),
            ],
            capture_output=True,
            text=True,
            timeout=_FFMPEG_TIMEOUT,
        )
    except FileNotFoundError as exc:
        raise DownsizeError(
            f"ffmpeg is not available, so {src.name} cannot be compressed to fit "
            f"the request limit: {exc}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise DownsizeError(
            f"ffmpeg timed out after {_FFMPEG_TIMEOUT}s compressing {src.name}"
        ) from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise DownsizeError(f"ffmpeg could not be run on {src.name}: {exc}") from exc

    if proc.returncode != 0:
        # Clamped at 400: mark_error truncates the whole message at 1000
        # (database.py:557) and the caller prepends its own context.
        raise DownsizeError(
            f"ffmpeg exited {proc.returncode} on {src.name}: "
            f"{proc.stderr.strip()[:400]}"
        )

    # ffmpeg reports success without writing anything on some edge paths; an
    # empty file would sail through the size gate and be sent as silence.
    if not dest.exists() or dest.stat().st_size == 0:
        raise DownsizeError(
            f"ffmpeg reported success but produced no audio for {src.name}"
        )

    return dest
