import React, { useEffect, useState } from 'react';
import { api, ApiError } from '../api/client';
import { ClipExtractRequest, JobSubmitResponse } from '../types';
import { NavView } from '../components/Layout';
import {
  Scissors,
  AlertCircle,
  CheckCircle2,
  Clock,
  ArrowRight,
  Loader2,
} from 'lucide-react';

interface ClipWorkflowViewProps {
  initialSource?: string;
  initialDuration?: number;
  onNavigate: (view: NavView) => void;
  onViewJob: (jobId: string) => void;
}

export const ClipWorkflowView: React.FC<ClipWorkflowViewProps> = ({
  initialSource = '',
  initialDuration = 0,
  onNavigate,
  onViewJob,
}) => {
  const [source, setSource] = useState(initialSource);
  const [sourceDuration, setSourceDuration] = useState<number>(initialDuration);
  const [startTime, setStartTime] = useState('00:00:00');
  const [endTime, setEndTime] = useState('00:00:10');
  const [mode, setMode] = useState<'precise' | 'fast'>('precise');
  const [outputFormat, setOutputFormat] = useState<'mp4' | 'mkv' | 'webm'>('mp4');
  const [resolution, setResolution] = useState('');
  const [availableMedia, setAvailableMedia] = useState<
    { id: string; name: string; duration: number; sourceRef: string }[]
  >([]);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [submitResult, setSubmitResult] = useState<JobSubmitResponse | null>(null);

  // Load available library media
  useEffect(() => {
    async function loadSources() {
      try {
        const [arts, dls] = await Promise.allSettled([
          api.listArtifacts({ limit: 50 }),
          api.listDownloads(),
        ]);

        const list: { id: string; name: string; duration: number; sourceRef: string }[] = [];

        if (arts.status === 'fulfilled') {
          for (const a of arts.value.items) {
            list.push({
              id: a.id,
              name: a.filename,
              duration: a.duration_seconds || 0,
              sourceRef: a.storage_path || a.id,
            });
          }
        }

        if (dls.status === 'fulfilled') {
          for (const d of dls.value.jobs) {
            list.push({
              id: d.id,
              name: d.title || d.filename || `Download ${d.id}`,
              duration: d.duration || 0,
              sourceRef: d.filename ? `downloads/${d.filename}` : d.id,
            });
          }
        }

        setAvailableMedia(list);
      } catch {
        // Ignore
      }
    }

    loadSources();
  }, []);

  // Update source duration if initialDuration changes
  useEffect(() => {
    if (initialDuration > 0) {
      setSourceDuration(initialDuration);
    }
    if (initialSource) {
      setSource(initialSource);
    }
  }, [initialSource, initialDuration]);

  const handleSelectMedia = (sourceRef: string) => {
    const found = availableMedia.find((m) => m.sourceRef === sourceRef || m.id === sourceRef);
    if (found) {
      setSource(found.sourceRef);
      setSourceDuration(found.duration);
      if (found.duration > 10) {
        setEndTime('00:00:10');
      } else if (found.duration > 0) {
        setEndTime(found.duration.toString());
      }
    } else {
      setSource(sourceRef);
    }
  };

  const parseTimeToSeconds = (t: string): number => {
    if (!t) return 0;
    const parts = t.trim().split(':');
    if (parts.length === 1) return parseFloat(parts[0]) || 0;
    if (parts.length === 2) return parseInt(parts[0], 10) * 60 + parseFloat(parts[1]);
    if (parts.length === 3)
      return parseInt(parts[0], 10) * 3600 + parseInt(parts[1], 10) * 60 + parseFloat(parts[2]);
    return 0;
  };

  const handleValidateAndSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setValidationError(null);
    setSubmitResult(null);

    if (!source.trim()) {
      setValidationError('Source video selection or URL is required.');
      return;
    }

    const startSec = parseTimeToSeconds(startTime);
    const endSec = parseTimeToSeconds(endTime);

    if (startSec < 0) {
      setValidationError('Start time must be greater than or equal to 0.');
      return;
    }

    if (endSec <= startSec) {
      setValidationError('End time must be strictly after start time.');
      return;
    }

    if (sourceDuration > 0 && endSec > sourceDuration + 1) {
      setValidationError(
        `End time (${endTime}) exceeds source duration (${Math.floor(sourceDuration)} seconds).`
      );
      return;
    }

    setIsSubmitting(true);
    try {
      const payload: ClipExtractRequest = {
        source: source.trim(),
        start_time: startTime.trim(),
        end_time: endTime.trim(),
        mode,
        output_format: outputFormat,
        resolution: resolution.trim() || undefined,
      };

      const resp = await api.createClipJob(payload);
      setSubmitResult(resp);
    } catch (err: any) {
      const msg = err instanceof ApiError ? err.detail : err.message || 'Failed to submit clip job';
      setValidationError(msg);
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      {/* Header */}
      <div>
        <h2 className="text-xl font-bold text-white flex items-center space-x-2">
          <Scissors className="w-5 h-5 text-emerald-400" />
          <span>Extract Video Clip</span>
        </h2>
        <p className="text-xs text-slate-400 mt-1">
          Submit an asynchronous media processing job to extract a frame-accurate clip segment.
        </p>
      </div>

      {/* Success Notification */}
      {submitResult && (
        <div className="p-5 rounded-xl bg-emerald-500/10 border border-emerald-500/30 text-emerald-300 space-y-3">
          <div className="flex items-center space-x-3">
            <CheckCircle2 className="w-6 h-6 text-emerald-400 shrink-0" />
            <div>
              <div className="font-semibold text-sm">Clip Job Accepted (HTTP 202)</div>
              <div className="text-xs text-emerald-400/80 font-mono mt-0.5">
                Job ID: {submitResult.job_id} • Status: {submitResult.status}
              </div>
            </div>
          </div>
          <div className="flex items-center space-x-3 pt-2 border-t border-emerald-500/20">
            <button
              onClick={() => {
                onViewJob(submitResult.job_id);
                onNavigate('jobs');
              }}
              className="px-3.5 py-1.5 bg-emerald-500 hover:bg-emerald-600 text-slate-950 font-semibold rounded-lg text-xs flex items-center space-x-1.5 transition-colors"
            >
              <span>Track Job in Background Jobs</span>
              <ArrowRight className="w-3.5 h-3.5" />
            </button>
            <button
              onClick={() => setSubmitResult(null)}
              className="px-3 py-1.5 text-xs text-slate-400 hover:text-white"
            >
              Dismiss
            </button>
          </div>
        </div>
      )}

      {/* Validation Error Alert */}
      {validationError && (
        <div className="p-4 rounded-xl bg-rose-500/10 border border-rose-500/30 flex items-start space-x-3 text-rose-400 text-xs">
          <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
          <div>
            <span className="font-semibold">Validation error: </span>
            {validationError}
          </div>
        </div>
      )}

      {/* Form */}
      <form onSubmit={handleValidateAndSubmit} className="bg-slate-950/60 border border-slate-800 rounded-xl p-6 space-y-5">
        {/* Source Media Picker */}
        <div>
          <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-1.5">
            Source Video
          </label>

          {availableMedia.length > 0 && (
            <div className="mb-2">
              <select
                value={source}
                onChange={(e) => handleSelectMedia(e.target.value)}
                className="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-xs text-white focus:outline-none focus:border-emerald-500"
              >
                <option value="">-- Choose from your Library or enter below --</option>
                {availableMedia.map((m) => (
                  <option key={m.id} value={m.sourceRef}>
                    {m.name} {m.duration > 0 ? `(${Math.round(m.duration)}s)` : ''}
                  </option>
                ))}
              </select>
            </div>
          )}

          <div className="flex items-center space-x-2">
            <input
              type="text"
              required
              value={source}
              onChange={(e) => {
                setSource(e.target.value);
                const found = availableMedia.find((m) => m.sourceRef === e.target.value || m.id === e.target.value);
                if (found) setSourceDuration(found.duration);
              }}
              placeholder="Source path, library ID, or direct URL..."
              className="flex-1 px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-xs text-white placeholder-slate-500 focus:outline-none focus:border-emerald-500 font-mono transition-colors"
            />
            <button
              type="button"
              onClick={() => onNavigate('media')}
              className="px-3 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs rounded-lg border border-slate-700 transition-colors shrink-0"
            >
              Browse Library
            </button>
          </div>

          {sourceDuration > 0 && (
            <div className="mt-1 text-[11px] text-slate-400">
              Source duration: <span className="font-semibold text-emerald-400">{Math.floor(sourceDuration)}s</span>
            </div>
          )}
        </div>

        {/* Start and End Times */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-1.5">
              Start Time (HH:MM:SS or Sec)
            </label>
            <div className="relative">
              <Clock className="w-3.5 h-3.5 absolute left-3 top-3 text-slate-500" />
              <input
                type="text"
                required
                value={startTime}
                onChange={(e) => setStartTime(e.target.value)}
                placeholder="00:00:00"
                className="w-full pl-9 pr-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-xs text-white placeholder-slate-500 font-mono focus:outline-none focus:border-emerald-500 transition-colors"
              />
            </div>
          </div>

          <div>
            <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-1.5">
              End Time (HH:MM:SS or Sec)
            </label>
            <div className="relative">
              <Clock className="w-3.5 h-3.5 absolute left-3 top-3 text-slate-500" />
              <input
                type="text"
                required
                value={endTime}
                onChange={(e) => setEndTime(e.target.value)}
                placeholder="00:01:30"
                className="w-full pl-9 pr-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-xs text-white placeholder-slate-500 font-mono focus:outline-none focus:border-emerald-500 transition-colors"
              />
            </div>
          </div>
        </div>

        {/* Cut Mode Toggle */}
        <div>
          <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-2">
            Extraction Precision Mode
          </label>
          <div className="grid grid-cols-2 gap-3">
            <div
              onClick={() => setMode('precise')}
              className={`p-3.5 rounded-xl border cursor-pointer transition-all ${
                mode === 'precise'
                  ? 'bg-emerald-500/15 border-emerald-500/50 text-white'
                  : 'bg-slate-900 border-slate-800 text-slate-400 hover:border-slate-700'
              }`}
            >
              <div className="flex items-center justify-between mb-1">
                <span className="font-semibold text-xs">Precise Mode (Default)</span>
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-400 font-medium">
                  Accurate
                </span>
              </div>
              <p className="text-[11px] text-slate-400">
                Full re-encode cut. Guarantees frame-accurate cuts at exact millisecond boundaries.
              </p>
            </div>

            <div
              onClick={() => setMode('fast')}
              className={`p-3.5 rounded-xl border cursor-pointer transition-all ${
                mode === 'fast'
                  ? 'bg-emerald-500/15 border-emerald-500/50 text-white'
                  : 'bg-slate-900 border-slate-800 text-slate-400 hover:border-slate-700'
              }`}
            >
              <div className="flex items-center justify-between mb-1">
                <span className="font-semibold text-xs">Fast Mode</span>
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-purple-500/20 text-purple-400 font-medium">
                  Instant
                </span>
              </div>
              <p className="text-[11px] text-slate-400">
                Stream copy without re-encoding. Cuts occur on nearest I-frame keyframes.
              </p>
            </div>
          </div>
        </div>

        {/* Format and Resolution */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-2 border-t border-slate-800/80">
          <div>
            <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-1.5">
              Output Container Format
            </label>
            <select
              value={outputFormat}
              onChange={(e) => setOutputFormat(e.target.value as 'mp4' | 'mkv' | 'webm')}
              className="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-xs text-white focus:outline-none focus:border-emerald-500"
            >
              <option value="mp4">MP4 (H.264 / AAC - Universal)</option>
              <option value="mkv">MKV (Matroska)</option>
              <option value="webm">WebM (VP9 / Opus)</option>
            </select>
          </div>

          <div>
            <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-1.5">
              Resolution (Optional)
            </label>
            <input
              type="text"
              value={resolution}
              onChange={(e) => setResolution(e.target.value)}
              placeholder="e.g. 1920x1080 or leave blank"
              className="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-xs text-white placeholder-slate-500 font-mono focus:outline-none focus:border-emerald-500"
            />
          </div>
        </div>

        {/* Submit */}
        <button
          type="submit"
          disabled={isSubmitting}
          className="w-full py-2.5 px-4 bg-emerald-500 hover:bg-emerald-600 disabled:bg-emerald-500/50 text-slate-950 font-semibold rounded-lg text-xs flex items-center justify-center space-x-2 transition-all shadow-md shadow-emerald-500/20 active:scale-[0.99]"
        >
          {isSubmitting ? (
            <>
              <Loader2 className="w-4 h-4 animate-spin" />
              <span>Submitting Job to Background Worker...</span>
            </>
          ) : (
            <>
              <Scissors className="w-4 h-4" />
              <span>Submit Asynchronous Clip Job</span>
            </>
          )}
        </button>
      </form>
    </div>
  );
};
