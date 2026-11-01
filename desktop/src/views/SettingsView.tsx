import React, { useEffect, useState } from 'react';
import { api } from '../api/client';
import { useApiStatus } from '../context/ApiStatusContext';
import { TenantCommercialSubscription } from '../types';
import {
  Settings,
  Server,
  Trash2,
  CheckCircle2,
  AlertCircle,
  RefreshCw,
  Cpu,
  HardDrive,
  CreditCard,
  Clock,
  Info,
} from 'lucide-react';

export const SettingsView: React.FC = () => {
  const { version, checkConnection } = useApiStatus();
  const [backendUrl, setBackendUrl] = useState(api.getBaseUrl());
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null);
  const [isTesting, setIsTesting] = useState(false);
  const [isClearingCache, setIsClearingCache] = useState(false);
  const [cacheClearSuccess, setCacheClearSuccess] = useState(false);
  const [appVersion, setAppVersion] = useState<string>('0.1.0');
  const [platform, setPlatform] = useState<string>('win32');
  const [subscription, setSubscription] = useState<TenantCommercialSubscription | null>(null);
  const [isSubLoading, setIsSubLoading] = useState(false);

  const exceededChecks = subscription
    ? Object.values(subscription.checks || {}).filter((c) => c.is_exceeded)
    : [];

  useEffect(() => {
    if (window.mediaforge) {
      window.mediaforge.getAppVersion().then(setAppVersion).catch(() => {});
      window.mediaforge.getPlatform().then(setPlatform).catch(() => {});
    }
    loadSubscription();
  }, []);

  const loadSubscription = async () => {
    setIsSubLoading(true);
    try {
      const data = await api.getSubscription();
      setSubscription(data);
    } catch {
      // Ignored for unauthenticated or local network dropouts
    } finally {
      setIsSubLoading(false);
    }
  };

  const formatBytes = (bytes: number): string => {
    if (!bytes || bytes === 0) return '0 MB';
    if (bytes >= 1024 * 1024 * 1024) {
      return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
    }
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  const handleSaveAndTestUrl = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsTesting(true);
    setTestResult(null);

    try {
      if (window.mediaforge?.backend) {
        const syncRes = await window.mediaforge.backend.setUrl(backendUrl);
        if (!syncRes.success) {
          throw new Error(syncRes.error || 'Failed to update backend URL in desktop process');
        }
      }
      api.setBaseUrl(backendUrl);
      const res = await api.checkHealth();
      setTestResult({
        ok: true,
        message: `Successfully connected! Status: ${res.status}, Version: ${res.version}, Uptime: ${Math.round(
          res.uptime_seconds
        )}s`,
      });
      await checkConnection();
    } catch (err: any) {
      setTestResult({
        ok: false,
        message: err.message || 'Failed to reach API endpoint',
      });
    } finally {
      setIsTesting(false);
    }
  };

  const handleClearCache = async () => {
    if (!window.mediaforge) return;
    if (!confirm('Are you sure you want to purge all cached local media?')) return;
    setIsClearingCache(true);
    setCacheClearSuccess(false);

    try {
      const ok = await window.mediaforge.media.clearCache();
      if (ok) {
        setCacheClearSuccess(true);
        setTimeout(() => setCacheClearSuccess(false), 3000);
      }
    } catch (err: any) {
      alert(`Failed to clear cache: ${err.message}`);
    } finally {
      setIsClearingCache(false);
    }
  };

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      {/* Header */}
      <div>
        <h2 className="text-xl font-bold text-white flex items-center space-x-2">
          <Settings className="w-5 h-5 text-emerald-400" />
          <span>Application Settings</span>
        </h2>
        <p className="text-xs text-slate-400 mt-1">
          Configure API endpoints, manage offline storage, and view runtime security details.
        </p>
      </div>

      {/* Commercial Subscription & Quota Limits */}
      <div className="bg-slate-950/60 border border-slate-800 rounded-xl p-6 space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center space-x-2 text-slate-200 font-semibold text-sm">
            <CreditCard className="w-4 h-4 text-emerald-400" />
            <span>Commercial Plan & Entitlements</span>
          </div>
          <button
            onClick={loadSubscription}
            disabled={isSubLoading}
            className="text-xs text-slate-400 hover:text-slate-200 flex items-center space-x-1"
          >
            <RefreshCw className={`w-3 h-3 ${isSubLoading ? 'animate-spin' : ''}`} />
            <span>Refresh</span>
          </button>
        </div>

        {subscription ? (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3 p-3.5 bg-slate-900/80 border border-slate-800 rounded-lg">
              <div>
                <span className="text-[10px] text-slate-500 font-medium uppercase tracking-wider">Current Plan</span>
                <div className="text-sm font-bold text-white flex items-center space-x-2 mt-0.5">
                  <span>{subscription.plan_name}</span>
                  <span className="text-[10px] px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 font-mono">
                    v{subscription.plan_version}
                  </span>
                  <span className={`text-[10px] px-2 py-0.5 rounded capitalize font-medium ${
                    subscription.subscription_status === 'active'
                      ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'
                      : subscription.subscription_status === 'legacy_active'
                      ? 'bg-blue-500/10 text-blue-400 border border-blue-500/20'
                      : subscription.subscription_status === 'trial'
                      ? 'bg-amber-500/10 text-amber-400 border border-amber-500/20'
                      : subscription.subscription_status === 'grace_period'
                      ? 'bg-amber-500/20 text-amber-300 border border-amber-500/40'
                      : subscription.subscription_status === 'scheduled_cancellation'
                      ? 'bg-purple-500/15 text-purple-300 border border-purple-500/30'
                      : 'bg-rose-500/10 text-rose-400 border border-rose-500/20'
                  }`}>
                    {subscription.subscription_status.replace('_', ' ')}
                  </span>
                </div>
              </div>

              {subscription.is_trial && subscription.trial_days_remaining !== null && (
                <div className="text-right">
                  <span className="text-[10px] text-amber-400 font-medium flex items-center space-x-1 justify-end">
                    <Clock className="w-3 h-3" />
                    <span>Trial Remaining</span>
                  </span>
                  <div className="text-xs font-semibold text-white mt-0.5">
                    {subscription.trial_days_remaining} days ({subscription.trial_expires_at?.slice(0, 10)})
                  </div>
                </div>
              )}

              {subscription.subscription_status === 'grace_period' && subscription.grace_period_expires_at && (
                <div className="text-right">
                  <span className="text-[10px] text-amber-400 font-medium flex items-center space-x-1 justify-end">
                    <Clock className="w-3 h-3" />
                    <span>Grace Period</span>
                  </span>
                  <div className="text-xs font-semibold text-white mt-0.5">
                    {subscription.grace_period_remaining_days ?? 0} days ({subscription.grace_period_expires_at.slice(0, 10)})
                  </div>
                </div>
              )}
            </div>

            {/* Scheduled Transition Notice */}
            {subscription.scheduled_transition && (
              <div className="flex items-start space-x-3 p-3 bg-indigo-950/40 border border-indigo-500/30 rounded-lg text-indigo-300">
                <Info className="w-4 h-4 text-indigo-400 shrink-0 mt-0.5" />
                <div className="text-xs space-y-0.5">
                  <div className="font-semibold text-indigo-200">
                    Scheduled Plan Change: {subscription.scheduled_transition.transition_type.toUpperCase()}
                  </div>
                  <div className="text-indigo-300/90">
                    Target plan: {subscription.scheduled_transition.target_plan_name || subscription.scheduled_transition.target_plan_code || 'Custom'} effective on{' '}
                    <span className="font-mono text-white">{subscription.scheduled_transition.scheduled_at.slice(0, 10)}</span>.
                  </div>
                </div>
              </div>
            )}

            {/* Suspension Notice */}
            {subscription.is_suspended && (
              <div className="flex items-start space-x-3 p-3.5 bg-rose-950/50 border border-rose-500/50 rounded-lg text-rose-300">
                <AlertCircle className="w-5 h-5 text-rose-400 shrink-0 mt-0.5" />
                <div className="text-xs space-y-1">
                  <div className="font-semibold text-rose-200">Commercial Access Suspended</div>
                  <div>{subscription.suspension_reason || 'Your commercial subscription is currently suspended pending review.'}</div>
                </div>
              </div>
            )}

            {/* Quota Exceeded Warning Banner */}
            {exceededChecks.length > 0 && (
              <div className="flex items-start space-x-3 p-3.5 bg-rose-950/40 border border-rose-500/40 rounded-lg text-rose-300">
                <AlertCircle className="w-5 h-5 text-rose-400 shrink-0 mt-0.5" />
                <div className="text-xs space-y-1">
                  <div className="font-semibold text-rose-200">
                    Plan Quota Limit Reached ({exceededChecks.length} metric{exceededChecks.length > 1 ? 's' : ''})
                  </div>
                  <div className="text-rose-300/90">
                    {exceededChecks.map((c) => c.message || `${c.metric} limit reached`).join('. ')}
                  </div>
                  <div className="text-[11px] text-rose-400/80 pt-0.5">
                    Usage will automatically reset on {subscription.period_reset_at?.slice(0, 10) || 'the next billing cycle'}.
                  </div>
                </div>
              </div>
            )}

            {/* Quota Meters Grid */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs">
              <div className="p-3 bg-slate-900/60 border border-slate-800 rounded-lg space-y-1">
                <div className="text-slate-400 flex justify-between">
                  <span>Monthly Downloads</span>
                  <span className="font-mono text-slate-200">
                    {subscription.consumption.monthly_download_count || 0} /{' '}
                    {subscription.checks.monthly_download_count?.is_unlimited
                      ? 'Unlimited'
                      : subscription.checks.monthly_download_count?.limit_value ?? '—'}
                  </span>
                </div>
                <div className="w-full bg-slate-800 h-1.5 rounded-full overflow-hidden">
                  <div
                    className="bg-emerald-500 h-full rounded-full transition-all"
                    style={{
                      width: subscription.checks.monthly_download_count?.is_unlimited
                        ? '10%'
                        : `${Math.min(
                            100,
                            ((subscription.consumption.monthly_download_count || 0) /
                              (subscription.checks.monthly_download_count?.limit_value || 1)) *
                              100
                          )}%`,
                    }}
                  />
                </div>
              </div>

              <div className="p-3 bg-slate-900/60 border border-slate-800 rounded-lg space-y-1">
                <div className="text-slate-400 flex justify-between">
                  <span>Download Bandwidth</span>
                  <span className="font-mono text-slate-200">
                    {formatBytes(subscription.consumption.monthly_download_bytes || 0)} /{' '}
                    {subscription.checks.monthly_download_bytes?.is_unlimited
                      ? 'Unlimited'
                      : formatBytes(subscription.checks.monthly_download_bytes?.limit_value || 0)}
                  </span>
                </div>
                <div className="w-full bg-slate-800 h-1.5 rounded-full overflow-hidden">
                  <div
                    className="bg-teal-500 h-full rounded-full transition-all"
                    style={{
                      width: subscription.checks.monthly_download_bytes?.is_unlimited
                        ? '10%'
                        : `${Math.min(
                            100,
                            ((subscription.consumption.monthly_download_bytes || 0) /
                              (subscription.checks.monthly_download_bytes?.limit_value || 1)) *
                              100
                          )}%`,
                    }}
                  />
                </div>
              </div>

              <div className="p-3 bg-slate-900/60 border border-slate-800 rounded-lg space-y-1">
                <div className="text-slate-400 flex justify-between">
                  <span>Persistent Storage</span>
                  <span className="font-mono text-slate-200">
                    {formatBytes(subscription.consumption.storage_bytes || 0)} /{' '}
                    {subscription.checks.storage_bytes?.is_unlimited
                      ? 'Unlimited'
                      : formatBytes(subscription.checks.storage_bytes?.limit_value || 0)}
                  </span>
                </div>
                <div className="w-full bg-slate-800 h-1.5 rounded-full overflow-hidden">
                  <div
                    className="bg-blue-500 h-full rounded-full transition-all"
                    style={{
                      width: subscription.checks.storage_bytes?.is_unlimited
                        ? '10%'
                        : `${Math.min(
                            100,
                            ((subscription.consumption.storage_bytes || 0) /
                              (subscription.checks.storage_bytes?.limit_value || 1)) *
                              100
                          )}%`,
                    }}
                  />
                </div>
              </div>

              <div className="p-3 bg-slate-900/60 border border-slate-800 rounded-lg space-y-1">
                <div className="text-slate-400 flex justify-between">
                  <span>Concurrent Background Jobs</span>
                  <span className="font-mono text-slate-200">
                    {subscription.consumption.max_concurrent_jobs || 0} /{' '}
                    {subscription.checks.max_concurrent_jobs?.is_unlimited
                      ? 'Unlimited'
                      : subscription.checks.max_concurrent_jobs?.limit_value ?? '—'}
                  </span>
                </div>
                <div className="w-full bg-slate-800 h-1.5 rounded-full overflow-hidden">
                  <div
                    className="bg-indigo-500 h-full rounded-full transition-all"
                    style={{
                      width: subscription.checks.max_concurrent_jobs?.is_unlimited
                        ? '10%'
                        : `${Math.min(
                            100,
                            ((subscription.consumption.max_concurrent_jobs || 0) /
                              (subscription.checks.max_concurrent_jobs?.limit_value || 1)) *
                              100
                          )}%`,
                    }}
                  />
                </div>
              </div>
            </div>

            <div className="flex items-center space-x-2 text-[11px] text-slate-500 bg-slate-900/40 p-2.5 rounded-lg border border-slate-800/80">
              <Info className="w-3.5 h-3.5 text-slate-400 shrink-0" />
              <span>
                Usage resets on {subscription.period_reset_at?.slice(0, 10)}. Entitlement limits are advisory for local preview.
              </span>
            </div>
          </div>
        ) : (
          <div className="text-xs text-slate-400 italic">
            {isSubLoading ? 'Loading subscription details...' : 'Subscription information unavailable or offline.'}
          </div>
        )}
      </div>

      {/* Backend API Configuration */}
      <div className="bg-slate-950/60 border border-slate-800 rounded-xl p-6 space-y-4">
        <div className="flex items-center space-x-2 text-slate-200 font-semibold text-sm">
          <Server className="w-4 h-4 text-emerald-400" />
          <span>FastAPI Backend Connection</span>
        </div>

        <form onSubmit={handleSaveAndTestUrl} className="space-y-3">
          <div>
            <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-1.5">
              API Base URL
            </label>
            <input
              type="text"
              required
              value={backendUrl}
              onChange={(e) => setBackendUrl(e.target.value)}
              placeholder="http://127.0.0.1:58000/api/v1"
              className="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-xs text-white font-mono focus:outline-none focus:border-emerald-500"
            />
            <p className="text-[11px] text-slate-500 mt-1">
              Loopback HTTP permitted for local development. Remote endpoints must use HTTPS.
            </p>
          </div>

          <div className="flex items-center space-x-3 pt-2">
            <button
              type="submit"
              disabled={isTesting}
              className="px-4 py-2 bg-emerald-500 hover:bg-emerald-600 disabled:opacity-50 text-slate-950 font-semibold rounded-lg text-xs flex items-center space-x-1.5 transition-colors"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${isTesting ? 'animate-spin' : ''}`} />
              <span>{isTesting ? 'Testing Connection...' : 'Save & Test Connection'}</span>
            </button>
          </div>
        </form>

        {testResult && (
          <div
            className={`p-3.5 rounded-lg border text-xs flex items-center space-x-2.5 ${
              testResult.ok
                ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-300'
                : 'bg-rose-500/10 border-rose-500/30 text-rose-400'
            }`}
          >
            {testResult.ok ? (
              <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />
            ) : (
              <AlertCircle className="w-4 h-4 text-rose-400 shrink-0" />
            )}
            <span>{testResult.message}</span>
          </div>
        )}
      </div>

      {/* Local Media Storage & Cache */}
      <div className="bg-slate-950/60 border border-slate-800 rounded-xl p-6 space-y-4">
        <div className="flex items-center space-x-2 text-slate-200 font-semibold text-sm">
          <HardDrive className="w-4 h-4 text-teal-400" />
          <span>Local Media Cache</span>
        </div>

        <p className="text-xs text-slate-400 leading-relaxed">
          Media downloaded for playback is securely contained in the application cache directory (%APPDATA%/MediaForge/media_cache).
          Clearing the cache frees local disk space without deleting server-side media artifacts.
        </p>

        <div className="pt-2 flex items-center space-x-3">
          <button
            onClick={handleClearCache}
            disabled={isClearingCache}
            className="px-3.5 py-2 bg-rose-500/10 hover:bg-rose-500/20 text-rose-400 border border-rose-500/30 rounded-lg text-xs font-semibold flex items-center space-x-1.5 transition-colors"
          >
            <Trash2 className="w-3.5 h-3.5" />
            <span>{isClearingCache ? 'Clearing...' : 'Purge Local Media Cache'}</span>
          </button>
          {cacheClearSuccess && (
            <span className="text-xs text-emerald-400 font-medium flex items-center space-x-1">
              <CheckCircle2 className="w-3.5 h-3.5" />
              <span>Cache purged successfully!</span>
            </span>
          )}
        </div>
      </div>

      {/* Platform & Runtime Information */}
      <div className="bg-slate-950/60 border border-slate-800 rounded-xl p-6 space-y-3">
        <div className="flex items-center space-x-2 text-slate-200 font-semibold text-sm">
          <Cpu className="w-4 h-4 text-blue-400" />
          <span>Runtime Environment</span>
        </div>

        <div className="grid grid-cols-2 gap-3 text-xs">
          <div>
            <span className="text-slate-500">Desktop Client:</span>
            <div className="font-mono text-slate-300">MediaForge v{appVersion}</div>
          </div>
          <div>
            <span className="text-slate-500">Operating System:</span>
            <div className="font-mono text-slate-300 capitalize">{platform} (Windows 11)</div>
          </div>
          <div>
            <span className="text-slate-500">Security Architecture:</span>
            <div className="font-mono text-emerald-400">Isolated Sandbox & ContextBridge</div>
          </div>
          <div>
            <span className="text-slate-500">Backend API:</span>
            <div className="font-mono text-slate-300">FastAPI {version || 'Unknown'}</div>
          </div>
        </div>
      </div>
    </div>
  );
};
