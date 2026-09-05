"""Configuration management for the unified transcription workflow."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    YAML_AVAILABLE = False


DEFAULT_SUPPORTED_FORMATS = [".m4a", ".mp3", ".wav", ".mp4"]

# Fallback for a config.yaml that is missing `model.prompt`. It carries only
# the script-variant guarantee, deliberately not the proper-noun vocabulary the
# YAML also holds: that list is edited as names are confirmed, and duplicating
# it here would only give it a second copy to drift from. What this default
# must not do is contradict the YAML — the previous one asked, in Simplified
# Chinese, for Simplified output.
DEFAULT_PROMPT = (
    "Output in Traditional Chinese (Taiwan, 繁體中文) only — never Simplified "
    "Chinese. Add appropriate punctuation. Transcribe verbatim: do not "
    "summarise, omit, or rephrase."
)


@dataclass
class SafeguardSettings:
    """Safety knobs to prevent unintended large batch operations."""

    default_max_files: int = 5
    confirmation_threshold: int = 3
    skip_completed_by_default: bool = True


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


@dataclass
class SkillConfig:
    """Runtime configuration for script."""

    # API / model settings
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    transcription_mode: str = "audio_api"  # "audio_api" or "chat_completions"
    model: str = "gpt-4o-transcribe"
    language: str = "zh"
    temperature: float = 0.2
    response_format: str = "text"
    prompt: str = DEFAULT_PROMPT

    # File handling
    supported_formats: List[str] = field(default_factory=lambda: DEFAULT_SUPPORTED_FORMATS.copy())
    max_file_size_mb: int = 25
    output_suffix: str = ".md"
    output_encoding: str = "utf-8"
    default_output_dir: Optional[str] = None
    # Optional default directory where voice memos are stored
    voice_memo_directory: Optional[str] = None
    # Default synchronization date range (days) when selecting from DB
    default_sync_days: int = 30

    # Infrastructure
    database_path: str = "./audio_metadata.db"
    database_timeout: float = 10.0

    safeguards: SafeguardSettings = field(default_factory=SafeguardSettings)
    retry: RetrySettings = field(default_factory=RetrySettings)

    @staticmethod
    def _load_env() -> None:
        """Attempt to load environment variables from the nearest .env file."""

        current = Path(__file__).resolve().parent
        for directory in [current] + list(current.parents):
            env_file = directory / ".env"
            if env_file.exists():
                load_dotenv(dotenv_path=env_file, override=False)
                return
        load_dotenv(override=False)

    @classmethod
    def from_yaml(cls, yaml_path: Optional[str] = None) -> "SkillConfig":
        """Load configuration from a YAML file if PyYAML is installed."""

        if not YAML_AVAILABLE:
            raise ImportError(
                "PyYAML is required to load YAML configuration. Install it via 'pip install pyyaml'."
            )

        config_path = Path(yaml_path) if yaml_path else Path(__file__).with_name("config.yaml")
        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        with config_path.open("r", encoding="utf-8") as f:
            raw: Dict[str, Any] = yaml.safe_load(f) or {}

        safeguards_data = raw.get("safeguards", {})
        retry_data = raw.get("retry", {})

        return cls(
            api_key=None,  # YAML never carries secrets
            base_url=raw.get("api", {}).get("base_url"),
            transcription_mode=raw.get("api", {}).get("transcription_mode", "audio_api"),
            model=raw.get("model", {}).get("name", "gpt-4o-transcribe"),
            language=raw.get("model", {}).get("language", "zh"),
            temperature=float(raw.get("model", {}).get("temperature", 0.2)),
            response_format=raw.get("model", {}).get("response_format", "text"),
            prompt=raw.get("model", {}).get("prompt", DEFAULT_PROMPT),
            supported_formats=raw.get("files", {}).get("supported_formats", DEFAULT_SUPPORTED_FORMATS),
            max_file_size_mb=int(raw.get("files", {}).get("max_file_size_mb", 25)),
            output_suffix=raw.get("output", {}).get("suffix", ".md"),
            output_encoding=raw.get("output", {}).get("encoding", "utf-8"),
            default_output_dir=raw.get("output", {}).get("directory"),
            voice_memo_directory=raw.get("sources", {}).get("voice_memo_directory"),
            default_sync_days=int(raw.get("sources", {}).get("default_sync_days", 30)),
            database_path=raw.get("database", {}).get("path", "./audio_metadata.db"),
            database_timeout=float(raw.get("database", {}).get("timeout", 10.0)),
            safeguards=SafeguardSettings(
                default_max_files=int(safeguards_data.get("default_max_files", 5)),
                confirmation_threshold=int(safeguards_data.get("confirmation_threshold", 3)),
                skip_completed_by_default=bool(safeguards_data.get("skip_completed_by_default", True)),
            ),
            retry=RetrySettings(
                max_attempts=int(retry_data.get("max_attempts", 3)),
                backoff_seconds=float(retry_data.get("backoff_seconds", 2.0)),
            ),
        )

    @classmethod
    def from_env(cls) -> "SkillConfig":
        """Load configuration exclusively from environment variables."""

        supported_formats_raw = os.getenv("TRANSCRIBE_SKILL_SUPPORTED_FORMATS")
        supported_formats = (
            [fmt.strip() for fmt in supported_formats_raw.split(",") if fmt.strip()]
            if supported_formats_raw
            else DEFAULT_SUPPORTED_FORMATS
        )

        return cls(
            api_key=os.getenv("OTHER_API_KEY") or os.getenv("OPENAI_API_KEY"),
            base_url=os.getenv("OPENAI_BASE_URL"),
            transcription_mode=os.getenv("TRANSCRIBE_SKILL_MODE", "audio_api"),
            model=os.getenv("TRANSCRIBE_SKILL_MODEL", "gpt-4o-transcribe"),
            language=os.getenv("TRANSCRIBE_SKILL_LANGUAGE", "zh"),
            temperature=float(os.getenv("TRANSCRIBE_SKILL_TEMPERATURE", "0.2")),
            response_format=os.getenv("TRANSCRIBE_SKILL_RESPONSE_FORMAT", "text"),
            prompt=os.getenv("TRANSCRIBE_SKILL_PROMPT", DEFAULT_PROMPT),
            supported_formats=supported_formats,
            max_file_size_mb=int(os.getenv("TRANSCRIBE_SKILL_MAX_FILE_SIZE_MB", "25")),
            output_suffix=os.getenv("TRANSCRIBE_SKILL_OUTPUT_SUFFIX", ".md"),
            output_encoding=os.getenv("TRANSCRIBE_SKILL_OUTPUT_ENCODING", "utf-8"),
            voice_memo_directory=os.getenv("VOICE_MEMO_DIR") or os.getenv("TRANSCRIBE_SKILL_VOICE_MEMO_DIR"),
            default_sync_days=int(os.getenv("TRANSCRIBE_SKILL_DEFAULT_SYNC_DAYS", "30")),
            database_path=os.getenv("TRANSCRIBE_SKILL_DATABASE_PATH", "./audio_metadata.db"),
            database_timeout=float(os.getenv("TRANSCRIBE_SKILL_DATABASE_TIMEOUT", "10.0")),
            default_output_dir=os.getenv("RAW_TRANSCRIPT_DIR") or os.getenv("TRANSCRIBE_SKILL_OUTPUT_DIR"),
            safeguards=SafeguardSettings(
                default_max_files=int(os.getenv("TRANSCRIBE_SKILL_MAX_FILES", "5")),
                confirmation_threshold=int(os.getenv("TRANSCRIBE_SKILL_CONFIRM_THRESHOLD", "3")),
                skip_completed_by_default=os.getenv("TRANSCRIBE_SKILL_SKIP_COMPLETED", "true").lower() == "true",
            ),
            retry=RetrySettings(
                max_attempts=int(os.getenv("TRANSCRIBE_SKILL_RETRY_MAX_ATTEMPTS", "3")),
                backoff_seconds=float(os.getenv("TRANSCRIBE_SKILL_RETRY_BACKOFF_SECONDS", "2.0")),
            ),
        )

    @classmethod
    def load(cls, yaml_path: Optional[str] = None) -> "SkillConfig":
        """Load configuration using YAML (if present) and environment overrides."""

        cls._load_env()

        config: Optional[SkillConfig] = None
        if yaml_path:
            try:
                config = cls.from_yaml(yaml_path)
            except Exception:
                config = None
        else:
            default_yaml = Path(__file__).with_name("config.yaml")
            if default_yaml.exists():
                try:
                    config = cls.from_yaml(str(default_yaml))
                except Exception:
                    config = None

        if config is None:
            config = cls.from_env()
        else:
            # Only override values where env vars are explicitly set
            env_values = {
                "api_key": os.getenv("OTHER_API_KEY") or os.getenv("OPENAI_API_KEY"),
                "base_url": os.getenv("OPENAI_BASE_URL"),
                "transcription_mode": os.getenv("TRANSCRIBE_SKILL_MODE"),
                "model": os.getenv("TRANSCRIBE_SKILL_MODEL"),
                "language": os.getenv("TRANSCRIBE_SKILL_LANGUAGE"),
                "temperature": os.getenv("TRANSCRIBE_SKILL_TEMPERATURE"),
                "response_format": os.getenv("TRANSCRIBE_SKILL_RESPONSE_FORMAT"),
                "prompt": os.getenv("TRANSCRIBE_SKILL_PROMPT"),
                "supported_formats": os.getenv("TRANSCRIBE_SKILL_SUPPORTED_FORMATS"),
                "max_file_size_mb": os.getenv("TRANSCRIBE_SKILL_MAX_FILE_SIZE_MB"),
                "output_suffix": os.getenv("TRANSCRIBE_SKILL_OUTPUT_SUFFIX"),
                "output_encoding": os.getenv("TRANSCRIBE_SKILL_OUTPUT_ENCODING"),
                "default_output_dir": os.getenv("RAW_TRANSCRIPT_DIR") or os.getenv("TRANSCRIBE_SKILL_OUTPUT_DIR"),
                "voice_memo_directory": os.getenv("VOICE_MEMO_DIR") or os.getenv("TRANSCRIBE_SKILL_VOICE_MEMO_DIR"),
                "default_sync_days": os.getenv("TRANSCRIBE_SKILL_DEFAULT_SYNC_DAYS"),
                "database_path": os.getenv("TRANSCRIBE_SKILL_DATABASE_PATH"),
                "database_timeout": os.getenv("TRANSCRIBE_SKILL_DATABASE_TIMEOUT"),
                "safeguards_default_max_files": os.getenv("TRANSCRIBE_SKILL_MAX_FILES"),
                "safeguards_confirmation_threshold": os.getenv("TRANSCRIBE_SKILL_CONFIRM_THRESHOLD"),
                "safeguards_skip_completed": os.getenv("TRANSCRIBE_SKILL_SKIP_COMPLETED"),
                "retry_max_attempts": os.getenv("TRANSCRIBE_SKILL_RETRY_MAX_ATTEMPTS"),
                "retry_backoff_seconds": os.getenv("TRANSCRIBE_SKILL_RETRY_BACKOFF_SECONDS"),
            }

            if env_values["api_key"]:
                config.api_key = env_values["api_key"]
            if env_values["base_url"]:
                config.base_url = env_values["base_url"]
            if env_values["transcription_mode"]:
                config.transcription_mode = env_values["transcription_mode"]
            if env_values["model"]:
                config.model = env_values["model"]
            if env_values["language"]:
                config.language = env_values["language"]
            if env_values["temperature"]:
                config.temperature = float(env_values["temperature"])
            if env_values["response_format"]:
                config.response_format = env_values["response_format"]
            if env_values["prompt"]:
                config.prompt = env_values["prompt"]
            if env_values["supported_formats"]:
                config.supported_formats = [
                    fmt.strip()
                    for fmt in env_values["supported_formats"].split(",")
                    if fmt.strip()
                ]
            if env_values["max_file_size_mb"]:
                config.max_file_size_mb = int(env_values["max_file_size_mb"])
            if env_values["output_suffix"]:
                config.output_suffix = env_values["output_suffix"]
            if env_values["output_encoding"]:
                config.output_encoding = env_values["output_encoding"]
            if env_values["default_output_dir"]:
                config.default_output_dir = env_values["default_output_dir"]
            if env_values["voice_memo_directory"]:
                config.voice_memo_directory = env_values["voice_memo_directory"]
            if env_values["default_sync_days"]:
                config.default_sync_days = int(env_values["default_sync_days"])
            if env_values["database_path"]:
                config.database_path = env_values["database_path"]
            if env_values["database_timeout"]:
                config.database_timeout = float(env_values["database_timeout"])

            safeguards = config.safeguards
            if env_values["safeguards_default_max_files"]:
                safeguards.default_max_files = int(env_values["safeguards_default_max_files"])
            if env_values["safeguards_confirmation_threshold"]:
                safeguards.confirmation_threshold = int(env_values["safeguards_confirmation_threshold"])
            if env_values["safeguards_skip_completed"]:
                safeguards.skip_completed_by_default = (
                    env_values["safeguards_skip_completed"].lower() == "true"
                )

            config.safeguards = safeguards

            retry = config.retry
            if env_values["retry_max_attempts"]:
                retry.max_attempts = int(env_values["retry_max_attempts"])
            if env_values["retry_backoff_seconds"]:
                retry.backoff_seconds = float(env_values["retry_backoff_seconds"])

            config.retry = retry

        return config

    def validate(self) -> None:
        """Validate essential configuration values."""

        if not self.api_key:
            raise ValueError(
                "OpenAI API key is required. Set OTHER_API_KEY or OPENAI_API_KEY, "
                "or provide --api-key via CLI."
            )
        if not (0.0 <= self.temperature <= 1.0):
            raise ValueError("Temperature must be between 0.0 and 1.0")
        if not self.supported_formats:
            raise ValueError("At least one supported audio format must be configured")
        if self.retry.max_attempts < 1:
            raise ValueError("retry.max_attempts must be at least 1")
        if self.retry.backoff_seconds < 0:
            raise ValueError("retry.backoff_seconds must not be negative")

    def resolve_database_path(self, base_dir: Optional[Path] = None) -> Path:
        """Resolve the configured database path relative to a base directory."""

        db_path = Path(self.database_path)
        if db_path.is_absolute():
            return db_path
        base = base_dir or Path(__file__).resolve().parent
        return (base / db_path).resolve()

    def is_supported_format(self, filename: str) -> bool:
        """Return True if the filename matches one of the supported extensions."""

        filename_lower = filename.lower()
        return any(filename_lower.endswith(fmt.lower()) for fmt in self.supported_formats)

    @property
    def max_file_size_bytes(self) -> int:
        """Return the maximum file size in bytes."""

        return int(self.max_file_size_mb * 1024 * 1024)
