# Manual ElevenLabs TTS Dubbing Guide

This guide explains how to use the semi-manual dubbing workflow in this application. This mode allows you to transcribe and translate video audio, manually generate high-quality voiceovers (TTS) using the ElevenLabs platform, and stitch/mix them back with the original background music.

---

## 🚀 Step-by-Step Workflow

### Step 1: Prepare the Video
To separate vocals/background audio, transcribe, translate, and slice vocal segments for reference, run:
```bash
python run.py --prepare --file input/your_video.mp4 --language bn
```

*Replace `input/your_video.mp4` with your source file and `bn` with your target language code (e.g., `bn`, `hi`, `ur`, `es`, `ar`, `en`, `de`).*

---

### Step 2: Review Translations & Listen to Original Audio
After Step 1 completes, a folder will be created:
`processing/<video_name>_<job_id>/`

Inside this folder, you will find:
1. **`translations.json`**: Open this file to see each segment's start/end times, original text, and translated text.
2. **`extracted_vocals/`**: Contains original sliced audio clips for each segment (e.g., `segment_000_original.wav`). Use these to listen to the original speech inflection.

---

### Step 3: Automatically Generate TTS using Gemini
To automatically generate speech segments for the entire video using Gemini's native voice mapping, run:
```bash
python run.py --gemini-tts --job-id <job_id>
```
* This reads `translations.json` and generates WAV audio files inside the `manual_tts/` folder.
* **Gender-Aware Voices**: By default, segments are generated using the female voice `Achernar`. If you want specific segments in a male voice, open `translations.json` and change `"gender": "female"` to `"gender": "male"` for those segments *before* running this command. It will use the `Fenrir` male voice.
* **Non-Verbal Preservation**: The command will automatically skip generating audio for segments with `"status": "keep_original"` (preserving original laughter, sighs, grunts, etc.).

---

### Step 4: Review and Manually Update Specific Segments
If any generated audio segment does not sound natural:
1. Go to the [ElevenLabs Reader/Synthesis Platform](https://elevenlabs.io) or another TTS engine.
2. Generate the TTS audio segment for the translation.
3. Download it and place it in the `manual_tts/` folder (e.g., `segment_003.wav` or `segment_003.mp3`), overwriting the automatically generated file.
* *Note: Re-running `--gemini-tts` will skip any segments that already have an audio file in `manual_tts/`, preserving your manual overrides.*

---

### Step 5: Stitch and Mux Final Video
Once all expected files are saved in `manual_tts/`, stitch the segments back together and mix them with the background track by running:
```bash
python run.py --stitch --job-id <job_id>
```

The final video will be generated and saved in your output folder:
`output/<video_name>_dubbed_<language>.mp4`

---

## 💡 Tips & Useful Commands

### Resume / Skip Processing
If you need to stop and resume preparation later, the `--prepare` command will automatically skip Demucs stem separation and API translations if they are already present:
```bash
python run.py --prepare --job-id <job_id>
```

### Re-generating a Specific Segment
If a generated TTS segment (e.g., segment 3) does not sound right:
1. Re-generate the audio on ElevenLabs.
2. Replace `manual_tts/segment_003.wav` (or `.mp3`) with the new file.
3. Rerun the stitch command:
   ```bash
   python run.py --stitch --job-id <job_id>
   ```
*Reprocessing is near-instantaneous since it skips transcription, translation, and separation steps.*

---

## ⚡ Automated Alternative: One-Command Gemini 3.5 Live Translate

If you do not want to go through the multi-step manual ElevenLabs/TTS process and instead want to dub a video in a single step using the **Gemini 3.5 Live Translate** model, you can use the dedicated `--live-translate` command.

This command will automatically scan the input folder (or look at a specific file), stream the audio through the real-time Gemini Live Translate model, and output the stitched video directly.

### Dub all videos in the input folder:
```bash
python run.py --live-translate --language bn
```

### Dub a single specific video file:
```bash
python run.py --live-translate --file input/your_video.mp4 --language bn
```
*Note: This mode is fully automated, bypassing ElevenLabs entirely and completing the task from input to output in one go.*
