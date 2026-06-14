import { Video } from "lucide-react";
import type { JobDetails } from "../../types";
import StatusBadge from "../StatusBadge";

interface VideoPreviewProps {
  selectedJobId: string | null;
  selectedJob: JobDetails | null;
}

export default function VideoPreview({
  selectedJobId,
  selectedJob
}: VideoPreviewProps) {
  return (
    <div className="glass-panel-glow rounded-2xl p-6 flex flex-col gap-5">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Video className="w-5 h-5 text-indigo-400" />
          <h2 className="text-lg font-bold text-white">Source Video Preview</h2>
        </div>
        {selectedJob && <StatusBadge status={selectedJob.status} />}
      </div>

      {selectedJobId && (
        <div className="relative aspect-video rounded-xl overflow-hidden bg-black border border-slate-800 shadow-inner">
          <video
            src={`/api/jobs/${selectedJobId}/video/original`}
            controls
            className="w-full h-full object-contain"
          />
        </div>
      )}

      {selectedJob && (
        <div className="flex flex-col gap-1 text-xs text-slate-400 border-t border-slate-800 pt-4">
          <div>
            Source: <strong className="text-white">{selectedJob.video_file}</strong>
          </div>
          <div>
            Target Language: <strong className="text-white uppercase">{selectedJob.target_language}</strong>
          </div>
        </div>
      )}
    </div>
  );
}
