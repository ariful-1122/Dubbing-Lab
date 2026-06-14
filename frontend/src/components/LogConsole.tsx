import { Terminal, RefreshCw } from "lucide-react";

interface LogConsoleProps {
  consoleJobId: string | null;
  consoleLogs: string[];
  onRefreshLogs: (jobId: string) => void;
  onClose: () => void;
}

export default function LogConsole({
  consoleJobId,
  consoleLogs,
  onRefreshLogs,
  onClose
}: LogConsoleProps) {
  return (
    <footer className="glass-panel-glow border-t border-slate-800 h-64 flex flex-col shadow-2xl z-40 transition-all duration-300">
      <div className="bg-slate-950 px-4 py-2 border-b border-slate-800 flex items-center justify-between">
        <div className="flex items-center gap-2 text-xs font-semibold text-indigo-400">
          <Terminal className="w-4 h-4" />
          <span>LOG CONSOLE: {consoleJobId ? `Job ${consoleJobId}` : "No Active Job"}</span>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => consoleJobId && onRefreshLogs(consoleJobId)}
            className="p-1 rounded text-slate-400 hover:text-white hover:bg-slate-900 text-[10px] flex items-center gap-1 border border-slate-800 transition"
          >
            <RefreshCw className="w-3 h-3" /> Refresh
          </button>
          <button
            onClick={onClose}
            className="text-slate-400 hover:text-white p-1 rounded hover:bg-slate-900 transition text-[10px] px-2 border border-slate-800"
          >
            Close
          </button>
        </div>
      </div>
      <div className="flex-1 bg-black p-4 font-mono text-[11px] text-slate-300 overflow-y-auto leading-relaxed select-text selection:bg-indigo-500/30">
        {consoleLogs.length === 0 ? (
          <div className="text-slate-600 italic">Logs are empty or no job is currently selected...</div>
        ) : (
          consoleLogs.map((log, index) => {
            // Style log level
            let colorClass = "text-slate-300";
            if (log.includes("| ERROR    |")) colorClass = "text-rose-400 font-semibold";
            else if (log.includes("| WARNING  |")) colorClass = "text-amber-400 font-semibold";
            else if (log.includes("| INFO     |")) colorClass = "text-indigo-300";

            return (
              <div key={index} className={`whitespace-pre-wrap ${colorClass}`}>
                {log}
              </div>
            );
          })
        )}
      </div>
    </footer>
  );
}
