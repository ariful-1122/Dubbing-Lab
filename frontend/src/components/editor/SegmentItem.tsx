import { Clock, User, Pause, Play, Upload } from "lucide-react";
import type { Segment } from "../../types";
import { formatTime } from "../../utils/format";

interface SegmentItemProps {
  segment: Segment;
  isPlayingOriginal: boolean;
  isPlayingDubbed: boolean;
  onPlayOriginal: () => void;
  onPlayDubbed: () => void;
  onUpdateField: (field: keyof Segment, value: any) => void;
  onUploadAudio: (file: File) => void;
}

export default function SegmentItem({
  segment,
  isPlayingOriginal,
  isPlayingDubbed,
  onPlayOriginal,
  onPlayDubbed,
  onUpdateField,
  onUploadAudio
}: SegmentItemProps) {
  return (
    <div className="glass-panel p-5 rounded-xl border border-slate-900/60 hover:border-slate-800 transition flex flex-col gap-4">
      {/* Segment Header */}
      <div className="flex items-center justify-between border-b border-slate-900 pb-2">
        <span className="text-xs font-bold text-indigo-400">SEGMENT #{segment.id}</span>
        <div className="flex items-center gap-2 text-xs text-slate-500">
          <Clock className="w-3.5 h-3.5" />
          <span>
            {formatTime(segment.start)} - {formatTime(segment.end)}
          </span>
          <span>({segment.duration.toFixed(2)}s)</span>
        </div>
      </div>

      {/* Original Text Transcript */}
      <div className="text-xs italic text-slate-400 bg-slate-950/40 p-3 rounded-lg border border-slate-900">
        {segment.original_text || "(Silent segment / background vocals)"}
      </div>

      {/* Editable Translated text */}
      <div className="flex flex-col gap-1.5">
        <label className="text-[10px] font-bold tracking-wider text-slate-500 uppercase">TRANSLATED SPEECH TEXT</label>
        <textarea
          value={segment.translated_text}
          onChange={(e) => onUpdateField("translated_text", e.target.value)}
          rows={2}
          className="bg-slate-950/60 border border-slate-800 focus:border-indigo-500 rounded-lg p-2.5 text-sm text-white outline-none w-full resize-none transition"
          placeholder="Insert target language text here..."
        />
      </div>

      {/* Speaker Gender & Action status settings */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div className="flex flex-col gap-1.5">
          <label className="text-[10px] font-bold tracking-wider text-slate-500 uppercase">SPEAKER GENDER</label>
          <div className="flex bg-slate-950 p-0.5 rounded-lg border border-slate-900">
            <button
              onClick={() => onUpdateField("gender", "female")}
              className={`flex-1 py-1 rounded text-xs font-semibold flex items-center justify-center gap-1.5 transition ${
                segment.gender.toLowerCase() === "female"
                  ? "bg-indigo-600 text-white"
                  : "text-slate-400 hover:text-white"
              }`}
            >
              <User className="w-3.5 h-3.5" />
              Female
            </button>
            <button
              onClick={() => onUpdateField("gender", "male")}
              className={`flex-1 py-1 rounded text-xs font-semibold flex items-center justify-center gap-1.5 transition ${
                segment.gender.toLowerCase() === "male"
                  ? "bg-indigo-600 text-white"
                  : "text-slate-400 hover:text-white"
              }`}
            >
              <User className="w-3.5 h-3.5" />
              Male
            </button>
          </div>
        </div>

        <div className="flex flex-col gap-1.5">
          <label className="text-[10px] font-bold tracking-wider text-slate-500 uppercase">TIMELINE ACTION</label>
          <select
            value={segment.status}
            onChange={(e) => onUpdateField("status", e.target.value)}
            className="bg-slate-950 border border-slate-900 text-xs rounded-lg block w-full p-2 text-white outline-none transition"
          >
            <option value="pending">Generate Voice-Over (TTS)</option>
            <option value="keep_original">Keep Original Sound</option>
            <option value="completed">Completed (Stitched)</option>
          </select>
        </div>
      </div>

      {/* Audios preview and custom upload overrides */}
      <div className="flex flex-wrap items-center justify-between gap-4 border-t border-slate-900 pt-3">
        <div className="flex items-center gap-2">
          {/* Play Original */}
          <button
            onClick={onPlayOriginal}
            className={`px-2.5 py-1.5 rounded-lg border text-xs font-semibold flex items-center gap-1.5 transition ${
              isPlayingOriginal
                ? "bg-amber-600/10 border-amber-500 text-amber-400"
                : "bg-slate-900 border-slate-800 text-slate-300 hover:text-white"
            }`}
          >
            {isPlayingOriginal ? (
              <>
                <Pause className="w-3.5 h-3.5 fill-amber-400" />
                Playing Original
              </>
            ) : (
              <>
                <Play className="w-3.5 h-3.5 fill-slate-300" />
                Play Original
              </>
            )}
          </button>

          {/* Play Dubbed */}
          <button
            onClick={onPlayDubbed}
            disabled={segment.status === "keep_original"}
            className={`px-2.5 py-1.5 rounded-lg border text-xs font-semibold flex items-center gap-1.5 transition ${
              isPlayingDubbed
                ? "bg-emerald-600/10 border-emerald-500 text-emerald-400 glow-emerald"
                : "bg-slate-900 border-slate-800 text-slate-300 hover:text-white disabled:opacity-40"
            }`}
          >
            {isPlayingDubbed ? (
              <>
                <Pause className="w-3.5 h-3.5 fill-emerald-400" />
                Playing TTS
              </>
            ) : (
              <>
                <Play className="w-3.5 h-3.5 fill-slate-300" />
                Play TTS / Dub
              </>
            )}
          </button>
        </div>

        {/* Custom segment audio upload */}
        {segment.status !== "keep_original" && (
          <div className="flex items-center gap-2">
            <input
              type="file"
              id={`audio-upload-${segment.id}`}
              className="hidden"
              accept="audio/*"
              onChange={(e) => e.target.files && onUploadAudio(e.target.files[0])}
            />
            <label
              htmlFor={`audio-upload-${segment.id}`}
              className="px-2.5 py-1.5 rounded-lg border border-slate-800 hover:border-slate-700 bg-slate-950 text-slate-400 hover:text-white text-xs font-semibold flex items-center gap-1.5 cursor-pointer transition"
              title="Upload custom audio clip for this segment"
            >
              <Upload className="w-3.5 h-3.5" />
              Custom Audio
            </label>
          </div>
        )}
      </div>
    </div>
  );
}
