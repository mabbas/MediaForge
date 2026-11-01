"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  Users,
  Layers,
  CalendarClock,
  Activity,
  ShieldAlert,
  ShieldCheck,
  RefreshCw,
  Clock,
  AlertTriangle,
  ArrowUpRight,
  Loader2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import adminApi from "@/lib/admin-api";
import { toast } from "sonner";

export default function AdminDashboardPage() {
  const [stats, setStats] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const fetchStats = async () => {
    try {
      const data = await adminApi.getDashboard();
      setStats(data);
    } catch (err: any) {
      toast.error(err.message || "Failed to load dashboard metrics");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    fetchStats();
  }, []);

  const handleManualScan = async () => {
    setRefreshing(true);
    try {
      const res = await adminApi.triggerSchedulerScan();
      toast.success(res.message || "Processed due transitions");
      await fetchStats();
    } catch (err: any) {
      toast.error(err.message || "Failed to trigger scheduler scan");
      setRefreshing(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20 text-slate-400">
        <Loader2 className="h-8 w-8 animate-spin text-blue-500 mr-3" />
        <span>Loading commercial dashboard statistics...</span>
      </div>
    );
  }

  const enforcementMode = stats?.enforcement?.mode || "disabled";
  const schedulerPaused = stats?.scheduler?.paused || false;

  return (
    <div className="space-y-6">
      {/* Top Banner & Refresh */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-white">Commercial Operations Dashboard</h1>
          <p className="text-xs text-slate-400 mt-1">
            Real-time status of commercial plan catalog, tenant subscriptions, and automated background jobs.
          </p>
        </div>
        <div className="flex items-center space-x-3">
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              setRefreshing(true);
              fetchStats();
            }}
            disabled={refreshing}
            className="border-slate-800 bg-slate-900 text-slate-300 hover:text-white hover:bg-slate-800"
          >
            <RefreshCw className={`h-4 w-4 mr-1.5 ${refreshing ? "animate-spin" : ""}`} />
            Refresh
          </Button>
          <Button
            size="sm"
            onClick={handleManualScan}
            disabled={refreshing}
            className="bg-blue-600 hover:bg-blue-500 text-white shadow-sm"
          >
            <Clock className="h-4 w-4 mr-1.5" />
            Scan Due Transitions
          </Button>
        </div>
      </div>

      {/* Global Enforcement Guard Callout */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 sm:p-5 flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div className="flex items-start space-x-3">
          <div className="p-2.5 rounded-lg bg-amber-500/10 border border-amber-500/30 text-amber-400 mt-0.5">
            <ShieldAlert className="h-5 w-5" />
          </div>
          <div>
            <div className="flex items-center space-x-2">
              <span className="font-semibold text-slate-200">Global Enforcement Status:</span>
              <Badge variant="outline" className="border-amber-500/50 bg-amber-500/10 text-amber-400 uppercase text-xs">
                {enforcementMode}
              </Badge>
              <span className="text-xs text-slate-400 font-mono">
                (Allowlist: {stats?.enforcement?.tenant_allowlist?.length || 0} tenants)
              </span>
            </div>
            <p className="text-xs text-slate-400 mt-1">
              Commercial quota enforcement remains strictly disabled for general accounts. All 481 reconciled baseline
              tenants and media files operate with zero disruption.
            </p>
          </div>
        </div>
        <Link href="/admin/tenants">
          <Button variant="ghost" size="sm" className="text-blue-400 hover:text-blue-300 shrink-0">
            View Tenant Allowlist
            <ArrowUpRight className="h-4 w-4 ml-1" />
          </Button>
        </Link>
      </div>

      {/* Primary KPI Grid */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 space-y-1">
          <div className="flex items-center justify-between text-slate-400 text-xs">
            <span>Total Tenants</span>
            <Users className="h-4 w-4 text-slate-500" />
          </div>
          <div className="text-2xl font-bold text-white">{stats?.total_tenants ?? 0}</div>
          <p className="text-[11px] text-slate-400">Reconciled platform accounts</p>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 space-y-1">
          <div className="flex items-center justify-between text-slate-400 text-xs">
            <span>Active Trials</span>
            <Clock className="h-4 w-4 text-blue-400" />
          </div>
          <div className="text-2xl font-bold text-blue-400">{stats?.trial_tenants ?? 0}</div>
          <p className="text-[11px] text-slate-400">Time-bounded evaluation tenants</p>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 space-y-1">
          <div className="flex items-center justify-between text-slate-400 text-xs">
            <span>Grandfathered</span>
            <ShieldCheck className="h-4 w-4 text-emerald-400" />
          </div>
          <div className="text-2xl font-bold text-emerald-400">{stats?.legacy_tenants ?? 0}</div>
          <p className="text-[11px] text-slate-400">Protected legacy_active accounts</p>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 space-y-1">
          <div className="flex items-center justify-between text-slate-400 text-xs">
            <span>Pending Transitions</span>
            <CalendarClock className="h-4 w-4 text-indigo-400" />
          </div>
          <div className="text-2xl font-bold text-indigo-400">{stats?.pending_transitions ?? 0}</div>
          <p className="text-[11px] text-slate-400">Scheduled plan changes / renewals</p>
        </div>
      </div>

      {/* Secondary Operational Metrics Grid */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-4 space-y-1">
          <span className="text-slate-400 text-xs">Paid Tier Tenants</span>
          <div className="text-xl font-semibold text-slate-200">{stats?.paid_tenants ?? 0}</div>
        </div>
        <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-4 space-y-1">
          <span className="text-slate-400 text-xs">Grace Period Accounts</span>
          <div className="text-xl font-semibold text-amber-400">{stats?.grace_period_tenants ?? 0}</div>
        </div>
        <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-4 space-y-1">
          <span className="text-slate-400 text-xs">Suspended Subscriptions</span>
          <div className="text-xl font-semibold text-rose-400">{stats?.suspended_tenants ?? 0}</div>
        </div>
        <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-4 space-y-1">
          <span className="text-slate-400 text-xs">Published Active Plans</span>
          <div className="text-xl font-semibold text-slate-200">{stats?.active_plans ?? 0}</div>
        </div>
      </div>

      {/* Background Scheduler Status Section */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center space-x-2">
            <Activity className="h-5 w-5 text-blue-400" />
            <h2 className="text-base font-semibold text-white">Automated Subscription Scheduler</h2>
          </div>
          <Link href="/admin/scheduler">
            <Button variant="ghost" size="sm" className="text-xs text-blue-400 hover:text-blue-300">
              Open Scheduler Controls →
            </Button>
          </Link>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 text-xs">
          <div className="bg-slate-950 p-3 rounded-lg border border-slate-800 space-y-1">
            <span className="text-slate-400">Scheduler State:</span>
            <div className="flex items-center space-x-2">
              <span className={`inline-block h-2 w-2 rounded-full ${schedulerPaused ? "bg-amber-400" : "bg-emerald-400 animate-pulse"}`} />
              <span className="font-semibold text-slate-200 uppercase">
                {schedulerPaused ? "PAUSED" : "ACTIVE (RUNNING)"}
              </span>
            </div>
          </div>

          <div className="bg-slate-950 p-3 rounded-lg border border-slate-800 space-y-1">
            <span className="text-slate-400">Rollout Mode:</span>
            <div className="font-mono text-slate-200">
              {stats?.scheduler?.rollout_mode || "test_tenants_only"}
            </div>
          </div>

          <div className="bg-slate-950 p-3 rounded-lg border border-slate-800 space-y-1">
            <span className="text-slate-400">Last Worker Scan:</span>
            <div className="text-slate-200 font-mono">
              {stats?.scheduler?.last_scan_at ? new Date(stats.scheduler.last_scan_at).toLocaleString() : "Awaiting scan"}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
