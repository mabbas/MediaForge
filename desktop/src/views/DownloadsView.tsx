import React, { useEffect, useState } from 'react';
import { api, ApiError } from '../api/client';
import { DownloadJob, SubmitDownloadRequest } from '../types';
import { StatusBadge } from '../components/StatusBadge';
import { NavView } from '../components/Layout';
import {
  Download,
  Play,
  Pause,
  XCircle,
  ArrowUp,
  ArrowDown,
  RefreshCw,
  PlusCircle,
  AlertCircle,
  CheckCircle2,
  Film,
  Loader2,
} from 'lucide-react';

interface DownloadsViewProps {
  onNavigate: (view: NavView) => void;
  onSelectForClip?: (sourcePath: string, duration?: number) => void;
}

export const DownloadsView: React.FC<DownloadsViewProps> = ({ onNavigate, onSelectForClip }) => {
  const [downloads, setDownloads] = useState<DownloadJob[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [url, setUrl] = useState('');
  const [quality, setQuality] = useState<'best' | '1080p' | '720p' | '480p' | 'audio'>('1080p');
  const [mode, setMode] = useState<'video' | 'audio'>('video');
  const [priority, setPriority] = useState<'high' | 'normal' | 'low'>('normal');
  const [startImmediately, setStartImmediately] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [actionInProgress, setActionInProgress] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  const loadDownloads = async (silent = false) => {
    if (!silent) setIsLoading(true);
    setErrorMessage(null);
    try {
      const res = await api.listDownloads();
      setDownloads(res.jobs || []);
    } catch (err: any) {
      if (!silent) {
        setErrorMessage(err.message || 'Failed to fetch downloads');
      }
    } finally {
      if (!silent) setIsLoading(false);
    }
  };

  useEffect(() => {
    loadDownloads();
    const interval = setInterval(() => {
      loadDownloads(true);
    }, 3000);
    return () => clearInterval(interval);
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!url.trim()) return;

    setIsSubmitting(true);
    setErrorMessage(null);
    setSuccessMessage(null);

    try {
      const req: SubmitDownloadRequest = {
        url: url.trim(),
        quality,
        mode,
        priority,
        start: startImmediately,
      };

      const res = await api.submitDownload(req);
      const serverJobId = res.job?.job_id || res.job?.id || 'queued';
      setSuccessMessage(`Download queued successfully! Job ID: ${serverJobId}`);
      setUrl('');
      await loadDownloads(true);
    } catch (err: any) {
      const msg = err instanceof ApiError ? err.detail : err.message || 'Failed to submit download';
      setErrorMessage(msg);
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleStart = async (jobId: string) => {
    setActionInProgress(jobId);
    try {
      await api.startDownload(jobId);
      await loadDownloads(true);
    } catch (err: any) {
      alert(`Start failed: ${err.message}`);
    } finally {
      setActionInProgress(null);
    }
  };

  const handlePause = async (jobId: string) => {
    setActionInProgress(jobId);
    try {
      await api.pauseDownload(jobId);
      await loadDownloads(true);
    } catch (err: any) {
      alert(`Pause failed: ${err.message}`);
    } finally {
      setActionInProgress(null);
    }
  };

  const handleResume = async (jobId: string) => {
    setActionInProgress(jobId);
    try {
      await api.resumeDownload(jobId);
      await loadDownloads(true);
    } catch (err: any) {
      alert(`Resume failed: ${err.message}`);
    } finally {
      setActionInProgress(null);
    }
  };

  const handleCancel = async (jobId: string) => {
    if (!confirm('Cancel this download job?')) return;
    setActionInProgress(jobId);
    try {
      await api.cancelDownload(jobId);
      await loadDownloads(true);
    } catch (err: any) {
      alert(`Cancellation failed: ${err.message}`);
    } finally {
      setActionInProgress(null);
    }
  };

  const handleMoveUp = async (jobId: string) => {
    try {
      await api.moveDownloadUp(jobId);
      await loadDownloads(true);
    } catch (err: any) {
      alert(`Move up failed: ${err.message}`);
    }
  };

  const handleMoveDown = async (jobId: string) => {
    try {
      await api.moveDownloadDown(jobId);
      await loadDownloads(true);
    } catch (err: any) {
      alert(`Move down failed: ${err.message}`);
    }
  };

  return (
    <div className="space-y-6 max-w-6xl mx-auto">
      {/* Top Header */}
      <div className="flex items-center justify-between bg-slate-950/60 p-4 rounded-xl border border-slate-800">
        <div>
          <h2 className="text-base font-bold text-white flex items-center space-x-2">
            <Download className="w-5 h-5 text-emerald-400" />
            <span>Download Management</span>
          </h2>
          <p className="text-xs text-slate-400 mt-0.5">
            Download media from supported platforms, control queue priorities, and track real-time progress.
          </p>
        </div>

        <button
          onClick={() => loadDownloads(false)}
          disabled={isLoading}
          className="flex items-center space-x-2 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-lg text-xs font-medium border border-slate-700 transition-colors"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${isLoading ? 'animate-spin' : ''}`} />
          <span>Refresh Queue</span>
        </button>
      </div>

      {/* Messages */}
      {errorMessage && (
        <div className="p-4 rounded-xl bg-rose-500/10 border border-rose-500/30 flex items-start space-x-3 text-rose-400 text-xs">
          <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
          <span>{errorMessage}</span>
        </div>
      )}

      {successMessage && (
        <div className="p-4 rounded-xl bg-emerald-500/10 border border-emerald-500/30 flex items-center justify-between text-emerald-400 text-xs">
          <div className="flex items-center space-x-2">
            <CheckCircle2 className="w-4 h-4 shrink-0" />
            <span>{successMessage}</span>
          </div>
          <button onClick={() => setSuccessMessage(null)} className="text-slate-400 hover:text-white">
            Dismiss
          </button>
        </div>
      )}

      {/* Submit Download Card */}
      <div className="bg-slate-950/60 border border-slate-800 rounded-xl p-5 space-y-4">
        <div className="flex items-center space-x-2 text-slate-200 font-semibold text-sm">
          <PlusCircle className="w-4 h-4 text-emerald-400" />
          <span>New Video Download</span>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-1.5">
              Media URL
            </label>
            <input
              type="url"
              required
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://www.youtube.com/watch?v=..."
              className="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-xs text-white placeholder-slate-500 focus:outline-none focus:border-emerald-500 font-mono transition-colors"
            />
          </div>

          <div className="grid grid-cols-1 md:grid-cols-4 gap-3 text-xs">
            <div>
              <label className="block font-semibold text-slate-300 mb-1">Quality</label>
              <select
                value={quality}
                onChange={(e) => setQuality(e.target.value as any)}
                className="w-full px-2.5 py-1.5 bg-slate-900 border border-slate-700 rounded-lg text-white focus:outline-none focus:border-emerald-500"
              >
                <option value="best">Best Available</option>
                <option value="1080p">1080p Full HD</option>
                <option value="720p">720p HD</option>
                <option value="480p">480p SD</option>
                <option value="audio">Audio Only (MP3)</option>
              </select>
            </div>

            <div>
              <label className="block font-semibold text-slate-300 mb-1">Mode</label>
              <select
                value={mode}
                onChange={(e) => setMode(e.target.value as any)}
                className="w-full px-2.5 py-1.5 bg-slate-900 border border-slate-700 rounded-lg text-white focus:outline-none focus:border-emerald-500"
              >
                <option value="video">Video + Audio</option>
                <option value="audio">Audio Only</option>
              </select>
            </div>

            <div>
              <label className="block font-semibold text-slate-300 mb-1">Priority</label>
              <select
                value={priority}
                onChange={(e) => setPriority(e.target.value as any)}
                className="w-full px-2.5 py-1.5 bg-slate-900 border border-slate-700 rounded-lg text-white focus:outline-none focus:border-emerald-500"
              >
                <option value="normal">Normal</option>
                <option value="high">High Priority</option>
                <option value="low">Low Priority</option>
              </select>
            </div>

            <div className="flex items-center pt-5">
              <label className="flex items-center space-x-2 cursor-pointer text-slate-300">
                <input
                  type="checkbox"
                  checked={startImmediately}
                  onChange={(e) => setStartImmediately(e.target.checked)}
                  className="rounded border-slate-700 bg-slate-900 text-emerald-500 focus:ring-0"
                />
                <span className="text-xs">Start Immediately</span>
              </label>
            </div>
          </div>

          <div className="pt-2">
            <button
              type="submit"
              disabled={isSubmitting}
              className="px-4 py-2 bg-emerald-500 hover:bg-emerald-600 disabled:opacity-50 text-slate-950 font-semibold rounded-lg text-xs flex items-center space-x-2 transition-colors shadow-md shadow-emerald-500/20"
            >
              {isSubmitting ? (
                <>
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  <span>Submitting to Engine...</span>
                </>
              ) : (
                <>
                  <Download className="w-3.5 h-3.5" />
                  <span>Enqueue Download</span>
                </>
              )}
            </button>
          </div>
        </form>
      </div>

      {/* Downloads Queue & History */}
      <div className="bg-slate-950/60 border border-slate-800 rounded-xl overflow-hidden shadow-sm">
        <div className="px-5 py-3 border-b border-slate-800 bg-slate-900/60 flex items-center justify-between">
          <h3 className="text-xs font-semibold text-slate-200 uppercase tracking-wider">Active Queue & Downloads</h3>
          <span className="text-xs text-slate-400">{downloads.length} Total Jobs</span>
        </div>

        {downloads.length === 0 ? (
          <div className="text-center py-12 text-slate-500 text-xs">
            No active or past downloads in queue. Submit a URL above to start downloading.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="bg-slate-950/80 border-b border-slate-800 text-slate-400 uppercase font-semibold">
                <tr>
                  <th className="px-4 py-3">Title / Source</th>
                  <th className="px-4 py-3">Status</th>
                  <th className="px-4 py-3">Progress</th>
                  <th className="px-4 py-3">Size / Format</th>
                  <th className="px-4 py-3">Queue Order</th>
                  <th className="px-4 py-3 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60">
                {downloads.map((job) => {
                  const isAction = actionInProgress === job.id;
                  const isFinished = ['completed', 'finished'].includes(job.status);
                  const isDeferred = job.status === 'deferred';
                  const isPaused = job.status === 'paused';
                  const isActive = ['downloading', 'processing', 'active'].includes(job.status);

                  return (
                    <tr key={job.id} className="hover:bg-slate-800/30 transition-colors">
                      <td className="px-4 py-3.5 max-w-xs">
                        <div className="font-medium text-slate-200 truncate">{job.title || job.url}</div>
                        <div className="text-[10px] text-slate-500 font-mono mt-0.5 truncate max-w-xs">
                          {job.url}
                        </div>
                      </td>
                      <td className="px-4 py-3.5">
                        <StatusBadge status={job.status} />
                      </td>
                      <td className="px-4 py-3.5">
                        <div className="w-28">
                          <div className="flex justify-between text-[10px] text-slate-400 mb-1">
                            <span>{Math.round(job.progress)}%</span>
                          </div>
                          <div className="w-full bg-slate-800 h-1.5 rounded-full overflow-hidden">
                            <div
                              className={`h-full transition-all duration-300 ${
                                isFinished ? 'bg-emerald-500' : 'bg-blue-500'
                              }`}
                              style={{ width: `${Math.max(job.progress, isFinished ? 100 : 0)}%` }}
                            />
                          </div>
                        </div>
                      </td>
                      <td className="px-4 py-3.5 text-slate-400">
                        <div>{job.file_size_human || 'Pending'}</div>
                        <div className="text-[10px] uppercase font-semibold text-slate-500">{job.format || 'mp4'}</div>
                      </td>
                      <td className="px-4 py-3.5">
                        <div className="flex items-center space-x-1">
                          <button
                            onClick={() => handleMoveUp(job.id)}
                            className="p-1 rounded bg-slate-800 hover:bg-slate-700 text-slate-400 hover:text-white"
                            title="Move Up in Priority Queue"
                          >
                            <ArrowUp className="w-3 h-3" />
                          </button>
                          <button
                            onClick={() => handleMoveDown(job.id)}
                            className="p-1 rounded bg-slate-800 hover:bg-slate-700 text-slate-400 hover:text-white"
                            title="Move Down in Priority Queue"
                          >
                            <ArrowDown className="w-3 h-3" />
                          </button>
                        </div>
                      </td>
                      <td className="px-4 py-3.5 text-right space-x-1.5">
                        {isDeferred && (
                          <button
                            onClick={() => handleStart(job.id)}
                            disabled={isAction}
                            className="inline-flex items-center space-x-1 px-2.5 py-1 bg-emerald-500/15 hover:bg-emerald-500/25 text-emerald-400 rounded-lg text-xs font-medium border border-emerald-500/30 transition-colors"
                          >
                            <Play className="w-3 h-3 fill-emerald-400" />
                            <span>Start</span>
                          </button>
                        )}

                        {isActive && (
                          <button
                            onClick={() => handlePause(job.id)}
                            disabled={isAction}
                            className="inline-flex items-center space-x-1 px-2 py-1 bg-amber-500/10 hover:bg-amber-500/20 text-amber-400 rounded-lg text-xs font-medium border border-amber-500/30 transition-colors"
                          >
                            <Pause className="w-3 h-3" />
                            <span>Pause</span>
                          </button>
                        )}

                        {isPaused && (
                          <button
                            onClick={() => handleResume(job.id)}
                            disabled={isAction}
                            className="inline-flex items-center space-x-1 px-2 py-1 bg-emerald-500/15 hover:bg-emerald-500/25 text-emerald-400 rounded-lg text-xs font-medium border border-emerald-500/30 transition-colors"
                          >
                            <Play className="w-3 h-3 fill-emerald-400" />
                            <span>Resume</span>
                          </button>
                        )}

                        {!isFinished && (
                          <button
                            onClick={() => handleCancel(job.id)}
                            disabled={isAction}
                            className="inline-flex items-center space-x-1 px-2 py-1 bg-rose-500/10 hover:bg-rose-500/20 text-rose-400 rounded-lg text-xs font-medium border border-rose-500/30 transition-colors"
                            title="Cancel Download"
                          >
                            <XCircle className="w-3 h-3" />
                          </button>
                        )}

                        {isFinished && (
                          <>
                            <button
                              onClick={() => onNavigate('media')}
                              className="inline-flex items-center space-x-1 px-2.5 py-1 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-lg text-xs font-medium border border-slate-700 transition-colors"
                            >
                              <Film className="w-3 h-3 text-emerald-400" />
                              <span>View in Library</span>
                            </button>
                            {onSelectForClip && (
                              <button
                                onClick={() => {
                                  onSelectForClip(job.id, job.duration);
                                  onNavigate('clip');
                                }}
                                className="inline-flex items-center space-x-1 px-2 py-1 bg-slate-800 hover:bg-slate-700 text-purple-400 rounded-lg text-xs font-medium border border-slate-700 transition-colors"
                              >
                                <span>Clip</span>
                              </button>
                            )}
                          </>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
};
