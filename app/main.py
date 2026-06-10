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
        description="Automatically dub videos into another language using Gemini Live API.",
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
    return parser


def validate_startup() -> None:
    """Fail fast if required dependencies or configuration are missing."""
    settings = get_settings()

    if not settings.gemini_api_key.strip():
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
    """Process a single video file."""
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
    """Process all existing video files in the input folder and exit."""
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


async def async_main(args: argparse.Namespace) -> None:
    """Async application entry."""
    if args.file:
        await run_single_file(args.file, args.language)
    else:
        await run_input_folder(args.language)


def main() -> None:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args()

    setup_logging()
    validate_startup()

    log_event(
        logger,
        logging.INFO,
        "app_start",
        "Gemini Video Dubbing Application starting",
        mode="single_file" if args.file else "folder",
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