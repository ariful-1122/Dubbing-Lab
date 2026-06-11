"""Orchestrates the full video dubbing pipeline."""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from app.config import Settings, get_settings
from app.ffmpeg_service import FFmpegError, FFmpegService
from app.gemini_service import GeminiService
from app.logger import get_logger, log_event
from app.models import DubbingJobResult, JobStatus, new_job_id
from app.providers.gemini_live import GeminiTranslationError

logger = get_logger("dubbing")


class DubbingService:
    """End-to-end dubbing: extract → translate → sync → mux."""

    def __init__(
        self,
        settings: Settings | None = None,
        ffmpeg_service: FFmpegService | None = None,
        gemini_service: GeminiService | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.ffmpeg = ffmpeg_service or FFmpegService(self.settings)
        self.gemini = gemini_service or GeminiService(settings=self.settings)

    async def process_job(
        self,
        video_path: Path,
        target_language: str | None = None,
    ) -> DubbingJobResult:
        """Run the complete dubbing workflow for a single video file."""
        job_id = new_job_id()
        language = (target_language or self.settings.target_language).lower()
        source = video_path.resolve()
        started = time.monotonic()
        work_dir = self.settings.processing_path / job_id
        work_dir.mkdir(parents=True, exist_ok=True)

        result = DubbingJobResult(
            job_id=job_id,
            status=JobStatus.PROCESSING,
            source_file=source,
            target_language=language,
        )

        log_event(
            logger,
            logging.INFO,
            "job_started",
            f"Starting dubbing job for {source.name}",
            job_id=job_id,
            source_file=str(source),
            target_language=language,
        )

        processing_video = work_dir / source.name

        try:
            # Step 1: Move video to processing folder
            if source.parent != work_dir:
                if source.parent == self.settings.input_path:
                    shutil.move(str(source), str(processing_video))
                else:
                    shutil.copy2(source, processing_video)
            else:
                processing_video = source

            use_voice_cloning = bool(self.settings.elevenlabs_api_key.strip())

            if use_voice_cloning:
                # Upgraded Demucs + Whisper + ElevenLabs voice-cloning pipeline
                
                # A1. Extract full audio from video as WAV
                original_wav = work_dir / "original.wav"
                await asyncio.to_thread(
                    self.ffmpeg.extract_audio_as_wav,
                    processing_video,
                    original_wav,
                )
                
                original_duration = await asyncio.to_thread(
                    self.ffmpeg.get_duration,
                    original_wav,
                )
                result.original_audio_duration = original_duration
                
                # A2. Run Demucs locally on original_wav to separate vocals and background
                logger.info("Running Demucs stem separation on %s", original_wav.name)
                import sys
                import subprocess
                
                # Demucs CLI command
                demucs_cmd = [
                    sys.executable, "-m", "demucs.separate",
                    "-n", self.settings.demucs_model,
                    "--two-stems", "vocals",
                    "-o", str(work_dir),
                    str(original_wav)
                ]
                
                # Run demucs in thread pool since it's CPU-heavy
                def run_demucs():
                    subprocess.run(demucs_cmd, check=True)
                
                await asyncio.to_thread(run_demucs)
                
                # Locate separated stems
                demucs_out_dir = work_dir / self.settings.demucs_model / original_wav.stem
                vocals_wav = demucs_out_dir / "vocals.wav"
                no_vocals_wav = demucs_out_dir / "no_vocals.wav"
                
                if not vocals_wav.exists() or not no_vocals_wav.exists():
                    raise FileNotFoundError(
                        f"Demucs failed to produce vocals or background stem. Expected at {demucs_out_dir}"
                    )
                
                # A3. Convert vocals WAV to PCM (16kHz 16-bit mono) as expected by providers
                extracted_pcm = work_dir / "vocals.pcm"
                await asyncio.to_thread(
                    self.ffmpeg.convert_audio_format,
                    vocals_wav,
                    extracted_pcm,
                    "pcm",
                )
                
                # A4. Translate and synthesize dubbed vocals via ElevenLabs
                translation = await self.gemini.translate_audio(
                    extracted_pcm,
                    language,
                    job_id=job_id,
                    output_dir=work_dir,
                )
                result.api_call_count = translation.api_call_count
                result.segments_processed = translation.segments_processed
                result.translated_audio_duration = translation.output_duration_seconds
                
                # A5. Convert returned dubbed PCM vocals to WAV
                translated_pcm = translation.output_pcm_path
                translated_wav = work_dir / "translated.wav"
                await asyncio.to_thread(
                    self.ffmpeg.pcm_to_wav,
                    translated_pcm,
                    translated_wav,
                    self.settings.output_sample_rate,
                )
                
                # A6. Compare durations and log delta
                duration_delta = original_duration - translation.output_duration_seconds
                result.duration_delta_seconds = duration_delta
                
                log_event(
                    logger,
                    logging.INFO,
                    "duration_comparison",
                    f"Duration delta: {duration_delta:.3f}s",
                    job_id=job_id,
                    original_duration_sec=original_duration,
                    translated_duration_sec=translation.output_duration_seconds,
                    delta_sec=duration_delta,
                )
                
                audio_for_mux = translated_wav
                
                # A7. Tempo correction if drift exceeds threshold
                if abs(duration_delta) > self.settings.sync_threshold_seconds:
                    tempo_ratio = translation.output_duration_seconds / original_duration
                    adjusted_wav = work_dir / "translated_adjusted.wav"
                    await asyncio.to_thread(
                        self.ffmpeg.adjust_audio_tempo,
                        translated_wav,
                        tempo_ratio,
                        adjusted_wav,
                    )
                    audio_for_mux = adjusted_wav
                    result.tempo_adjusted = True
                    result.translated_audio_duration = self.ffmpeg.get_duration(adjusted_wav)
                    
                    log_event(
                        logger,
                        logging.INFO,
                        "duration_mismatch",
                        f"Applied tempo correction ratio={tempo_ratio:.4f}",
                        job_id=job_id,
                        tempo_ratio=tempo_ratio,
                        threshold_sec=self.settings.sync_threshold_seconds,
                    )
                
                # A8. Normalize the dubbed vocals
                normalized_wav = work_dir / "translated_normalized.wav"
                await asyncio.to_thread(
                    self.ffmpeg.normalize_audio,
                    audio_for_mux,
                    normalized_wav,
                )
                
                # A9. Mix normalized dubbed vocals with background music stem (no_vocals.wav)
                mixed_wav = work_dir / "final_mixed.wav"
                await asyncio.to_thread(
                    self.ffmpeg.mix_audio,
                    normalized_wav,
                    no_vocals_wav,
                    mixed_wav,
                    self.settings.background_volume,
                )
                
                # A10. Convert mixed audio to AAC for final video muxing
                final_audio_aac = work_dir / "final_mixed.aac"
                await asyncio.to_thread(
                    self.ffmpeg.convert_audio_format,
                    mixed_wav,
                    final_audio_aac,
                    "aac",
                )
                
                # A11. Mux the final mixed audio track back into the video
                output_name = f"{processing_video.stem}_dubbed_{language}.mp4"
                output_path = self.settings.output_path / output_name
                await asyncio.to_thread(
                    self.ffmpeg.replace_audio,
                    processing_video,
                    final_audio_aac,
                    output_path,
                )
                
            else:
                # Original pipeline (replacing original audio completely)
                
                # Step 2: Extract audio as 16 kHz PCM
                extracted_pcm = work_dir / "original.pcm"
                await asyncio.to_thread(
                    self.ffmpeg.extract_audio,
                    processing_video,
                    extracted_pcm,
                )
                original_duration = self.ffmpeg.get_pcm_duration(
                    extracted_pcm,
                    self.settings.input_sample_rate,
                )
                result.original_audio_duration = original_duration

                # Steps 3–5: Translate via Gemini Live API
                translation = await self.gemini.translate_audio(
                    extracted_pcm,
                    language,
                    job_id=job_id,
                    output_dir=work_dir,
                )
                result.api_call_count = translation.api_call_count
                result.segments_processed = translation.segments_processed
                result.translated_audio_duration = translation.output_duration_seconds

                translated_pcm = translation.output_pcm_path
                translated_wav = work_dir / "translated.wav"
                await asyncio.to_thread(
                    self.ffmpeg.pcm_to_wav,
                    translated_pcm,
                    translated_wav,
                    self.settings.output_sample_rate,
                )

                # Step 7–8: Compare durations and log delta
                duration_delta = original_duration - translation.output_duration_seconds
                result.duration_delta_seconds = duration_delta

                log_event(
                    logger,
                    logging.INFO,
                    "duration_comparison",
                    f"Duration delta: {duration_delta:.3f}s",
                    job_id=job_id,
                    original_duration_sec=original_duration,
                    translated_duration_sec=translation.output_duration_seconds,
                    delta_sec=duration_delta,
                )

                audio_for_mux = translated_wav

                # Step 9: Tempo correction if drift exceeds threshold
                if abs(duration_delta) > self.settings.sync_threshold_seconds:
                    tempo_ratio = translation.output_duration_seconds / original_duration
                    adjusted_wav = work_dir / "translated_adjusted.wav"
                    await asyncio.to_thread(
                        self.ffmpeg.adjust_audio_tempo,
                        translated_wav,
                        tempo_ratio,
                        adjusted_wav,
                    )
                    audio_for_mux = adjusted_wav
                    result.tempo_adjusted = True
                    result.translated_audio_duration = self.ffmpeg.get_duration(adjusted_wav)

                    log_event(
                        logger,
                        logging.INFO,
                        "duration_mismatch",
                        f"Applied tempo correction ratio={tempo_ratio:.4f}",
                        job_id=job_id,
                        tempo_ratio=tempo_ratio,
                        threshold_sec=self.settings.sync_threshold_seconds,
                    )

                # Step 10: Normalize and convert to AAC for muxing
                normalized_aac = work_dir / "translated_normalized.aac"
                normalized_wav = work_dir / "translated_normalized.wav"
                await asyncio.to_thread(
                    self.ffmpeg.normalize_audio,
                    audio_for_mux,
                    normalized_wav,
                )
                await asyncio.to_thread(
                    self.ffmpeg.convert_audio_format,
                    normalized_wav,
                    normalized_aac,
                    "aac",
                )

                # Step 11: Mux translated audio into video
                output_name = f"{processing_video.stem}_dubbed_{language}.mp4"
                output_path = self.settings.output_path / output_name
                await asyncio.to_thread(
                    self.ffmpeg.replace_audio,
                    processing_video,
                    normalized_aac,
                    output_path,
                )


            result.output_file = output_path
            result.status = JobStatus.COMPLETED
            result.completed_at = datetime.now(timezone.utc)
            result.duration_seconds = time.monotonic() - started

            # Cleanup processing artifacts on success
            shutil.rmtree(work_dir, ignore_errors=True)

            log_event(
                logger,
                logging.INFO,
                "job_completed",
                f"Job completed: {output_name}",
                job_id=job_id,
                source_file=str(source),
                target_language=language,
                output_file=str(output_path),
                duration_seconds=result.duration_seconds,
                api_call_count=result.api_call_count,
            )

        except (FFmpegError, GeminiTranslationError, OSError, ValueError, RuntimeError) as exc:
            result.status = JobStatus.FAILED
            result.error_message = str(exc)
            result.completed_at = datetime.now(timezone.utc)
            result.duration_seconds = time.monotonic() - started

            log_event(
                logger,
                logging.ERROR,
                "job_failed",
                f"Job failed: {exc}",
                job_id=job_id,
                source_file=str(source),
                target_language=language,
                error=str(exc),
            )

            await self._move_to_failed(source, processing_video, work_dir, job_id)

        return result

    async def _move_to_failed(
        self,
        source: Path,
        processing_video: Path,
        work_dir: Path,
        job_id: str,
    ) -> None:
        """Move failed job artifacts to the failed directory."""
        failed_dir = self.settings.failed_path / job_id
        failed_dir.mkdir(parents=True, exist_ok=True)

        for path in (source, processing_video):
            if path.exists() and path.parent != failed_dir:
                try:
                    shutil.move(str(path), str(failed_dir / path.name))
                except OSError:
                    pass

        if work_dir.exists():
            for item in work_dir.iterdir():
                try:
                    shutil.move(str(item), str(failed_dir / item.name))
                except OSError:
                    pass
            shutil.rmtree(work_dir, ignore_errors=True)
