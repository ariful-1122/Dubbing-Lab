export interface Job {
  job_id: string;
  video_file: string;
  target_language: string;
  status: string;
  mode: string;
  started_at?: string;
  completed_at?: string;
  size_bytes?: number;
}

export interface Segment {
  id: number;
  start: number;
  end: number;
  duration: number;
  original_text: string;
  translated_text: string;
  expected_audio_file: string;
  original_audio_file: string;
  gender: string;
  status: string;
}

export interface JobDetails {
  job_id: string;
  status: string;
  video_file: string;
  target_language: string;
  mode: string;
  error_message?: string;
  segments: Segment[];
}

export interface Language {
  code: string;
  name: string;
}

export interface InputFile {
  name: string;
  size_bytes: number;
  modified: string;
}
