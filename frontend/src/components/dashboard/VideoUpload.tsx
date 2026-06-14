import React, { useState } from "react";
import { Upload, Loader2 } from "lucide-react";

interface VideoUploadProps {
  uploading: boolean;
  uploadProgress: number;
  onUpload: (file: File) => void;
}

export default function VideoUpload({
  uploading,
  uploadProgress,
  onUpload
}: VideoUploadProps) {
  const [dragActive, setDragActive] = useState<boolean>(false);

  const handleDrag = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === "dragenter" || e.type === "dragover") {
      setDragActive(true);
    } else if (e.type === "dragleave") {
      setDragActive(false);
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);

    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      const file = e.dataTransfer.files[0];
      const suffix = file.name.split(".").pop()?.toLowerCase();
      const supported = ["mp4", "mkv", "mov", "avi", "webm"];
      if (suffix && supported.includes(suffix)) {
        onUpload(file);
      } else {
        alert("Unsupported file format. Please upload an MP4, MKV, MOV, AVI, or WEBM video.");
      }
    }
  };

  return (
    <div
      className={`glass-panel-glow rounded-2xl p-6 relative border-2 border-dashed transition-all duration-300 flex flex-col items-center justify-center text-center group cursor-pointer ${
        dragActive
          ? "border-indigo-500 bg-indigo-500/5 shadow-indigo-500/10"
          : "border-slate-800 hover:border-slate-700 bg-slate-950/20"
      }`}
      onDragEnter={handleDrag}
      onDragOver={handleDrag}
      onDragLeave={handleDrag}
      onDrop={handleDrop}
    >
      <input
        type="file"
        id="video-upload"
        className="hidden"
        accept=".mp4,.mkv,.mov,.avi,.webm"
        onChange={(e) => e.target.files && onUpload(e.target.files[0])}
        disabled={uploading}
      />
      <label htmlFor="video-upload" className="cursor-pointer flex flex-col items-center w-full">
        <div className="w-12 h-12 rounded-full bg-slate-800 border border-slate-700 flex items-center justify-center mb-4 text-slate-300 group-hover:scale-110 group-hover:bg-indigo-600/10 group-hover:text-indigo-400 group-hover:border-indigo-500/20 transition-all">
          {uploading ? (
            <Loader2 className="w-6 h-6 animate-spin text-indigo-400" />
          ) : (
            <Upload className="w-6 h-6" />
          )}
        </div>
        <h3 className="text-sm font-semibold text-white">
          {uploading ? "Uploading Video..." : "Upload New Video"}
        </h3>
        <p className="text-xs text-slate-400 mt-1 max-w-[200px]">
          {uploading ? `Progress: ${uploadProgress}%` : "Drag and drop video here, or click to browse files"}
        </p>
        <p className="text-[10px] text-slate-500 mt-3">Supports MP4, MKV, MOV, AVI, WEBM</p>
      </label>

      {uploading && (
        <div className="absolute bottom-0 left-0 right-0 h-1 bg-slate-800 rounded-b-2xl overflow-hidden">
          <div
            className="h-full bg-indigo-500 transition-all duration-300"
            style={{ width: `${uploadProgress}%` }}
          />
        </div>
      )}
    </div>
  );
}
