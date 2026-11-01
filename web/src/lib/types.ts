export interface DownloadJob {
  job_id: string;
  url: string;
  title?: string;
  status: string;
  progress_percent?: number;
  speed_bps?: number;
  speed_human?: string;
  eta_human?: string;
  bytes_downloaded?: number;
  total_bytes?: number;
  error?: string;
  created_at?: string;
  completed_at?: string;
  format?: string;
  filename?: string;
  [key: string]: any;
}

export interface QueueStats {
  active: number;
  max_concurrent: number;
  queue_total: number;
  queue_high: number;
  queue_normal: number;
  queue_low: number;
  total_jobs: number;
  is_paused: boolean;
  jobs_by_status: Record<string, number>;
}
