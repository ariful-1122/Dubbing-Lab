"""Facade for Gemini speech translation (dependency injection entry point)."""

from __future__ import annotations

from pathlib import Path

from app.config import Settings, get_settings
from app.models import TranslationResult
from app.providers.base import SpeechTranslationProvider
from app.providers.gemini_live import GeminiLiveProvider


class GeminiService:
    """
    Thin facade over the Gemini Live translation provider.

    Allows swapping the underlying provider implementation without changing
    the dubbing pipeline.
    """

    def __init__(
        self,
        provider: SpeechTranslationProvider | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._provider = provider or GeminiLiveProvider(self.settings)

    async def translate_audio(
        self,
        input_pcm_path: Path,
        target_language: str,
        *,
        job_id: str,
        output_dir: Path,
    ) -> TranslationResult:
        """Delegate translation to the configured provider."""
        return await self._provider.translate_audio(
            input_pcm_path,
            target_language,
            job_id=job_id,
            output_dir=output_dir,
        )

    async def health_check(self) -> bool:
        """Verify provider is ready."""
        return await self._provider.health_check()
