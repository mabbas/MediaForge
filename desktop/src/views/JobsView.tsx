import React, { useEffect, useState } from 'react';
import { api } from '../api/client';
import { JobDetail } from '../types';
import { StatusBadge } from '../components/StatusBadge';
import { VideoPlayerModal } from '../components/VideoPlayerModal';
import { useAuth } from '../context/AuthContext';
import {
  ListTodo,
  RefreshCw,
  XCircle,
  Play,
  AlertCircle,
  Info,
  Layers,
  Scissors,
} from 'lucide-react';

interface JobsViewProps {
  highlightJobId?: string;
}

export const JobsView: React.FC<JobsViewProps> = ({ highlightJobId }) => {
  const { user } = useAuth();
  const [jobs, setJobs] = useState<JobDetail[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [selectedJob, setSelectedJob] = useState<JobDetail | null>(null);
  const [operationFilter, setOperationFilter] = useState<'all' | 'clip_extract' | 'merge'>('all');
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [cancellingJobId, setCancellingJobId] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  // Player state for succeeded jobs
  const [playerModal, setPlayerModal] = useState<{
    isOpen: boolean;
    mediaUrl: string;
    title: string;
    format: string;
    duration?: number;
  }>({
    isOpen: false,
    mediaUrl: '',
    title: '',
    format: '',
  });

  const loadJobs = async (silent = false) => {
    if (!silent) setIsLoading(true);
    setErrorMessage(null);
    try {
      const res = await api.listJobs({ limit: 50 });
      setJobs(res.jobs);

      // Auto-select highlightJobId if provided
      if (highlightJobId) {
        const found = res.jobs.find((j) => j.job_id === highlightJobId);
        if (found) setSelectedJob(found);
      }
    } catch (err: any) {
      if (!silent) {
        setErrorMessage(err.message || 'Failed to fetch jobs');
      }
    } finally {
      if (!silent) setIsLoading(false);
    }
  };

  useEffect(() => {
    loadJobs();

    // Poll every 3 seconds while active jobs exist
    const interval = setInterval(() => {
      loadJobs(true);
    }, 3000);

    return () => clearInterval(interval);
  }, [highlightJobId]);

  const handleCancel = async (jobId: string) => {
    if (!confirm('Are you sure you want to request cancellation for this job?')) return;
    setCancellingJobId(jobId);
    try {
      await api.cancelJob(jobId);
      await loadJobs(true);
    } catch (err: any) {
      alert(`Cancellation failed: ${err.message}`);
    } finally {
      setCancellingJobId(null);
    }
  };

  const handlePlayArtifact = async (job: JobDetail) => {
    if (!job.result_artifact || !window.mediaforge || !user) return;
    const art = job.result_artifact;
    const cache = await window.mediaforge.media.checkCache(art.id, user.tenant_id);

    if (cache.isCached && cache.mediaUrl) {
      setPlayerModal({
        isOpen: true,
        mediaUrl: cache.mediaUrl,
        title: art.filename,
        format: art.format,
        duration: art.duration_seconds,
      });
    } else {
      // Trigger download to cache first
      try {
        const res = await window.mediaforge.media.download({
          artifactId: art.id,
          downloadUrl: api.getArtifactDownloadUrl(art.id),
          filename: art.filename,
          tenantId: user.tenant_id,
          expectedSha256: art.sha256_checksum || undefined,
        });
        if (res.success) {
          setPlayerModal({
            isOpen: true,
            mediaUrl: res.mediaUrl,
            title: art.filename,
            format: art.format,
            duration: art.duration_seconds,
          });
        }
      } catch (err: any) {
        alert(`Download failed: ${err.message}`);
      }
    }
  };

  const filteredJobs = jobs.filter((j) => {
    if (operationFilter !== 'all' && j.operation_type !== operationFilter) {
      return false;
    }
    if (statusFilter !== 'all' && j.status !== statusFilter) {
      return false;
    }
    return true;
  });

  return (
    <div className="space-y-6 max-w-6xl mx-auto">
      {/* Top Bar */}
      <div className="flex flex-col md:flex-row items-center justify-between gap-4 bg-slate-950/60 p-4 rounded-xl border border-slate-800">
        <div>
          <h2 className="text-base font-bold text-white flex items-center space-x-2">
            <ListTodo className="w-5 h-5 text-blue-400" />
            <span>Background Media Processing Jobs</span>
          </h2>
          <p className="text-xs text-slate-400 mt-0.5">
            Durable PostgreSQL work items with worker fencing, lease heartbeat and real progress tracking.
          </p>
        </div>

        <div className="flex items-center space-x-3 w-full md:w-auto justify-end">
          {/* Operation & Status Filter */}
          <div className="flex items-center space-x-2">
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="px-2.5 py-1 bg-slate-900 border border-slate-700 rounded-lg text-xs text-white focus:outline-none focus:border-blue-500"
            >
              <option value="all">All Statuses</option>
              <option value="queued">Queued</option>
              <option value="running">Running</option>
              <option value="succeeded">Succeeded</option>
              <option value="failed">Failed</option>
              <option value="cancelled">Cancelled</option>
            </select>

            <div className="flex items-center space-x-1 bg-slate-900 p-1 rounded-lg border border-slate-700 text-xs">
              {(['all', 'clip_extract', 'merge'] as const).map((op) => (
                <button
                  key={op}
                  onClick={() => setOperationFilter(op)}
                  className={`px-2.5 py-1 rounded font-medium capitalize transition-colors ${
                    operationFilter === op
                      ? 'bg-blue-500/20 text-blue-400 border border-blue-500/30'
                      : 'text-slate-400 hover:text-white'
                  }`}
                >
                  {op === 'all' ? 'All Ops' : op === 'clip_extract' ? 'Clips' : 'Merges'}
                </button>
              ))}
            </div>
          </div>

          <button
            onClick={() => loadJobs(false)}
            disabled={isLoading}
            className="flex items-center space-x-2 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-lg text-xs font-medium border border-slate-700 transition-colors"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isLoading ? 'animate-spin' : ''}`} />
            <span>Refresh</span>
          </button>
        </div>
      </div>

      {errorMessage && (
        <div className="p-4 rounded-xl bg-rose-500/10 border border-rose-500/30 flex items-center space-x-3 text-rose-400 text-xs">
          <AlertCircle className="w-4 h-4 shrink-0" />
          <span>{errorMessage}</span>
        </div>
      )}

      {/* Jobs Table */}
      {filteredJobs.length === 0 ? (
        <div className="text-center py-16 bg-slate-900/30 border border-slate-800/80 rounded-2xl">
          <ListTodo className="w-12 h-12 text-slate-600 mx-auto mb-3" />
          <h3 className="text-base font-semibold text-slate-300">No Background Jobs Found</h3>
          <p className="text-xs text-slate-500 mt-1 max-w-sm mx-auto">
            No background media processing jobs match your filter.
          </p>
        </div>
      ) : (
        <div className="bg-slate-900/40 border border-slate-800 rounded-xl overflow-hidden shadow-sm">
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="bg-slate-950/70 border-b border-slate-800 text-slate-400 uppercase font-semibold tracking-wider">
                <tr>
                  <th className="px-4 py-3">Job ID</th>
                  <th className="px-4 py-3">Operation</th>
                  <th className="px-4 py-3">Status</th>
                  <th className="px-4 py-3">Progress</th>
                  <th className="px-4 py-3">Attempts</th>
                  <th className="px-4 py-3">Created</th>
                  <th className="px-4 py-3 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60">
                {filteredJobs.map((job) => {
                  const isHighlighted = job.job_id === highlightJobId;
                  const canCancel = ['queued', 'running'].includes(job.status) && !job.cancellation_requested;
                  const isCancelling = cancellingJobId === job.job_id;

                  return (
                    <tr
                      key={job.job_id}
                      onClick={() => setSelectedJob(job)}
                      className={`hover:bg-slate-800/40 cursor-pointer transition-colors ${
                        isHighlighted ? 'bg-blue-500/10' : ''
                      }`}
                    >
                      <td className="px-4 py-3.5">
                        <div className="font-mono text-slate-300 font-medium truncate max-w-[150px]">
                          {job.job_id}
                        </div>
                      </td>
                      <td className="px-4 py-3.5">
                        <span className="px-2 py-0.5 rounded text-[10px] font-semibold bg-slate-800 text-slate-300 uppercase flex items-center space-x-1 w-max">
                          {job.operation_type === 'clip_extract' ? (
                            <Scissors className="w-3 h-3 text-emerald-400" />
                          ) : (
                            <Layers className="w-3 h-3 text-purple-400" />
                          )}
                          <span>{job.operation_type === 'clip_extract' ? 'Clip' : 'Merge'}</span>
                        </span>
                      </td>
                      <td className="px-4 py-3.5">
                        <StatusBadge status={job.status} />
                        {job.cancellation_requested && (
                          <span className="ml-1.5 text-[10px] text-amber-400 italic">(Cancelling)</span>
                        )}
                      </td>
                      <td className="px-4 py-3.5">
                        <div className="w-28">
                          <div className="flex justify-between text-[10px] text-slate-400 mb-1">
                            <span>{Math.round(job.progress_percent)}%</span>
                          </div>
                          <div className="w-full bg-slate-800 h-1.5 rounded-full overflow-hidden">
                            <div
                              className={`h-full transition-all duration-300 ${
                                job.status === 'succeeded'
                                  ? 'bg-emerald-500'
                                  : job.status === 'failed'
                                  ? 'bg-rose-500'
                                  : 'bg-blue-500'
                              }`}
                              style={{ width: `${Math.max(job.progress_percent, job.status === 'succeeded' ? 100 : 0)}%` }}
                            />
                          </div>
                        </div>
                      </td>
                      <td className="px-4 py-3.5 text-slate-400">
                        {job.attempt_count} / {job.max_attempts}
                      </td>
                      <td className="px-4 py-3.5 text-slate-400 font-mono text-[11px]">
                        {new Date(job.created_at).toLocaleTimeString()}
                      </td>
                      <td className="px-4 py-3.5 text-right space-x-2" onClick={(e) => e.stopPropagation()}>
                        {job.status === 'succeeded' && job.result_artifact && (
                          <button
                            onClick={() => handlePlayArtifact(job)}
                            className="inline-flex items-center space-x-1 px-2.5 py-1 bg-emerald-500/15 hover:bg-emerald-500/25 text-emerald-400 rounded-lg text-xs font-medium border border-emerald-500/30 transition-colors"
                          >
                            <Play className="w-3 h-3 fill-emerald-400" />
                            <span>Play Result</span>
                          </button>
                        )}

                        {canCancel && (
                          <button
                            onClick={() => handleCancel(job.job_id)}
                            disabled={isCancelling}
                            className="inline-flex items-center space-x-1 px-2.5 py-1 bg-rose-500/10 hover:bg-rose-500/20 text-rose-400 rounded-lg text-xs font-medium border border-rose-500/30 transition-colors"
                          >
                            <XCircle className="w-3 h-3" />
                            <span>{isCancelling ? 'Cancelling...' : 'Cancel'}</span>
                          </button>
                        )}

                        <button
                          onClick={() => setSelectedJob(job)}
                          className="inline-flex items-center space-x-1 px-2 py-1 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg text-xs transition-colors"
                          title="View Details"
                        >
                          <Info className="w-3 h-3" />
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Job Details Modal */}
      {selectedJob && (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
          <div className="w-full max-w-2xl bg-slate-900 border border-slate-700 rounded-xl overflow-hidden shadow-2xl p-6 space-y-4">
            <div className="flex items-center justify-between border-b border-slate-800 pb-3">
              <div>
                <h3 className="text-base font-bold text-white">Job Details</h3>
                <p className="text-xs text-slate-400 font-mono mt-0.5">{selectedJob.job_id}</p>
              </div>
              <button
                onClick={() => setSelectedJob(null)}
                className="text-slate-400 hover:text-white text-xs px-2.5 py-1 rounded-lg bg-slate-800"
              >
                Close
              </button>
            </div>

            <div className="grid grid-cols-2 gap-4 text-xs">
              <div>
                <span className="text-slate-500">Operation:</span>
                <div className="font-semibold text-slate-200 uppercase mt-0.5">{selectedJob.operation_type}</div>
              </div>
              <div>
                <span className="text-slate-500">Status:</span>
                <div className="mt-0.5">
                  <StatusBadge status={selectedJob.status} />
                </div>
              </div>
              <div>
                <span className="text-slate-500">Progress:</span>
                <div className="font-semibold text-slate-200 mt-0.5">{selectedJob.progress_percent}%</div>
              </div>
              <div>
                <span className="text-slate-500">Attempts / Max:</span>
                <div className="font-semibold text-slate-200 mt-0.5">
                  {selectedJob.attempt_count} / {selectedJob.max_attempts}
                </div>
              </div>
              <div>
                <span className="text-slate-500">Assigned Worker:</span>
                <div className="font-mono text-slate-300 mt-0.5 truncate">
                  {selectedJob.claimed_by || 'Unclaimed / Finished'}
                </div>
              </div>
              <div>
                <span className="text-slate-500">Created At:</span>
                <div className="font-mono text-slate-300 mt-0.5">
                  {new Date(selectedJob.created_at).toLocaleString()}
                </div>
              </div>
            </div>

            {selectedJob.error_message && (
              <div className="p-3 bg-rose-500/10 border border-rose-500/30 rounded-lg text-rose-400 text-xs">
                <span className="font-semibold">Error Message: </span>
                {selectedJob.error_message}
              </div>
            )}

            {selectedJob.result_artifact && (
              <div className="p-3 bg-emerald-500/10 border border-emerald-500/30 rounded-lg text-xs space-y-1">
                <div className="font-semibold text-emerald-400">Result Artifact Created:</div>
                <div className="text-slate-300">Filename: {selectedJob.result_artifact.filename}</div>
                <div className="text-slate-400 font-mono text-[10px]">ID: {selectedJob.result_artifact.id}</div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Video Player Modal */}
      <VideoPlayerModal
        isOpen={playerModal.isOpen}
        onClose={() => setPlayerModal((prev) => ({ ...prev, isOpen: false }))}
        mediaUrl={playerModal.mediaUrl}
        title={playerModal.title}
        format={playerModal.format}
        duration={playerModal.duration}
      />
    </div>
  );
};
