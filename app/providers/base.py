"""Abstract base class for speech translation providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from app.models import TranslationResult


class SpeechTranslationProvider(ABC):
    """
    Interface for speech-to-speech translation backends.

    Implementations may use streaming (Gemini Live), batch upload, or other
    transports. The dubbing pipeline depends only on this contract.
    """

    @abstractmethod
    async def translate_audio(
        self,
        input_pcm_path: Path,
        target_language: str,
        *,
        job_id: str,
        output_dir: Path,
    ) -> TranslationResult:
        """
        Translate spoken audio in input_pcm_path to the target language.

        Returns a TranslationResult with the path to translated PCM audio.
        """

    @abstractmethod
    async def health_check(self) -> bool:
        """Verify provider connectivity and credentials."""
