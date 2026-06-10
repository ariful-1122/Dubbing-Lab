"""Gemini Live API speech-to-speech translation provider."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types

from app.config import Settings, get_settings
from app.ffmpeg_service import FFmpegService
from app.logger import get_logger, log_event
from app.models import TranslationResult
from app.providers.base import SpeechTranslationProvider

logger = get_logger("gemini_live")

# Substrings that indicate non-retryable API failures (quota, auth, etc.)
_NON_RETRYABLE_ERRORS = (
    "invalid api key",
    "permission denied",
    "unauthenticated",
)


class GeminiTranslationError(Exception):
    """Raised when Gemini Live translation fails."""


def _is_non_retryable(error: Exception) -> bool:
    message = str(error).lower()
    return any(marker in message for marker in _NON_RETRYABLE_ERRORS)


class GeminiLiveProvider(SpeechTranslationProvider):
    """
    Translate audio via Gemini Live API (gemini-3.5-live-translate-preview).

    Streams raw 16 kHz PCM input in 100 ms chunks and collects 24 kHz PCM output.
    Long audio is split into segments to stay within the ~15 minute session limit.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        ffmpeg_service: FFmpegService | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.ffmpeg = ffmpeg_service or FFmpegService(self.settings)
        self._client = genai.Client(api_key=self.settings.gemini_api_key)

    async def health_check(self) -> bool:
        """Verify API key is configured (lightweight check)."""
        return bool(self.settings.gemini_api_key.strip())

    async def translate_audio(
        self,
        input_pcm_path: Path,
        target_language: str,
        *,
        job_id: str,
        output_dir: Path,
    ) -> TranslationResult:
        """Translate PCM audio, splitting into segments if needed."""
        output_dir.mkdir(parents=True, exist_ok=True)

        input_duration = self.ffmpeg.get_pcm_duration(
            input_pcm_path,
            self.settings.input_sample_rate,
        )
        segments = self._plan_segments(input_duration)
        segment_outputs: list[Path] = []
        input_transcripts: list[str] = []
        output_transcripts: list[str] = []
        api_calls = 0

        log_event(
            logger,
            logging.INFO,
            "translation_started",
            f"Translating {input_duration:.1f}s audio in {len(segments)} segment(s) (parallel={self.settings.parallel_segments})",
            job_id=job_id,
            input_duration_sec=input_duration,
            segment_count=len(segments),
            target_language=target_language,
        )

        async def process_segment(index: int, start_sec: float, duration_sec: float) -> dict[str, Any]:
            if len(segments) == 1:
                segment_pcm = input_pcm_path
            else:
                segment_pcm = output_dir / f"{job_id}_segment_{index:03d}_in.pcm"
                await asyncio.to_thread(
                    self.ffmpeg.split_audio_segment,
                    input_pcm_path,
                    start_sec,
                    duration_sec,
                    segment_pcm,
                )

            segment_out = output_dir / f"{job_id}_segment_{index:03d}_out.pcm"
            result = await self._translate_segment_with_retry(
                segment_pcm=segment_pcm,
                output_pcm_path=segment_out,
                target_language=target_language,
                job_id=job_id,
                segment_index=index,
                segment_duration_sec=duration_sec,
            )
            
            log_event(
                logger,
                logging.INFO,
                "segment_translated",
                f"Segment {index + 1}/{len(segments)} translated",
                job_id=job_id,
                segment_index=index,
                segment_duration_sec=duration_sec,
            )
            return result

        if self.settings.parallel_segments:
            tasks = [
                process_segment(index, start_sec, duration_sec)
                for index, (start_sec, duration_sec) in enumerate(segments)
            ]
            results = await asyncio.gather(*tasks)
        else:
            results = []
            for index, (start_sec, duration_sec) in enumerate(segments):
                res = await process_segment(index, start_sec, duration_sec)
                results.append(res)

        for result in results:
            segment_outputs.append(result["output_path"])
            if result.get("input_transcript"):
                input_transcripts.append(result["input_transcript"])
            if result.get("output_transcript"):
                output_transcripts.append(result["output_transcript"])
            api_calls += 1

        if len(segment_outputs) == 1:
            final_output = segment_outputs[0]
        else:
            final_output = output_dir / f"{job_id}_translated.pcm"
            await asyncio.to_thread(
                self.ffmpeg.concat_pcm_files,
                segment_outputs,
                final_output,
                self.settings.output_sample_rate,
            )

        output_duration = self.ffmpeg.get_pcm_duration(
            final_output,
            self.settings.output_sample_rate,
        )

        return TranslationResult(
            output_pcm_path=final_output,
            input_duration_seconds=input_duration,
            output_duration_seconds=output_duration,
            segments_processed=len(segments),
            api_call_count=api_calls,
            input_transcript=" ".join(input_transcripts) or None,
            output_transcript=" ".join(output_transcripts) or None,
        )

    def _plan_segments(self, total_duration: float) -> list[tuple[float, float]]:
        """Split audio into segments that fit within MAX_SEGMENT_SECONDS."""
        max_seg = self.settings.max_segment_seconds
        if total_duration <= max_seg:
            return [(0.0, total_duration)]

        segments: list[tuple[float, float]] = []
        start = 0.0
        while start < total_duration:
            duration = min(max_seg, total_duration - start)
            segments.append((start, duration))
            start += duration
        return segments

    def _receive_timeout_for_segment(self, segment_duration_sec: float) -> float:
        """Scale receive timeout with segment length (paced send takes ~real time)."""
        paced_send_time = segment_duration_sec
        dynamic = paced_send_time * self.settings.receive_timeout_multiplier + 60.0
        return max(self.settings.receive_timeout_seconds, dynamic)

    async def _translate_segment_with_retry(
        self,
        segment_pcm: Path,
        output_pcm_path: Path,
        target_language: str,
        job_id: str,
        segment_index: int,
        segment_duration_sec: float,
    ) -> dict[str, Any]:
        """Translate a single segment with exponential backoff retries."""
        last_error: Exception | None = None

        for attempt in range(self.settings.max_retries + 1):
            try:
                return await self._translate_segment(
                    segment_pcm=segment_pcm,
                    output_pcm_path=output_pcm_path,
                    target_language=target_language,
                    job_id=job_id,
                    segment_index=segment_index,
                    segment_duration_sec=segment_duration_sec,
                )
            except (GeminiTranslationError, ConnectionError, TimeoutError, OSError) as exc:
                last_error = exc
                if _is_non_retryable(exc):
                    log_event(
                        logger,
                        logging.ERROR,
                        "api_quota_error",
                        f"Non-retryable API error: {exc}",
                        job_id=job_id,
                        segment_index=segment_index,
                        error=str(exc),
                    )
                    break
                if attempt >= self.settings.max_retries:
                    break
                delay = self.settings.retry_base_delay_seconds * (2**attempt)
                log_event(
                    logger,
                    logging.WARNING,
                    "api_retry",
                    f"Retrying segment {segment_index} (attempt {attempt + 1}): {exc}",
                    job_id=job_id,
                    segment_index=segment_index,
                    attempt=attempt + 1,
                    delay_seconds=delay,
                    error=str(exc),
                )
                await asyncio.sleep(delay)

        raise GeminiTranslationError(
            f"Translation failed after {self.settings.max_retries + 1} attempts: {last_error}"
        ) from last_error

    async def _translate_segment(
        self,
        segment_pcm: Path,
        output_pcm_path: Path,
        target_language: str,
        job_id: str,
        segment_index: int,
        segment_duration_sec: float,
    ) -> dict[str, Any]:
        """Open a Live API session, stream PCM with pacing, and collect output."""
        config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            translation_config=types.TranslationConfig(
                target_language_code=target_language,
                echo_target_language=self.settings.echo_target_language,
            ),
        )

        audio_chunks: list[bytes] = []
        input_transcript_parts: list[str] = []
        output_transcript_parts: list[str] = []
        receive_done = asyncio.Event()
        receive_error: Exception | None = None
        stream_ended = False
        last_audio_at = 0.0
        consecutive_silence_seconds = 0.0
        pace_seconds = (
            self.settings.chunk_duration_ms / 1000.0
            if self.settings.stream_realtime_pace
            else self.settings.send_pace_seconds
        )
        receive_timeout = self._receive_timeout_for_segment(segment_duration_sec)
        idle_seconds = self.settings.receive_idle_seconds

        async def receive_loop(session: Any) -> None:
            nonlocal receive_error, stream_ended, last_audio_at, consecutive_silence_seconds
            try:
                async for response in session.receive():
                    if not response.server_content:
                        continue

                    content = response.server_content

                    if content.input_transcription and content.input_transcription.text:
                        input_transcript_parts.append(content.input_transcription.text)

                    if content.output_transcription and content.output_transcription.text:
                        output_transcript_parts.append(content.output_transcription.text)

                    if content.model_turn and content.model_turn.parts:
                        for part in content.model_turn.parts:
                            if part.inline_data and part.inline_data.data:
                                data = part.inline_data.data
                                audio_chunks.append(data)
                                last_audio_at = time.monotonic()

                                # Detect if chunk is silent (mono 16-bit signed PCM)
                                import struct
                                samples = []
                                for i in range(0, len(data), 2):
                                    if i + 1 < len(data):
                                        val = struct.unpack("<h", data[i : i + 2])[0]
                                        samples.append(abs(val))
                                max_amp = max(samples) if samples else 0
                                is_silent = max_amp < self.settings.silence_amplitude_threshold

                                chunk_duration = (len(data) // 2) / 24000
                                if is_silent:
                                    consecutive_silence_seconds += chunk_duration
                                else:
                                    consecutive_silence_seconds = 0.0

                                if (
                                    stream_ended
                                    and consecutive_silence_seconds
                                    >= self.settings.silence_threshold_seconds
                                ):
                                    receive_done.set()
                                    return

                    if content.turn_complete:
                        receive_done.set()
                        return

                    if (
                        stream_ended
                        and audio_chunks
                        and last_audio_at > 0
                        and (time.monotonic() - last_audio_at) >= idle_seconds
                    ):
                        return

                # Stream closed by server
                if audio_chunks:
                    return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                receive_error = exc
            finally:
                receive_done.set()

        pcm_data = segment_pcm.read_bytes()
        chunk_size = self.settings.input_chunk_bytes
        total_chunks = (len(pcm_data) + chunk_size - 1) // chunk_size

        log_event(
            logger,
            logging.INFO,
            "segment_streaming",
            (
                f"Streaming segment {segment_index} ({segment_duration_sec:.1f}s, "
                f"{total_chunks} chunks, pace={pace_seconds:.3f}s/chunk)"
            ),
            job_id=job_id,
            segment_index=segment_index,
            segment_duration_sec=segment_duration_sec,
            total_chunks=total_chunks,
            estimated_send_seconds=total_chunks * pace_seconds,
        )

        async with self._client.aio.live.connect(
            model=self.settings.gemini_model,
            config=config,
        ) as session:
            receive_task = asyncio.create_task(receive_loop(session))

            for chunk_index, offset in enumerate(range(0, len(pcm_data), chunk_size)):
                chunk = pcm_data[offset : offset + chunk_size]
                await session.send_realtime_input(
                    audio=types.Blob(
                        data=chunk,
                        mime_type=f"audio/pcm;rate={self.settings.input_sample_rate}",
                    )
                )
                if pace_seconds > 0:
                    await asyncio.sleep(pace_seconds)

                # Progress log every ~30 seconds of audio
                if chunk_index > 0 and chunk_index % 300 == 0:
                    pct = (chunk_index / total_chunks) * 100
                    logger.info(
                        "Segment %s upload progress: %.0f%%",
                        segment_index,
                        pct,
                    )

            await session.send_realtime_input(audio_stream_end=True)
            stream_ended = True

            logger.info(
                "Segment %s audio sent, waiting for translation (timeout %.0fs)...",
                segment_index,
                receive_timeout,
            )

            # Poll until receive completes, turn_complete, or idle after last audio chunk
            deadline = time.monotonic() + receive_timeout
            try:
                while time.monotonic() < deadline:
                    if receive_done.is_set():
                        break
                    if (
                        stream_ended
                        and audio_chunks
                        and last_audio_at > 0
                        and (time.monotonic() - last_audio_at) >= idle_seconds
                    ):
                        break
                    await asyncio.sleep(0.25)
                else:
                    if not audio_chunks:
                        raise GeminiTranslationError(
                            f"Timed out after {receive_timeout:.0f}s waiting for translation"
                        )
            finally:
                receive_task.cancel()
                try:
                    await receive_task
                except asyncio.CancelledError:
                    pass

        if receive_error:
            raise GeminiTranslationError(str(receive_error)) from receive_error

        if not audio_chunks:
            raise GeminiTranslationError("Gemini returned empty translated audio")

        output_data = b"".join(audio_chunks)

        # Strip trailing silence beyond 1.0 second (leave 1.0 second buffer for natural fade)
        if consecutive_silence_seconds > 1.0:
            silence_bytes_to_strip = int((consecutive_silence_seconds - 1.0) * 24000 * 2)
            silence_bytes_to_strip = min(silence_bytes_to_strip, len(output_data))
            if silence_bytes_to_strip > 0:
                output_data = output_data[:-silence_bytes_to_strip]
                logger.info(
                    "Segment %s: Stripped %.2fs of trailing silence",
                    segment_index,
                    silence_bytes_to_strip / (24000 * 2),
                )

        output_pcm_path.parent.mkdir(parents=True, exist_ok=True)
        output_pcm_path.write_bytes(output_data)

        return {
            "output_path": output_pcm_path,
            "input_transcript": " ".join(input_transcript_parts) or None,
            "output_transcript": " ".join(output_transcript_parts) or None,
        }
