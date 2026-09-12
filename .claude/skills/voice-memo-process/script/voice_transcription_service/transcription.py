"""Transcription service wrapper around the OpenAI API."""

from __future__ import annotations

import base64
import sys
import time
from pathlib import Path

from openai import OpenAI

from .config import SkillConfig


class EmptyTranscriptionError(RuntimeError):
    """The API answered 200 but carried no usable transcript.

    Proven transient in practice: the identical payload replayed straight
    away came back with a full transcript. Distinct from every other
    failure so the retry loop can catch this and nothing else — an auth
    failure or an oversize file must not buy three backoff sleeps.
    """


class TranscriptionService:
    """High-level transcription helper built on OpenAI's audio API."""

    def __init__(self, config: SkillConfig):
        self.config = config
        self._client: OpenAI | None = None

    @property
    def client(self) -> OpenAI:
        """Built on first use so `sync` and `--dry-run` need no API key."""

        if self._client is None:
            client_kwargs = {"api_key": self.config.api_key}
            if self.config.base_url:
                client_kwargs["base_url"] = self.config.base_url
            self._client = OpenAI(**client_kwargs)
        return self._client

    def ensure_client(self) -> None:
        """Build the client up front, before any row is marked 'processing'.

        Without this the lazy property defers a bad base_url or proxy setting
        until inside the per-file loop, failing every target one by one.
        """

        _ = self.client

    def transcribe(self, audio_path: Path) -> str:
        """Transcribe an audio file into text using configured parameters."""

        audio_path = audio_path.expanduser().resolve()
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        if not self.config.is_supported_format(audio_path.name):
            raise ValueError(
                f"Unsupported file format for {audio_path.name}. Supported: {self.config.supported_formats}"
            )

        # A backstop, not the first line of defence: workflow downsizes an
        # oversize memo before calling, so in the normal pipeline nothing
        # reaches this raise. It still guards direct use of the service, and a
        # downsized copy that is somehow still too large. Stays a bare
        # ValueError and stays above the retry loop — see
        # EmptyTranscriptionError — so an oversize file fails once, fast.
        file_size = audio_path.stat().st_size
        if file_size > self.config.max_file_size_bytes:
            raise ValueError(
                f"File size {file_size / (1024 * 1024):.2f} MB exceeds the "
                f"{self.config.effective_max_file_size_mb} MB limit for "
                f"transcription_mode {self.config.transcription_mode!r}"
            )

        attempts = max(self.config.retry.max_attempts, 1)
        last_error: EmptyTranscriptionError | None = None

        # The retry lives here, under workflow's mark_processing/mark_error
        # pair, so a retried file passes through the DB state machine exactly
        # once. attempt_count therefore keeps counting runs, not network calls.
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
                # flush: the loop sleeps next, and under --workers N nothing
                # would reach the operator until the process exited.
                print(
                    f"↻ {audio_path.name}: attempt {attempt} of {attempts} returned "
                    f"no transcript ({exc}); retrying in {delay:.0f}s",
                    file=sys.stderr,
                    flush=True,
                )
                time.sleep(delay)

        raise ValueError(f"No transcript after {attempts} attempt(s): {last_error}")

    def _transcribe_via_audio_api(self, audio_path: Path) -> str:
        """Use OpenAI native audio transcription endpoint."""
        with audio_path.open("rb") as audio_file:
            response = self.client.audio.transcriptions.create(
                model=self.config.model,
                file=audio_file,
                language=self.config.language,
                response_format=self.config.response_format,
                temperature=self.config.temperature,
                prompt=self.config.prompt,
            )

        if isinstance(response, str):
            text = response
        elif hasattr(response, "text"):
            text = response.text  # type: ignore[assignment]
        else:
            text = str(response)

        if not text.strip():
            raise EmptyTranscriptionError(
                f"model={self.config.model}; audio_api returned no text"
            )
        return text

    def _transcribe_via_chat(self, audio_path: Path) -> str:
        """Transcribe via chat completions with base64-encoded audio (OpenRouter format)."""
        suffix = audio_path.suffix.lstrip(".").lower()
        audio_data = base64.b64encode(audio_path.read_bytes()).decode("utf-8")

        response = self.client.chat.completions.create(
            model=self.config.model,
            temperature=self.config.temperature,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_audio",
                            "input_audio": {
                                "data": audio_data,
                                "format": suffix,
                            },
                        },
                        {
                            "type": "text",
                            "text": (
                                f"Please transcribe this audio. "
                                f"Language: {self.config.language}. "
                                f"{self.config.prompt}"
                            ),
                        },
                    ],
                }
            ],
        )
        choices = getattr(response, "choices", None)
        content = ""
        if choices:
            message = getattr(choices[0], "message", None)
            content = getattr(message, "content", None) or ""
        if not content.strip():
            raise EmptyTranscriptionError(self._describe_chat_response(response))
        return content

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
