import { create } from "zustand";
import type { DownloadJob, QueueStats } from "@/lib/types";

interface DownloadState {
  jobs: Record<string, DownloadJob>;
  stats: QueueStats;
  addJob: (job: DownloadJob) => void;
  updateJob: (jobId: string, patch: Partial<DownloadJob>) => void;
  removeJob: (jobId: string) => void;
  onProgress: (jobId: string, data: { percent?: number; speed_bps?: number; speed_human?: string; eta_human?: string; bytes_downloaded?: number; total_bytes?: number }) => void;
  setStats: (stats: Partial<QueueStats>) => void;
}

const defaultStats: QueueStats = {
  active: 0,
  max_concurrent: 3,
  queue_total: 0,
  queue_high: 0,
  queue_normal: 0,
  queue_low: 0,
  total_jobs: 0,
  is_paused: false,
  jobs_by_status: {},
};

export const useDownloadStore = create<DownloadState>((set) => ({
  jobs: {},
  stats: defaultStats,
  addJob: (job) => set((s) => ({ jobs: { ...s.jobs, [job.job_id]: job } })),
  updateJob: (jobId, patch) =>
    set((s) => {
      const j = s.jobs[jobId];
      if (!j) return s;
      return { jobs: { ...s.jobs, [jobId]: { ...j, ...patch } } };
    }),
  removeJob: (jobId) =>
    set((s) => {
      const { [jobId]: _, ...rest } = s.jobs;
      return { jobs: rest };
    }),
  onProgress: (jobId, data) =>
    set((s) => {
      const j = s.jobs[jobId];
      if (!j) return s;
      const next = { ...j };
      if (data.percent != null) next.progress_percent = data.percent;
      if (data.speed_human != null) next.speed_human = data.speed_human;
      if (data.eta_human != null) next.eta_human = data.eta_human;
      if (data.bytes_downloaded != null) next.bytes_downloaded = data.bytes_downloaded;
      if (data.total_bytes != null) next.total_bytes = data.total_bytes;
      return { jobs: { ...s.jobs, [jobId]: next } };
    }),
  setStats: (stats) => set((s) => ({ stats: { ...s.stats, ...stats } })),
}));
