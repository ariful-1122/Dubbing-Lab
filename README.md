# Gemini Video Dubbing Application

Automatically dub videos into another language using Google's **Gemini Live Translate API** (`gemini-3.5-live-translate-preview`). Drop a video into the `input/` folder and receive a dubbed version in `output/`.

Designed for **local desktop usage** — no authentication, cloud deployment, or multi-user support required.

## Features

- Watches an input folder for new video files (mp4, mkv, mov, avi, webm)
- Extracts audio with FFmpeg
- Translates speech via Gemini Live API (speech-to-speech streaming)
- Preserves timing with automatic tempo correction when needed
- Muxes translated audio back into the video
- Structured logging to `logs/app.log`
- Provider abstraction for future speech backends (OpenAI, ElevenLabs, Azure, etc.)

## Prerequisites

- **Python 3.10+**
- **FFmpeg** and **FFprobe** on your system PATH
- **Gemini API key** from [Google AI Studio](https://aistudio.google.com/)

### Installing FFmpeg

| Platform | Command |
|----------|---------|
| Windows | `winget install FFmpeg` or download from [ffmpeg.org](https://ffmpeg.org/download.html) |
| macOS | `brew install ffmpeg` |
| Linux (Debian/Ubuntu) | `sudo apt install ffmpeg` |

Verify installation:

```bash
ffmpeg -version
ffprobe -version
```

## Installation

```bash
# Clone or navigate to the project directory
cd DUB_SOFT

# Create a virtual environment (recommended)
python -m venv .venv
source .venv/bin/activate   # Linux/macOS
# .venv\Scripts\activate    # Windows

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env and set GEMINI_API_KEY
```

## Environment Variables

Copy `.env.example` to `.env` and configure:

| Variable | Default | Description |
|----------|---------|-------------|
| `GEMINI_API_KEY` | *(required)* | Your Google Gemini API key |
| `TARGET_LANGUAGE` | `bn` | BCP-47 language code for dubbing output |
| `INPUT_DIR` | `input` | Folder to watch for new videos |
| `OUTPUT_DIR` | `output` | Folder for dubbed videos |
| `PROCESSING_DIR` | `processing` | Temporary working directory |
| `FAILED_DIR` | `failed` | Failed jobs and artifacts |
| `MAX_RETRIES` | `3` | API retry attempts |
| `SYNC_THRESHOLD_SECONDS` | `0.5` | Max duration drift before tempo correction |
| `MAX_SEGMENT_SECONDS` | `600` | Audio segment size for long videos (10 min) |
| `LOG_LEVEL` | `INFO` | Logging verbosity |

### Supported Languages

| Language | Code |
|----------|------|
| Bengali | `bn` |
| Hindi | `hi` |
| Urdu | `ur` |
| Spanish | `es` |
| Arabic | `ar` |

Gemini Live Translate supports 70+ languages; extend `SUPPORTED_LANGUAGES` in `app/config.py` to add more.

## Usage

### Start the folder watcher

```bash
python run.py
```

Place video files in the `input/` folder. Dubbed videos appear in `output/` as `{name}_dubbed_{lang}.mp4`.

### Process a single file

```bash
python run.py --file path/to/video.mp4
```

### Override target language

```bash
python run.py --language hi
python run.py --file sample.mp4 --language es
```

## Architecture

```
input/ ──► watcher.py ──► dubbing_service.py ──► output/
                              │
                    ┌─────────┼─────────┐
                    ▼         ▼         ▼
            ffmpeg_service  gemini_service  logger
                              │
                    gemini_live.py (Live API)
                              │
                    SpeechTranslationProvider (ABC)
```

### Processing Pipeline

1. Move video to `processing/`
2. Extract mono 16 kHz PCM audio (FFmpeg)
3. Stream audio to Gemini Live Translate API in 100 ms chunks
4. Collect translated 24 kHz PCM output
5. Compare original vs translated duration
6. Apply tempo correction if drift exceeds threshold
7. Normalize audio and convert to AAC
8. Mux translated audio into video (copy video stream)
9. Save to `output/`; failed jobs go to `failed/`

### Provider Pattern

Speech translation is behind an abstract `SpeechTranslationProvider` interface. The current implementation uses **Gemini Live API** WebSocket streaming (not a batch upload endpoint). Future providers can be swapped in via dependency injection without changing the dubbing pipeline.

### Long Videos

Gemini Live API sessions are limited to ~15 minutes. Videos longer than `MAX_SEGMENT_SECONDS` (default 10 minutes) are split into segments, translated in separate sessions, and concatenated.

## Project Structure

```
project/
├── app/
│   ├── config.py           # Pydantic settings
│   ├── models.py           # Job and result models
│   ├── logger.py           # Structured logging
│   ├── providers/
│   │   ├── base.py         # Abstract provider interface
│   │   └── gemini_live.py  # Gemini Live implementation
│   ├── ffmpeg_service.py   # FFmpeg utilities
│   ├── gemini_service.py   # Provider facade
│   ├── dubbing_service.py  # Pipeline orchestrator
│   ├── watcher.py          # Folder monitoring
│   └── main.py             # CLI entry
├── input/                  # Drop videos here
├── output/                 # Dubbed videos
├── processing/             # In-progress jobs
├── failed/                 # Failed jobs
├── logs/                   # Application logs
├── .env.example
├── requirements.txt
├── README.md
└── run.py
```

## Troubleshooting

### `GEMINI_API_KEY is not set`

Create a `.env` file from `.env.example` and add your API key from [Google AI Studio](https://aistudio.google.com/apikey).

### `ffmpeg not found on PATH`

Install FFmpeg and ensure `ffmpeg` and `ffprobe` are accessible from your terminal. Restart the terminal after installation.

### API rate limits or timeouts

The application retries with exponential backoff (`MAX_RETRIES`, `RETRY_BASE_DELAY_SECONDS`). If failures persist, wait and retry, or reduce concurrent usage (only one file is processed at a time by design).

### Empty or silent dubbed output

- Verify the source video has an audio track
- Check `logs/app.log` for `job_failed` events
- Ensure the source language differs from `TARGET_LANGUAGE` (or set `ECHO_TARGET_LANGUAGE=true` in `.env`)

### Duration sync warnings

Small timing differences are normal. If `duration_mismatch` appears in logs, tempo correction was applied automatically. Large drift may indicate very long pauses or music-heavy content.

### Long video segmentation

Videos over 10 minutes are split automatically. Check logs for `segment_translated` events to confirm all segments completed.

## License

MIT
