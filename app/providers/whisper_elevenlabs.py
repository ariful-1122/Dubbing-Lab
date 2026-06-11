"""Speech translation provider using local Whisper transcription and ElevenLabs voice cloning."""

from __future__ import annotations

import asyncio
import json
import logging
import struct
import time
from pathlib import Path
from typing import Any

import httpx
from google import genai
from google.genai import types

from app.config import Settings, get_settings
from app.ffmpeg_service import FFmpegService
from app.logger import get_logger, log_event
from app.models import TranslationResult
from app.providers.base import SpeechTranslationProvider

logger = get_logger("whisper_elevenlabs")


class WhisperElevenLabsProvider(SpeechTranslationProvider):
    """
    Transcribes audio locally using Whisper, translates the transcript via Gemini,
    clones the original speaker's voice via ElevenLabs API, and generates dubbed audio.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        ffmpeg_service: FFmpegService | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.ffmpeg = ffmpeg_service or FFmpegService(self.settings)
        self._gemini_client = genai.Client(api_key=self.settings.gemini_api_key)
        self._whisper_model: Any = None

    async def health_check(self) -> bool:
        """Verify API keys are present."""
        return bool(self.settings.gemini_api_key.strip() and self.settings.elevenlabs_api_key.strip())

    def _load_whisper(self) -> None:
        """Lazily load the Whisper model on CPU."""
        if self._whisper_model is None:
            from faster_whisper import WhisperModel
            logger.info("Loading Whisper model '%s' on CPU...", self.settings.whisper_model)
            self._whisper_model = WhisperModel(
                self.settings.whisper_model,
                device="cpu",
                compute_type="int8"
            )

    async def translate_audio(
        self,
        input_pcm_path: Path,
        target_language: str,
        *,
        job_id: str,
        output_dir: Path,
    ) -> TranslationResult:
        """Runs transcription, translation, voice cloning, and audio synthesis."""
        started_time = time.monotonic()
        
        # 1. Convert input PCM to temporary WAV for Whisper
        temp_wav = output_dir / f"{job_id}_input_vocals.wav"
        await asyncio.to_thread(
            self.ffmpeg.pcm_to_wav,
            input_pcm_path,
            temp_wav,
            self.settings.input_sample_rate,
        )
        
        input_duration = self.ffmpeg.get_pcm_duration(
            input_pcm_path,
            self.settings.input_sample_rate,
        )

        # 2. Transcribe locally using Whisper
        logger.info("Transcribing vocals track %s", temp_wav.name)
        await asyncio.to_thread(self._load_whisper)
        
        segments_generator, info = await asyncio.to_thread(
            self._whisper_model.transcribe,
            str(temp_wav),
            beam_size=5,
        )
        whisper_segments = list(segments_generator)
        
        log_event(
            logger,
            logging.INFO,
            "transcription_completed",
            f"Whisper transcribed {len(whisper_segments)} segment(s).",
            job_id=job_id,
            segment_count=len(whisper_segments),
        )

        # Edge case: No speech detected
        if not whisper_segments:
            logger.warning("No speech detected in vocals track.")
            output_pcm_path = output_dir / f"{job_id}_translated.pcm"
            # Return silent PCM matching input duration
            silent_bytes = b"\x00" * int(input_duration * self.settings.output_sample_rate * 2)
            output_pcm_path.write_bytes(silent_bytes)
            return TranslationResult(
                output_pcm_path=output_pcm_path,
                input_duration_seconds=input_duration,
                output_duration_seconds=input_duration,
                segments_processed=0,
                api_call_count=0,
            )

        # 3. Slice a 15-second training snippet for voice cloning
        # Find the first segment that has actual words
        ref_start = 0.0
        for seg in whisper_segments:
            if len(seg.text.strip()) > 5:
                ref_start = seg.start
                break

        voice_ref_wav = output_dir / f"{job_id}_voice_ref.wav"
        await asyncio.to_thread(
            self.ffmpeg.slice_audio,
            temp_wav,
            ref_start,
            15.0,
            voice_ref_wav,
        )

        # 4. Translate transcript block in one batch using Gemini to keep context
        logger.info("Translating transcript via Gemini API...")
        segments_to_translate = [
            {"id": idx, "text": seg.text}
            for idx, seg in enumerate(whisper_segments)
        ]
        
        prompt = (
            f"You are an expert audio translator. Translate the following transcription segments "
            f"from the source language into the target language (language code: '{target_language}').\n\n"
            f"Instructions:\n"
            f"1. Translate the 'text' value for each object. Keep the 'id' value unchanged.\n"
            f"2. Keep all non-verbal vocalization tags exactly as they are in the translated text, "
            f"such as [um], [uh], [hmm], [giggle], [sigh], [humming], etc. Place them in natural positions in the translated sentences.\n"
            f"3. Maintain a natural, conversational tone.\n"
            f"4. Return a valid JSON array matching the input schema. Do not return any other text or markdown wrappers.\n\n"
            f"Input:\n{json.dumps(segments_to_translate, ensure_ascii=False)}"
        )

        response = None
        gemini_max_retries = 5
        for attempt in range(gemini_max_retries + 1):
            try:
                loop = asyncio.get_running_loop()
                response = await loop.run_in_executor(
                    None,
                    lambda: self._gemini_client.models.generate_content(
                        model="gemini-3.1-flash-lite",
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json",
                        )
                    )
                )
                break
            except Exception as e:
                if attempt >= gemini_max_retries:
                    logger.error("Gemini translation API call failed after max retries: %s", e)
                    raise
                delay = self.settings.retry_base_delay_seconds * (2 ** attempt)
                logger.warning("Gemini API call failed with %s. Retrying in %.1fs...", type(e).__name__, delay)
                await asyncio.sleep(delay)


        
        try:
            translated_list = json.loads(response.text)
            translated_map = {item["id"]: item["text"] for item in translated_list}
        except Exception as exc:
            logger.error("Failed to parse Gemini translation JSON response: %s. Raw response: %s", exc, response.text)
            # Fallback to original text if translation fails
            translated_map = {idx: seg.text for idx, seg in enumerate(whisper_segments)}

        # 5. Clone voice using ElevenLabs API or use pre-configured voice_id
        headers = {
            "xi-api-key": self.settings.elevenlabs_api_key
        }
        
        voice_id = self.settings.elevenlabs_voice_id.strip()
        should_delete_voice = False
        
        async with httpx.AsyncClient() as http_client:
            try:
                if not voice_id:
                    logger.info("Cloning voice via ElevenLabs API...")
                    try:
                        files = {
                            "files": (voice_ref_wav.name, voice_ref_wav.read_bytes(), "audio/wav")
                        }
                        data = {
                            "name": f"dubbed_voice_{job_id}",
                            "description": f"Temporary voice for job {job_id}"
                        }
                        resp = await http_client.post(
                            "https://api.elevenlabs.io/v1/voices/add",
                            headers=headers,
                            data=data,
                            files=files,
                            timeout=60.0
                        )
                        if resp.status_code != 200:
                            logger.error("ElevenLabs add voice failed: %d - %s", resp.status_code, resp.text)
                            if 400 <= resp.status_code < 500:
                                try:
                                    err_json = resp.json()
                                    msg = err_json.get("detail", {}).get("message", resp.text)
                                except Exception:
                                    msg = resp.text
                                raise RuntimeError(f"ElevenLabs Auth/Client Error ({resp.status_code}): {msg}")
                        resp.raise_for_status()
                        voice_id = resp.json()["voice_id"]
                        should_delete_voice = True
                        logger.info("Created temporary cloned voice ID: %s", voice_id)
                    except httpx.HTTPStatusError as e:
                        if e.response.status_code in (401, 403) and "create_instant_voice_clone" in e.response.text:
                            voice_id = "21m00Tcm4TlvDq8ikWAM"  # Rachel (default pre-made voice ID)
                            logger.warning(
                                "Your ElevenLabs key is missing Instant Voice Cloning permission (requires Starter tier). "
                                "Gracefully falling back to default pre-made voice (Rachel: %s).",
                                voice_id
                            )
                        else:
                            raise
                else:
                    logger.info("Using pre-configured ElevenLabs voice ID: %s", voice_id)

                # 6. Generate speech for each segment and overlay onto timeline
                # output_sample_rate is usually 24000 Hz
                sample_rate = self.settings.output_sample_rate
                timeline = bytearray(int(input_duration * sample_rate * 2))

                output_format = f"pcm_{sample_rate}"
                tts_url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format={output_format}"

                for idx, segment in enumerate(whisper_segments):
                    text = translated_map.get(idx, "").strip()
                    if not text:
                        continue
                        
                    logger.info("Generating TTS for segment %d: '%s'", idx, text)
                    
                    # Call ElevenLabs TTS with retry
                    segment_pcm = None
                    for attempt in range(self.settings.max_retries + 1):
                        try:
                            payload = {
                                "text": text,
                                "model_id": self.settings.elevenlabs_model_id,
                                "voice_settings": {
                                    "stability": 0.5,
                                    "similarity_boost": 0.75
                                }
                            }
                            tts_resp = await http_client.post(
                                tts_url,
                                headers=headers,
                                json=payload,
                                timeout=60.0
                            )
                            if tts_resp.status_code != 200:
                                logger.error("ElevenLabs TTS failed for segment %d: %d - %s", idx, tts_resp.status_code, tts_resp.text)
                                if 400 <= tts_resp.status_code < 500 and tts_resp.status_code != 429:
                                    try:
                                        err_json = tts_resp.json()
                                        msg = err_json.get("detail", {}).get("message", tts_resp.text)
                                    except Exception:
                                        msg = tts_resp.text
                                    raise RuntimeError(f"ElevenLabs Client Error ({tts_resp.status_code}): {msg}")
                            tts_resp.raise_for_status()
                            segment_pcm = tts_resp.content
                            break
                        except RuntimeError as client_err:
                            raise client_err
                        except Exception as e:
                            if attempt >= self.settings.max_retries:
                                logger.error("ElevenLabs TTS failed for segment %d: %s", idx, e)
                                break
                            delay = self.settings.retry_base_delay_seconds * (2 ** attempt)
                            logger.warning("ElevenLabs TTS failed. Retrying in %.1fs: %s", delay, e)
                            await asyncio.sleep(delay)

                    if segment_pcm:
                        self._overlay_pcm(timeline, segment_pcm, segment.start, sample_rate)

            finally:
                # Cleanup: Delete the cloned voice from ElevenLabs library if we created it
                if voice_id and should_delete_voice:
                    logger.info("Cleaning up temporary voice ID %s from ElevenLabs...", voice_id)
                    try:
                        del_resp = await http_client.delete(
                            f"https://api.elevenlabs.io/v1/voices/{voice_id}",
                            headers=headers,
                            timeout=30.0
                        )
                        if del_resp.status_code != 200:
                            logger.error("ElevenLabs delete voice failed: %d - %s", del_resp.status_code, del_resp.text)
                        del_resp.raise_for_status()
                        logger.info("Deleted voice %s successfully.", voice_id)

                    except Exception as e:
                        logger.error("Failed to delete voice %s from ElevenLabs: %s", voice_id, e)

        # 7. Write the generated timeline to output PCM
        output_pcm_path = output_dir / f"{job_id}_translated.pcm"
        output_pcm_path.write_bytes(timeline)
        
        output_duration = self.ffmpeg.get_pcm_duration(
            output_pcm_path,
            self.settings.output_sample_rate,
        )

        # Cleanup intermediate files
        for path in (temp_wav, voice_ref_wav):
            if path.exists():
                path.unlink()

        return TranslationResult(
            output_pcm_path=output_pcm_path,
            input_duration_seconds=input_duration,
            output_duration_seconds=output_duration,
            segments_processed=len(whisper_segments),
            api_call_count=len(whisper_segments),
            input_transcript=" ".join(seg.text for seg in whisper_segments),
            output_transcript=" ".join(translated_map.values()),
        )

    def _overlay_pcm(self, timeline: bytearray, segment_bytes: bytes, start_sec: float, sample_rate: int) -> None:
        """Overlays raw PCM audio bytes onto a timeline bytearray at the given start timestamp."""
        start_sample = int(start_sec * sample_rate)
        start_byte = start_sample * 2
        num_samples = len(segment_bytes) // 2
        
        # Ensure we don't go out of bounds of the timeline
        if start_byte + len(segment_bytes) > len(timeline):
            extra_needed = (start_byte + len(segment_bytes)) - len(timeline)
            timeline.extend(b"\x00" * extra_needed)
            
        for i in range(num_samples):
            offset = start_byte + i * 2
            # Read existing sample
            existing_val = struct.unpack_from("<h", timeline, offset)[0]
            # Read new sample
            new_val = struct.unpack_from("<h", segment_bytes, i * 2)[0]
            # Mix by adding
            mixed_val = existing_val + new_val
            # Clip to 16-bit signed boundaries
            mixed_val = max(-32768, min(32767, mixed_val))
            # Write back
            struct.pack_into("<h", timeline, offset, mixed_val)
