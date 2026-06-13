"""Application entry point and CLI orchestration."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from app.config import PROJECT_ROOT, get_settings
from app.dubbing_service import DubbingService
from app.ffmpeg_service import FFmpegError, FFmpegService
from app.logger import get_logger, log_event, setup_logging

logger = get_logger("main")


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Dub videos into another language using Gemini Live API or a manual TTS pipeline.",
    )
    parser.add_argument(
        "--file",
        type=Path,
        help="Process a single video file instead of watching the input folder.",
    )
    parser.add_argument(
        "--language",
        type=str,
        help="Override TARGET_LANGUAGE from .env (e.g. hi, bn, es).",
    )
    parser.add_argument(
        "--prepare",
        action="store_true",
        help="Prepare video for manual TTS (extract vocals/background, transcribe, translate, slice original segments).",
    )
    parser.add_argument(
        "--stitch",
        action="store_true",
        help="Stitch manually generated TTS segments and merge them back into the video.",
    )
    parser.add_argument(
        "--job-id",
        type=str,
        help="Specify the job ID for preparing (resuming) or stitching.",
    )
    parser.add_argument(
        "--job-dir",
        type=Path,
        help="Specify the exact job folder path in processing/ to stitch.",
    )
    parser.add_argument(
        "--gemini-tts",
        action="store_true",
        help="Generate TTS audio files for all pending segments in translations.json using Gemini.",
    )
    parser.add_argument(
        "--edge-tts",
        action="store_true",
        help="Generate TTS audio files for all pending segments in translations.json using Microsoft Edge TTS.",
    )
    parser.add_argument(
        "--live-translate",
        action="store_true",
        help="Dub video(s) in one step using Gemini 3.5 Live Translate model, bypassing ElevenLabs.",
    )
    return parser


def validate_startup(args: argparse.Namespace) -> None:
    """Fail fast if required dependencies or configuration are missing."""
    settings = get_settings()

    # Gemini API key is only required if we are preparing a job or running automated dubbing
    if not args.stitch and not settings.gemini_api_key.strip():
        print(
            "ERROR: GEMINI_API_KEY is not set. Copy .env.example to .env and add your key.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        FFmpegService.verify_ffmpeg_available(settings)
    except FFmpegError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    settings.ensure_directories()


async def run_single_file(file_path: Path, target_language: str | None) -> None:
    """Process a single video file (automated workflow)."""
    settings = get_settings()
    if target_language:
        settings.target_language = target_language.lower()

    resolved = file_path if file_path.is_absolute() else (PROJECT_ROOT / file_path).resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"File not found: {resolved}")

    ext = resolved.suffix.lower().lstrip(".")
    if ext not in settings.supported_format_set:
        supported = ", ".join(sorted(settings.supported_format_set))
        raise ValueError(f"Unsupported format '{ext}'. Supported: {supported}")

    dubbing_service = DubbingService(settings=settings)
    await dubbing_service.process_job(resolved, target_language)


async def run_input_folder(target_language: str | None) -> None:
    """Process all existing video files in the input folder and exit (automated workflow)."""
    settings = get_settings()
    if target_language:
        settings.target_language = target_language.lower()

    input_dir = settings.input_path
    if not input_dir.exists():
        print(f"Input directory does not exist: {input_dir}")
        return

    # Find all supported video files
    video_files = []
    for path in sorted(input_dir.iterdir()):
        if (
            path.is_file()
            and path.suffix.lower().lstrip(".") in settings.supported_format_set
        ):
            video_files.append(path)

    if not video_files:
        log_event(logger, logging.INFO, "no_files_found", "No video files found in the input folder.")
        print("No video files found in the input folder.")
        return

    log_event(
        logger,
        logging.INFO,
        "processing_folder_started",
        f"Found {len(video_files)} video file(s) in input folder. Starting manual processing.",
        count=len(video_files),
    )

    dubbing_service = DubbingService(settings=settings)
    for index, file_path in enumerate(video_files):
        print(f"\nProcessing video {index + 1}/{len(video_files)}: {file_path.name}")
        try:
            await dubbing_service.process_job(file_path, target_language)
        except Exception as exc:
            logger.error("Failed to process %s: %s", file_path.name, exc, exc_info=True)
            print(f"ERROR processing {file_path.name}: {exc}")

    log_event(logger, logging.INFO, "processing_folder_completed", "Completed processing input folder.")


async def run_live_translate_single(file_path: Path, target_language: str | None) -> None:
    """Process a single video file using Gemini 3.5 Live Translate directly."""
    settings = get_settings()
    if target_language:
        settings.target_language = target_language.lower()

    resolved = file_path if file_path.is_absolute() else (PROJECT_ROOT / file_path).resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"File not found: {resolved}")

    ext = resolved.suffix.lower().lstrip(".")
    if ext not in settings.supported_format_set:
        supported = ", ".join(sorted(settings.supported_format_set))
        raise ValueError(f"Unsupported format '{ext}'. Supported: {supported}")

    dubbing_service = DubbingService(settings=settings)
    await dubbing_service.process_job(resolved, target_language, force_live_translate=True)


async def run_live_translate_folder(target_language: str | None) -> None:
    """Process all existing video files in the input folder using Gemini 3.5 Live Translate and exit."""
    settings = get_settings()
    if target_language:
        settings.target_language = target_language.lower()

    input_dir = settings.input_path
    if not input_dir.exists():
        print(f"Input directory does not exist: {input_dir}")
        return

    # Find all supported video files
    video_files = []
    for path in sorted(input_dir.iterdir()):
        if (
            path.is_file()
            and path.suffix.lower().lstrip(".") in settings.supported_format_set
        ):
            video_files.append(path)

    if not video_files:
        print("No video files found in the input folder.")
        return

    print(f"Found {len(video_files)} video file(s) in input folder. Starting Gemini 3.5 Live Translate.")
    dubbing_service = DubbingService(settings=settings)
    for index, file_path in enumerate(video_files):
        print(f"\nProcessing video {index + 1}/{len(video_files)}: {file_path.name}")
        try:
            await dubbing_service.process_job(file_path, target_language, force_live_translate=True)
        except Exception as exc:
            logger.error("Failed to process %s: %s", file_path.name, exc, exc_info=True)
            print(f"ERROR processing {file_path.name}: {exc}")

    print("Completed processing input folder.")


async def run_prepare_single(file_path: Path, target_language: str | None, job_id: str | None = None) -> None:
    """Prepare a single video file for manual TTS."""
    settings = get_settings()
    if target_language:
        settings.target_language = target_language.lower()

    resolved = file_path if file_path.is_absolute() else (PROJECT_ROOT / file_path).resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"File not found: {resolved}")

    ext = resolved.suffix.lower().lstrip(".")
    if ext not in settings.supported_format_set:
        supported = ", ".join(sorted(settings.supported_format_set))
        raise ValueError(f"Unsupported format '{ext}'. Supported: {supported}")

    dubbing_service = DubbingService(settings=settings)
    await dubbing_service.prepare_job(resolved, target_language, job_id=job_id)


async def run_prepare_folder(target_language: str | None) -> None:
    """Prepare all video files in the input folder for manual TTS."""
    settings = get_settings()
    if target_language:
        settings.target_language = target_language.lower()

    input_dir = settings.input_path
    if not input_dir.exists():
        print(f"Input directory does not exist: {input_dir}")
        return

    video_files = []
    for path in sorted(input_dir.iterdir()):
        if (
            path.is_file()
            and path.suffix.lower().lstrip(".") in settings.supported_format_set
        ):
            video_files.append(path)

    if not video_files:
        print("No video files found in the input folder to prepare.")
        return

    dubbing_service = DubbingService(settings=settings)
    for index, file_path in enumerate(video_files):
        print(f"\nPreparing video {index + 1}/{len(video_files)}: {file_path.name}")
        try:
            await dubbing_service.prepare_job(file_path, target_language)
        except Exception as exc:
            logger.error("Failed to prepare %s: %s", file_path.name, exc, exc_info=True)
            print(f"ERROR preparing {file_path.name}: {exc}")


async def run_stitch(job_id: str | None, job_dir: Path | None) -> None:
    """Stitch manually generated TTS segments back together and mux with video."""
    settings = get_settings()
    dubbing_service = DubbingService(settings=settings)

    if job_dir:
        resolved_dir = Path(job_dir) if Path(job_dir).is_absolute() else (PROJECT_ROOT / job_dir).resolve()
        if not resolved_dir.is_dir():
            raise FileNotFoundError(f"Job directory not found: {resolved_dir}")
    elif job_id:
        processing_dir = settings.processing_path
        found_dirs = []
        if processing_dir.exists():
            for p in processing_dir.iterdir():
                if p.is_dir() and p.name.endswith(job_id):
                    found_dirs.append(p)
        if not found_dirs:
            raise FileNotFoundError(f"No job folder found matching job_id '{job_id}' in {processing_dir}")
        elif len(found_dirs) > 1:
            dirs_str = ", ".join(d.name for d in found_dirs)
            raise ValueError(f"Multiple job folders match job_id '{job_id}': {dirs_str}. Please specify the directory via --job-dir.")
        resolved_dir = found_dirs[0]
    else:
        raise ValueError("Please specify --job-id or --job-dir to run stitching.")

    await dubbing_service.stitch_job(resolved_dir)


async def run_gemini_tts(job_id: str | None, job_dir: Path | None) -> None:
    """Generate TTS audio files for the job using Gemini."""
    settings = get_settings()
    dubbing_service = DubbingService(settings=settings)

    if job_dir:
        resolved_dir = Path(job_dir) if Path(job_dir).is_absolute() else (PROJECT_ROOT / job_dir).resolve()
        if not resolved_dir.is_dir():
            raise FileNotFoundError(f"Job directory not found: {resolved_dir}")
    elif job_id:
        processing_dir = settings.processing_path
        found_dirs = []
        if processing_dir.exists():
            for p in processing_dir.iterdir():
                if p.is_dir() and p.name.endswith(job_id):
                    found_dirs.append(p)
        if not found_dirs:
            raise FileNotFoundError(f"No job folder found matching job_id '{job_id}' in {processing_dir}")
        elif len(found_dirs) > 1:
            dirs_str = ", ".join(d.name for d in found_dirs)
            raise ValueError(f"Multiple job folders match job_id '{job_id}': {dirs_str}. Please specify the directory via --job-dir.")
        resolved_dir = found_dirs[0]
    else:
        raise ValueError("Please specify --job-id or --job-dir to run Gemini TTS generation.")

    await dubbing_service.generate_gemini_tts(resolved_dir)


async def run_edge_tts(job_id: str | None, job_dir: Path | None) -> None:
    """Generate TTS audio files for the job using Microsoft Edge TTS."""
    settings = get_settings()
    dubbing_service = DubbingService(settings=settings)

    if job_dir:
        resolved_dir = Path(job_dir) if Path(job_dir).is_absolute() else (PROJECT_ROOT / job_dir).resolve()
        if not resolved_dir.is_dir():
            raise FileNotFoundError(f"Job directory not found: {resolved_dir}")
    elif job_id:
        processing_dir = settings.processing_path
        found_dirs = []
        if processing_dir.exists():
            for p in processing_dir.iterdir():
                if p.is_dir() and p.name.endswith(job_id):
                    found_dirs.append(p)
        if not found_dirs:
            raise FileNotFoundError(f"No job folder found matching job_id '{job_id}' in {processing_dir}")
        elif len(found_dirs) > 1:
            dirs_str = ", ".join(d.name for d in found_dirs)
            raise ValueError(f"Multiple job folders match job_id '{job_id}': {dirs_str}. Please specify the directory via --job-dir.")
        resolved_dir = found_dirs[0]
    else:
        raise ValueError("Please specify --job-id or --job-dir to run Edge TTS generation.")

    await dubbing_service.generate_edge_tts(resolved_dir)


async def async_main(args: argparse.Namespace) -> None:
    """Async application entry."""
    if args.prepare:
        if args.file:
            await run_prepare_single(args.file, args.language, args.job_id)
        else:
            await run_prepare_folder(args.language)
    elif args.stitch:
        await run_stitch(args.job_id, args.job_dir)
    elif args.gemini_tts:
        await run_gemini_tts(args.job_id, args.job_dir)
    elif args.edge_tts:
        await run_edge_tts(args.job_id, args.job_dir)
    elif args.live_translate:
        if args.file:
            await run_live_translate_single(args.file, args.language)
        else:
            await run_live_translate_folder(args.language)
    else:
        # Default fully automated workflow
        if args.file:
            await run_single_file(args.file, args.language)
        else:
            await run_input_folder(args.language)


def main() -> None:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args()

    setup_logging()
    validate_startup(args)

    mode = "automated"
    if args.prepare:
        mode = "prepare"
    elif args.stitch:
        mode = "stitch"
    elif args.gemini_tts:
        mode = "gemini_tts"
    elif args.edge_tts:
        mode = "edge_tts"
    elif args.live_translate:
        mode = "live_translate"

    log_event(
        logger,
        logging.INFO,
        "app_start",
        f"Gemini Video Dubbing Application starting ({mode} mode)",
        mode=mode,
        file=str(args.file) if args.file else None,
        job_id=args.job_id,
    )

    try:
        asyncio.run(async_main(args))
    except KeyboardInterrupt:
        log_event(logger, logging.INFO, "app_exit", "Application stopped by user")
    except Exception as exc:
        log_event(logger, logging.ERROR, "app_crash", str(exc), error=str(exc))
        raise


if __name__ == "__main__":
    main()