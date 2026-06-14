"""FastAPI application for Dubbing Lab web UI."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse, HTMLResponse
from pydantic import BaseModel

from app.config import get_settings, SUPPORTED_LANGUAGES
from app.dubbing_service import DubbingService
from app.ffmpeg_service import FFmpegService
from app.logger import get_logger, log_event, active_job_id
from app.models import JobStatus, new_job_id

logger = get_logger("api")

app = FastAPI(title="Gemini Video Dubbing API")

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SUPPORTED_LANGUAGES_MAP = {
    "bn": "Bengali (বাংলা)",
    "hi": "Hindi (हिन्दी)",
    "ur": "Urdu (اردو)",
    "es": "Spanish (Español)",
    "ar": "Arabic (العربية)"
}

# In-memory tracking of active running jobs
# Structure: { job_id: { "status": "processing", "video_file": "...", "target_language": "...", "mode": "...", "started_at": "..." } }
active_jobs: dict[str, dict[str, Any]] = {}


class StartJobRequest(BaseModel):
    file_name: str
    target_language: str
    mode: str  # "automated" | "live_translate" | "prepare"


class UpdateTranslationsRequest(BaseModel):
    segments: list[dict[str, Any]]


class GenerateTTSRequest(BaseModel):
    engine: str  # "gemini-tts" | "edge-tts"


def find_job_dir(job_id: str) -> Path | None:
    """Helper to locate a job directory in processing/ or failed/."""
    settings = get_settings()
    
    # 1. Check processing directory
    proc_dir = settings.processing_path
    if proc_dir.exists():
        for p in proc_dir.iterdir():
            if p.is_dir() and (p.name == job_id or p.name.endswith(f"_{job_id}")):
                return p
                
    # 2. Check failed directory
    failed_dir = settings.failed_path / job_id
    if failed_dir.is_dir():
        return failed_dir
        
    return None


def get_job_log_path(job_id: str) -> Path | None:
    """Get the log path for a job, checking job dir first then fallback to persistent logs."""
    job_dir = find_job_dir(job_id)
    if job_dir:
        log_file = job_dir / "job.log"
        if log_file.exists():
            return log_file
            
    # Check fallback persistent logs
    settings = get_settings()
    fallback_log = settings.log_path / f"job_{job_id}.log"
    if fallback_log.exists():
        return fallback_log
        
    return None


def get_completed_video_path(job_id: str) -> Path | None:
    """Helper to locate the completed dubbed video for a job ID."""
    settings = get_settings()
    out_dir = settings.output_path
    if not out_dir.exists():
        return None
        
    # 1. If job_id is in active_jobs, construct expected name from source video filename and language
    running_info = active_jobs.get(job_id)
    if running_info:
        source_file = running_info.get("video_file", "")
        lang = running_info.get("target_language", "bn")
        if source_file:
            expected_name = f"{Path(source_file).stem}_dubbed_{lang}.mp4"
            p = out_dir / expected_name
            if p.exists():
                return p
                
    # 2. If job_id starts with completed_
    if job_id.startswith("completed_"):
        target_name = job_id[len("completed_"):] + ".mp4"
        p = out_dir / target_name
        if p.exists():
            return p
            
    # 3. Fallback: check if job_id is in the filename (for manually prepared/stitched files)
    for p in out_dir.iterdir():
        if p.is_file() and job_id in p.name:
            return p
            
    return None


async def run_dubbing_task(
    job_id: str,
    video_path: Path,
    target_language: str,
    mode: str
) -> None:
    """Async background runner for a dubbing job."""
    # Set the contextvar for logger
    active_job_id.set(job_id)
    
    settings = get_settings()
    dubbing_service = DubbingService(settings=settings)
    
    # Find or create processing directory to write logs into before dubbing service creates it
    work_dir_name = job_id if mode != "prepare" else f"{video_path.stem}_{job_id}"
    work_dir = settings.processing_path / work_dir_name
    work_dir.mkdir(parents=True, exist_ok=True)
    
    log_event(
        logger,
        logging.INFO,
        "job_api_started",
        f"API starting background job {job_id} in mode '{mode}'",
        job_id=job_id,
        video_file=video_path.name,
        target_language=target_language,
        mode=mode
    )
    
    try:
        if mode == "prepare":
            await dubbing_service.prepare_job(video_path, target_language, job_id=job_id)
            # Update status in active_jobs
            if job_id in active_jobs:
                active_jobs[job_id]["status"] = "prepared"
        else:
            force_live = (mode == "live_translate")
            await dubbing_service.process_job(
                video_path,
                target_language,
                force_live_translate=force_live,
                job_id=job_id
            )
            if job_id in active_jobs:
                active_jobs[job_id]["status"] = "completed"
    except Exception as exc:
        log_event(
            logger,
            logging.ERROR,
            "job_api_error",
            f"API background job {job_id} failed: {exc}",
            job_id=job_id,
            error=str(exc)
        )
        if job_id in active_jobs:
            active_jobs[job_id]["status"] = "failed"
            active_jobs[job_id]["error_message"] = str(exc)
    finally:
        # Copy job.log from processing dir to persistent logs folder before directory is deleted/moved
        try:
            job_log = work_dir / "job.log"
            if job_log.exists():
                persistent_log = settings.log_path / f"job_{job_id}.log"
                shutil.copy2(job_log, persistent_log)
        except Exception as e:
            logger.warning("Failed to save persistent log for job %s: %s", job_id, e)
            
        # Clean from active_jobs after a buffer period or keep the completion state
        # We'll keep it in active_jobs so the client can query the final outcome of active jobs


async def run_tts_task(job_id: str, job_dir: Path, engine: str) -> None:
    """Async background runner for TTS generation."""
    active_job_id.set(job_id)
    settings = get_settings()
    dubbing_service = DubbingService(settings=settings)
    
    log_event(
        logger,
        logging.INFO,
        "tts_api_started",
        f"Starting background TTS generation using {engine} for job {job_id}",
        job_id=job_id,
        engine=engine
    )
    
    try:
        if engine == "gemini-tts":
            await dubbing_service.generate_gemini_tts(job_dir)
        else:
            await dubbing_service.generate_edge_tts(job_dir)
            
        if job_id in active_jobs:
            active_jobs[job_id]["status"] = "prepared"
    except Exception as exc:
        log_event(
            logger,
            logging.ERROR,
            "tts_api_error",
            f"TTS generation failed for job {job_id}: {exc}",
            job_id=job_id,
            error=str(exc)
        )
        if job_id in active_jobs:
            active_jobs[job_id]["status"] = "prepared"  # reset status so they can retry
    finally:
        # Save persistent log
        try:
            job_log = job_dir / "job.log"
            if job_log.exists():
                persistent_log = settings.log_path / f"job_{job_id}.log"
                shutil.copy2(job_log, persistent_log)
        except Exception:
            pass


async def run_stitch_task(job_id: str, job_dir: Path) -> None:
    """Async background runner for stitching and mixing."""
    active_job_id.set(job_id)
    settings = get_settings()
    dubbing_service = DubbingService(settings=settings)
    
    log_event(
        logger,
        logging.INFO,
        "stitch_api_started",
        f"Starting background stitching for job {job_id}",
        job_id=job_id
    )
    
    try:
        await dubbing_service.stitch_job(job_dir)
        if job_id in active_jobs:
            active_jobs[job_id]["status"] = "completed"
            
        # Clean up processing artifacts on success
        shutil.rmtree(job_dir, ignore_errors=True)
    except Exception as exc:
        log_event(
            logger,
            logging.ERROR,
            "stitch_api_error",
            f"Stitching failed for job {job_id}: {exc}",
            job_id=job_id,
            error=str(exc)
        )
        if job_id in active_jobs:
            active_jobs[job_id]["status"] = "failed"
            active_jobs[job_id]["error_message"] = str(exc)
    finally:
        # Save persistent log
        try:
            job_log = job_dir / "job.log"
            if job_log.exists():
                persistent_log = settings.log_path / f"job_{job_id}.log"
                shutil.copy2(job_log, persistent_log)
        except Exception:
            pass


@app.get("/api/config")
def get_config():
    """Retrieve editable configuration values."""
    settings = get_settings()
    return {
        "gemini_api_key": "***" if settings.gemini_api_key else "",
        "elevenlabs_api_key": "***" if settings.elevenlabs_api_key else "",
        "target_language": settings.target_language,
        "background_volume": settings.background_volume,
        "gemini_tts_female_voice": settings.gemini_tts_female_voice,
        "gemini_tts_male_voice": settings.gemini_tts_male_voice,
    }


@app.get("/api/languages")
def get_languages():
    """Retrieve BCP-47 supported languages list."""
    return [
        {"code": code, "name": SUPPORTED_LANGUAGES_MAP.get(code, code.upper())}
        for code in sorted(SUPPORTED_LANGUAGES)
    ]


@app.get("/api/input-files")
def get_input_files():
    """List video files currently in the input directory."""
    settings = get_settings()
    input_path = settings.input_path
    if not input_path.exists():
        return []
    
    files = []
    for p in sorted(input_path.iterdir()):
        if p.is_file() and p.suffix.lower().lstrip(".") in settings.supported_format_set:
            files.append({
                "name": p.name,
                "size_bytes": p.stat().st_size,
                "modified": datetime.fromtimestamp(p.stat().st_mtime).isoformat()
            })
    return files


@app.get("/api/jobs")
def get_jobs():
    """Get a list of all jobs across processing, failed, and output directories."""
    settings = get_settings()
    jobs = []
    
    # 1. Completed jobs (scanned from output/ directory)
    out_dir = settings.output_path
    if out_dir.exists():
        for p in out_dir.iterdir():
            if p.is_file() and p.suffix.lower().lstrip(".") in settings.supported_format_set:
                # Matches format: <video_name>_dubbed_<lang>.mp4
                match = re.match(r"^(.+)_dubbed_([a-z]{2})\.[a-zA-Z0-9]+$", p.name)
                if match:
                    video_name = match.group(1)
                    lang = match.group(2)
                    
                    # Look up active jobs in memory to see if we can reuse the real job_id and mode
                    matched_job_id = f"completed_{p.stem}"
                    matched_mode = "automated"
                    matched_started_at = None
                    
                    for j_id, info in active_jobs.items():
                        source_stem = Path(info.get("video_file", "")).stem
                        if source_stem == video_name and info.get("target_language") == lang:
                            matched_job_id = j_id
                            matched_mode = info.get("mode", "automated")
                            matched_started_at = info.get("started_at")
                            break
                            
                    jobs.append({
                        "job_id": matched_job_id,
                        "video_file": p.name,
                        "target_language": lang,
                        "status": "completed",
                        "mode": matched_mode,
                        "completed_at": datetime.fromtimestamp(p.stat().st_mtime).isoformat(),
                        "started_at": matched_started_at,
                        "size_bytes": p.stat().st_size
                    })

    # 2. Failed jobs (scanned from failed/ directory)
    failed_dir = settings.failed_path
    if failed_dir.exists():
        for p in failed_dir.iterdir():
            if p.is_dir():
                job_id = p.name
                # Try to extract details from translations.json or logs
                video_file = "Unknown Video"
                lang = settings.target_language
                translations_file = p / "translations.json"
                if translations_file.exists():
                    try:
                        with translations_file.open("r", encoding="utf-8") as f:
                            meta = json.load(f)
                            video_file = meta.get("video_file", video_file)
                            lang = meta.get("target_language", lang)
                    except Exception:
                        pass
                else:
                    # Look for video files in folder
                    for item in p.iterdir():
                        if item.is_file() and item.suffix.lower().lstrip(".") in settings.supported_format_set:
                            video_file = item.name
                            break
                            
                jobs.append({
                    "job_id": job_id,
                    "video_file": video_file,
                    "target_language": lang,
                    "status": "failed",
                    "mode": "unknown",
                    "completed_at": datetime.fromtimestamp(p.stat().st_mtime).isoformat(),
                })

    # 3. Processing and prepared manual jobs (scanned from processing/ directory)
    proc_dir = settings.processing_path
    if proc_dir.exists():
        for p in proc_dir.iterdir():
            if p.is_dir():
                folder_name = p.name
                
                # Deduce job_id from folder name
                # Folder format: <video_name>_<job_id> or exactly <job_id>
                parts = folder_name.split("_")
                job_id = parts[-1] if len(parts) > 1 else folder_name
                
                # Check if it's currently running in memory
                running_info = active_jobs.get(job_id)
                
                video_file = "Unknown Video"
                lang = settings.target_language
                status = "processing" if running_info else "pending"
                mode = "prepare"
                
                translations_file = p / "translations.json"
                if translations_file.exists():
                    try:
                        with translations_file.open("r", encoding="utf-8") as f:
                            meta = json.load(f)
                            video_file = meta.get("video_file", video_file)
                            lang = meta.get("target_language", lang)
                            # If translations.json exists, it is prepared/manual
                            status = "prepared" if status == "pending" else status
                    except Exception:
                        pass
                else:
                    # Look for video files
                    for item in p.iterdir():
                        if item.is_file() and item.suffix.lower().lstrip(".") in settings.supported_format_set:
                            video_file = item.name
                            break
                            
                if running_info:
                    status = running_info.get("status", status)
                    mode = running_info.get("mode", mode)
                    
                jobs.append({
                    "job_id": job_id,
                    "video_file": video_file,
                    "target_language": lang,
                    "status": status,
                    "mode": mode,
                    "started_at": running_info.get("started_at") if running_info else None
                })
                
    # Add any in-memory active jobs that haven't written to disk yet
    for j_id, info in active_jobs.items():
        if not any(j["job_id"] == j_id for j in jobs):
            jobs.append({
                "job_id": j_id,
                "video_file": info["video_file"],
                "target_language": info["target_language"],
                "status": info["status"],
                "mode": info["mode"],
                "started_at": info["started_at"]
            })

    return jobs


@app.post("/api/jobs/upload")
async def upload_video(file: UploadFile = File(...)):
    """Upload a new video file to the input folder."""
    settings = get_settings()
    input_dir = settings.input_path
    input_dir.mkdir(parents=True, exist_ok=True)
    
    # Clean filename
    filename = re.sub(r"[^\w\-\.]", "_", file.filename)
    target_path = input_dir / filename
    
    try:
        with target_path.open("wb") as buffer:
            while chunk := await file.read(65536):
                buffer.write(chunk)
        return {"filename": filename, "status": "success"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Upload failed: {exc}")


@app.post("/api/jobs/start")
def start_job(payload: StartJobRequest, background_tasks: BackgroundTasks):
    """Start a dubbing job (automated, live translate, or prepare manual)."""
    settings = get_settings()
    input_path = settings.input_path / payload.file_name
    
    if not input_path.exists():
        raise HTTPException(status_code=404, detail=f"Video file '{payload.file_name}' not found in input folder.")
        
    job_id = new_job_id()
    
    # Store in memory
    active_jobs[job_id] = {
        "status": "processing",
        "video_file": payload.file_name,
        "target_language": payload.target_language,
        "mode": payload.mode,
        "started_at": datetime.now(timezone.utc).isoformat()
    }
    
    # Queue execution
    background_tasks.add_task(
        run_dubbing_task,
        job_id,
        input_path,
        payload.target_language,
        payload.mode
    )
    
    return {"job_id": job_id, "status": "processing"}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    """Retrieve details and segment configurations for a job."""
    job_dir = find_job_dir(job_id)
    running_info = active_jobs.get(job_id)
    
    status = running_info.get("status", "unknown") if running_info else "unknown"
    video_file = running_info.get("video_file", "Unknown") if running_info else "Unknown"
    lang = running_info.get("target_language", "bn") if running_info else "bn"
    mode = running_info.get("mode", "unknown") if running_info else "unknown"
    error_msg = running_info.get("error_message") if running_info else None
    
    segments = []
    
    if job_dir:
        # Job directory exists
        if job_dir.parent.name == "failed":
            status = "failed"
            
        translations_file = job_dir / "translations.json"
        if translations_file.exists():
            try:
                with translations_file.open("r", encoding="utf-8") as f:
                    meta = json.load(f)
                    video_file = meta.get("video_file", video_file)
                    lang = meta.get("target_language", lang)
                    segments = meta.get("segments", [])
                    if status == "unknown" or status == "pending":
                        status = "prepared"
            except Exception as e:
                logger.error("Failed to parse translations.json for job %s: %s", job_id, e)
                
    elif job_id.startswith("completed_"):
        status = "completed"
        # Completed jobs don't have segment details stored on disk after cleanup
        
    return {
        "job_id": job_id,
        "status": status,
        "video_file": video_file,
        "target_language": lang,
        "mode": mode,
        "error_message": error_msg,
        "segments": segments
    }


@app.put("/api/jobs/{job_id}/translations")
def update_translations(job_id: str, payload: UpdateTranslationsRequest):
    """Overwrite the translations.json configuration file for a prepared manual job."""
    job_dir = find_job_dir(job_id)
    if not job_dir:
        raise HTTPException(status_code=404, detail="Job folder not found.")
        
    translations_file = job_dir / "translations.json"
    if not translations_file.exists():
        raise HTTPException(status_code=404, detail="translations.json not found in job folder.")
        
    try:
        # Read existing metadata
        with translations_file.open("r", encoding="utf-8") as f:
            meta = json.load(f)
            
        # Update segments
        meta["segments"] = payload.segments
        
        # Write back
        with translations_file.open("w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
            
        return {"status": "success"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to update translations: {exc}")


@app.post("/api/jobs/{job_id}/tts")
def generate_tts(job_id: str, payload: GenerateTTSRequest, background_tasks: BackgroundTasks):
    """Triggers background TTS generation for manual workflow."""
    job_dir = find_job_dir(job_id)
    if not job_dir:
        raise HTTPException(status_code=404, detail="Job folder not found.")
        
    # Set state in active jobs
    active_jobs[job_id] = {
        "status": f"generating_tts ({payload.engine})",
        "video_file": "Processing",
        "target_language": "bn",
        "mode": "prepare",
        "started_at": datetime.now(timezone.utc).isoformat()
    }
    
    # Read actual video file from translations.json to populate video_file
    translations_file = job_dir / "translations.json"
    if translations_file.exists():
        try:
            with translations_file.open("r", encoding="utf-8") as f:
                meta = json.load(f)
                active_jobs[job_id]["video_file"] = meta.get("video_file")
                active_jobs[job_id]["target_language"] = meta.get("target_language")
        except Exception:
            pass

    background_tasks.add_task(
        run_tts_task,
        job_id,
        job_dir,
        payload.engine
    )
    
    return {"status": "processing"}


@app.post("/api/jobs/{job_id}/stitch")
def stitch_job(job_id: str, background_tasks: BackgroundTasks):
    """Triggers background stitching and muxing."""
    job_dir = find_job_dir(job_id)
    if not job_dir:
        raise HTTPException(status_code=404, detail="Job folder not found.")
        
    # Check if translations.json exists
    translations_file = job_dir / "translations.json"
    if not translations_file.exists():
        raise HTTPException(status_code=404, detail="translations.json not found.")
        
    active_jobs[job_id] = {
        "status": "stitching",
        "video_file": "Processing",
        "target_language": "bn",
        "mode": "prepare",
        "started_at": datetime.now(timezone.utc).isoformat()
    }
    
    try:
        with translations_file.open("r", encoding="utf-8") as f:
            meta = json.load(f)
            active_jobs[job_id]["video_file"] = meta.get("video_file")
            active_jobs[job_id]["target_language"] = meta.get("target_language")
    except Exception:
        pass
        
    background_tasks.add_task(
        run_stitch_task,
        job_id,
        job_dir
    )
    
    return {"status": "processing"}


@app.post("/api/jobs/{job_id}/segments/{segment_id}/upload")
async def upload_custom_segment(job_id: str, segment_id: int, file: UploadFile = File(...)):
    """Upload a custom recorded or generated audio file for a specific segment."""
    job_dir = find_job_dir(job_id)
    if not job_dir:
        raise HTTPException(status_code=404, detail="Job folder not found.")
        
    manual_tts_dir = job_dir / "manual_tts"
    manual_tts_dir.mkdir(parents=True, exist_ok=True)
    
    # Save the file temporarily
    suffix = Path(file.filename).suffix.lower()
    temp_file = job_dir / f"temp_upload_seg_{segment_id}{suffix}"
    
    try:
        with temp_file.open("wb") as buffer:
            while chunk := await file.read(65536):
                buffer.write(chunk)
                
        # Convert to standard WAV inside manual_tts/
        target_wav = manual_tts_dir / f"segment_{segment_id:03d}.wav"
        
        settings = get_settings()
        ffmpeg_service = FFmpegService(settings)
        
        await asyncio.to_thread(
            ffmpeg_service.convert_audio_format,
            temp_file,
            target_wav,
            "wav"
        )
        
        # Update segment status to pending or completed in translations.json
        translations_file = job_dir / "translations.json"
        if translations_file.exists():
            with translations_file.open("r", encoding="utf-8") as f:
                meta = json.load(f)
            for seg in meta.get("segments", []):
                if seg.get("id") == segment_id:
                    seg["status"] = "pending"  # marks it as generated/uploaded, ready for stitching
                    break
            with translations_file.open("w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
                
        return {"status": "success", "file": target_wav.name}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Custom audio upload failed: {exc}")
    finally:
        if temp_file.exists():
            temp_file.unlink()


@app.get("/api/jobs/{job_id}/segments/{segment_id}/audio/{audio_type}")
def get_segment_audio(job_id: str, segment_id: int, audio_type: str):
    """Retrieve segment audio (original vocal slice or translated synthetic WAV)."""
    job_dir = find_job_dir(job_id)
    if not job_dir:
        raise HTTPException(status_code=404, detail="Job folder not found.")
        
    if audio_type == "original":
        audio_file = job_dir / "extracted_vocals" / f"segment_{segment_id:03d}_original.wav"
    elif audio_type == "dubbed":
        # Search for segment_000.wav, segment_000.mp3, etc.
        manual_tts_dir = job_dir / "manual_tts"
        audio_file = None
        for ext in (".wav", ".mp3", ".aac", ".pcm", ".m4a", ".ogg"):
            p = manual_tts_dir / f"segment_{segment_id:03d}{ext}"
            if p.exists():
                audio_file = p
                break
    else:
        raise HTTPException(status_code=400, detail="Invalid audio type. Choose 'original' or 'dubbed'.")
        
    if not audio_file or not audio_file.exists():
        raise HTTPException(status_code=404, detail="Audio file not found.")
        
    return FileResponse(audio_file, media_type="audio/wav")


@app.get("/api/jobs/{job_id}/video/{video_type}")
def get_video_stream(job_id: str, video_type: str):
    """Stream original video or final dubbed output video."""
    settings = get_settings()
    
    if video_type == "original":
        job_dir = find_job_dir(job_id)
        if not job_dir:
            raise HTTPException(status_code=404, detail="Job folder not found.")
            
        # Find video inside job folder
        video_file = None
        for item in job_dir.iterdir():
            if item.is_file() and item.suffix.lower().lstrip(".") in settings.supported_format_set:
                video_file = item
                break
    elif video_type == "dubbed":
        # Completed video is in output folder
        video_file = get_completed_video_path(job_id)
    else:
        raise HTTPException(status_code=400, detail="Invalid video type. Choose 'original' or 'dubbed'.")
        
    if not video_file or not video_file.exists():
        raise HTTPException(status_code=404, detail="Video file not found.")
        
    # Stream video
    return FileResponse(video_file, media_type="video/mp4")


@app.get("/api/jobs/{job_id}/logs")
def get_job_logs(job_id: str):
    """Retrieve execution log strings for a given job."""
    log_path = get_job_log_path(job_id)
    if not log_path or not log_path.exists():
        # Fallback: check if we have it in active jobs
        if job_id in active_jobs:
            return {"logs": ["Job is starting. Waiting for log events..."]}
        return {"logs": ["No logs found for this job ID."]}
        
    try:
        # Read the last 500 lines of logs
        with log_path.open("r", encoding="utf-8") as f:
            lines = f.readlines()
        return {"logs": [line.strip() for line in lines[-500:]]}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read logs: {exc}")


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str):
    """Delete all files and configurations associated with a job."""
    settings = get_settings()
    
    # 1. Delete in-memory job
    if job_id in active_jobs:
        active_jobs.pop(job_id)
        
    # 2. Delete job processing/failed directories
    job_dir = find_job_dir(job_id)
    if job_dir and job_dir.exists():
        try:
            shutil.rmtree(job_dir, ignore_errors=True)
        except Exception as exc:
            logger.warning("Failed to delete directory %s: %s", job_dir, exc)
            
    # 3. Delete output file if applicable
    out_file = get_completed_video_path(job_id)
    if out_file and out_file.exists():
        try:
            out_file.unlink()
        except Exception as exc:
            logger.warning("Failed to delete completed file %s: %s", out_file, exc)
                        
    # 4. Delete persistent log file
    persistent_log = settings.log_path / f"job_{job_id}.log"
    if persistent_log.exists():
        try:
            persistent_log.unlink()
        except Exception:
            pass
            
    return {"status": "success"}


@app.get("/{path:path}")
def serve_frontend(path: str):
    """Serve the compiled React static files or fallback to index.html for SPA routing."""
    # Prevent catching api routes
    if path.startswith("api/") or path.startswith("api"):
        raise HTTPException(status_code=404, detail="Not Found")
        
    dist_dir = Path("frontend/dist")
    if not dist_dir.exists():
        return HTMLResponse("<h1>Frontend is not built. Please run the build script.</h1>")
        
    # Check if path refers to a file in dist/
    file_path = dist_dir / path
    if file_path.is_file():
        # Determine content-type mapping
        media_type = None
        if path.endswith(".js"):
            media_type = "application/javascript"
        elif path.endswith(".css"):
            media_type = "text/css"
        elif path.endswith(".svg"):
            media_type = "image/svg+xml"
        elif path.endswith(".ico"):
            media_type = "image/x-icon"
        elif path.endswith(".png"):
            media_type = "image/png"
        return FileResponse(file_path, media_type=media_type)
        
    # Fallback to index.html for SPA routing
    index_path = dist_dir / "index.html"
    if index_path.is_file():
        return FileResponse(index_path, media_type="text/html")
        
    return HTMLResponse("<h1>Frontend index.html not found.</h1>")

