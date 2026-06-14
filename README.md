# 🎙️ Gemini Video Dubbing Application (Dubbing Lab)

Automatically translate and dub video files into other languages using Google's state-of-the-art **Gemini Live Translate API** (`gemini-3.5-live-translate-preview`) and other local speech tools. 

Dubbing Lab includes both a **command-line interface** and an **interactive Web Dashboard/Editor UI** to manage, review, and fine-tune your dubbing projects locally.

---

## 🚀 Key Features

* **Interactive Web Dashboard & Editor**: An HSL-tailored, modern React-based UI to upload videos, configure dubbing workflows, listen to/verify extracted vocal segments, manually override translations, and trigger synthetic voice generation step-by-step.
* **Real-time Live Translation**: Streams audio directly to the Gemini Live Translate WebSocket API in 100ms chunks to produce immediate vocal translations.
* **ElevenLabs Voice Cloning**: Automatically extracts original speaker vocal clips, clones the voice signature via ElevenLabs, and generates dubbed audio in the speaker's own voice.
* **Local Background Separation**: Utilizes Demucs to isolate background music and sound effects, mixing them back into the final dubbed video at customizable volumes.
* **Timing & Drift Alignment**: Automatically adjusts translated audio tempo using FFmpeg if duration drift exceeds a defined threshold (preventing overlaps and sync issues).
* **Multi-Step Manual Dubbing Workflow**: Allows preparing vocal segments, editing translations in a `translations.json` file (or directly in the UI), customizing genders, selecting specific local Edge TTS or Gemini TTS voices, and compiling manually.
* **Large Video Segmentation**: Splits videos longer than 10 minutes into smaller segments to comply with API session limits, translating them in parallel or sequence, and stitching them seamlessly.

---

## 🔄 Core Workflows & Pipelines

The application determines its operational flow based on your environment keys, CLI flags, or settings chosen in the Web UI:

### 1. Fully Automated Gemini Live (Default / Live Mode)
* **Trigger**: Defaults when running the app without ElevenLabs keys, or by specifying the `--live-translate` flag.
* **How it works**: Audio is extracted as mono 16 kHz PCM and streamed to `gemini-3.5-live-translate-preview`. Dubbed 24 kHz audio is received, checked for duration drift, tempo-corrected if needed, normalized, and muxed back into the video.
* **Best for**: Rapid, low-latency automated translation.

### 2. Automated Whisper + ElevenLabs Voice-Cloning
* **Trigger**: Occurs automatically if `ELEVENLABS_API_KEY` is present in your `.env` file and `--live-translate` is not set.
* **How it works**: Splits audio into vocal and background tracks using `demucs`. Transcribes vocals locally via Whisper (`faster-whisper`), translates transcripts via Gemini, and uses a 15-second vocal snippet to clone the speaker's voice on ElevenLabs. Cloned voice generates the dubbed track, which is mixed with the original background music and muxed back.
* **Best for**: Premium content where keeping the original speaker's voice is critical.

### 3. Semi-Manual Multi-Step TTS Pipeline
* **Trigger**: Commanded via CLI flags (`--prepare`, `--gemini-tts` / `--edge-tts`, and `--stitch`) or executed step-by-step in the **Web Editor Dashboard**.
* **How it works**: Separates and transcribes the audio into a `translations.json` file. The user reviews and manually edits translations, speaker voice gender, or selects parts to keep original. Next, the user generates synthetic voices (using Gemini or Edge TTS) and compiles/stitches the segments into the final video.
* **Best for**: Maximum quality assurance and full control over every single translated line.

---

## 📦 Prerequisites

1. **Python 3.10+**
2. **Node.js 18+** (Optional, required if building or developing the frontend React UI)
3. **FFmpeg** and **FFprobe** installed and available on your system `PATH`.
4. **Gemini API key** from [Google AI Studio](https://aistudio.google.com/).

### Installing FFmpeg

| Operating System | Command |
|---|---|
| **Windows** | Run `winget install FFmpeg` or download from [ffmpeg.org](https://ffmpeg.org/download.html) |
| **macOS** | Run `brew install ffmpeg` |
| **Linux (Debian/Ubuntu)** | Run `sudo apt update && sudo apt install ffmpeg` |

Verify FFmpeg is correctly installed by running:
```bash
ffmpeg -version
ffprobe -version
```

---

## 🛠️ Installation & Setup

1. Clone or navigate to the project root directory:
   ```bash
   cd DUB_SOFT
   ```

2. Create and activate a virtual environment:
   ```bash
   # Create virtual environment
   python -m venv .venv

   # Activate on Linux/macOS
   source .venv/bin/activate
   # Activate on Windows (PowerShell)
   .venv\Scripts\Activate.ps1
   # Activate on Windows (Command Prompt)
   .venv\Scripts\activate.bat
   ```

3. Install all required Python dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Configure environment settings:
   ```bash
   # Copy example environment configuration
   cp .env.example .env
   ```
   Open the `.env` file and add your `GEMINI_API_KEY`.

---

## 🎮 Operations Guide

### 1. Launching the Web UI (Recommended)

You can run the application in two different web modes depending on whether you want to build the static UI bundle or develop with live hot-reloading.

#### Option A: Standalone Launcher (Single command, Port 8000)
Runs the entire web application on a single port. If not already built, this compiles the React files and serves them from FastAPI:
```bash
python run_web.py
```
*Pass `--force-build` if you want to explicitly rebuild the frontend assets.*

#### Option B: Live Development Mode (For code edits and hot-reloading)
Start the backend API and frontend dev server in separate terminal windows:
* **Terminal 1 (Backend)**:
  ```bash
  .venv\Scripts\activate
  uvicorn app.api:app --reload --port 8000
  ```
* **Terminal 2 (Frontend)**:
  ```bash
  cd frontend
  npm install
  npm run dev
  ```
Open **`http://localhost:5173`** in your browser. All API requests starting with `/api` are automatically proxied to the backend running on port 8000.

---

### 2. CLI Core Workflows

* **Process All Videos in Input Directory**:
  Scan `input/` folder, dub all supported files, and exit:
  ```bash
  python run.py
  ```

* **Process a Single Specific File**:
  ```bash
  python run.py --file input/sample.mp4
  ```

* **Force Live Translation Mode**:
  By-passes ElevenLabs voice cloning, translating directly using Gemini Live Translate WebSocket:
  ```bash
  python run.py --live-translate --file input/sample.mp4
  ```

* **Override Target Language**:
  Specify a language code at runtime to override `.env` defaults:
  ```bash
  python run.py --language hi
  python run.py --file input/sample.mp4 --language es
  ```

* **Semi-Manual Workflow via CLI**:
  For detailed control over each step without the Web UI:
  1. **Prepare**: `python run.py --prepare --file input/sample.mp4 --language bn`
  2. **Edit**: Modify `translations.json` inside the generated processing folder.
  3. **TTS**: Generate speech using `python run.py --gemini-tts --job-id <job_id>` or `python run.py --edge-tts --job-id <job_id>`
  4. **Stitch**: Mix and compile the final video using `python run.py --stitch --job-id <job_id>`

---

## ⚙️ Configuration (`.env`)

Configure settings in `.env` to customize paths, translation models, and backend options:

| Variable | Default Value | Description |
|---|---|---|
| `GEMINI_API_KEY` | *(required)* | Your Google Gemini API Key. |
| `TARGET_LANGUAGE` | `bn` | Target language BCP-47 code (e.g., `bn`, `hi`, `ur`, `es`, `ar`). |
| `INPUT_DIR` | `input` | Folder scanned for source video files. |
| `OUTPUT_DIR` | `output` | Folder where dubbed output videos are saved. |
| `PROCESSING_DIR` | `processing` | Temporary storage for active files, audio slices, and metadata. |
| `FAILED_DIR` | `failed` | Backup location for files from failed runs. |
| `FFMPEG_BIN_DIR` | `None` | Custom path to FFmpeg binary directory (e.g., `C:\ffmpeg\bin`), automatically loaded if set. |
| `ELEVENLABS_API_KEY` | `None` | (Optional) Your ElevenLabs API key for voice cloning. |
| `ELEVENLABS_VOICE_ID` | `None` | (Optional) ElevenLabs voice ID to use instead of automatic cloning. |
| `DEMUCS_MODEL` | `htdemucs` | Vocal separation model. Options: `htdemucs` (faster), `htdemucs_ft` (slower, higher quality). |
| `DEMUCS_SHIFTS` | `0` | Demucs shift separation precision. Set to `2` or `4` for improved isolation (slower). |
| `BACKGROUND_VOLUME` | `0.5` | Mixing volume level (0.0 to 1.0) of background audio tracks. |
| `GEMINI_TTS_MODEL` | `gemini-3.1-flash-tts-preview` | Model used for Gemini TTS generation. |
| `GEMINI_TTS_FEMALE_VOICE` | `Achernar` | Default female voice for Gemini TTS. |
| `GEMINI_TTS_MALE_VOICE` | `Fenrir` | Default male voice for Gemini TTS. |

### Supported Languages (BCP-47)
The application validates BCP-47 codes on startup. Supported default languages:
* **Bengali (`bn`)**
* **Hindi (`hi`)**
* **Urdu (`ur`)**
* **Spanish (`es`)**
* **Arabic (`ar`)**

*To support additional languages, append BCP-47 language codes to `SUPPORTED_LANGUAGES` in [app/config.py](file:///f:/DUB_SOFT/app/config.py).*

---

## 📂 Project Structure Map

```
DUB_SOFT/
├── app/
│   ├── api.py              # FastAPI endpoints and static web routing
│   ├── config.py           # Settings loader & path resolver
│   ├── models.py           # Job structures & Pydantic models
│   ├── logger.py           # JSON logging utility
│   ├── ffmpeg_service.py   # FFmpeg commands wrapper
│   ├── gemini_service.py   # Translation provider facade
│   ├── dubbing_service.py  # Pipeline orchestrator & step managers
│   ├── watcher.py          # Watchdog folder watcher (idle)
│   ├── main.py             # CLI parser and command router
│   └── providers/
│       ├── base.py         # Abstract SpeechTranslationProvider interface
│       ├── gemini_live.py  # WebSocket streaming wrapper
│       └── whisper_elevenlabs.py # Automated Voice Cloning backend
├── frontend/               # React SPA Web UI client
│   ├── src/                # React components and hooks
│   ├── dist/               # Static compiled JS/HTML bundle served by FastAPI
│   └── vite.config.ts      # Vite dev server and proxy definitions
├── input/                  # Place video files here
├── output/                 # Dubbed videos generated here
├── processing/             # Active job work folders
├── failed/                 # Backup directories for failed tasks
├── logs/                   # System runtime logs (app.log)
├── .env.example            # Example configuration
├── requirements.txt        # Package dependencies
├── run.py                  # CLI entry script
├── run_web.py              # Web app launcher (build + serve)
├── MANUAL_TTS_GUIDE.md     # Multi-step TTS operation guide
├── Agents.md               # AI coding agent developer guide
└── README.md               # Main software guide
```

---

## 🔍 Troubleshooting & FAQs

### Error: `GEMINI_API_KEY is not set`
Copy `.env.example` to `.env` and fill in your API key from Google AI Studio. Ensure you are running commands in the project root containing your `.env` file.

### Error: `ffmpeg not found on PATH`
If FFmpeg is installed but the command fails:
1. Make sure to restart your terminal after installing.
2. If FFmpeg is placed in a custom folder, add its `bin` location to the `FFMPEG_BIN_DIR` variable in `.env` (e.g. `FFMPEG_BIN_DIR=C:\ffmpeg\bin`).

### Output video has empty or silent audio
* Confirm your source video actually has sound.
* Check the logs in the Web UI log terminal or `logs/app.log` for any API failures or service exceptions.
* Check that your `TARGET_LANGUAGE` is different from the source spoken language.

### Mismatched audio durations or speed changes
Slight alterations in video speech timing are normal when translating languages since some translations take more words. If the drift is greater than `SYNC_THRESHOLD_SECONDS`, FFmpeg tempo filters (`atempo`) are automatically applied to scale output vocals to fit the timeline.

---

## 📜 License

This project is licensed under the MIT License.
