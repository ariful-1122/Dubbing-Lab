import {
  Loader2,
  CheckCircle2,
  XCircle,
  Settings,
  RefreshCw,
  AlertCircle
} from "lucide-react";

interface StatusBadgeProps {
  status: string;
}

export default function StatusBadge({ status }: StatusBadgeProps) {
  switch (status.toLowerCase()) {
    case "processing":
    case "stitching":
      return (
        <span className="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-semibold bg-amber-500/10 text-amber-400 border border-amber-500/20 animate-pulse">
          <Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" />
          {status}
        </span>
      );
    case "completed":
      return (
        <span className="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 glow-emerald">
          <CheckCircle2 className="w-3.5 h-3.5 mr-1.5 text-emerald-400" />
          Completed
        </span>
      );
    case "failed":
      return (
        <span className="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-semibold bg-rose-500/10 text-rose-400 border border-rose-500/20">
          <XCircle className="w-3.5 h-3.5 mr-1.5 text-rose-400" />
          Failed
        </span>
      );
    case "prepared":
      return (
        <span className="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-semibold bg-blue-500/10 text-blue-400 border border-blue-500/20 glow-indigo">
          <Settings className="w-3.5 h-3.5 mr-1.5 text-blue-400" />
          Prepared (Edit)
        </span>
      );
    default:
      if (status.includes("generating_tts")) {
        return (
          <span className="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-semibold bg-purple-500/10 text-purple-400 border border-purple-500/20 animate-pulse">
            <RefreshCw className="w-3.5 h-3.5 mr-1.5 animate-spin" />
            Generating TTS
          </span>
        );
      }
      return (
        <span className="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-semibold bg-slate-500/10 text-slate-400 border border-slate-500/20">
          <AlertCircle className="w-3.5 h-3.5 mr-1.5 text-slate-400" />
          {status}
        </span>
      );
  }
}
