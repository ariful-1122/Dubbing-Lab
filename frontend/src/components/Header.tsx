import { Volume2, ArrowLeft, Terminal } from "lucide-react";

interface HeaderProps {
  activeTab: "dashboard" | "editor";
  onBackToDashboard: () => void;
  logConsoleOpen: boolean;
  onToggleLogConsole: () => void;
}

export default function Header({
  activeTab,
  onBackToDashboard,
  logConsoleOpen,
  onToggleLogConsole
}: HeaderProps) {
  return (
    <header className="glass-panel sticky top-0 z-50 px-6 py-4 flex items-center justify-between shadow-xl">
      <div className="flex items-center gap-3">
        <div className="w-9 h-9 rounded-xl bg-gradient-to-tr from-indigo-600 to-indigo-400 flex items-center justify-center shadow-lg shadow-indigo-500/20">
          <Volume2 className="w-5 h-5 text-white" />
        </div>
        <div>
          <h1 className="text-xl font-bold tracking-tight text-white flex items-center gap-1.5">
            Dubbing Lab
            <span className="text-xs font-medium text-slate-400 bg-slate-800 px-1.5 py-0.5 rounded border border-slate-700">v1.1</span>
          </h1>
          <p className="text-xs text-slate-400 hidden sm:block">Video Dubbing Software</p>
        </div>
      </div>

      <nav className="flex items-center gap-2">
        {activeTab === "editor" && (
          <button
            onClick={onBackToDashboard}
            className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm text-slate-300 hover:text-white hover:bg-slate-800 border border-transparent hover:border-slate-700 transition"
          >
            <ArrowLeft className="w-4 h-4" />
            Back to Dashboard
          </button>
        )}

        <button
          onClick={onToggleLogConsole}
          className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm transition border ${
            logConsoleOpen
              ? "bg-indigo-600/10 text-indigo-400 border-indigo-500/20"
              : "text-slate-300 hover:text-white hover:bg-slate-800 border-transparent hover:border-slate-700"
          }`}
        >
          <Terminal className="w-4 h-4" />
          Terminal logs
        </button>
      </nav>
    </header>
  );
}
