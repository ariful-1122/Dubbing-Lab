import React, { useState, useEffect, useRef } from "react";
import Header from "./components/Header";
import LogConsole from "./components/LogConsole";
import VideoUpload from "./components/dashboard/VideoUpload";
import JobConfig from "./components/dashboard/JobConfig";
import JobList from "./components/dashboard/JobList";
import VideoPreview from "./components/editor/VideoPreview";
import WorkflowControls from "./components/editor/WorkflowControls";
import SegmentList from "./components/editor/SegmentList";
import type { Job, Segment, JobDetails, Language, InputFile } from "./types";

export default function App() {
  // Navigation / Tabs
  const [activeTab, setActiveTab] = useState<"dashboard" | "editor">("dashboard");
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);

  // Data State
  const [jobs, setJobs] = useState<Job[]>([]);
  const [languages, setLanguages] = useState<Language[]>([]);
  const [inputFiles, setInputFiles] = useState<InputFile[]>([]);
  const [selectedJob, setSelectedJob] = useState<JobDetails | null>(null);

  // New Job Configuration
  const [selectedVideo, setSelectedVideo] = useState<string>("");
  const [targetLang, setTargetLang] = useState<string>("bn");
  const [dubbingMode, setDubbingMode] = useState<string>("automated"); // "automated" | "live_translate" | "prepare"

  // Logging & Console
  const [consoleJobId, setConsoleJobId] = useState<string | null>(null);
  const [consoleLogs, setConsoleLogs] = useState<string[]>([]);
  const [logConsoleOpen, setLogConsoleOpen] = useState<boolean>(false);

  // App Loading / Action States
  const [uploading, setUploading] = useState<boolean>(false);
  const [uploadProgress, setUploadProgress] = useState<number>(0);
  const [startingJob, setStartingJob] = useState<boolean>(false);
  const [editorActionRunning, setEditorActionRunning] = useState<boolean>(false);
  const [ttsEngine, setTtsEngine] = useState<string>("edge-tts"); // "edge-tts" | "gemini-tts"

  // Audio playing tracking
  const [playingAudio, setPlayingAudio] = useState<{ segmentId: number; type: "original" | "dubbed" } | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  // Interval references
  const jobsIntervalRef = useRef<any>(null);
  const logsIntervalRef = useRef<any>(null);
  const editorIntervalRef = useRef<any>(null);

  // Initialize
  useEffect(() => {
    fetchLanguages();
    fetchJobs();
    fetchInputFiles();

    // Poll jobs list every 3 seconds
    jobsIntervalRef.current = setInterval(fetchJobs, 3000);

    return () => {
      if (jobsIntervalRef.current) clearInterval(jobsIntervalRef.current);
      if (logsIntervalRef.current) clearInterval(logsIntervalRef.current);
      if (editorIntervalRef.current) clearInterval(editorIntervalRef.current);
    };
  }, []);

  // Poll logs when console is open on a running job
  useEffect(() => {
    if (logConsoleOpen && consoleJobId) {
      fetchLogs(consoleJobId);
      logsIntervalRef.current = setInterval(() => fetchLogs(consoleJobId), 2000);
    } else {
      if (logsIntervalRef.current) {
        clearInterval(logsIntervalRef.current);
        logsIntervalRef.current = null;
      }
    }
    return () => {
      if (logsIntervalRef.current) clearInterval(logsIntervalRef.current);
    };
  }, [logConsoleOpen, consoleJobId]);

  // Poll job editor details if it is processing an action (like generating tts, stitching)
  useEffect(() => {
    if (activeTab === "editor" && selectedJobId && selectedJob) {
      const isProcessing = ["processing", "generating_tts (edge-tts)", "generating_tts (gemini-tts)", "stitching"].includes(selectedJob.status);
      if (isProcessing) {
        if (!editorIntervalRef.current) {
          editorIntervalRef.current = setInterval(() => fetchJobDetails(selectedJobId), 2000);
        }
      } else {
        if (editorIntervalRef.current) {
          clearInterval(editorIntervalRef.current);
          editorIntervalRef.current = null;
        }
      }
    } else {
      if (editorIntervalRef.current) {
        clearInterval(editorIntervalRef.current);
        editorIntervalRef.current = null;
      }
    }
    return () => {
      if (editorIntervalRef.current) clearInterval(editorIntervalRef.current);
    };
  }, [activeTab, selectedJobId, selectedJob]);

  // Fetch API Helpers
  const fetchJobs = async () => {
    try {
      const res = await fetch("/api/jobs");
      if (res.ok) {
        const data = await res.json();
        setJobs(data);
      }
    } catch (err) {
      console.error("Failed to fetch jobs:", err);
    }
  };

  const fetchLanguages = async () => {
    try {
      const res = await fetch("/api/languages");
      if (res.ok) {
        const data = await res.json();
        setLanguages(data);
        if (data.length > 0 && !targetLang) {
          setTargetLang(data[0].code);
        }
      }
    } catch (err) {
      console.error("Failed to fetch languages:", err);
    }
  };

  const fetchInputFiles = async () => {
    try {
      const res = await fetch("/api/input-files");
      if (res.ok) {
        const data = await res.json();
        setInputFiles(data);
        setSelectedVideo(prev => {
          if (prev && !data.some((f: any) => f.name === prev)) {
            return ""; // Clear if the previously selected file is no longer available
          }
          return prev;
        });
      }
    } catch (err) {
      console.error("Failed to fetch input files:", err);
    }
  };

  const fetchJobDetails = async (jobId: string) => {
    try {
      const res = await fetch(`/api/jobs/${jobId}`);
      if (res.ok) {
        const data = await res.json();
        setSelectedJob(data);

        // Auto-scroll console if console is active for this job
        if (consoleJobId === jobId) {
          fetchLogs(jobId);
        }
      }
    } catch (err) {
      console.error("Failed to fetch job details:", err);
    }
  };

  const fetchLogs = async (jobId: string) => {
    try {
      const res = await fetch(`/api/jobs/${jobId}/logs`);
      if (res.ok) {
        const data = await res.json();
        setConsoleLogs(data.logs);
      }
    } catch (err) {
      console.error("Failed to fetch logs:", err);
    }
  };

  // Actions
  const handleUploadFile = async (file: File) => {
    if (!file) return;
    setUploading(true);
    setUploadProgress(10);

    const formData = new FormData();
    formData.append("file", file);

    try {
      const res = await fetch("/api/jobs/upload", {
        method: "POST",
        body: formData,
      });
      setUploadProgress(70);
      if (res.ok) {
        setUploadProgress(100);
        const data = await res.json();
        setSelectedVideo(data.filename);
        fetchInputFiles();
        alert(`Successfully uploaded ${file.name}`);
      } else {
        alert("Upload failed. Please try again.");
      }
    } catch (err) {
      console.error("Error uploading file:", err);
      alert("Error uploading file.");
    } finally {
      setUploading(false);
      setUploadProgress(0);
    }
  };

  const startJob = async () => {
    if (!selectedVideo) {
      alert("Please select a video file first.");
      return;
    }
    setStartingJob(true);
    try {
      const res = await fetch("/api/jobs/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          file_name: selectedVideo,
          target_language: targetLang,
          mode: dubbingMode,
        }),
      });
      if (res.ok) {
        const data = await res.json();
        // Open console for this new job automatically
        setConsoleJobId(data.job_id);
        setLogConsoleOpen(true);
        setSelectedVideo(""); // Clear the selected video input
        fetchJobs();
        fetchInputFiles(); // Refresh the input file list since the backend moves the file
      } else {
        alert("Failed to start job.");
      }
    } catch (err) {
      console.error("Error starting job:", err);
    } finally {
      setStartingJob(false);
    }
  };

  const deleteJob = async (jobId: string, event: React.MouseEvent) => {
    event.stopPropagation();
    if (!confirm("Are you sure you want to delete this job and all associated files?")) return;
    try {
      const res = await fetch(`/api/jobs/${jobId}`, {
        method: "DELETE",
      });
      if (res.ok) {
        fetchJobs();
        fetchInputFiles();
        if (selectedJobId === jobId) {
          setActiveTab("dashboard");
          setSelectedJobId(null);
          setSelectedJob(null);
        }
        if (consoleJobId === jobId) {
          setConsoleJobId(null);
          setConsoleLogs([]);
        }
      }
    } catch (err) {
      console.error("Error deleting job:", err);
    }
  };

  const openEditor = (jobId: string) => {
    setSelectedJobId(jobId);
    fetchJobDetails(jobId);
    setActiveTab("editor");
  };

  // Editor specific actions
  const saveSegmentDetails = async (updatedSegments: Segment[]) => {
    if (!selectedJobId) return;
    try {
      const res = await fetch(`/api/jobs/${selectedJobId}/translations`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ segments: updatedSegments }),
      });
      if (res.ok) {
        // Refresh local details
        if (selectedJob) {
          setSelectedJob({
            ...selectedJob,
            segments: updatedSegments,
          });
        }
      }
    } catch (err) {
      console.error("Error saving segment details:", err);
    }
  };

  const updateSegmentField = (segmentId: number, field: keyof Segment, value: any) => {
    if (!selectedJob) return;
    const updated = selectedJob.segments.map((seg) => {
      if (seg.id === segmentId) {
        return { ...seg, [field]: value };
      }
      return seg;
    });
    setSelectedJob({ ...selectedJob, segments: updated });
    saveSegmentDetails(updated);
  };

  const triggerTTS = async () => {
    if (!selectedJobId) return;
    setEditorActionRunning(true);
    try {
      const res = await fetch(`/api/jobs/${selectedJobId}/tts`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ engine: ttsEngine }),
      });
      if (res.ok) {
        setConsoleJobId(selectedJobId);
        setLogConsoleOpen(true);
        // Refresh details which starts polling
        fetchJobDetails(selectedJobId);
      }
    } catch (err) {
      console.error("Error triggering TTS:", err);
    } finally {
      setEditorActionRunning(false);
    }
  };

  const triggerStitch = async () => {
    if (!selectedJobId) return;
    setEditorActionRunning(true);
    try {
      const res = await fetch(`/api/jobs/${selectedJobId}/stitch`, {
        method: "POST",
      });
      if (res.ok) {
        setConsoleJobId(selectedJobId);
        setLogConsoleOpen(true);
        fetchJobDetails(selectedJobId);
      }
    } catch (err) {
      console.error("Error triggering stitch:", err);
    } finally {
      setEditorActionRunning(false);
    }
  };

  const playAudio = (segmentId: number, type: "original" | "dubbed") => {
    if (!selectedJobId) return;

    // If already playing this audio, pause it
    if (playingAudio && playingAudio.segmentId === segmentId && playingAudio.type === type) {
      if (audioRef.current) {
        audioRef.current.pause();
      }
      setPlayingAudio(null);
      return;
    }

    // Stop currently playing
    if (audioRef.current) {
      audioRef.current.pause();
    }

    const audioUrl = `/api/jobs/${selectedJobId}/segments/${segmentId}/audio/${type}?t=${Date.now()}`;
    const audio = new Audio(audioUrl);
    audioRef.current = audio;

    setPlayingAudio({ segmentId, type });

    audio.play().catch((e) => {
      console.error("Failed to play audio:", e);
      setPlayingAudio(null);
    });

    audio.onended = () => {
      setPlayingAudio(null);
    };
  };

  const handleCustomAudioUpload = async (segmentId: number, file: File) => {
    if (!selectedJobId || !file) return;

    const formData = new FormData();
    formData.append("file", file);

    try {
      const res = await fetch(`/api/jobs/${selectedJobId}/segments/${segmentId}/upload`, {
        method: "POST",
        body: formData,
      });
      if (res.ok) {
        alert(`Successfully uploaded custom audio for segment ${segmentId}`);
        fetchJobDetails(selectedJobId);
      } else {
        alert("Audio upload failed.");
      }
    } catch (err) {
      console.error("Error uploading custom segment audio:", err);
      alert("Error uploading audio.");
    }
  };

  const handleBackToDashboard = () => {
    setActiveTab("dashboard");
    setSelectedJobId(null);
    setSelectedJob(null);
  };

  const handleToggleLogConsole = () => {
    setLogConsoleOpen(!logConsoleOpen);
  };

  return (
    <div className="min-h-screen flex flex-col font-sans select-none">
      {/* Top Header */}
      <Header
        activeTab={activeTab}
        onBackToDashboard={handleBackToDashboard}
        logConsoleOpen={logConsoleOpen}
        onToggleLogConsole={handleToggleLogConsole}
      />

      {/* Main Content Area */}
      <main className="flex-1 p-6 flex flex-col gap-6 overflow-hidden">
        {activeTab === "dashboard" ? (
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
            {/* Control Panel (Upload & Start Job) */}
            <div className="lg:col-span-4 flex flex-col gap-6">
              {/* Drag and Drop Video Upload */}
              <VideoUpload
                uploading={uploading}
                uploadProgress={uploadProgress}
                onUpload={handleUploadFile}
              />

              {/* Start New Dubbing Job Form */}
              <JobConfig
                inputFiles={inputFiles}
                languages={languages}
                selectedVideo={selectedVideo}
                setSelectedVideo={setSelectedVideo}
                targetLang={targetLang}
                setTargetLang={setTargetLang}
                dubbingMode={dubbingMode}
                setDubbingMode={setDubbingMode}
                startingJob={startingJob}
                onStartJob={startJob}
              />
            </div>

            {/* Jobs List Board */}
            <div className="lg:col-span-8 flex flex-col gap-6">
              <JobList
                jobs={jobs}
                onRefresh={fetchJobs}
                onOpenEditor={openEditor}
                onOpenConsole={(jobId) => {
                  setConsoleJobId(jobId);
                  setLogConsoleOpen(true);
                }}
                onDeleteJob={deleteJob}
              />
            </div>
          </div>
        ) : (
          /* Segment Timeline Editor View */
          <div className="grid grid-cols-1 xl:grid-cols-12 gap-6 items-start flex-1 min-h-0">
            {/* Editor Workspace: Video and Action controls */}
            <div className="xl:col-span-5 flex flex-col gap-6">
              <VideoPreview
                selectedJobId={selectedJobId}
                selectedJob={selectedJob}
              />

              <WorkflowControls
                ttsEngine={ttsEngine}
                setTtsEngine={setTtsEngine}
                onTriggerTTS={triggerTTS}
                onTriggerStitch={triggerStitch}
                editorActionRunning={editorActionRunning}
                selectedJobStatus={selectedJob?.status}
              />
            </div>

            {/* Timeline segment list editor */}
            <SegmentList
              selectedJob={selectedJob}
              playingAudio={playingAudio}
              onPlayAudio={playAudio}
              onUpdateSegmentField={updateSegmentField}
              onCustomAudioUpload={handleCustomAudioUpload}
            />
          </div>
        )}
      </main>

      {/* Real-time Streaming Logs Terminal Console at the bottom */}
      {logConsoleOpen && (
        <LogConsole
          consoleJobId={consoleJobId}
          consoleLogs={consoleLogs}
          onRefreshLogs={fetchLogs}
          onClose={() => setLogConsoleOpen(false)}
        />
      )}
    </div>
  );
}
