"use client";

import { useEffect, useState } from "react";
import {
  Activity,
  Pause,
  Play,
  RotateCcw,
  Clock,
  ShieldAlert,
  ShieldCheck,
  AlertCircle,
  RefreshCw,
  Loader2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import adminApi from "@/lib/admin-api";
import { toast } from "sonner";

export default function AdminSchedulerPage() {
  const [status, setStatus] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  // Modals
  const [pauseModal, setPauseModal] = useState({ open: false, reason: "" });
  const [resumeModal, setResumeModal] = useState({ open: false, reason: "" });
  const [retryModal, setRetryModal] = useState({ open: false, reason: "" });

  const [submitting, setSubmitting] = useState(false);

  const fetchStatus = async () => {
    try {
      const data = await adminApi.getSchedulerStatus();
      setStatus(data);
    } catch (err: any) {
      toast.error(err.message || "Failed to retrieve scheduler status");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    fetchStatus();
  }, []);

  const handlePause = async () => {
    if (!pauseModal.reason.trim()) {
      toast.error("Administrative reason is required");
      return;
    }
    setSubmitting(true);
    try {
      await adminApi.pauseScheduler(pauseModal.reason.trim());
      toast.success("Background transition scheduler paused");
      setPauseModal({ open: false, reason: "" });
      await fetchStatus();
    } catch (err: any) {
      toast.error(err.message || "Failed to pause scheduler");
    } finally {
      setSubmitting(false);
    }
  };

  const handleResume = async () => {
    if (!resumeModal.reason.trim()) {
      toast.error("Administrative reason is required");
      return;
    }
    setSubmitting(true);
    try {
      await adminApi.resumeScheduler(resumeModal.reason.trim());
      toast.success("Background transition scheduler resumed");
      setResumeModal({ open: false, reason: "" });
      await fetchStatus();
    } catch (err: any) {
      toast.error(err.message || "Failed to resume scheduler");
    } finally {
      setSubmitting(false);
    }
  };

  const handleForceScan = async () => {
    setSubmitting(true);
    try {
      const res = await adminApi.triggerSchedulerScan();
      toast.success(res.message || "Executed due transitions");
      await fetchStatus();
    } catch (err: any) {
      toast.error(err.message || "Failed to trigger scan");
    } finally {
      setSubmitting(false);
    }
  };

  const handleRetryFailed = async () => {
    if (!retryModal.reason.trim()) {
      toast.error("Administrative reason is required");
      return;
    }
    setSubmitting(true);
    try {
      const res = await adminApi.retryFailedTransitions(retryModal.reason.trim());
      toast.success(res.message || "Reset failed transitions to pending");
      setRetryModal({ open: false, reason: "" });
      await fetchStatus();
    } catch (err: any) {
      toast.error(err.message || "Failed to reset transitions");
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20 text-slate-400">
        <Loader2 className="h-8 w-8 animate-spin text-blue-500 mr-3" />
        <span>Loading scheduler status...</span>
      </div>
    );
  }

  const isPaused = status?.paused ?? false;

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-white">Transition Scheduler Observability</h1>
          <p className="text-xs text-slate-400 mt-1">
            Operational runtime state, emergency pause/resume controls, and test-tenant rollout guard.
          </p>
        </div>
        <div className="flex items-center space-x-3">
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              setRefreshing(true);
              fetchStatus();
            }}
            disabled={refreshing}
            className="border-slate-800 bg-slate-900 text-slate-300 hover:text-white"
          >
            <RefreshCw className={`h-4 w-4 mr-1.5 ${refreshing ? "animate-spin" : ""}`} />
            Refresh
          </Button>

          {isPaused ? (
            <Button
              size="sm"
              onClick={() => setResumeModal({ open: true, reason: "" })}
              className="bg-emerald-600 hover:bg-emerald-500 text-white"
            >
              <Play className="h-4 w-4 mr-1.5" />
              Resume Scheduler
            </Button>
          ) : (
            <Button
              size="sm"
              onClick={() => setPauseModal({ open: true, reason: "" })}
              className="bg-amber-600 hover:bg-amber-500 text-white"
            >
              <Pause className="h-4 w-4 mr-1.5" />
              Pause Scheduler
            </Button>
          )}

          <Button
            size="sm"
            onClick={handleForceScan}
            disabled={submitting}
            className="bg-blue-600 hover:bg-blue-500 text-white"
          >
            <Clock className="h-4 w-4 mr-1.5" />
            Force Immediate Scan
          </Button>
        </div>
      </div>

      {/* Main Status Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-4 gap-4">
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 space-y-1">
          <span className="text-xs text-slate-400">Processor Status</span>
          <div className="flex items-center space-x-2 pt-1">
            <span className={`inline-block h-3 w-3 rounded-full ${isPaused ? "bg-amber-400" : "bg-emerald-400 animate-pulse"}`} />
            <span className="text-lg font-bold text-white uppercase">{isPaused ? "PAUSED" : "ACTIVE"}</span>
          </div>
          <p className="text-[11px] text-slate-400">Background recurring loop</p>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 space-y-1">
          <span className="text-xs text-slate-400">Due Now Transitions</span>
          <div className="text-2xl font-bold text-indigo-400">{status?.pending_due_count ?? 0}</div>
          <p className="text-[11px] text-slate-400">Ready for immediate execution</p>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 space-y-1">
          <span className="text-xs text-slate-400">Total Pending Transitions</span>
          <div className="text-2xl font-bold text-slate-200">{status?.total_pending_count ?? 0}</div>
          <p className="text-[11px] text-slate-400">Scheduled in current period</p>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 space-y-1">
          <span className="text-xs text-slate-400">Failed Transitions</span>
          <div className="flex items-center justify-between">
            <div className="text-2xl font-bold text-rose-400">{status?.failed_count ?? 0}</div>
            {(status?.failed_count ?? 0) > 0 && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setRetryModal({ open: true, reason: "" })}
                className="text-xs text-rose-400 hover:text-rose-300 p-1 h-auto"
              >
                <RotateCcw className="h-3.5 w-3.5 mr-1" />
                Retry
              </Button>
            )}
          </div>
          <p className="text-[11px] text-slate-400">Transitions needing operator review</p>
        </div>
      </div>

      {/* Rollout Guard & Safety Boundaries */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 space-y-4">
        <div className="flex items-center space-x-2 text-white font-semibold">
          <ShieldCheck className="h-5 w-5 text-emerald-400" />
          <span>Rollout Guard Configuration</span>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-xs">
          <div className="bg-slate-950 p-4 rounded-lg border border-slate-800 space-y-1">
            <span className="text-slate-400">Rollout Mode:</span>
            <div className="font-mono text-emerald-400 font-semibold uppercase">{status?.rollout_mode}</div>
            <p className="text-[11px] text-slate-400">Restricts automated worker actions to test accounts.</p>
          </div>

          <div className="bg-slate-950 p-4 rounded-lg border border-slate-800 space-y-1">
            <span className="text-slate-400">Target Tenant Prefixes:</span>
            <div className="font-mono text-slate-200">
              {status?.test_tenant_prefixes ? status.test_tenant_prefixes.join(", ") : "test_, qa_, demo_"}
            </div>
            <p className="text-[11px] text-slate-400">Accounts with these prefixes are eligible for automated transition.</p>
          </div>

          <div className="bg-slate-950 p-4 rounded-lg border border-slate-800 space-y-1">
            <span className="text-slate-400">Poll Interval:</span>
            <div className="font-mono text-slate-200">{status?.poll_interval_seconds ?? 60} seconds</div>
            <p className="text-[11px] text-slate-400">Frequency of background queue evaluations.</p>
          </div>
        </div>
      </div>

      {/* Execution Health */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 space-y-3">
        <h3 className="text-sm font-semibold text-white">Last Worker Execution</h3>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 text-xs">
          <div className="text-slate-400">
            Scan Timestamp:{" "}
            <span className="text-slate-200 font-mono">
              {status?.last_scan_at ? new Date(status.last_scan_at).toLocaleString() : "Never"}
            </span>
          </div>
          <div className="text-slate-400">
            Worker ID:{" "}
            <span className="text-slate-200 font-mono truncate max-w-xs">{status?.worker_id || "mediaforge-worker"}</span>
          </div>
          <div className="text-slate-400">
            Executed in Last Scan: <span className="text-slate-200 font-semibold">{status?.last_executed_count ?? 0}</span>
          </div>
        </div>
      </div>

      {/* Pause Modal */}
      <Dialog open={pauseModal.open} onOpenChange={(open) => !submitting && setPauseModal({ ...pauseModal, open })}>
        <DialogContent className="bg-slate-900 border-slate-800 text-slate-100 max-w-md">
          <DialogHeader>
            <DialogTitle className="text-base font-semibold text-amber-400 flex items-center space-x-2">
              <Pause className="h-5 w-5" />
              <span>Emergency Pause Transition Scheduler</span>
            </DialogTitle>
            <DialogDescription className="text-xs text-slate-400">
              Pausing prevents the background worker from executing due transitions. You can still process transitions manually.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 py-2">
            <Label className="text-xs text-slate-300">Administrative Reason (Required)</Label>
            <Input
              placeholder="e.g. Investigating billing webhook incident"
              value={pauseModal.reason}
              onChange={(e) => setPauseModal({ ...pauseModal, reason: e.target.value })}
              className="bg-slate-950 border-slate-700 text-xs"
            />
          </div>
          <DialogFooter>
            <Button variant="ghost" size="sm" onClick={() => setPauseModal({ open: false, reason: "" })}>
              Cancel
            </Button>
            <Button size="sm" onClick={handlePause} disabled={submitting} className="bg-amber-600 hover:bg-amber-500 text-white">
              {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : "Confirm Pause"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Resume Modal */}
      <Dialog open={resumeModal.open} onOpenChange={(open) => !submitting && setResumeModal({ ...resumeModal, open })}>
        <DialogContent className="bg-slate-900 border-slate-800 text-slate-100 max-w-md">
          <DialogHeader>
            <DialogTitle className="text-base font-semibold text-emerald-400 flex items-center space-x-2">
              <Play className="h-5 w-5" />
              <span>Resume Automatic Transition Scheduler</span>
            </DialogTitle>
            <DialogDescription className="text-xs text-slate-400">
              Resuming will allow the background worker to continuously process due transitions in the next cycle.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 py-2">
            <Label className="text-xs text-slate-300">Administrative Reason (Required)</Label>
            <Input
              placeholder="e.g. Investigation completed; system resumed"
              value={resumeModal.reason}
              onChange={(e) => setResumeModal({ ...resumeModal, reason: e.target.value })}
              className="bg-slate-950 border-slate-700 text-xs"
            />
          </div>
          <DialogFooter>
            <Button variant="ghost" size="sm" onClick={() => setResumeModal({ open: false, reason: "" })}>
              Cancel
            </Button>
            <Button size="sm" onClick={handleResume} disabled={submitting} className="bg-emerald-600 hover:bg-emerald-500 text-white">
              {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : "Resume Scheduler"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Retry Modal */}
      <Dialog open={retryModal.open} onOpenChange={(open) => !submitting && setRetryModal({ ...retryModal, open })}>
        <DialogContent className="bg-slate-900 border-slate-800 text-slate-100 max-w-md">
          <DialogHeader>
            <DialogTitle className="text-base font-semibold text-blue-400 flex items-center space-x-2">
              <RotateCcw className="h-5 w-5" />
              <span>Reset Failed Transitions</span>
            </DialogTitle>
            <DialogDescription className="text-xs text-slate-400">
              Resetting moves all {status?.failed_count ?? 0} failed transitions back to pending status for re-evaluation.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 py-2">
            <Label className="text-xs text-slate-300">Administrative Reason (Required)</Label>
            <Input
              placeholder="e.g. Transient database issue resolved"
              value={retryModal.reason}
              onChange={(e) => setRetryModal({ ...retryModal, reason: e.target.value })}
              className="bg-slate-950 border-slate-700 text-xs"
            />
          </div>
          <DialogFooter>
            <Button variant="ghost" size="sm" onClick={() => setRetryModal({ open: false, reason: "" })}>
              Cancel
            </Button>
            <Button size="sm" onClick={handleRetryFailed} disabled={submitting} className="bg-blue-600 hover:bg-blue-500 text-white">
              {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : "Reset Failed to Pending"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
