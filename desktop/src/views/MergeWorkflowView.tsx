import React, { useEffect, useState } from 'react';
import { api, ApiError } from '../api/client';
import { MediaArtifact, MergeRequest, JobSubmitResponse } from '../types';
import { NavView } from '../components/Layout';
import {
  Layers,
  ArrowUp,
  ArrowDown,
  Trash2,
  Plus,
  CheckCircle2,
  AlertCircle,
  Clock,
  ArrowRight,
  Loader2,
} from 'lucide-react';

interface MergeWorkflowViewProps {
  initialSelectedClipIds?: string[];
  onNavigate: (view: NavView) => void;
  onViewJob: (jobId: string) => void;
}

interface MergeItem {
  id: string;
  sourceRef: string;
  filename: string;
  duration: number;
}

export const MergeWorkflowView: React.FC<MergeWorkflowViewProps> = ({
  initialSelectedClipIds = [],
  onNavigate,
  onViewJob,
}) => {
  const [availableClips, setAvailableClips] = useState<MediaArtifact[]>([]);
  const [selectedClips, setSelectedClips] = useState<MergeItem[]>([]);
  const [outputFormat, setOutputFormat] = useState<'mp4' | 'mkv' | 'webm'>('mp4');
  const [mode, setMode] = useState<'auto' | 'concat' | 'reencode'>('auto');
  const [resolution, setResolution] = useState('');
  const [isLoadingMedia, setIsLoadingMedia] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [submitResult, setSubmitResult] = useState<JobSubmitResponse | null>(null);
  const [selectedToAdd, setSelectedToAdd] = useState<string>('');

  // Load available clip artifacts from backend
  useEffect(() => {
    async function fetchClips() {
      setIsLoadingMedia(true);
      try {
        const res = await api.listArtifacts({ artifact_type: 'clip', status: 'ready', limit: 50 });
        setAvailableClips(res.items);

        // Populate initial selection if provided
        if (initialSelectedClipIds.length > 0) {
          const matched = res.items
            .filter((art) => initialSelectedClipIds.includes(art.id))
            .map((art) => ({
              id: art.id,
              sourceRef: art.storage_path || art.id,
              filename: art.filename,
              duration: art.duration_seconds || 0,
            }));
          if (matched.length > 0) {
            setSelectedClips(matched);
          }
        }
      } catch (err: any) {
        console.error('Failed to load clip artifacts:', err);
      } finally {
        setIsLoadingMedia(false);
      }
    }

    fetchClips();
  }, [initialSelectedClipIds]);

  const handleAddClip = () => {
    if (!selectedToAdd) return;
    const found = availableClips.find((c) => c.id === selectedToAdd);
    if (!found) return;

    if (selectedClips.some((c) => c.id === found.id)) {
      alert('This clip is already in the merge queue.');
      return;
    }

    setSelectedClips((prev) => [
      ...prev,
      {
        id: found.id,
        sourceRef: found.storage_path || found.id,
        filename: found.filename,
        duration: found.duration_seconds || 0,
      },
    ]);
    setSelectedToAdd('');
  };

  const handleMoveUp = (index: number) => {
    if (index <= 0) return;
    setSelectedClips((prev) => {
      const next = [...prev];
      const temp = next[index - 1];
      next[index - 1] = next[index];
      next[index] = temp;
      return next;
    });
  };

  const handleMoveDown = (index: number) => {
    if (index >= selectedClips.length - 1) return;
    setSelectedClips((prev) => {
      const next = [...prev];
      const temp = next[index + 1];
      next[index + 1] = next[index];
      next[index] = temp;
      return next;
    });
  };

  const handleRemove = (index: number) => {
    setSelectedClips((prev) => prev.filter((_, i) => i !== index));
  };

  const totalDuration = selectedClips.reduce((acc, c) => acc + c.duration, 0);

  const handleSubmitMerge = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMessage(null);
    setSubmitResult(null);

    if (selectedClips.length < 2) {
      setErrorMessage('At least two clips are required to perform a video merge.');
      return;
    }
    if (selectedClips.length > 20) {
      setErrorMessage('A maximum of 20 clips can be merged in one request.');
      return;
    }

    setIsSubmitting(true);
    try {
      const payload: MergeRequest = {
        clips: selectedClips.map((c) => c.sourceRef),
        output_format: outputFormat,
        mode,
        resolution: resolution.trim() || undefined,
      };

      const res = await api.createMergeJob(payload);
      setSubmitResult(res);
    } catch (err: any) {
      const msg = err instanceof ApiError ? err.detail : err.message || 'Failed to submit merge job';
      setErrorMessage(msg);
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      {/* Header */}
      <div>
        <h2 className="text-xl font-bold text-white flex items-center space-x-2">
          <Layers className="w-5 h-5 text-purple-400" />
          <span>Merge Video Clips</span>
        </h2>
        <p className="text-xs text-slate-400 mt-1">
          Combine multiple extracted video clips into a single contiguous sequence with reordering and mode controls.
        </p>
      </div>

      {/* Success Notification */}
      {submitResult && (
        <div className="p-5 rounded-xl bg-purple-500/10 border border-purple-500/30 text-purple-300 space-y-3">
          <div className="flex items-center space-x-3">
            <CheckCircle2 className="w-6 h-6 text-purple-400 shrink-0" />
            <div>
              <div className="font-semibold text-sm">Merge Job Enqueued (HTTP 202)</div>
              <div className="text-xs text-purple-400/80 font-mono mt-0.5">
                Job ID: {submitResult.job_id} • Status: {submitResult.status}
              </div>
            </div>
          </div>
          <div className="flex items-center space-x-3 pt-2 border-t border-purple-500/20">
            <button
              onClick={() => {
                onViewJob(submitResult.job_id);
                onNavigate('jobs');
              }}
              className="px-3.5 py-1.5 bg-purple-500 hover:bg-purple-600 text-slate-950 font-semibold rounded-lg text-xs flex items-center space-x-1.5 transition-colors"
            >
              <span>Track Merge in Background Jobs</span>
              <ArrowRight className="w-3.5 h-3.5" />
            </button>
            <button onClick={() => setSubmitResult(null)} className="px-3 py-1.5 text-xs text-slate-400 hover:text-white">
              Dismiss
            </button>
          </div>
        </div>
      )}

      {/* Error Alert */}
      {errorMessage && (
        <div className="p-4 rounded-xl bg-rose-500/10 border border-rose-500/30 flex items-start space-x-3 text-rose-400 text-xs">
          <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
          <div>
            <span className="font-semibold">Validation error: </span>
            {errorMessage}
          </div>
        </div>
      )}

      {/* Add Clips Selector Bar */}
      <div className="bg-slate-950/60 border border-slate-800 rounded-xl p-5 space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="text-xs font-semibold text-slate-200 uppercase tracking-wider">Select Clips to Sequence</h3>
          <span className="text-xs text-slate-400">
            {availableClips.length} Available Library Clips
          </span>
        </div>

        <div className="flex items-center space-x-3">
          <select
            value={selectedToAdd}
            onChange={(e) => setSelectedToAdd(e.target.value)}
            disabled={isLoadingMedia || availableClips.length === 0}
            className="flex-1 px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-xs text-white focus:outline-none focus:border-purple-500"
          >
            <option value="">-- Choose a clip from your library --</option>
            {availableClips.map((clip) => (
              <option key={clip.id} value={clip.id}>
                {clip.filename} ({Math.round(clip.duration_seconds)}s)
              </option>
            ))}
          </select>

          <button
            type="button"
            onClick={handleAddClip}
            disabled={!selectedToAdd}
            className="px-4 py-2 bg-purple-500 hover:bg-purple-600 disabled:opacity-50 text-slate-950 font-semibold rounded-lg text-xs flex items-center space-x-1.5 transition-colors shrink-0"
          >
            <Plus className="w-3.5 h-3.5" />
            <span>Add to Merge</span>
          </button>
        </div>
      </div>

      {/* Ordered Clips Table */}
      <div className="bg-slate-950/60 border border-slate-800 rounded-xl overflow-hidden shadow-sm">
        <div className="px-5 py-3 border-b border-slate-800 bg-slate-900/60 flex items-center justify-between">
          <div className="flex items-center space-x-3">
            <h3 className="text-xs font-semibold text-slate-200 uppercase tracking-wider">Merge Sequence</h3>
            <span className="text-xs text-purple-400 font-medium">({selectedClips.length} Clips Selected)</span>
          </div>

          <div className="flex items-center space-x-2 text-xs text-slate-400">
            <Clock className="w-3.5 h-3.5 text-slate-400" />
            <span>
              Estimated Duration:{' '}
              <span className="font-semibold text-white">
                {Math.floor(totalDuration / 60)}:{(Math.floor(totalDuration % 60)).toString().padStart(2, '0')}
              </span>
            </span>
          </div>
        </div>

        {selectedClips.length === 0 ? (
          <div className="text-center py-12 text-slate-500 text-xs">
            No clips added yet. Select at least 2 clips from the dropdown above or from the Media Library.
          </div>
        ) : (
          <div className="divide-y divide-slate-800/60">
            {selectedClips.map((clip, index) => (
              <div
                key={`${clip.id}-${index}`}
                className="px-4 py-3 flex items-center justify-between hover:bg-slate-800/20 transition-colors"
              >
                <div className="flex items-center space-x-3 max-w-md">
                  <div className="w-6 h-6 rounded bg-purple-500/20 text-purple-400 flex items-center justify-center font-bold text-xs shrink-0">
                    {index + 1}
                  </div>
                  <div className="truncate">
                    <div className="font-medium text-slate-200 text-xs truncate">{clip.filename}</div>
                    <div className="text-[10px] text-slate-500 font-mono">ID: {clip.id}</div>
                  </div>
                </div>

                <div className="flex items-center space-x-4">
                  <span className="text-xs text-slate-300 font-mono">
                    {Math.floor(clip.duration / 60)}:{(Math.floor(clip.duration % 60)).toString().padStart(2, '0')}
                  </span>

                  <div className="flex items-center space-x-1">
                    <button
                      type="button"
                      onClick={() => handleMoveUp(index)}
                      disabled={index === 0}
                      className="p-1 rounded bg-slate-800 hover:bg-slate-700 disabled:opacity-30 text-slate-300"
                      title="Move Up"
                    >
                      <ArrowUp className="w-3.5 h-3.5" />
                    </button>
                    <button
                      type="button"
                      onClick={() => handleMoveDown(index)}
                      disabled={index === selectedClips.length - 1}
                      className="p-1 rounded bg-slate-800 hover:bg-slate-700 disabled:opacity-30 text-slate-300"
                      title="Move Down"
                    >
                      <ArrowDown className="w-3.5 h-3.5" />
                    </button>
                    <button
                      type="button"
                      onClick={() => handleRemove(index)}
                      className="p-1 rounded bg-rose-500/10 hover:bg-rose-500/20 text-rose-400"
                      title="Remove Clip"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Merge Settings & Submission Form */}
      <form onSubmit={handleSubmitMerge} className="bg-slate-950/60 border border-slate-800 rounded-xl p-6 space-y-5">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div>
            <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-1.5">
              Output Container
            </label>
            <select
              value={outputFormat}
              onChange={(e) => setOutputFormat(e.target.value as any)}
              className="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-xs text-white focus:outline-none focus:border-purple-500"
            >
              <option value="mp4">MP4 (H.264 / AAC)</option>
              <option value="mkv">MKV (Matroska)</option>
              <option value="webm">WebM (VP9 / Opus)</option>
            </select>
          </div>

          <div>
            <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-1.5">
              Merge Mode
            </label>
            <select
              value={mode}
              onChange={(e) => setMode(e.target.value as any)}
              className="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-xs text-white focus:outline-none focus:border-purple-500"
            >
              <option value="auto">Auto (Smart Detect)</option>
              <option value="concat">Concat Protocol (Fast, Same Codecs)</option>
              <option value="reencode">Full Re-encode (Safe, Any Codecs)</option>
            </select>
          </div>

          <div>
            <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-1.5">
              Target Resolution (Optional)
            </label>
            <input
              type="text"
              value={resolution}
              onChange={(e) => setResolution(e.target.value)}
              placeholder="e.g. 1920x1080"
              className="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-xs text-white font-mono focus:outline-none focus:border-purple-500"
            />
          </div>
        </div>

        <button
          type="submit"
          disabled={isSubmitting || selectedClips.length < 2}
          className="w-full py-2.5 px-4 bg-purple-500 hover:bg-purple-600 disabled:opacity-50 text-slate-950 font-semibold rounded-lg text-xs flex items-center justify-center space-x-2 transition-all shadow-md shadow-purple-500/20 active:scale-[0.99]"
        >
          {isSubmitting ? (
            <>
              <Loader2 className="w-4 h-4 animate-spin" />
              <span>Submitting Merge Job to Background Worker...</span>
            </>
          ) : (
            <>
              <Layers className="w-4 h-4" />
              <span>Submit Asynchronous Merge Job</span>
            </>
          )}
        </button>
      </form>
    </div>
  );
};
