import { Settings, Loader2, Play } from "lucide-react";
import type { InputFile, Language } from "../../types";
import { formatBytes } from "../../utils/format";

interface JobConfigProps {
  inputFiles: InputFile[];
  languages: Language[];
  selectedVideo: string;
  setSelectedVideo: (video: string) => void;
  targetLang: string;
  setTargetLang: (lang: string) => void;
  dubbingMode: string;
  setDubbingMode: (mode: string) => void;
  startingJob: boolean;
  onStartJob: () => void;
}

export default function JobConfig({
  inputFiles,
  languages,
  selectedVideo,
  setSelectedVideo,
  targetLang,
  setTargetLang,
  dubbingMode,
  setDubbingMode,
  startingJob,
  onStartJob
}: JobConfigProps) {
  return (
    <div className="glass-panel-glow rounded-2xl p-6 flex flex-col gap-5">
      <h2 className="text-lg font-bold text-white flex items-center gap-2">
        <Settings className="w-5 h-5 text-indigo-400" />
        Configure Dubbing Job
      </h2>

      {/* Select Video */}
      <div className="flex flex-col gap-2">
        <label className="text-xs font-semibold text-slate-400">SELECT VIDEO FILE</label>
        {inputFiles.length === 0 ? (
          <div className="text-xs text-slate-500 border border-slate-800 rounded-lg p-3 bg-slate-900/30 text-center">
            No video files found. Upload one above.
          </div>
        ) : (
          <select
            value={selectedVideo}
            onChange={(e) => setSelectedVideo(e.target.value)}
            className="bg-slate-950 border border-slate-800 hover:border-slate-700 focus:border-indigo-500 text-sm rounded-lg block w-full p-2.5 text-white outline-none transition"
          >
            <option value="" disabled>Select a video...</option>
            {inputFiles.map((f) => (
              <option key={f.name} value={f.name}>
                {f.name} ({formatBytes(f.size_bytes)})
              </option>
            ))}
          </select>
        )}
      </div>

      {/* Target Language */}
      <div className="flex flex-col gap-2">
        <label className="text-xs font-semibold text-slate-400">TARGET DUBBING LANGUAGE</label>
        <select
          value={targetLang}
          onChange={(e) => setTargetLang(e.target.value)}
          className="bg-slate-950 border border-slate-800 hover:border-slate-700 focus:border-indigo-500 text-sm rounded-lg block w-full p-2.5 text-white outline-none transition"
        >
          {languages.map((l) => (
            <option key={l.code} value={l.code}>
              {l.name} ({l.code})
            </option>
          ))}
        </select>
      </div>

      {/* Dubbing Mode */}
      <div className="flex flex-col gap-2">
        <label className="text-xs font-semibold text-slate-400">DUBBING WORKFLOW MODE</label>
        <div className="flex flex-col gap-2">
          <label
            className={`flex items-start gap-3 p-3 rounded-xl border cursor-pointer transition ${
              dubbingMode === "automated"
                ? "bg-indigo-600/5 border-indigo-500/50"
                : "bg-slate-950 border-slate-800 hover:bg-slate-900/30"
            }`}
          >
            <input
              type="radio"
              name="dubbingMode"
              value="automated"
              checked={dubbingMode === "automated"}
              onChange={() => setDubbingMode("automated")}
              className="mt-1 accent-indigo-500"
            />
            <div>
              <div className="text-xs font-bold text-white">Automated Voice Cloning</div>
              <div className="text-[10px] text-slate-400 mt-0.5">Separate background, transcribe vocals, translate & clone voice using ElevenLabs.</div>
            </div>
          </label>

          <label
            className={`flex items-start gap-3 p-3 rounded-xl border cursor-pointer transition ${
              dubbingMode === "live_translate"
                ? "bg-indigo-600/5 border-indigo-500/50"
                : "bg-slate-950 border-slate-800 hover:bg-slate-900/30"
            }`}
          >
            <input
              type="radio"
              name="dubbingMode"
              value="live_translate"
              checked={dubbingMode === "live_translate"}
              onChange={() => setDubbingMode("live_translate")}
              className="mt-1 accent-indigo-500"
            />
            <div>
              <div className="text-xs font-bold text-white">Gemini Live Translate (Default)</div>
              <div className="text-[10px] text-slate-400 mt-0.5">Stream mono audio to WebSocket, receive live speech, auto-sync tempo, remux.</div>
            </div>
          </label>

          <label
            className={`flex items-start gap-3 p-3 rounded-xl border cursor-pointer transition ${
              dubbingMode === "prepare"
                ? "bg-indigo-600/5 border-indigo-500/50"
                : "bg-slate-950 border-slate-800 hover:bg-slate-900/30"
            }`}
          >
            <input
              type="radio"
              name="dubbingMode"
              value="prepare"
              checked={dubbingMode === "prepare"}
              onChange={() => setDubbingMode("prepare")}
              className="mt-1 accent-indigo-500"
            />
            <div>
              <div className="text-xs font-bold text-white">Manual Multi-Step Workflow</div>
              <div className="text-[10px] text-slate-400 mt-0.5">Extract and separate, transcribe, translate to segment files, open editor to review & voice.</div>
            </div>
          </label>
        </div>
      </div>

      {/* Submit button */}
      <button
        onClick={onStartJob}
        disabled={startingJob || !selectedVideo}
        className="w-full py-3 rounded-xl bg-gradient-to-tr from-indigo-600 to-indigo-500 hover:from-indigo-500 hover:to-indigo-400 disabled:from-slate-800 disabled:to-slate-800 text-white font-bold text-sm tracking-wide shadow-lg shadow-indigo-500/10 hover:shadow-indigo-500/20 active:scale-98 transition flex items-center justify-center gap-2 cursor-pointer"
      >
        {startingJob ? (
          <>
            <Loader2 className="w-4 h-4 animate-spin" />
            Queuing Dubbing Run...
          </>
        ) : (
          <>
            <Play className="w-4 h-4 fill-white" />
            Execute Dubbing Pipeline
          </>
        )}
      </button>
    </div>
  );
}
