import { describe, test, expect, beforeEach } from "vitest";
import { useDownloadStore } from "@/stores/download-store";
import { useConnectionStore } from "@/stores/connection-store";

const emptyStats = {
  active: 0,
  max_concurrent: 3,
  queue_total: 0,
  queue_high: 0,
  queue_normal: 0,
  queue_low: 0,
  total_jobs: 0,
  is_paused: false,
  jobs_by_status: {} as Record<string, number>,
};

describe("download store", () => {
  beforeEach(() => {
    useDownloadStore.setState({ jobs: {}, stats: emptyStats });
  });

  test("addJob adds to state", () => {
    const job = {
      job_id: "test-1",
      url: "https://test.com",
      media_type: "video",
      status: "queued",
      priority: "normal",
      progress_percent: 0,
      bytes_downloaded: 0,
      retry_count: 0,
    };
    useDownloadStore.getState().addJob(job as any);
    const state = useDownloadStore.getState();
    expect(state.jobs["test-1"]).toBeDefined();
    expect(state.jobs["test-1"].status).toBe("queued");
  });

  test("updateJob updates fields", () => {
    useDownloadStore.getState().addJob({
      job_id: "test-2",
      url: "https://test.com",
      media_type: "video",
      status: "queued",
      priority: "normal",
      progress_percent: 0,
      bytes_downloaded: 0,
      retry_count: 0,
    } as any);
    useDownloadStore.getState().updateJob("test-2", { status: "downloading" });
    const job = useDownloadStore.getState().jobs["test-2"];
    expect(job.status).toBe("downloading");
  });

  test("removeJob removes from state", () => {
    useDownloadStore.getState().addJob({
      job_id: "test-3",
      url: "https://test.com",
      media_type: "video",
      status: "completed",
      priority: "normal",
      progress_percent: 100,
      bytes_downloaded: 1000,
      retry_count: 0,
    } as any);
    useDownloadStore.getState().removeJob("test-3");
    expect(useDownloadStore.getState().jobs["test-3"]).toBeUndefined();
  });

  test("onProgress updates progress", () => {
    useDownloadStore.getState().addJob({
      job_id: "test-4",
      url: "https://test.com",
      media_type: "video",
      status: "downloading",
      priority: "normal",
      progress_percent: 0,
      bytes_downloaded: 0,
      retry_count: 0,
    } as any);
    useDownloadStore.getState().onProgress("test-4", {
      percent: 50,
      speed_bps: 5242880,
      speed_human: "5.0 MB/s",
    });
    const job = useDownloadStore.getState().jobs["test-4"];
    expect(job.progress_percent).toBe(50);
    expect(job.speed_human).toBe("5.0 MB/s");
  });

  test("setStats updates stats", () => {
    useDownloadStore.getState().setStats({
      active: 2,
      max_concurrent: 3,
      queue_total: 5,
      queue_high: 1,
      queue_normal: 3,
      queue_low: 1,
      total_jobs: 7,
      is_paused: false,
      jobs_by_status: { downloading: 2, queued: 5 },
    });
    const stats = useDownloadStore.getState().stats;
    expect(stats.active).toBe(2);
    expect(stats.queue_total).toBe(5);
  });
});

describe("connection store", () => {
  test("setApiOnline", () => {
    useConnectionStore.getState().setApiOnline(false);
    expect(useConnectionStore.getState().apiOnline).toBe(false);
  });

  test("setWsConnected", () => {
    useConnectionStore.getState().setWsConnected(true);
    expect(useConnectionStore.getState().wsConnected).toBe(true);
  });

  test("incrementReconnect", () => {
    useConnectionStore.getState().resetReconnect();
    useConnectionStore.getState().incrementReconnect();
    useConnectionStore.getState().incrementReconnect();
    expect(useConnectionStore.getState().reconnectAttempts).toBe(2);
  });
});
