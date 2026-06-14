import { Music } from "lucide-react";
import type { JobDetails, Segment } from "../../types";
import SegmentItem from "./SegmentItem";

interface SegmentListProps {
  selectedJob: JobDetails | null;
  playingAudio: { segmentId: number; type: "original" | "dubbed" } | null;
  onPlayAudio: (segmentId: number, type: "original" | "dubbed") => void;
  onUpdateSegmentField: (segmentId: number, field: keyof Segment, value: any) => void;
  onCustomAudioUpload: (segmentId: number, file: File) => void;
}

export default function SegmentList({
  selectedJob,
  playingAudio,
  onPlayAudio,
  onUpdateSegmentField,
  onCustomAudioUpload
}: SegmentListProps) {
  const segments = selectedJob?.segments || [];

  return (
    <div className="xl:col-span-7 flex flex-col gap-6 h-full max-h-[85vh]">
      <div className="glass-panel-glow rounded-2xl p-6 flex flex-col gap-4 h-full overflow-hidden">
        <div className="flex items-center justify-between border-b border-slate-800 pb-4">
          <h2 className="text-lg font-bold text-white flex items-center gap-2">
            <Music className="w-5 h-5 text-indigo-400" />
            Translations Timeline Editor
          </h2>
          <span className="text-xs text-slate-400 font-semibold bg-slate-900 px-2.5 py-1 rounded-full border border-slate-800">
            {segments.length} Segment(s)
          </span>
        </div>

        {segments.length === 0 ? (
          <div className="text-center py-20 text-slate-500">
            No segments transcribed for this video.
          </div>
        ) : (
          <div className="flex-1 overflow-y-auto pr-1 flex flex-col gap-4">
            {segments.map((seg) => {
              const isOriginalPlaying =
                playingAudio?.segmentId === seg.id && playingAudio?.type === "original";
              const isDubbedPlaying =
                playingAudio?.segmentId === seg.id && playingAudio?.type === "dubbed";

              return (
                <SegmentItem
                  key={seg.id}
                  segment={seg}
                  isPlayingOriginal={isOriginalPlaying}
                  isPlayingDubbed={isDubbedPlaying}
                  onPlayOriginal={() => onPlayAudio(seg.id, "original")}
                  onPlayDubbed={() => onPlayAudio(seg.id, "dubbed")}
                  onUpdateField={(field, value) => onUpdateSegmentField(seg.id, field, value)}
                  onUploadAudio={(file) => onCustomAudioUpload(seg.id, file)}
                />
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
