"""Folder watcher for automatic video dubbing."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from app.config import Settings, get_settings
from app.dubbing_service import DubbingService
from app.logger import get_logger, log_event
from app.models import JobStatus

logger = get_logger("watcher")


class VideoFileHandler(FileSystemEventHandler):
    """Watchdog handler that enqueues new video files for processing."""

    def __init__(
        self,
        enqueue: Callable[[Path], None],
        settings: Settings,
    ) -> None:
        super().__init__()
        self._enqueue = enqueue
        self.settings = settings

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self._maybe_enqueue(Path(event.src_path))

    def on_moved(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self._maybe_enqueue(Path(event.dest_path))

    def _maybe_enqueue(self, path: Path) -> None:
        if path.suffix.lower().lstrip(".") in self.settings.supported_format_set:
            self._enqueue(path)


class FolderWatcher:
    """
    Monitor the input folder for new videos and process them serially.

    Waits until file copies complete (stability check) and prevents duplicate
    processing of the same file.
    """

    def __init__(
        self,
        dubbing_service: DubbingService | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.dubbing_service = dubbing_service or DubbingService(self.settings)
        self._queue: asyncio.Queue[Path] = asyncio.Queue()
        self._processed_keys: set[str] = set()
        self._in_flight_keys: set[str] = set()
        self._observer: Observer | None = None
        self._running = False
        self._consumer_task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def _file_key(self, path: Path) -> str:
        """Unique key for deduplication based on path, size, and mtime."""
        resolved = path.resolve()
        if not resolved.exists():
            return str(resolved)
        stat = resolved.stat()
        return f"{resolved}|{stat.st_size}|{stat.st_mtime_ns}"

    def _enqueue_sync(self, path: Path) -> None:
        """Thread-safe enqueue from watchdog callback."""
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._queue.put_nowait, path)

    async def _wait_for_stable_file(self, path: Path) -> bool:
        """Poll until file size is unchanged for FILE_STABILITY_SECONDS."""
        if not path.exists():
            return False

        stable_duration = 0.0
        last_size = -1

        while stable_duration < self.settings.file_stability_seconds:
            if not path.exists():
                return False
            current_size = path.stat().st_size
            if current_size == 0:
                stable_duration = 0.0
            elif current_size == last_size:
                stable_duration += self.settings.file_stability_poll_seconds
            else:
                stable_duration = 0.0
                last_size = current_size
            await asyncio.sleep(self.settings.file_stability_poll_seconds)

        return path.exists() and path.stat().st_size > 0

    async def _consumer(self) -> None:
        """Process queued videos one at a time."""
        while self._running:
            try:
                path = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            if not await self._wait_for_stable_file(path):
                log_event(
                    logger,
                    logging.WARNING,
                    "file_unstable",
                    f"Skipping unstable or missing file: {path}",
                    source_file=str(path),
                )
                self._queue.task_done()
                continue

            file_key = self._file_key(path)
            if file_key in self._processed_keys or file_key in self._in_flight_keys:
                log_event(
                    logger,
                    logging.DEBUG,
                    "duplicate_skipped",
                    f"Skipping duplicate: {path.name}",
                    source_file=str(path),
                )
                self._queue.task_done()
                continue

            self._in_flight_keys.add(file_key)

            try:
                result = await self.dubbing_service.process_job(path)
                if result.status == JobStatus.COMPLETED:
                    self._processed_keys.add(file_key)
            finally:
                self._in_flight_keys.discard(file_key)
                self._queue.task_done()

    async def _scan_existing(self) -> None:
        """Enqueue videos already present in the input folder at startup."""
        input_dir = self.settings.input_path
        if not input_dir.exists():
            return

        for path in sorted(input_dir.iterdir()):
            if (
                path.is_file()
                and path.suffix.lower().lstrip(".") in self.settings.supported_format_set
            ):
                await self._queue.put(path)

    async def start(self) -> None:
        """Start watching and processing."""
        self._loop = asyncio.get_running_loop()
        self._running = True

        handler = VideoFileHandler(self._enqueue_sync, self.settings)
        self._observer = Observer()
        self._observer.schedule(
            handler,
            str(self.settings.input_path),
            recursive=False,
        )
        self._observer.start()

        self._consumer_task = asyncio.create_task(self._consumer())
        await self._scan_existing()

        log_event(
            logger,
            logging.INFO,
            "watcher_started",
            f"Watching {self.settings.input_path}",
            input_dir=str(self.settings.input_path),
        )

    async def stop(self) -> None:
        """Stop the watcher and drain the queue."""
        self._running = False

        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None

        if self._consumer_task:
            await self._queue.join()
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except asyncio.CancelledError:
                pass
            self._consumer_task = None

        log_event(
            logger,
            logging.INFO,
            "watcher_stopped",
            "Folder watcher stopped",
        )

    async def process_single(self, file_path: Path, target_language: str | None) -> None:
        """Process a single file without starting the folder watcher."""
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        ext = file_path.suffix.lower().lstrip(".")
        if ext not in self.settings.supported_format_set:
            supported = ", ".join(sorted(self.settings.supported_format_set))
            raise ValueError(
                f"Unsupported format '{ext}'. Supported: {supported}"
            )

        await self.dubbing_service.process_job(file_path, target_language)
