"""FFmpeg utility functions for audio/video processing."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Sequence

import ffmpeg

from app.config import Settings, get_settings
from app.logger import get_logger

logger = get_logger("ffmpeg")


class FFmpegError(Exception):
    """Raised when an FFmpeg or FFprobe operation fails."""

    def __init__(self, message: str, stderr: str = "") -> None:
        super().__init__(message)
        self.stderr = stderr


class FFmpegService:
    """Programmatic FFmpeg operations for the dubbing pipeline."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    @staticmethod
    def verify_ffmpeg_available(settings: Settings | None = None) -> None:
        """Ensure ffmpeg and ffprobe are available on PATH."""
        settings = settings or get_settings()
        settings.apply_ffmpeg_path()

        missing: list[str] = []
        for binary in ("ffmpeg", "ffprobe"):
            if shutil.which(binary) is None:
                missing.append(binary)

        if missing:
            hint = (
                " Set FFMPEG_BIN_DIR in .env to the folder containing ffmpeg.exe "
                "(e.g. FFMPEG_BIN_DIR=C:\\ffmpeg\\bin), then restart the terminal."
            )
            names = ", ".join(f"'{name}'" for name in missing)
            raise FFmpegError(
                f"{names} not found on PATH. Install FFmpeg and add it to PATH,{hint}"
            )

    def _run_ffmpeg(self, stream: ffmpeg.nodes.FilterableStream, output_path: Path) -> None:
        """Execute an ffmpeg-python stream graph and capture errors."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            ffmpeg.run(
                stream,
                overwrite_output=True,
                capture_stdout=True,
                capture_stderr=True,
            )
        except ffmpeg.Error as exc:
            stderr = ""
            if exc.stderr:
                stderr = exc.stderr.decode("utf-8", errors="replace")
            raise FFmpegError(f"FFmpeg failed: {stderr[:500]}", stderr=stderr) from exc

    def extract_audio(self, video_path: Path, output_pcm_path: Path) -> Path:
        """
        Extract mono 16 kHz 16-bit PCM audio from a video file.

        Gemini Live API requires raw PCM at 16 kHz.
        """
        logger.info("Extracting audio from %s", video_path)
        stream = (
            ffmpeg.input(str(video_path))
            .audio
            .output(
                str(output_pcm_path),
                format="s16le",
                acodec="pcm_s16le",
                ac=1,
                ar=self.settings.input_sample_rate,
            )
            .overwrite_output()
        )
        self._run_ffmpeg(stream, output_pcm_path)
        return output_pcm_path

    def get_duration(self, media_path: Path) -> float:
        """Return media duration in seconds via ffprobe."""
        try:
            probe = ffmpeg.probe(str(media_path))
        except ffmpeg.Error as exc:
            stderr = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else ""
            raise FFmpegError(f"ffprobe failed for {media_path}", stderr=stderr) from exc

        duration = probe.get("format", {}).get("duration")
        if duration is None:
            # Fall back to stream duration for raw PCM
            if media_path.suffix.lower() in {".pcm", ".raw"}:
                byte_size = media_path.stat().st_size
                samples = byte_size // 2
                return samples / self.settings.input_sample_rate
            raise FFmpegError(f"Could not determine duration for {media_path}")

        return float(duration)

    def get_pcm_duration(self, pcm_path: Path, sample_rate: int | None = None) -> float:
        """Return duration of raw PCM file based on byte size."""
        rate = sample_rate or self.settings.input_sample_rate
        byte_size = pcm_path.stat().st_size
        if byte_size < 2:
            return 0.0
        return (byte_size // 2) / rate

    def replace_audio(
        self,
        video_path: Path,
        audio_path: Path,
        output_video_path: Path,
    ) -> Path:
        """Replace video audio track with translated audio, copying video stream."""
        logger.info("Muxing translated audio into %s", video_path.name)
        video_input = ffmpeg.input(str(video_path))
        audio_input = ffmpeg.input(str(audio_path))
        stream = (
            ffmpeg.output(
                video_input.video,
                audio_input.audio,
                str(output_video_path),
                vcodec="copy",
                acodec="aac",
                audio_bitrate="192k",
            )
            .overwrite_output()
        )
        self._run_ffmpeg(stream, output_video_path)
        return output_video_path

    def adjust_audio_tempo(
        self,
        audio_path: Path,
        tempo_ratio: float,
        output_path: Path,
    ) -> Path:
        """
        Adjust audio playback speed using atempo filter.

        tempo_ratio > 1.0 speeds up audio; < 1.0 slows it down.
        atempo only supports 0.5–2.0 per filter, so ratios outside that range
        are achieved by chaining multiple atempo filters.
        """
        if tempo_ratio <= 0:
            raise FFmpegError(f"Invalid tempo ratio: {tempo_ratio}")

        if abs(tempo_ratio - 1.0) < 0.001:
            shutil.copy2(audio_path, output_path)
            return output_path

        filters = self._build_atempo_chain(tempo_ratio)
        logger.info("Applying tempo adjustment ratio=%.4f filters=%s", tempo_ratio, filters)

        stream = ffmpeg.input(str(audio_path))
        for factor in filters:
            stream = stream.filter("atempo", factor)

        out = stream.output(str(output_path)).overwrite_output()
        self._run_ffmpeg(out, output_path)
        return output_path

    @staticmethod
    def _build_atempo_chain(ratio: float) -> list[float]:
        """Build a chain of atempo factors to achieve the desired ratio."""
        if 0.5 <= ratio <= 2.0:
            return [ratio]

        factors: list[float] = []
        remaining = ratio

        if ratio > 2.0:
            while remaining > 2.0:
                factors.append(2.0)
                remaining /= 2.0
            if abs(remaining - 1.0) > 0.001:
                factors.append(remaining)
        else:
            while remaining < 0.5:
                factors.append(0.5)
                remaining /= 0.5
            if abs(remaining - 1.0) > 0.001:
                factors.append(remaining)

        return factors or [ratio]

    def normalize_audio(self, audio_path: Path, output_path: Path) -> Path:
        """Normalize audio loudness for consistent output levels."""
        logger.info("Normalizing audio %s", audio_path.name)
        stream = (
            ffmpeg.input(str(audio_path))
            .output(
                str(output_path),
                af="dynaudnorm",
            )
            .overwrite_output()
        )
        self._run_ffmpeg(stream, output_path)
        return output_path

    def convert_audio_format(
        self,
        source_path: Path,
        output_path: Path,
        output_format: str = "aac",
    ) -> Path:
        """
        Convert audio to the requested format.

        Supports 'aac', 'wav', and 'pcm' (s16le raw).
        """
        fmt = output_format.lower()
        logger.info("Converting %s to %s", source_path.name, fmt)

        input_stream = ffmpeg.input(str(source_path))

        if fmt == "aac":
            stream = input_stream.output(
                str(output_path),
                acodec="aac",
                audio_bitrate="192k",
            ).overwrite_output()
        elif fmt == "wav":
            stream = input_stream.output(
                str(output_path),
                format="wav",
                acodec="pcm_s16le",
            ).overwrite_output()
        elif fmt in {"pcm", "s16le", "raw"}:
            stream = input_stream.output(
                str(output_path),
                format="s16le",
                acodec="pcm_s16le",
                ac=1,
                ar=self.settings.output_sample_rate,
            ).overwrite_output()
        else:
            raise FFmpegError(f"Unsupported output format: {output_format}")

        self._run_ffmpeg(stream, output_path)
        return output_path

    def split_audio_segment(
        self,
        pcm_path: Path,
        start_sec: float,
        duration_sec: float,
        output_path: Path,
    ) -> Path:
        """Extract a time-based segment from raw PCM audio."""
        sample_rate = self.settings.input_sample_rate
        bytes_per_sample = 2
        start_byte = int(start_sec * sample_rate * bytes_per_sample)
        # Align to sample boundary
        start_byte -= start_byte % bytes_per_sample
        num_bytes = int(duration_sec * sample_rate * bytes_per_sample)
        num_bytes -= num_bytes % bytes_per_sample

        data = pcm_path.read_bytes()
        segment = data[start_byte : start_byte + num_bytes]
        if not segment:
            raise FFmpegError(
                f"Empty segment at start={start_sec}s duration={duration_sec}s"
            )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(segment)
        return output_path

    def concat_pcm_files(
        self,
        pcm_paths: Sequence[Path],
        output_path: Path,
        sample_rate: int | None = None,
    ) -> Path:
        """Concatenate multiple raw PCM files into one."""
        rate = sample_rate or self.settings.output_sample_rate
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if len(pcm_paths) == 1:
            shutil.copy2(pcm_paths[0], output_path)
            return output_path

        # Use ffmpeg concat demuxer for reliability across segment boundaries
        list_file = output_path.parent / f"{output_path.stem}_concat_list.txt"
        try:
            with list_file.open("w", encoding="utf-8") as handle:
                for path in pcm_paths:
                    # Convert each PCM segment to WAV for concat demuxer
                    wav_path = path.with_suffix(".wav")
                    if not wav_path.exists() or wav_path.stat().st_mtime < path.stat().st_mtime:
                        self.convert_audio_format(path, wav_path, "wav")
                    escaped = str(wav_path.resolve()).replace("'", "'\\''")
                    handle.write(f"file '{escaped}'\n")

            stream = (
                ffmpeg.input(str(list_file), format="concat", safe=0)
                .output(
                    str(output_path),
                    format="s16le",
                    acodec="pcm_s16le",
                    ac=1,
                    ar=rate,
                )
                .overwrite_output()
            )
            self._run_ffmpeg(stream, output_path)
        finally:
            if list_file.exists():
                list_file.unlink()

        return output_path

    def pcm_to_wav(self, pcm_path: Path, wav_path: Path, sample_rate: int) -> Path:
        """Wrap raw PCM in a WAV container."""
        stream = (
            ffmpeg.input(
                str(pcm_path),
                format="s16le",
                acodec="pcm_s16le",
                ac=1,
                ar=sample_rate,
            )
            .output(str(wav_path), format="wav")
            .overwrite_output()
        )
        self._run_ffmpeg(stream, wav_path)
        return wav_path

    def resample_pcm(
        self,
        pcm_path: Path,
        output_path: Path,
        source_rate: int,
        target_rate: int,
    ) -> Path:
        """Resample raw PCM from source_rate to target_rate."""
        stream = (
            ffmpeg.input(
                str(pcm_path),
                format="s16le",
                acodec="pcm_s16le",
                ac=1,
                ar=source_rate,
            )
            .output(
                str(output_path),
                format="s16le",
                acodec="pcm_s16le",
                ac=1,
                ar=target_rate,
            )
            .overwrite_output()
        )
        self._run_ffmpeg(stream, output_path)
        return output_path
