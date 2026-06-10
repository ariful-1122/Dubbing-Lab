"""Data models for dubbing jobs and translation results."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4


class JobStatus(str, Enum):
    """Lifecycle status of a dubbing job."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class TranslationResult:
    """Output from a speech translation provider."""

    output_pcm_path: Path
    input_duration_seconds: float
    output_duration_seconds: float
    segments_processed: int = 1
    api_call_count: int = 1
    input_transcript: str | None = None
    output_transcript: str | None = None


@dataclass
class DubbingJobResult:
    """Final result of a complete dubbing pipeline run."""

    job_id: str
    status: JobStatus
    source_file: Path
    target_language: str
    output_file: Path | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None
    duration_seconds: float | None = None
    original_audio_duration: float | None = None
    translated_audio_duration: float | None = None
    duration_delta_seconds: float | None = None
    tempo_adjusted: bool = False
    error_message: str | None = None
    api_call_count: int = 0
    segments_processed: int = 0

    def to_log_dict(self) -> dict[str, Any]:
        """Serialize job result for structured logging."""
        return {
            "job_id": self.job_id,
            "status": self.status.value,
            "source_file": str(self.source_file),
            "target_language": self.target_language,
            "output_file": str(self.output_file) if self.output_file else None,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_seconds": self.duration_seconds,
            "original_audio_duration": self.original_audio_duration,
            "translated_audio_duration": self.translated_audio_duration,
            "duration_delta_seconds": self.duration_delta_seconds,
            "tempo_adjusted": self.tempo_adjusted,
            "error_message": self.error_message,
            "api_call_count": self.api_call_count,
            "segments_processed": self.segments_processed,
        }


def new_job_id() -> str:
    """Generate a short unique job identifier."""
    return uuid4().hex[:12]
