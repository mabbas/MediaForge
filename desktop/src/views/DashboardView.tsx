import React, { useEffect, useState } from 'react';
import { useAuth } from '../context/AuthContext';
import { useApiStatus } from '../context/ApiStatusContext';
import { api } from '../api/client';
import {
  Film,
  Download,
  Scissors,
  Layers,
  FileText,
  ListTodo,
  ShieldCheck,
  HardDrive,
  RefreshCw,
  ArrowUpRight,
} from 'lucide-react';
import { NavView } from '../components/Layout';

interface DashboardViewProps {
  onNavigate: (view: NavView) => void;
}

export const DashboardView: React.FC<DashboardViewProps> = ({ onNavigate }) => {
  const { user } = useAuth();
  const { status, version } = useApiStatus();
  const [stats, setStats] = useState({
    artifactsCount: 0,
    jobsCount: 0,
    downloadsCount: 0,
  });
  const [isLoading, setIsLoading] = useState(false);

  const fetchStats = async () => {
    setIsLoading(true);
    try {
      const [artifactsRes, jobsRes, downloadsRes] = await Promise.allSettled([
        api.listArtifacts({ limit: 1 }),
        api.listJobs({ limit: 1 }),
        api.listDownloads(),
      ]);

      setStats({
        artifactsCount: artifactsRes.status === 'fulfilled' ? artifactsRes.value.total : 0,
        jobsCount: jobsRes.status === 'fulfilled' ? jobsRes.value.total : 0,
        downloadsCount: downloadsRes.status === 'fulfilled' ? downloadsRes.value.total : 0,
      });
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchStats();
  }, []);

  return (
    <div className="space-y-6 max-w-6xl mx-auto">
      {/* Welcome Banner */}
      <div className="p-6 rounded-2xl bg-gradient-to-r from-emerald-950/40 via-slate-900 to-slate-900 border border-emerald-500/20 flex flex-col md:flex-row items-start md:items-center justify-between gap-4">
        <div>
          <span className="text-xs font-semibold uppercase tracking-wider text-emerald-400 bg-emerald-500/10 px-2.5 py-1 rounded-full border border-emerald-500/20">
            Tenant: {user?.tenant_id.slice(0, 8)}...
          </span>
          <h2 className="text-2xl font-bold text-white mt-2">Welcome to MediaForge</h2>
          <p className="text-sm text-slate-400 mt-1 max-w-xl">
            Commercial media processing, persistent background jobs, and isolated desktop playback powered by FastAPI
            and FFmpeg.
          </p>
        </div>
        <button
          onClick={fetchStats}
          disabled={isLoading}
          className="flex items-center space-x-2 px-3.5 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg text-xs font-medium border border-slate-700 transition-colors"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${isLoading ? 'animate-spin' : ''}`} />
          <span>Refresh Metrics</span>
        </button>
      </div>

      {/* KPI Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
        <div
          onClick={() => onNavigate('media')}
          className="p-5 rounded-xl bg-slate-800/40 border border-slate-800 hover:border-slate-700 transition-all cursor-pointer group"
        >
          <div className="flex items-center justify-between">
            <div className="p-2.5 rounded-lg bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
              <Film className="w-5 h-5" />
            </div>
            <ArrowUpRight className="w-4 h-4 text-slate-500 group-hover:text-emerald-400 transition-colors" />
          </div>
          <div className="mt-4">
            <div className="text-2xl font-bold text-white">{stats.artifactsCount}</div>
            <div className="text-xs text-slate-400 mt-0.5">Media Artifacts</div>
          </div>
        </div>

        <div
          onClick={() => onNavigate('jobs')}
          className="p-5 rounded-xl bg-slate-800/40 border border-slate-800 hover:border-slate-700 transition-all cursor-pointer group"
        >
          <div className="flex items-center justify-between">
            <div className="p-2.5 rounded-lg bg-blue-500/10 text-blue-400 border border-blue-500/20">
              <ListTodo className="w-5 h-5" />
            </div>
            <ArrowUpRight className="w-4 h-4 text-slate-500 group-hover:text-blue-400 transition-colors" />
          </div>
          <div className="mt-4">
            <div className="text-2xl font-bold text-white">{stats.jobsCount}</div>
            <div className="text-xs text-slate-400 mt-0.5">Background Media Jobs</div>
          </div>
        </div>

        <div
          onClick={() => onNavigate('downloads')}
          className="p-5 rounded-xl bg-slate-800/40 border border-slate-800 hover:border-slate-700 transition-all cursor-pointer group"
        >
          <div className="flex items-center justify-between">
            <div className="p-2.5 rounded-lg bg-teal-500/10 text-teal-400 border border-teal-500/20">
              <Download className="w-5 h-5" />
            </div>
            <ArrowUpRight className="w-4 h-4 text-slate-500 group-hover:text-teal-400 transition-colors" />
          </div>
          <div className="mt-4">
            <div className="text-2xl font-bold text-white">{stats.downloadsCount}</div>
            <div className="text-xs text-slate-400 mt-0.5">Queued / Active Downloads</div>
          </div>
        </div>
      </div>

      {/* Quick Actions Bar */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <button
          onClick={() => onNavigate('downloads')}
          className="p-4 rounded-xl bg-slate-900 border border-slate-800 hover:border-slate-700 flex flex-col items-center justify-center space-y-2 text-center transition-colors group"
        >
          <Download className="w-5 h-5 text-teal-400 group-hover:scale-110 transition-transform" />
          <span className="text-xs font-semibold text-slate-200">Download Video</span>
        </button>

        <button
          onClick={() => onNavigate('clip')}
          className="p-4 rounded-xl bg-slate-900 border border-slate-800 hover:border-slate-700 flex flex-col items-center justify-center space-y-2 text-center transition-colors group"
        >
          <Scissors className="w-5 h-5 text-emerald-400 group-hover:scale-110 transition-transform" />
          <span className="text-xs font-semibold text-slate-200">Extract Clip</span>
        </button>

        <button
          onClick={() => onNavigate('merge')}
          className="p-4 rounded-xl bg-slate-900 border border-slate-800 hover:border-slate-700 flex flex-col items-center justify-center space-y-2 text-center transition-colors group"
        >
          <Layers className="w-5 h-5 text-purple-400 group-hover:scale-110 transition-transform" />
          <span className="text-xs font-semibold text-slate-200">Merge Clips</span>
        </button>

        <button
          onClick={() => onNavigate('transcripts')}
          className="p-4 rounded-xl bg-slate-900 border border-slate-800 hover:border-slate-700 flex flex-col items-center justify-center space-y-2 text-center transition-colors group"
        >
          <FileText className="w-5 h-5 text-blue-400 group-hover:scale-110 transition-transform" />
          <span className="text-xs font-semibold text-slate-200">Subtitles / Transcript</span>
        </button>
      </div>

      {/* Security & System Info Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
        <div className="p-5 rounded-xl bg-slate-800/30 border border-slate-800/80">
          <div className="flex items-center space-x-2 text-slate-200 font-semibold text-sm mb-3">
            <ShieldCheck className="w-4 h-4 text-emerald-400" />
            <span>Active Security Sandbox</span>
          </div>
          <ul className="space-y-2 text-xs text-slate-400">
            <li className="flex items-center justify-between">
              <span>Context Isolation:</span>
              <span className="text-emerald-400 font-mono">true (Enforced)</span>
            </li>
            <li className="flex items-center justify-between">
              <span>Node.js Integration:</span>
              <span className="text-emerald-400 font-mono">false (Disabled)</span>
            </li>
            <li className="flex items-center justify-between">
              <span>Media Stream Protocol:</span>
              <span className="text-slate-300 font-mono">mediaforge-media:// (Isolated)</span>
            </li>
            <li className="flex items-center justify-between">
              <span>Session Vault:</span>
              <span className="text-slate-300 font-mono">safeStorage (Encrypted)</span>
            </li>
          </ul>
        </div>

        <div className="p-5 rounded-xl bg-slate-800/30 border border-slate-800/80">
          <div className="flex items-center space-x-2 text-slate-200 font-semibold text-sm mb-3">
            <HardDrive className="w-4 h-4 text-teal-400" />
            <span>Backend Connection</span>
          </div>
          <ul className="space-y-2 text-xs text-slate-400">
            <li className="flex items-center justify-between">
              <span>Endpoint:</span>
              <span className="text-slate-300 font-mono">{api.getBaseUrl()}</span>
            </li>
            <li className="flex items-center justify-between">
              <span>Status:</span>
              <span className={status === 'connected' ? 'text-emerald-400 font-medium' : 'text-rose-400 font-medium'}>
                {status.toUpperCase()}
              </span>
            </li>
            <li className="flex items-center justify-between">
              <span>Backend Core:</span>
              <span className="text-slate-300 font-mono">FastAPI {version ? `v${version}` : ''}</span>
            </li>
            <li className="flex items-center justify-between">
              <span>User Role & Tier:</span>
              <span className="text-emerald-400 font-medium capitalize">
                {user?.role} / {user?.tier}
              </span>
            </li>
          </ul>
        </div>
      </div>
    </div>
  );
};
