"""Application configuration via Pydantic Settings."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# BCP-47 codes supported by this application.
SUPPORTED_LANGUAGES: frozenset[str] = frozenset({"bn", "hi", "ur", "es", "ar"})

# Project root (parent of app/).
PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Central configuration loaded from environment variables and .env file."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # API credentials (validated at startup)
    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")
    elevenlabs_api_key: str = Field(default="", alias="ELEVENLABS_API_KEY")
    elevenlabs_voice_id: str = Field(default="", alias="ELEVENLABS_VOICE_ID")
    elevenlabs_model_id: str = Field(default="eleven_v3", alias="ELEVENLABS_MODEL_ID")


    # Language and directories
    target_language: str = Field(default="bn", alias="TARGET_LANGUAGE")
    input_dir: Path = Field(default=Path("input"), alias="INPUT_DIR")
    output_dir: Path = Field(default=Path("output"), alias="OUTPUT_DIR")
    processing_dir: Path = Field(default=Path("processing"), alias="PROCESSING_DIR")
    failed_dir: Path = Field(default=Path("failed"), alias="FAILED_DIR")

    # FFmpeg (optional — use when ffmpeg is not visible on PATH, common on Windows)
    ffmpeg_bin_dir: Path | None = Field(default=None, alias="FFMPEG_BIN_DIR")

    # Retry and sync
    max_retries: int = Field(default=3, alias="MAX_RETRIES")
    retry_base_delay_seconds: float = Field(default=2.0, alias="RETRY_BASE_DELAY_SECONDS")
    sync_threshold_seconds: float = Field(default=0.5, alias="SYNC_THRESHOLD_SECONDS")

    # Video formats
    supported_formats: str = Field(
        default="mp4,mkv,mov,avi,webm",
        alias="SUPPORTED_FORMATS",
    )

    # Gemini Live API
    gemini_model: str = Field(
        default="gemini-3.5-live-translate-preview",
        alias="GEMINI_MODEL",
    )
    input_sample_rate: int = Field(default=16000, alias="INPUT_SAMPLE_RATE")
    output_sample_rate: int = Field(default=24000, alias="OUTPUT_SAMPLE_RATE")
    chunk_duration_ms: int = Field(default=100, alias="CHUNK_DURATION_MS")
    max_segment_seconds: int = Field(default=600, alias="MAX_SEGMENT_SECONDS")
    echo_target_language: bool = Field(default=False, alias="ECHO_TARGET_LANGUAGE")
    receive_timeout_seconds: float = Field(default=120.0, alias="RECEIVE_TIMEOUT_SECONDS")
    # Live API expects ~100ms chunks; pacing avoids "resource exhausted" on long audio
    stream_realtime_pace: bool = Field(default=True, alias="STREAM_REALTIME_PACE")
    send_pace_seconds: float = Field(default=0.005, alias="SEND_PACE_SECONDS")
    parallel_segments: bool = Field(default=False, alias="PARALLEL_SEGMENTS")
    receive_timeout_multiplier: float = Field(
        default=3.0,
        alias="RECEIVE_TIMEOUT_MULTIPLIER",
    )
    # Stop receiving when no new audio for this many seconds after input ends
    receive_idle_seconds: float = Field(default=5.0, alias="RECEIVE_IDLE_SECONDS")
    silence_threshold_seconds: float = Field(default=3.0, alias="SILENCE_THRESHOLD_SECONDS")
    silence_amplitude_threshold: int = Field(default=500, alias="SILENCE_AMPLITUDE_THRESHOLD")

    # Local Models & Mixing (Whisper, Demucs, ElevenLabs)
    whisper_model: str = Field(default="base", alias="WHISPER_MODEL")
    demucs_model: str = Field(default="htdemucs", alias="DEMUCS_MODEL")
    background_volume: float = Field(default=0.5, alias="BACKGROUND_VOLUME")

    # Watcher
    file_stability_seconds: float = Field(default=3.0, alias="FILE_STABILITY_SECONDS")
    file_stability_poll_seconds: float = Field(default=0.5, alias="FILE_STABILITY_POLL_SECONDS")

    # Logging
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        alias="LOG_LEVEL",
    )
    log_dir: Path = Field(default=Path("logs"), alias="LOG_DIR")
    log_file: str = Field(default="app.log", alias="LOG_FILE")
    log_max_bytes: int = Field(default=10_485_760, alias="LOG_MAX_BYTES")  # 10 MB
    log_backup_count: int = Field(default=5, alias="LOG_BACKUP_COUNT")

    @field_validator("target_language")
    @classmethod
    def validate_target_language(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in SUPPORTED_LANGUAGES:
            supported = ", ".join(sorted(SUPPORTED_LANGUAGES))
            raise ValueError(
                f"Unsupported TARGET_LANGUAGE '{value}'. Supported: {supported}"
            )
        return normalized

    @property
    def supported_format_set(self) -> frozenset[str]:
        """Return supported video extensions as a lowercase set."""
        return frozenset(ext.strip().lower() for ext in self.supported_formats.split(","))

    @property
    def input_chunk_bytes(self) -> int:
        """PCM chunk size in bytes for 100 ms at 16 kHz mono 16-bit."""
        samples_per_chunk = int(self.input_sample_rate * self.chunk_duration_ms / 1000)
        return samples_per_chunk * 2  # 16-bit = 2 bytes per sample

    def resolve_path(self, path: Path) -> Path:
        """Resolve a path relative to project root if not absolute."""
        if path.is_absolute():
            return path
        return (PROJECT_ROOT / path).resolve()

    @property
    def input_path(self) -> Path:
        return self.resolve_path(self.input_dir)

    @property
    def output_path(self) -> Path:
        return self.resolve_path(self.output_dir)

    @property
    def processing_path(self) -> Path:
        return self.resolve_path(self.processing_dir)

    @property
    def failed_path(self) -> Path:
        return self.resolve_path(self.failed_dir)

    @property
    def log_path(self) -> Path:
        return self.resolve_path(self.log_dir)

    @property
    def ffmpeg_bin_path(self) -> Path | None:
        """Resolved FFmpeg bin directory, if configured."""
        if self.ffmpeg_bin_dir is None:
            return None
        path = self.resolve_path(self.ffmpeg_bin_dir)
        return path if path.is_dir() else None

    def apply_ffmpeg_path(self) -> None:
        """
        Prepend FFMPEG_BIN_DIR to PATH so ffmpeg/ffprobe are discoverable.

        Cursor and Git Bash on Windows often do not inherit updated system PATH
        until the IDE is fully restarted.
        """
        bin_path = self.ffmpeg_bin_path
        if bin_path is None:
            return
        bin_str = str(bin_path)
        current = os.environ.get("PATH", "")
        if bin_str not in current.split(os.pathsep):
            os.environ["PATH"] = bin_str + os.pathsep + current

    def ensure_directories(self) -> None:
        """Create all required working directories."""
        for directory in (
            self.input_path,
            self.output_path,
            self.processing_path,
            self.failed_path,
            self.log_path,
        ):
            directory.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings singleton."""
    settings = Settings()
    settings.apply_ffmpeg_path()
    settings.ensure_directories()
    return settings
