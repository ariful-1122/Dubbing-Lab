"""Orchestrates the full video dubbing pipeline."""

from __future__ import annotations

import asyncio
import json
import logging
import re
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


def is_pure_non_verbal(text: str) -> bool:
    """
    Returns True if the segment's text consists entirely of non-verbal tokens,
    tags in brackets/parentheses, or standard pause/filler tokens.
    """
    cleaned = text.strip()
    if not cleaned:
        return True
    
    # Check if the entire string is just punctuation or symbols (e.g. "...", "?")
    if re.match(r"^[^\w\s]+$", cleaned):
        return True
        
    # Pattern to match brackets or parentheses with words, e.g. [laughter] or (sighs)
    tag_pattern = re.compile(r"(\[[a-zA-Z\s\-_]+\]|\([a-zA-Z\s\-_]+\))")
    remaining = tag_pattern.sub("", cleaned).strip()
    
    if not remaining:
        return True
        
    # Check for common filler words (case-insensitive)
    filler_pattern = re.compile(r"^(uh|um|ah|eh|er|oh|hmm|mhm|uh-huh|uh-uh|huh)[\s.,?!]*$", re.IGNORECASE)
    if filler_pattern.match(remaining):
        return True
        
    # Check if the remaining text (stripped of punctuation) is exactly one of the common non-verbal words
    nonverbal_words = {
        "laughter", "laughs", "chuckle", "chuckles", "sigh", "sighs", "gasp", "gasps",
        "groan", "groans", "hum", "hums", "sniff", "sniffs", "whisper", "whispers",
        "crying", "shout", "shouting", "grunt", "grunts", "screaming", "screams",
        "cough", "coughing", "throat-clearing", "clears throat"
    }
    word_cleaned = re.sub(r"[^\w\s]", "", remaining).lower().strip()
    if word_cleaned in nonverbal_words:
        return True
        
    return False


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
        force_live_translate: bool = False,
        job_id: str | None = None,
    ) -> DubbingJobResult:
        """Run the complete dubbing workflow for a single video file."""
        job_id = job_id or new_job_id()
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

            if force_live_translate:
                from app.providers.gemini_live import GeminiLiveProvider
                self.gemini = GeminiService(
                    provider=GeminiLiveProvider(self.settings),
                    settings=self.settings
                )

            use_voice_cloning = bool(self.settings.elevenlabs_api_key.strip()) and not force_live_translate

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
                ]
                if self.settings.demucs_shifts > 0:
                    demucs_cmd.extend(["--shifts", str(self.settings.demucs_shifts)])
                demucs_cmd.append(str(original_wav))
                
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

    async def prepare_job(
        self,
        video_path: Path,
        target_language: str | None = None,
        job_id: str | None = None,
    ) -> str:
        """
        Runs Demucs stem separation, Whisper transcription, Gemini translation,
        slices original vocal segments, and generates translations.json for manual TTS.
        """
        # 1. Resolve Job ID and directory name
        actual_job_id = job_id or new_job_id()
        language = (target_language or self.settings.target_language).lower()
        source = video_path.resolve()
        
        # Folder name is descriptive: <video_name>_<job_id>
        work_dir_name = f"{source.stem}_{actual_job_id}"
        work_dir = self.settings.processing_path / work_dir_name
        work_dir.mkdir(parents=True, exist_ok=True)

        log_event(
            logger,
            logging.INFO,
            "prepare_started",
            f"Starting preparation for video {source.name} (Job ID: {actual_job_id})",
            job_id=actual_job_id,
            work_dir=str(work_dir),
        )

        processing_video = work_dir / source.name
        # Copy or move the source video to the processing folder
        if source.parent != work_dir:
            if source.parent == self.settings.input_path:
                shutil.move(str(source), str(processing_video))
            else:
                shutil.copy2(source, processing_video)
        else:
            processing_video = source

        # Stems definition
        original_wav = work_dir / "original.wav"
        vocals_wav = work_dir / "vocals.wav"
        no_vocals_wav = work_dir / "no_vocals.wav"
        vocals_pcm = work_dir / "vocals.pcm"
        metadata_file = work_dir / "translations.json"

        # 2. Extract original audio as WAV (Skip if original.wav already exists)
        if not original_wav.exists():
            await asyncio.to_thread(
                self.ffmpeg.extract_audio_as_wav,
                processing_video,
                original_wav,
            )
        else:
            logger.info("Resuming: Found existing original.wav, skipping extraction.")

        # 3. Demucs separation (Skip if vocals.wav and no_vocals.wav already exist)
        if not vocals_wav.exists() or not no_vocals_wav.exists():
            logger.info("Running Demucs stem separation on %s", original_wav.name)
            import sys
            import subprocess
            
            demucs_cmd = [
                sys.executable, "-m", "demucs.separate",
                "-n", self.settings.demucs_model,
                "--two-stems", "vocals",
                "-o", str(work_dir),
            ]
            if self.settings.demucs_shifts > 0:
                demucs_cmd.extend(["--shifts", str(self.settings.demucs_shifts)])
            demucs_cmd.append(str(original_wav))
            
            def run_demucs():
                subprocess.run(demucs_cmd, check=True)
            
            await asyncio.to_thread(run_demucs)
            
            # Locate and move separated stems
            demucs_out_dir = work_dir / self.settings.demucs_model / original_wav.stem
            vocals_gen = demucs_out_dir / "vocals.wav"
            no_vocals_gen = demucs_out_dir / "no_vocals.wav"
            
            if not vocals_gen.exists() or not no_vocals_gen.exists():
                raise FileNotFoundError(
                    f"Demucs failed to produce vocals or background stem. Expected at {demucs_out_dir}"
                )
            # Move stems to the root of work_dir for simplicity and direct inspection
            shutil.move(str(vocals_gen), str(vocals_wav))
            shutil.move(str(no_vocals_gen), str(no_vocals_wav))
            # Cleanup demucs internal folder structure
            shutil.rmtree(work_dir / self.settings.demucs_model, ignore_errors=True)
        else:
            logger.info("Resuming: Found existing vocals/background stems, skipping separation.")

        # 4. Convert vocals WAV to PCM (Skip if vocals.pcm already exists)
        if not vocals_pcm.exists():
            await asyncio.to_thread(
                self.ffmpeg.convert_audio_format,
                vocals_wav,
                vocals_pcm,
                "pcm",
            )

        # 5. Transcription and Translation
        # Check if metadata file already exists
        segments = []
        if metadata_file.exists():
            logger.info("Resuming: Found existing translations.json.")
            try:
                with metadata_file.open("r", encoding="utf-8") as f:
                    meta_data = json.load(f)
                    segments = meta_data.get("segments", [])
            except Exception as e:
                logger.warning("Failed to parse existing translations.json, will re-generate: %s", e)

        if not segments:
            # Retrieve vocals duration
            vocals_duration = await asyncio.to_thread(
                self.ffmpeg.get_duration,
                vocals_wav,
            )
            
            # Calculate 1-minute segments
            segment_duration = 60.0  # 1 minute
            import math
            num_segments = int(math.ceil(vocals_duration / segment_duration))
            if num_segments == 0:
                num_segments = 1

            # Load Whisper model and transcribe the entire vocals track
            from faster_whisper import WhisperModel
            logger.info("Loading Whisper model '%s' on CPU...", self.settings.whisper_model)
            whisper_model = await asyncio.to_thread(
                WhisperModel,
                self.settings.whisper_model,
                device="cpu",
                compute_type="int8"
            )
            
            logger.info("Transcribing vocals track using Whisper (with word-level timestamps)...")
            segments_generator, info = await asyncio.to_thread(
                whisper_model.transcribe,
                str(vocals_wav),
                beam_size=5,
                word_timestamps=True,
            )
            whisper_segments = list(segments_generator)
            logger.info("Whisper transcribed %d segment(s).", len(whisper_segments))

            # Group Whisper words or segments into the 1-minute chunks based on word start timestamp
            seg_texts = [[] for _ in range(num_segments)]
            for w_seg in whisper_segments:
                if w_seg.words:
                    for word_obj in w_seg.words:
                        idx = int(word_obj.start // segment_duration)
                        if idx < 0:
                            idx = 0
                        elif idx >= num_segments:
                            idx = num_segments - 1
                        seg_texts[idx].append(word_obj.word.strip())
                else:
                    # Fallback if word-level timestamps are not populated for some reason
                    idx = int(w_seg.start // segment_duration)
                    if idx < 0:
                        idx = 0
                    elif idx >= num_segments:
                        idx = num_segments - 1
                    seg_texts[idx].append(w_seg.text.strip())

            joined_texts = []
            for t_list in seg_texts:
                joined_texts.append(" ".join([w for w in t_list if w]).strip())

            # Translate transcript segments in one batch via Gemini
            logger.info("Translating transcript via Gemini API...")
            from google import genai
            from google.genai import types
            
            gemini_client = genai.Client(api_key=self.settings.gemini_api_key)
            
            segments_to_translate = []
            for idx, text in enumerate(joined_texts):
                if text and not is_pure_non_verbal(text):
                    segments_to_translate.append({"id": idx, "text": text})

            translated_map = {}
            if segments_to_translate:
                prompt = (
                    f"You are an expert audio translator. Translate the following transcription segments "
                    f"from the source language into the target language (language code: '{language}').\n\n"
                    f"Instructions:\n"
                    f"1. Translate the 'text' value for each object. Keep the 'id' value unchanged.\n"
                    f"2. Keep all non-verbal vocalization tags exactly as they are in the translated text, "
                    f"such as [um], [uh], [hmm], [giggle], [sigh], [humming], etc. Place them in natural positions in the translated sentences.\n"
                    f"3. Maintain a natural, conversational tone.\n"
                    f"4. Return a valid JSON array matching the input schema. Do not return any other text or markdown wrappers.\n\n"
                    f"Input:\n{json.dumps(segments_to_translate, ensure_ascii=False)}"
                )

                # API Call with Retry
                response = None
                gemini_max_retries = 5
                for attempt in range(gemini_max_retries + 1):
                    try:
                        loop = asyncio.get_running_loop()
                        response = await loop.run_in_executor(
                            None,
                            lambda: gemini_client.models.generate_content(
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

                if response:
                    try:
                        translated_list = json.loads(response.text)
                        translated_map = {item["id"]: item["text"] for item in translated_list}
                    except Exception as exc:
                        logger.error("Failed to parse Gemini translation JSON response: %s. Raw response: %s", exc, response.text)

            # Build metadata segments list
            for idx in range(num_segments):
                orig_text = joined_texts[idx]
                seg_start = idx * 60.0
                seg_end = min((idx + 1) * 60.0, vocals_duration)
                seg_dur = seg_end - seg_start
                
                if not orig_text or is_pure_non_verbal(orig_text):
                    status_val = "keep_original"
                    trans_text = orig_text
                else:
                    status_val = "pending"
                    trans_text = translated_map.get(idx, orig_text).strip()

                segments.append({
                    "id": idx,
                    "start": seg_start,
                    "end": seg_end,
                    "duration": seg_dur,
                    "original_text": orig_text,
                    "translated_text": trans_text,
                    "expected_audio_file": f"manual_tts/segment_{idx:03d}.wav",
                    "original_audio_file": f"extracted_vocals/segment_{idx:03d}_original.wav",
                    "gender": "female",
                    "status": status_val
                })

            # Save to translations.json
            meta_data = {
                "job_id": actual_job_id,
                "video_file": processing_video.name,
                "source_video_path": str(source),
                "target_language": language,
                "original_audio_duration": vocals_duration,
                "segments": segments
            }
            with metadata_file.open("w", encoding="utf-8") as f:
                json.dump(meta_data, f, ensure_ascii=False, indent=2)

        # 6. Slicing original vocal segments for reference
        extracted_vocals_dir = work_dir / "extracted_vocals"
        extracted_vocals_dir.mkdir(parents=True, exist_ok=True)
        
        # Also create manual_tts directory for user convenience
        manual_tts_dir = work_dir / "manual_tts"
        manual_tts_dir.mkdir(parents=True, exist_ok=True)

        logger.info("Extracting reference vocal segments...")
        for seg in segments:
            idx = seg["id"]
            ref_path = extracted_vocals_dir / f"segment_{idx:03d}_original.wav"
            if not ref_path.exists():
                await asyncio.to_thread(
                    self.ffmpeg.slice_audio,
                    vocals_wav,
                    seg["start"],
                    seg["duration"],
                    ref_path,
                )
        
        # Success output
        print(f"\n=======================================================")
        print(f"Preparation Completed Successfully for Job: {actual_job_id}")
        print(f"Directory: {work_dir.resolve()}")
        print(f"=======================================================")
        print(f"Instructions:")
        print(f"1. Open '{metadata_file.name}' to review translations.")
        print(f"2. Generate audio segments manually using ElevenLabs (or another engine) using 'translated_text'.")
        print(f"3. Place the generated audio files in the 'manual_tts/' folder as:")
        for seg in segments[:3]:
            print(f"   - manual_tts/segment_{seg['id']:03d}.wav (or .mp3)")
        if len(segments) > 3:
            print(f"   ... up to manual_tts/segment_{segments[-1]['id']:03d}.wav")
        print(f"4. Once all files are placed, compile/stitch the final video by running:")
        print(f"   python run.py --stitch --job-id {actual_job_id}")
        print(f"=======================================================\n")

        return actual_job_id

    async def stitch_job(self, job_dir: Path) -> None:
        """
        Loads translations.json, checks manual_tts/ segments, resamples and overlays them on a silent timeline,
        normalizes the stitched vocals, mixes them with background sounds, and muxes them back into the video.
        """
        metadata_file = job_dir / "translations.json"
        if not metadata_file.exists():
            raise FileNotFoundError(f"Missing translations.json in job directory: {job_dir}")
            
        with metadata_file.open("r", encoding="utf-8") as f:
            meta_data = json.load(f)
            
        job_id = meta_data.get("job_id")
        video_filename = meta_data.get("video_file")
        target_language = meta_data.get("target_language", "bn")
        original_duration = meta_data.get("original_audio_duration")
        segments = meta_data.get("segments", [])
        
        original_video = job_dir / video_filename
        if not original_video.exists():
            raise FileNotFoundError(f"Original video not found in job directory: {original_video}")
            
        no_vocals_wav = job_dir / "no_vocals.wav"
        if not no_vocals_wav.exists():
            raise FileNotFoundError(f"Background audio track (no_vocals.wav) not found: {no_vocals_wav}")

        manual_tts_dir = job_dir / "manual_tts"
        if not manual_tts_dir.exists():
            raise FileNotFoundError(f"manual_tts/ directory does not exist: {manual_tts_dir}")

        log_event(
            logger,
            logging.INFO,
            "stitch_started",
            f"Stitching job {job_id} in {job_dir.name}",
            job_id=job_id,
        )

        # 1. Verify all expected TTS audio files exist
        missing_files = []
        found_segments = {}
        for seg in segments:
            idx = seg["id"]
            if seg.get("status") == "keep_original":
                # Check for original vocal slice
                ref_path = job_dir / "extracted_vocals" / f"segment_{idx:03d}_original.wav"
                if ref_path.exists():
                    found_segments[idx] = ref_path
                else:
                    vocals_wav = job_dir / "vocals.wav"
                    if vocals_wav.exists():
                        logger.info("Re-extracting original vocal segment %d...", idx)
                        try:
                            ref_path.parent.mkdir(parents=True, exist_ok=True)
                            self.ffmpeg.slice_audio(
                                vocals_wav,
                                seg["start"],
                                seg["duration"],
                                ref_path,
                            )
                            found_segments[idx] = ref_path
                        except Exception as e:
                            logger.error("Failed to re-extract original vocal segment %d: %s", idx, e)
                            missing_files.append(f"extracted_vocals/segment_{idx:03d}_original.wav")
                    else:
                        missing_files.append(f"extracted_vocals/segment_{idx:03d}_original.wav (vocals.wav not found)")
                continue

            # Search for segment_000.wav, segment_000.mp3, etc.
            found_path = None
            for ext in (".wav", ".mp3", ".aac", ".pcm", ".m4a", ".ogg"):
                p = manual_tts_dir / f"segment_{idx:03d}{ext}"
                if p.exists():
                    found_path = p
                    break
            if found_path is None:
                missing_files.append(f"segment_{idx:03d}.wav (or .mp3)")
            else:
                found_segments[idx] = found_path
                
        if missing_files:
            print(f"\nERROR: Cannot stitch job. Missing {len(missing_files)} TTS audio segment(s):")
            for filename in missing_files[:10]:
                print(f"  - {filename}")
            if len(missing_files) > 10:
                print(f"  - ... and {len(missing_files) - 10} more.")
            print(f"Please generate these segments and place them in: {manual_tts_dir.resolve()}\n")
            raise FileNotFoundError("Missing manual TTS audio segments.")

        # 2. Build the stitched timeline
        # Target sample rate (usually 24000 Hz)
        sample_rate = self.settings.output_sample_rate
        # Allocate timeline buffer for 16-bit signed mono PCM (2 bytes per sample)
        timeline = bytearray(int(original_duration * sample_rate * 2))
        
        logger.info("Resampling and overlaying %d manual TTS audio segment(s)...", len(segments))
        
        # Temp folder inside job_dir for temporary PCM conversions
        temp_pcm_dir = job_dir / "temp_pcm"
        temp_pcm_dir.mkdir(parents=True, exist_ok=True)
        
        try:
            for seg in segments:
                idx = seg["id"]
                audio_file = found_segments.get(idx)
                if audio_file is None:
                    continue
                
                # Check actual audio file duration to enforce zero overlap
                tts_dur = await asyncio.to_thread(
                    self.ffmpeg.get_duration,
                    audio_file
                )
                allowed_dur = seg.get("duration", 0.0)
                if allowed_dur > 0 and tts_dur > allowed_dur:
                    # Calculate required tempo ratio to fit within allowed slot
                    tempo_ratio = tts_dur / allowed_dur
                    logger.warning(
                        "Segment %d audio duration (%.2fs) exceeds allowed duration (%.2fs). "
                        "Applying tempo adjustment (ratio=%.4f) to prevent overlap.",
                        idx, tts_dur, allowed_dur, tempo_ratio
                    )
                    adjusted_wav = temp_pcm_dir / f"segment_{idx:03d}_adjusted.wav"
                    await asyncio.to_thread(
                        self.ffmpeg.adjust_audio_tempo,
                        audio_file,
                        tempo_ratio,
                        adjusted_wav,
                    )
                    audio_file = adjusted_wav

                # Convert the user's manual segment to raw 16-bit signed PCM at self.settings.output_sample_rate
                temp_segment_pcm = temp_pcm_dir / f"segment_{idx:03d}.pcm"
                
                await asyncio.to_thread(
                    self.ffmpeg.convert_audio_format,
                    audio_file,
                    temp_segment_pcm,
                    "pcm"
                )
                
                if temp_segment_pcm.exists():
                    segment_pcm_bytes = temp_segment_pcm.read_bytes()
                    # Overlay onto the timeline at segment start time
                    self._overlay_pcm(timeline, segment_pcm_bytes, seg["start"], sample_rate)
                    if seg.get("status") != "keep_original":
                        seg["status"] = "completed"
        finally:
            # Clean up temporary PCM files
            shutil.rmtree(temp_pcm_dir, ignore_errors=True)

        # 3. Write compiled timeline to WAV
        translated_pcm = job_dir / "translated.pcm"
        translated_wav = job_dir / "translated.wav"
        translated_pcm.write_bytes(timeline)
        
        await asyncio.to_thread(
            self.ffmpeg.pcm_to_wav,
            translated_pcm,
            translated_wav,
            sample_rate,
        )

        # 4. Normalize the stitched vocals
        normalized_wav = job_dir / "translated_normalized.wav"
        await asyncio.to_thread(
            self.ffmpeg.normalize_audio,
            translated_wav,
            normalized_wav,
        )

        # 5. Mix with background audio stem
        mixed_wav = job_dir / "final_mixed.wav"
        await asyncio.to_thread(
            self.ffmpeg.mix_audio,
            normalized_wav,
            no_vocals_wav,
            mixed_wav,
            self.settings.background_volume,
        )

        # 6. Convert final mixed audio to AAC format for final video
        final_audio_aac = job_dir / "final_mixed.aac"
        await asyncio.to_thread(
            self.ffmpeg.convert_audio_format,
            mixed_wav,
            final_audio_aac,
            "aac",
        )

        # 7. Mux final audio back with original video stream
        output_name = f"{original_video.stem}_dubbed_{target_language}.mp4"
        output_path = self.settings.output_path / output_name
        
        await asyncio.to_thread(
            self.ffmpeg.replace_audio,
            original_video,
            final_audio_aac,
            output_path,
        )

        # 8. Save updated translations metadata status
        with metadata_file.open("w", encoding="utf-8") as f:
            json.dump(meta_data, f, ensure_ascii=False, indent=2)

        print(f"\n=======================================================")
        print(f"Stitching Completed Successfully!")
        print(f"Final Dubbed Video: {output_path.resolve()}")
        print(f"=======================================================\n")

        log_event(
            logger,
            logging.INFO,
            "stitch_completed",
            f"Stitching completed successfully: {output_name}",
            job_id=job_id,
            output_file=str(output_path),
        )

    async def generate_gemini_tts(self, job_dir: Path) -> None:
        """
        Generates Text-to-Speech audio files for all translations in translations.json
        using Gemini API, converting the audio to WAV and placing them in manual_tts/.
        """
        metadata_file = job_dir / "translations.json"
        if not metadata_file.exists():
            raise FileNotFoundError(f"Missing translations.json in job directory: {job_dir}")
            
        with metadata_file.open("r", encoding="utf-8") as f:
            meta_data = json.load(f)
            
        job_id = meta_data.get("job_id")
        segments = meta_data.get("segments", [])
        
        manual_tts_dir = job_dir / "manual_tts"
        manual_tts_dir.mkdir(parents=True, exist_ok=True)
        
        log_event(
            logger,
            logging.INFO,
            "gemini_tts_started",
            f"Generating Gemini TTS for job {job_id} in {job_dir.name}",
            job_id=job_id,
        )
        
        from google import genai
        from google.genai import types
        
        gemini_client = genai.Client(api_key=self.settings.gemini_api_key)
        
        generated_count = 0
        skipped_count = 0
        
        for seg in segments:
            idx = seg["id"]
            text = seg.get("translated_text", "").strip()
            
            # 1. Skip non-verbal segments
            if seg.get("status") == "keep_original":
                logger.info("Segment %d is marked as keep_original, skipping TTS generation.", idx)
                skipped_count += 1
                continue
                
            # 2. Skip if file already exists in manual_tts/
            # Search for segment_000.wav, segment_000.mp3, etc.
            found_existing = False
            for ext in (".wav", ".mp3", ".aac", ".pcm", ".m4a", ".ogg"):
                if (manual_tts_dir / f"segment_{idx:03d}{ext}").exists():
                    found_existing = True
                    break
            if found_existing:
                logger.info("Segment %d already has an audio file in manual_tts/, skipping.", idx)
                skipped_count += 1
                continue
                
            if not text:
                logger.warning("Segment %d has empty translated_text, skipping.", idx)
                skipped_count += 1
                continue
                
            # 3. Determine gender-based voice
            gender = seg.get("gender", "female").lower()
            if gender == "male":
                voice_name = self.settings.gemini_tts_male_voice
            else:
                voice_name = self.settings.gemini_tts_female_voice
                
            logger.info("Generating Gemini TTS for segment %d (voice: %s): '%s'", idx, voice_name, text)
            
            # Pre-generation check: Estimate speech duration
            words = text.split()
            # Estimate 130 words per minute (approx 2.17 words per second)
            estimated_duration = len(words) / 2.17
            allowed_duration = seg.get("duration", 0.0)
            if allowed_duration > 0 and estimated_duration > allowed_duration * (1 + self.settings.manual_tts_overlap_threshold_percent / 100.0):
                logger.warning(
                    "WARNING: Pre-generation estimate for segment %d: "
                    "Expected speech duration (%.2fs based on %d words) is significantly longer than "
                    "original segment duration (%.2fs by %.1f%%). Check translation text length.",
                    idx, estimated_duration, len(words), allowed_duration,
                    ((estimated_duration - allowed_duration) / allowed_duration) * 100.0
                )
            
            # 4. Formulate the prompt with style instructions
            prompt = (
                f"Read the following text aloud with an Empathic style, a Neutral accent, and at a Drift pace. "
                f"Do not say anything other than the text itself:\n\n{text}"
            )
            
            # 5. Call Gemini API
            temp_pcm = job_dir / f"temp_segment_{idx:03d}.pcm"
            target_wav = manual_tts_dir / f"segment_{idx:03d}.wav"
            
            loop = asyncio.get_running_loop()
            
            def call_api():
                return gemini_client.models.generate_content(
                    model=self.settings.gemini_tts_model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_modalities=["AUDIO"],
                        speech_config=types.SpeechConfig(
                            voice_config=types.VoiceConfig(
                                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                    voice_name=voice_name
                                )
                            )
                        )
                    )
                )
                
            response = None
            gemini_max_retries = 3
            for attempt in range(gemini_max_retries + 1):
                try:
                    response = await loop.run_in_executor(None, call_api)
                    break
                except Exception as e:
                    if attempt >= gemini_max_retries:
                        logger.error("Gemini TTS API call failed for segment %d after retries: %s", idx, e)
                        raise
                    delay = self.settings.retry_base_delay_seconds * (2 ** attempt)
                    logger.warning("Gemini TTS API call failed for segment %d. Retrying in %.1fs...", idx, delay)
                    await asyncio.sleep(delay)
            
            # Parse audio bytes from response
            audio_bytes = None
            if response and response.candidates:
                candidate = response.candidates[0]
                if candidate.content and candidate.content.parts:
                    for part in candidate.content.parts:
                        if part.inline_data and part.inline_data.data:
                            audio_bytes = part.inline_data.data
                            break
                            
            if not audio_bytes:
                logger.error("Gemini did not return any audio data for segment %d.", idx)
                continue
                
            # 6. Save as temporary PCM and convert to WAV
            temp_pcm.write_bytes(audio_bytes)
            
            # The model gemini-3.1-flash-tts-preview returns 24kHz L16 PCM
            # Convert it to standard WAV at 24000 Hz sample rate
            await asyncio.to_thread(
                self.ffmpeg.pcm_to_wav,
                temp_pcm,
                target_wav,
                24000
            )
            
            if temp_pcm.exists():
                temp_pcm.unlink()
                
            # Post-generation diagnostics
            tts_dur = await asyncio.to_thread(
                self.ffmpeg.get_duration,
                target_wav,
            )
            orig_dur = seg.get("duration", 0.0)
            diff_sec = tts_dur - orig_dur
            diff_percent = (diff_sec / orig_dur) * 100.0 if orig_dur > 0 else 0.0
            
            logger.info(
                "Diagnostics for segment %d: "
                "Original vocal duration: %.2fs, "
                "Generated TTS duration: %.2fs, "
                "Duration difference: %+.2fs (%+.1f%%)",
                idx, orig_dur, tts_dur, diff_sec, diff_percent
            )
            
            if diff_percent > self.settings.manual_tts_overlap_threshold_percent:
                logger.warning(
                    "FLAGGED SEGMENT %d: Generated TTS duration (%.2fs) exceeds original segment duration "
                    "(%.2fs) by %.1f%%, exceeding threshold of %.1f%%.",
                    idx, tts_dur, orig_dur, diff_percent, self.settings.manual_tts_overlap_threshold_percent
                )

            generated_count += 1
            print(f"  - Generated manual_tts/segment_{idx:03d}.wav")
            
        print(f"\n=======================================================")
        print(f"Gemini TTS Generation Completed!")
        print(f"Generated: {generated_count} segment(s)")
        print(f"Skipped: {skipped_count} segment(s)")
        print(f"=======================================================\n")
        
        log_event(
            logger,
            logging.INFO,
            "gemini_tts_completed",
            f"TTS generation completed. Generated: {generated_count}, skipped: {skipped_count}",
            job_id=job_id,
        )

    def _get_edge_tts_voice(self, language: str, gender: str) -> str:
        """Get standard Microsoft Edge TTS voice based on target language and gender."""
        is_male = gender.lower() == "male"
        if is_male and self.settings.edge_tts_male_voice:
            return self.settings.edge_tts_male_voice
        if not is_male and self.settings.edge_tts_female_voice:
            return self.settings.edge_tts_female_voice

        lang = language.lower()
        if lang == "bn":
            return "bn-BD-PradeepNeural" if is_male else "bn-BD-NabanitaNeural"
        elif lang == "hi":
            return "hi-IN-MadhurNeural" if is_male else "hi-IN-SwaraNeural"
        elif lang == "ur":
            return "ur-PK-AsadNeural" if is_male else "ur-PK-UzmaNeural"
        elif lang == "es":
            return "es-ES-AlvaroNeural" if is_male else "es-ES-ElviraNeural"
        elif lang == "ar":
            return "ar-EG-ShakirNeural" if is_male else "ar-EG-SalmaNeural"
        elif lang == "en":
            return "en-US-BrianNeural" if is_male else "en-US-EmmaNeural"
        elif lang == "de":
            return "de-DE-ConradNeural" if is_male else "de-DE-KatjaNeural"
        else:
            return "en-US-BrianNeural" if is_male else "en-US-EmmaNeural"

    async def generate_edge_tts(self, job_dir: Path) -> None:
        """
        Generates Text-to-Speech audio files for all translations in translations.json
        using Microsoft Edge TTS, converting the audio to WAV and placing them in manual_tts/.
        """
        metadata_file = job_dir / "translations.json"
        if not metadata_file.exists():
            raise FileNotFoundError(f"Missing translations.json in job directory: {job_dir}")
            
        with metadata_file.open("r", encoding="utf-8") as f:
            meta_data = json.load(f)
            
        job_id = meta_data.get("job_id")
        target_language = meta_data.get("target_language", "bn")
        segments = meta_data.get("segments", [])
        
        manual_tts_dir = job_dir / "manual_tts"
        manual_tts_dir.mkdir(parents=True, exist_ok=True)
        
        log_event(
            logger,
            logging.INFO,
            "edge_tts_started",
            f"Generating Microsoft Edge TTS for job {job_id} in {job_dir.name}",
            job_id=job_id,
        )
        
        import edge_tts
        
        generated_count = 0
        skipped_count = 0
        
        for seg in segments:
            idx = seg["id"]
            text = seg.get("translated_text", "").strip()
            
            # 1. Skip non-verbal segments
            if seg.get("status") == "keep_original":
                logger.info("Segment %d is marked as keep_original, skipping TTS generation.", idx)
                skipped_count += 1
                continue
                
            # 2. Skip if file already exists in manual_tts/
            # Search for segment_000.wav, segment_000.mp3, etc.
            found_existing = False
            for ext in (".wav", ".mp3", ".aac", ".pcm", ".m4a", ".ogg"):
                if (manual_tts_dir / f"segment_{idx:03d}{ext}").exists():
                    found_existing = True
                    break
            if found_existing:
                logger.info("Segment %d already has an audio file in manual_tts/, skipping.", idx)
                skipped_count += 1
                continue
                
            if not text:
                logger.warning("Segment %d has empty translated_text, skipping.", idx)
                skipped_count += 1
                continue
                
            # 3. Determine voice based on language and gender
            gender = seg.get("gender", "female").lower()
            voice_name = self._get_edge_tts_voice(target_language, gender)
                
            logger.info("Generating Edge TTS for segment %d (voice: %s): '%s'", idx, voice_name, text)
            
            # Pre-generation check: Estimate speech duration
            words = text.split()
            # Estimate 130 words per minute (approx 2.17 words per second)
            estimated_duration = len(words) / 2.17
            allowed_duration = seg.get("duration", 0.0)
            if allowed_duration > 0 and estimated_duration > allowed_duration * (1 + self.settings.manual_tts_overlap_threshold_percent / 100.0):
                logger.warning(
                    "WARNING: Pre-generation estimate for segment %d: "
                    "Expected speech duration (%.2fs based on %d words) is significantly longer than "
                    "original segment duration (%.2fs by %.1f%%). Check translation text length.",
                    idx, estimated_duration, len(words), allowed_duration,
                    ((estimated_duration - allowed_duration) / allowed_duration) * 100.0
                )
            
            # 4. Generate audio via Edge TTS
            target_wav = manual_tts_dir / f"segment_{idx:03d}.wav"
            temp_mp3 = job_dir / f"temp_segment_{idx:03d}.mp3"
            
            try:
                communicate = edge_tts.Communicate(text, voice_name)
                await communicate.save(str(temp_mp3))
                
                # Convert the generated MP3 file to standard WAV at output_sample_rate
                await asyncio.to_thread(
                    self.ffmpeg.convert_audio_format,
                    temp_mp3,
                    target_wav,
                    "wav"
                )
            finally:
                if temp_mp3.exists():
                    temp_mp3.unlink()
            
            # Post-generation diagnostics
            tts_dur = await asyncio.to_thread(
                self.ffmpeg.get_duration,
                target_wav,
            )
            orig_dur = seg.get("duration", 0.0)
            diff_sec = tts_dur - orig_dur
            diff_percent = (diff_sec / orig_dur) * 100.0 if orig_dur > 0 else 0.0
            
            logger.info(
                "Diagnostics for segment %d: "
                "Original vocal duration: %.2fs, "
                "Generated TTS duration: %.2fs, "
                "Duration difference: %+.2fs (%+.1f%%)",
                idx, orig_dur, tts_dur, diff_sec, diff_percent
            )
            
            if diff_percent > self.settings.manual_tts_overlap_threshold_percent:
                logger.warning(
                    "FLAGGED SEGMENT %d: Generated TTS duration (%.2fs) exceeds original segment duration "
                    "(%.2fs) by %.1f%%, exceeding threshold of %.1f%%.",
                    idx, tts_dur, orig_dur, diff_percent, self.settings.manual_tts_overlap_threshold_percent
                )
                
            generated_count += 1
            print(f"  - Generated manual_tts/segment_{idx:03d}.wav")
            
        print(f"\n=======================================================")
        print(f"Microsoft Edge TTS Generation Completed!")
        print(f"Generated: {generated_count} segment(s)")
        print(f"Skipped: {skipped_count} segment(s)")
        print(f"=======================================================\n")
        
        log_event(
            logger,
            logging.INFO,
            "edge_tts_completed",
            f"Edge TTS generation completed. Generated: {generated_count}, skipped: {skipped_count}",
            job_id=job_id,
        )

    def _overlay_pcm(self, timeline: bytearray, segment_bytes: bytes, start_sec: float, sample_rate: int) -> None:
        """Overlays raw PCM audio bytes onto a timeline bytearray at the given start timestamp."""
        import struct
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
