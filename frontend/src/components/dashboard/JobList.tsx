import React from "react";
import { Video, RefreshCw, Music, Languages, Clock, Terminal, Edit2, Download, Trash2 } from "lucide-react";
import type { Job } from "../../types";
import StatusBadge from "../StatusBadge";

interface JobListProps {
  jobs: Job[];
  onRefresh: () => void;
  onOpenEditor: (jobId: string) => void;
  onOpenConsole: (jobId: string) => void;
  onDeleteJob: (jobId: string, event: React.MouseEvent) => void;
}

export default function JobList({
  jobs,
  onRefresh,
  onOpenEditor,
  onOpenConsole,
  onDeleteJob
}: JobListProps) {
  return (
    <div className="glass-panel-glow rounded-2xl p-6 flex flex-col gap-4">
      <div className="flex items-center justify-between border-b border-slate-800 pb-4">
        <h2 className="text-lg font-bold text-white flex items-center gap-2">
          <Video className="w-5 h-5 text-indigo-400" />
          Dubbing Runs Dashboard
        </h2>
        <button
          onClick={onRefresh}
          className="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-slate-800 transition"
          title="Refresh Jobs"
        >
          <RefreshCw className="w-4 h-4" />
        </button>
      </div>

      {jobs.length === 0 ? (
        <div className="text-center py-20 flex flex-col items-center justify-center gap-4 text-slate-500">
          <div className="w-16 h-16 rounded-full bg-slate-900 border border-slate-800 flex items-center justify-center">
            <Music className="w-6 h-6 text-slate-600" />
          </div>
          <div>
            <h3 className="text-sm font-semibold text-slate-400">No Jobs Found</h3>
            <p className="text-xs text-slate-500 mt-1 max-w-[300px]">Configure and launch a dubbing pipeline run above to see it in your dashboard.</p>
          </div>
        </div>
      ) : (
        <div className="flex flex-col gap-3 max-h-[600px] overflow-y-auto pr-1">
          {jobs.map((job) => {
            const isPrepared = job.status === "prepared";
            const isCompleted = job.status === "completed";

            return (
              <div
                key={job.job_id}
                onClick={() => isPrepared && onOpenEditor(job.job_id)}
                className={`glass-panel p-4 rounded-xl flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 border border-transparent hover:border-slate-800 transition ${
                  isPrepared ? "cursor-pointer hover:bg-slate-900/20" : ""
                }`}
              >
                {/* Info */}
                <div className="flex items-start gap-3 flex-1 min-w-0">
                  <div className="w-10 h-10 rounded-lg bg-slate-900 border border-slate-800 flex items-center justify-center flex-shrink-0 text-slate-400">
                    <Video className="w-5 h-5" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <h3 className="text-sm font-bold text-white truncate" title={job.video_file}>
                      {job.video_file}
                    </h3>
                    <div className="flex items-center gap-3 text-xs text-slate-400 mt-1">
                      <span className="flex items-center gap-1">
                        <Languages className="w-3.5 h-3.5 text-indigo-400" />
                        Target: <strong className="text-white uppercase">{job.target_language}</strong>
                      </span>
                      <span className="text-slate-600">•</span>
                      <span className="capitalize">Mode: {job.mode.replace("_", " ")}</span>
                      {job.started_at && (
                        <>
                          <span className="text-slate-600">•</span>
                          <span className="flex items-center gap-1">
                            <Clock className="w-3 h-3" />
                            {new Date(job.started_at).toLocaleTimeString()}
                          </span>
                        </>
                      )}
                    </div>
                  </div>
                </div>

                {/* Status and Action controls */}
                <div className="flex items-center gap-3 justify-between sm:justify-end flex-shrink-0">
                  <StatusBadge status={job.status} />

                  <div className="flex items-center gap-1.5">
                    {/* Open Console log */}
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        onOpenConsole(job.job_id);
                      }}
                      className="p-2 rounded-lg bg-slate-900 border border-slate-800 hover:border-slate-700 text-slate-400 hover:text-white transition"
                      title="Show Logs"
                    >
                      <Terminal className="w-4 h-4" />
                    </button>

                    {/* Open editor button if prepared */}
                    {isPrepared && (
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          onOpenEditor(job.job_id);
                        }}
                        className="px-3 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white font-bold text-xs shadow hover:shadow-indigo-500/20 transition flex items-center gap-1.5"
                      >
                        <Edit2 className="w-3.5 h-3.5" />
                        Edit Segments
                      </button>
                    )}

                    {/* Watch dubbed video if completed */}
                    {isCompleted && (
                      <a
                        href={`/api/jobs/${job.job_id}/video/dubbed`}
                        target="_blank"
                        rel="noopener noreferrer"
                        onClick={(e) => e.stopPropagation()}
                        className="px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white font-bold text-xs shadow hover:shadow-emerald-500/20 transition flex items-center gap-1.5"
                      >
                        <Download className="w-3.5 h-3.5" />
                        Get Video
                      </a>
                    )}

                    {/* Delete job */}
                    <button
                      onClick={(e) => onDeleteJob(job.job_id, e)}
                      className="p-2 rounded-lg bg-slate-900/50 hover:bg-rose-950/20 border border-slate-800 hover:border-rose-900/30 text-slate-400 hover:text-rose-400 transition"
                      title="Delete Job"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>

                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
