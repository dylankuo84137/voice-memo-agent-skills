"""Transcription service wrapper around the OpenAI API."""

from __future__ import annotations

import base64
from pathlib import Path

from openai import OpenAI

from .config import SkillConfig


class TranscriptionService:
    """High-level transcription helper built on OpenAI's audio API."""

    def __init__(self, config: SkillConfig):
        self.config = config
        client_kwargs = {"api_key": config.api_key}
        if config.base_url:
            client_kwargs["base_url"] = config.base_url
        self.client = OpenAI(**client_kwargs)

    def transcribe(self, audio_path: Path) -> str:
        """Transcribe an audio file into text using configured parameters."""

        audio_path = audio_path.expanduser().resolve()
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        if not self.config.is_supported_format(audio_path.name):
            raise ValueError(
                f"Unsupported file format for {audio_path.name}. Supported: {self.config.supported_formats}"
            )

        file_size = audio_path.stat().st_size
        if file_size > self.config.max_file_size_bytes:
            raise ValueError(
                f"File size {file_size / (1024 * 1024):.2f} MB exceeds the configured limit of "
                f"{self.config.max_file_size_mb} MB"
            )

        if self.config.transcription_mode == "chat_completions":
            return self._transcribe_via_chat(audio_path)
        return self._transcribe_via_audio_api(audio_path)

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
            return response
        if hasattr(response, "text"):
            return response.text  # type: ignore[return-value]
        return str(response)

    def _transcribe_via_chat(self, audio_path: Path) -> str:
        """Transcribe via chat completions with base64-encoded audio (OpenRouter format)."""
        suffix = audio_path.suffix.lstrip(".").lower()
        audio_data = base64.b64encode(audio_path.read_bytes()).decode("utf-8")

        response = self.client.chat.completions.create(
            model=self.config.model,
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
        return response.choices[0].message.content or ""
