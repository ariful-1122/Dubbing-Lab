import { Settings, RefreshCw, Check } from "lucide-react";

interface WorkflowControlsProps {
  ttsEngine: string;
  setTtsEngine: (engine: string) => void;
  onTriggerTTS: () => void;
  onTriggerStitch: () => void;
  editorActionRunning: boolean;
  selectedJobStatus?: string;
}

export default function WorkflowControls({
  ttsEngine,
  setTtsEngine,
  onTriggerTTS,
  onTriggerStitch,
  editorActionRunning,
  selectedJobStatus
}: WorkflowControlsProps) {
  const isJobProcessing = !!(selectedJobStatus && ["stitching", "processing"].includes(selectedJobStatus));

  return (
    <div className="glass-panel-glow rounded-2xl p-6 flex flex-col gap-5">
      <h2 className="text-lg font-bold text-white flex items-center gap-2">
        <Settings className="w-5 h-5 text-indigo-400" />
        Workflow Controls
      </h2>

      {/* Voice Generation Engine */}
      <div className="flex flex-col gap-2">
        <label className="text-xs font-semibold text-slate-400">TTS SYNTHESIS ENGINE</label>
        <div className="grid grid-cols-2 gap-3">
          <button
            onClick={() => setTtsEngine("edge-tts")}
            className={`py-2 px-3 rounded-lg border text-xs font-bold transition ${
              ttsEngine === "edge-tts"
                ? "bg-indigo-600/10 border-indigo-500 text-indigo-400"
                : "bg-slate-950 border-slate-800 text-slate-400 hover:bg-slate-900/30"
            }`}
          >
            Microsoft Edge (Local)
          </button>
          <button
            onClick={() => setTtsEngine("gemini-tts")}
            className={`py-2 px-3 rounded-lg border text-xs font-bold transition ${
              ttsEngine === "gemini-tts"
                ? "bg-indigo-600/10 border-indigo-500 text-indigo-400"
                : "bg-slate-950 border-slate-800 text-slate-400 hover:bg-slate-900/30"
            }`}
          >
            Gemini Flash TTS (API)
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mt-2">
        {/* TTS Button */}
        <button
          onClick={onTriggerTTS}
          disabled={editorActionRunning || isJobProcessing}
          className="py-3 px-4 rounded-xl bg-slate-900 hover:bg-slate-800 border border-slate-800 hover:border-slate-700 disabled:opacity-50 text-white font-bold text-xs tracking-wider transition flex items-center justify-center gap-2 cursor-pointer"
        >
          <RefreshCw className="w-3.5 h-3.5" />
          Generate TTS Audios
        </button>

        {/* Stitch Button */}
        <button
          onClick={onTriggerStitch}
          disabled={editorActionRunning || isJobProcessing}
          className="py-3 px-4 rounded-xl bg-gradient-to-tr from-indigo-600 to-indigo-500 hover:from-indigo-500 hover:to-indigo-400 disabled:opacity-50 text-white font-bold text-xs tracking-wider transition shadow-lg shadow-indigo-500/10 flex items-center justify-center gap-2 cursor-pointer"
        >
          <Check className="w-3.5 h-3.5" />
          Stitch & Compile Video
        </button>
      </div>
    </div>
  );
}
