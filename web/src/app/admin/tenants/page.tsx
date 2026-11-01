"use client";

import { useEffect, useState } from "react";
import {
  Users,
  Search,
  Filter,
  RefreshCw,
  Clock,
  ShieldCheck,
  AlertTriangle,
  Calendar,
  Layers,
  ArrowRight,
  Sliders,
  PauseCircle,
  PlayCircle,
  CheckCircle2,
  Loader2,
  DollarSign,
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import adminApi from "@/lib/admin-api";
import { toast } from "sonner";

export default function AdminTenantsPage() {
  const [tenants, setTenants] = useState<any[]>([]);
  const [plans, setPlans] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [total, setTotal] = useState(0);

  // Modals state
  const [selectedTenant, setSelectedTenant] = useState<any | null>(null);

  // 1. Plan Change modal
  const [planChangeModal, setPlanChangeModal] = useState<{
    open: boolean;
    targetPlanId: string;
    preview: any | null;
    timing: "recommended" | "immediate" | "period_end";
    reason: string;
    loadingPreview: boolean;
  }>({
    open: false,
    targetPlanId: "",
    preview: null,
    timing: "recommended",
    reason: "",
    loadingPreview: false,
  });

  // 2. Extend trial modal
  const [extendTrialModal, setExtendTrialModal] = useState<{
    open: boolean;
    days: string;
    reason: string;
  }>({
    open: false,
    days: "14",
    reason: "",
  });

  // 3. Grace period modal
  const [gracePeriodModal, setGracePeriodModal] = useState<{
    open: boolean;
    mode: "enter" | "exit";
    days: string;
    newStatus: string;
    reason: string;
  }>({
    open: false,
    mode: "enter",
    days: "14",
    newStatus: "active",
    reason: "",
  });

  // 4. Suspend modal
  const [suspendModal, setSuspendModal] = useState<{
    open: boolean;
    action: "suspend" | "unsuspend";
    reason: string;
  }>({
    open: false,
    action: "suspend",
    reason: "",
  });

  const [submitting, setSubmitting] = useState(false);

  const fetchTenants = async () => {
    try {
      const res = await adminApi.listTenants({
        search: search.trim() || undefined,
        status: statusFilter === "all" ? undefined : statusFilter,
        limit: 50,
      });
      setTenants(res.items || []);
      setTotal(res.total || 0);
    } catch (err: any) {
      toast.error(err.message || "Failed to load tenant list");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchTenants();
    adminApi.listPlans().then((res) => {
      // Keep only active plans for assignment
      setPlans((res || []).filter((p: any) => p.status === "active"));
    }).catch(() => {});
  }, [statusFilter]);

  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    fetchTenants();
  };

  // Plan change preview loader
  const loadPlanChangePreview = async (planId: string) => {
    if (!selectedTenant || !planId) return;
    setPlanChangeModal((prev) => ({ ...prev, targetPlanId: planId, loadingPreview: true }));
    try {
      const prev = await adminApi.previewPlanChange(selectedTenant.tenant_id, planId);
      setPlanChangeModal((m) => ({
        ...m,
        preview: prev,
        timing: prev.recommended_effective_at === "period_end" ? "period_end" : "immediate",
        loadingPreview: false,
      }));
    } catch (err: any) {
      toast.error(err.message || "Failed to generate plan change preview");
      setPlanChangeModal((m) => ({ ...m, loadingPreview: false }));
    }
  };

  const handleExecutePlanChange = async () => {
    if (!selectedTenant || !planChangeModal.targetPlanId || !planChangeModal.reason.trim()) {
      toast.error("Administrative reason and target plan are required");
      return;
    }
    setSubmitting(true);
    try {
      if (planChangeModal.timing === "immediate") {
        await adminApi.assignPlan(selectedTenant.tenant_id, {
          plan_id: planChangeModal.targetPlanId,
          reason: planChangeModal.reason.trim(),
        });
        toast.success(`Immediately upgraded tenant ${selectedTenant.name}`);
      } else {
        await adminApi.scheduleTransition(selectedTenant.tenant_id, {
          target_plan_id: planChangeModal.targetPlanId,
          transition_type: planChangeModal.preview?.transition_type || "downgrade",
          reason: planChangeModal.reason.trim(),
        });
        toast.success(`Scheduled transition for tenant ${selectedTenant.name} at period end`);
      }
      setPlanChangeModal({ open: false, targetPlanId: "", preview: null, timing: "recommended", reason: "", loadingPreview: false });
      await fetchTenants();
    } catch (err: any) {
      toast.error(err.message || "Plan change failed");
    } finally {
      setSubmitting(false);
    }
  };

  const handleExtendTrial = async () => {
    if (!selectedTenant || !extendTrialModal.reason.trim()) {
      toast.error("Administrative reason is required");
      return;
    }
    const days = parseInt(extendTrialModal.days, 10);
    if (isNaN(days) || days <= 0) {
      toast.error("Valid positive days required");
      return;
    }
    setSubmitting(true);
    try {
      await adminApi.extendTrial(selectedTenant.tenant_id, days, extendTrialModal.reason.trim());
      toast.success(`Extended trial by ${days} days for tenant ${selectedTenant.name}`);
      setExtendTrialModal({ open: false, days: "14", reason: "" });
      await fetchTenants();
    } catch (err: any) {
      toast.error(err.message || "Failed to extend trial");
    } finally {
      setSubmitting(false);
    }
  };

  const handleGracePeriod = async () => {
    if (!selectedTenant || !gracePeriodModal.reason.trim()) {
      toast.error("Administrative reason is required");
      return;
    }
    setSubmitting(true);
    try {
      if (gracePeriodModal.mode === "enter") {
        const days = parseInt(gracePeriodModal.days, 10) || 14;
        await adminApi.enterGracePeriod(selectedTenant.tenant_id, days, gracePeriodModal.reason.trim());
        toast.success(`Entered grace period for ${selectedTenant.name}`);
      } else {
        await adminApi.exitGracePeriod(selectedTenant.tenant_id, gracePeriodModal.newStatus, gracePeriodModal.reason.trim());
        toast.success(`Exited grace period with status ${gracePeriodModal.newStatus}`);
      }
      setGracePeriodModal({ open: false, mode: "enter", days: "14", newStatus: "active", reason: "" });
      await fetchTenants();
    } catch (err: any) {
      toast.error(err.message || "Grace period update failed");
    } finally {
      setSubmitting(false);
    }
  };

  const handleSuspendToggle = async () => {
    if (!selectedTenant || !suspendModal.reason.trim()) {
      toast.error("Administrative reason is mandatory");
      return;
    }
    setSubmitting(true);
    try {
      if (suspendModal.action === "suspend") {
        await adminApi.suspendSubscription(selectedTenant.tenant_id, suspendModal.reason.trim());
        toast.success(`Suspended commercial subscription for ${selectedTenant.name}`);
      } else {
        await adminApi.unsuspendSubscription(selectedTenant.tenant_id, suspendModal.reason.trim());
        toast.success(`Lifted suspension for ${selectedTenant.name}`);
      }
      setSuspendModal({ open: false, action: "suspend", reason: "" });
      await fetchTenants();
    } catch (err: any) {
      toast.error(err.message || "Failed to update suspension status");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-white">Tenants & Subscriptions</h1>
          <p className="text-xs text-slate-400 mt-1">
            Commercial status, subscription lifecycle controls, and entitlement overrides.
          </p>
        </div>
        <div className="flex items-center space-x-2 text-xs text-slate-400">
          <span>Found {total} tenants</span>
          <Button
            variant="outline"
            size="sm"
            onClick={fetchTenants}
            className="border-slate-800 bg-slate-900 text-slate-300 hover:text-white"
          >
            <RefreshCw className="h-4 w-4" />
          </Button>
        </div>
      </div>

      {/* Filter and Search Bar */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 flex flex-col sm:flex-row items-center gap-3">
        <form onSubmit={handleSearchSubmit} className="flex-1 flex items-center space-x-2 w-full">
          <div className="relative flex-1">
            <Search className="h-4 w-4 absolute left-3 top-2.5 text-slate-500" />
            <Input
              placeholder="Search by tenant name, slug, or ID..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="bg-slate-950 border-slate-800 pl-9 text-xs"
            />
          </div>
          <Button type="submit" size="sm" variant="secondary" className="bg-slate-800 text-slate-200 text-xs">
            Search
          </Button>
        </form>

        <div className="flex items-center space-x-2 shrink-0 w-full sm:w-auto">
          <Filter className="h-4 w-4 text-slate-500" />
          <Select value={statusFilter} onValueChange={setStatusFilter}>
            <SelectTrigger className="w-[180px] bg-slate-950 border-slate-800 text-xs text-slate-200">
              <SelectValue placeholder="All Statuses" />
            </SelectTrigger>
            <SelectContent className="bg-slate-900 border-slate-800 text-slate-200 text-xs">
              <SelectItem value="all">All Statuses</SelectItem>
              <SelectItem value="active">Active</SelectItem>
              <SelectItem value="trial">Trial</SelectItem>
              <SelectItem value="legacy_active">Legacy Active</SelectItem>
              <SelectItem value="grace_period">Grace Period</SelectItem>
              <SelectItem value="suspended">Suspended</SelectItem>
              <SelectItem value="scheduled_cancellation">Scheduled Cancellation</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>

      {/* Tenants Table */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden shadow-sm">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="bg-slate-950/70 border-b border-slate-800 text-slate-400 uppercase tracking-wider font-semibold">
              <tr>
                <th className="py-3 px-4">Tenant</th>
                <th className="py-3 px-4">Commercial Plan</th>
                <th className="py-3 px-4">Subscription Status</th>
                <th className="py-3 px-4">Trial / Grace</th>
                <th className="py-3 px-4">Period End</th>
                <th className="py-3 px-4 text-right">Operations</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60 text-slate-200">
              {loading ? (
                <tr>
                  <td colSpan={6} className="py-12 text-center text-slate-500">
                    <Loader2 className="h-6 w-6 animate-spin mx-auto mb-2 text-blue-500" />
                    Loading tenants...
                  </td>
                </tr>
              ) : tenants.length === 0 ? (
                <tr>
                  <td colSpan={6} className="py-12 text-center text-slate-500">
                    No tenants found matching criteria.
                  </td>
                </tr>
              ) : (
                tenants.map((t) => {
                  let statusBadge = (
                    <Badge variant="outline" className="border-slate-700 bg-slate-800 text-slate-300 uppercase text-[10px]">
                      {t.subscription_status}
                    </Badge>
                  );
                  if (t.subscription_status === "active") {
                    statusBadge = (
                      <Badge variant="outline" className="border-emerald-500/40 bg-emerald-500/10 text-emerald-400 uppercase text-[10px]">
                        ACTIVE
                      </Badge>
                    );
                  } else if (t.subscription_status === "trial") {
                    statusBadge = (
                      <Badge variant="outline" className="border-blue-500/40 bg-blue-500/10 text-blue-400 uppercase text-[10px]">
                        TRIAL
                      </Badge>
                    );
                  } else if (t.subscription_status === "legacy_active") {
                    statusBadge = (
                      <Badge variant="outline" className="border-purple-500/40 bg-purple-500/10 text-purple-400 uppercase text-[10px]">
                        LEGACY ACTIVE
                      </Badge>
                    );
                  } else if (t.subscription_status === "grace_period") {
                    statusBadge = (
                      <Badge variant="outline" className="border-amber-500/40 bg-amber-500/10 text-amber-400 uppercase text-[10px]">
                        GRACE PERIOD
                      </Badge>
                    );
                  } else if (t.subscription_status === "suspended") {
                    statusBadge = (
                      <Badge variant="outline" className="border-rose-500/40 bg-rose-500/10 text-rose-400 uppercase text-[10px]">
                        SUSPENDED
                      </Badge>
                    );
                  }

                  return (
                    <tr key={t.tenant_id} className="hover:bg-slate-800/30 transition-colors">
                      <td className="py-3 px-4">
                        <div className="font-medium text-white">{t.name}</div>
                        <div className="text-[11px] font-mono text-slate-400">{t.slug}</div>
                      </td>
                      <td className="py-3 px-4">
                        <div className="font-medium text-slate-200">{t.plan_name || "No Plan"}</div>
                        <div className="text-[11px] font-mono text-slate-400">{t.plan_code || "—"}</div>
                      </td>
                      <td className="py-3 px-4 space-y-1">
                        <div>{statusBadge}</div>
                        {t.has_pending_transition && (
                          <div className="text-[10px] text-indigo-400 flex items-center">
                            <Clock className="h-3 w-3 mr-1 inline" />
                            Pending Transition
                          </div>
                        )}
                      </td>
                      <td className="py-3 px-4 text-slate-300">
                        {t.subscription_status === "trial" && (
                          <span className="text-blue-400 font-medium">
                            {t.trial_days_remaining ?? 0} days remaining
                          </span>
                        )}
                        {t.subscription_status === "grace_period" && (
                          <span className="text-amber-400 font-medium">
                            {t.grace_period_remaining_days ?? 0} days grace
                          </span>
                        )}
                        {t.subscription_status !== "trial" && t.subscription_status !== "grace_period" && (
                          <span className="text-slate-500">—</span>
                        )}
                      </td>
                      <td className="py-3 px-4 text-slate-400 font-mono text-[11px]">
                        {t.current_period_end ? new Date(t.current_period_end).toLocaleDateString() : "—"}
                      </td>
                      <td className="py-3 px-4 text-right space-x-1">
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => {
                            setSelectedTenant(t);
                            setPlanChangeModal({
                              open: true,
                              targetPlanId: "",
                              preview: null,
                              timing: "recommended",
                              reason: "",
                              loadingPreview: false,
                            });
                          }}
                          className="text-xs text-blue-400 hover:text-blue-300 hover:bg-slate-800 px-2 py-1"
                        >
                          Plan
                        </Button>
                        {t.subscription_status === "trial" && (
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => {
                              setSelectedTenant(t);
                              setExtendTrialModal({ open: true, days: "14", reason: "" });
                            }}
                            className="text-xs text-emerald-400 hover:text-emerald-300 hover:bg-slate-800 px-2 py-1"
                          >
                            +Trial
                          </Button>
                        )}
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => {
                            setSelectedTenant(t);
                            setGracePeriodModal({
                              open: true,
                              mode: t.subscription_status === "grace_period" ? "exit" : "enter",
                              days: "14",
                              newStatus: "active",
                              reason: "",
                            });
                          }}
                          className="text-xs text-amber-400 hover:text-amber-300 hover:bg-slate-800 px-2 py-1"
                        >
                          Grace
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => {
                            setSelectedTenant(t);
                            setSuspendModal({
                              open: true,
                              action: t.is_suspended ? "unsuspend" : "suspend",
                              reason: "",
                            });
                          }}
                          className={`text-xs px-2 py-1 ${
                            t.is_suspended
                              ? "text-emerald-400 hover:text-emerald-300 hover:bg-slate-800"
                              : "text-rose-400 hover:text-rose-300 hover:bg-slate-800"
                          }`}
                        >
                          {t.is_suspended ? "Unsuspend" : "Suspend"}
                        </Button>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Plan Change Modal with Quotation & Entitlement Diff */}
      <Dialog
        open={planChangeModal.open}
        onOpenChange={(open) => !submitting && setPlanChangeModal({ ...planChangeModal, open })}
      >
        <DialogContent className="bg-slate-900 border-slate-800 text-slate-100 max-w-lg">
          <DialogHeader>
            <DialogTitle className="text-base font-semibold flex items-center space-x-2">
              <Layers className="h-5 w-5 text-blue-400" />
              <span>Change Commercial Plan — {selectedTenant?.name}</span>
            </DialogTitle>
            <DialogDescription className="text-xs text-slate-400">
              Current Plan: <span className="text-white font-medium">{selectedTenant?.plan_name}</span> ({selectedTenant?.plan_code})
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4 py-2">
            <div className="space-y-1.5">
              <Label className="text-xs text-slate-300">Target Plan (Published & Active)</Label>
              <Select
                value={planChangeModal.targetPlanId}
                onValueChange={(val) => loadPlanChangePreview(val)}
              >
                <SelectTrigger className="bg-slate-950 border-slate-700 text-xs text-slate-200">
                  <SelectValue placeholder="Select target plan..." />
                </SelectTrigger>
                <SelectContent className="bg-slate-900 border-slate-800 text-slate-200 text-xs">
                  {plans
                    .filter((p) => p.id !== selectedTenant?.plan_id)
                    .map((p) => (
                      <SelectItem key={p.id} value={p.id}>
                        {p.name} ({p.plan_code} v{p.version})
                      </SelectItem>
                    ))}
                </SelectContent>
              </Select>
            </div>

            {planChangeModal.loadingPreview && (
              <div className="py-4 text-center text-xs text-slate-400">
                <Loader2 className="h-4 w-4 animate-spin mx-auto mb-1 text-blue-400" />
                Calculating quota diff and commercial quotation preview...
              </div>
            )}

            {planChangeModal.preview && !planChangeModal.loadingPreview && (
              <div className="p-3 bg-slate-950 border border-slate-800 rounded-lg space-y-2 text-xs">
                <div className="flex justify-between items-center pb-2 border-b border-slate-800">
                  <span className="text-slate-400">Transition Classification:</span>
                  <Badge variant="outline" className="border-blue-500/50 text-blue-400 uppercase font-mono">
                    {planChangeModal.preview.transition_type}
                  </Badge>
                </div>
                <div className="flex justify-between items-center">
                  <span className="text-slate-400">Commercial Pricing:</span>
                  <span className="font-semibold text-white">{planChangeModal.preview.pricing_display}</span>
                </div>
                <div className="flex justify-between items-center">
                  <span className="text-slate-400">Recommended Timing:</span>
                  <span className="text-slate-200 font-medium">
                    {planChangeModal.preview.recommended_effective_at === "immediate"
                      ? "Immediate Activation"
                      : "Scheduled at Period End"}
                  </span>
                </div>
              </div>
            )}

            <div className="space-y-1.5">
              <Label className="text-xs text-slate-300">Execution Timing</Label>
              <div className="flex gap-3">
                <label className="flex items-center space-x-2 text-xs text-slate-300 cursor-pointer">
                  <input
                    type="radio"
                    name="timing"
                    checked={planChangeModal.timing === "immediate"}
                    onChange={() => setPlanChangeModal({ ...planChangeModal, timing: "immediate" })}
                  />
                  <span>Immediate (Recommended for Upgrades)</span>
                </label>
                <label className="flex items-center space-x-2 text-xs text-slate-300 cursor-pointer">
                  <input
                    type="radio"
                    name="timing"
                    checked={planChangeModal.timing === "period_end"}
                    onChange={() => setPlanChangeModal({ ...planChangeModal, timing: "period_end" })}
                  />
                  <span>At Period End (Scheduled)</span>
                </label>
              </div>
            </div>

            <div className="space-y-1.5">
              <Label className="text-xs text-slate-300">Administrative Reason (Required)</Label>
              <Input
                placeholder="e.g. Enterprise package negotiated upgrade"
                value={planChangeModal.reason}
                onChange={(e) => setPlanChangeModal({ ...planChangeModal, reason: e.target.value })}
                className="bg-slate-950 border-slate-700 text-xs"
              />
            </div>
          </div>

          <DialogFooter>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setPlanChangeModal({ open: false, targetPlanId: "", preview: null, timing: "recommended", reason: "", loadingPreview: false })}
            >
              Cancel
            </Button>
            <Button
              size="sm"
              onClick={handleExecutePlanChange}
              disabled={submitting || !planChangeModal.targetPlanId}
              className="bg-blue-600 hover:bg-blue-500 text-white"
            >
              {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : "Confirm Plan Change"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Extend Trial Modal */}
      <Dialog
        open={extendTrialModal.open}
        onOpenChange={(open) => !submitting && setExtendTrialModal({ ...extendTrialModal, open })}
      >
        <DialogContent className="bg-slate-900 border-slate-800 text-slate-100 max-w-md">
          <DialogHeader>
            <DialogTitle className="text-base font-semibold flex items-center space-x-2">
              <Clock className="h-5 w-5 text-emerald-400" />
              <span>Extend Commercial Trial — {selectedTenant?.name}</span>
            </DialogTitle>
            <DialogDescription className="text-xs text-slate-400">
              Grant additional evaluation days to trial tenant.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 py-2">
            <div className="space-y-1.5">
              <Label className="text-xs text-slate-300">Additional Days</Label>
              <Input
                type="number"
                min="1"
                max="365"
                value={extendTrialModal.days}
                onChange={(e) => setExtendTrialModal({ ...extendTrialModal, days: e.target.value })}
                className="bg-slate-950 border-slate-700 text-xs"
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs text-slate-300">Administrative Reason (Required)</Label>
              <Input
                placeholder="e.g. Granted POC extension per sales review"
                value={extendTrialModal.reason}
                onChange={(e) => setExtendTrialModal({ ...extendTrialModal, reason: e.target.value })}
                className="bg-slate-950 border-slate-700 text-xs"
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="ghost" size="sm" onClick={() => setExtendTrialModal({ open: false, days: "14", reason: "" })}>
              Cancel
            </Button>
            <Button size="sm" onClick={handleExtendTrial} disabled={submitting} className="bg-emerald-600 hover:bg-emerald-500 text-white">
              {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : "Grant Extension"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Grace Period Modal */}
      <Dialog
        open={gracePeriodModal.open}
        onOpenChange={(open) => !submitting && setGracePeriodModal({ ...gracePeriodModal, open })}
      >
        <DialogContent className="bg-slate-900 border-slate-800 text-slate-100 max-w-md">
          <DialogHeader>
            <DialogTitle className="text-base font-semibold flex items-center space-x-2 text-amber-400">
              <AlertTriangle className="h-5 w-5" />
              <span>
                {gracePeriodModal.mode === "enter" ? "Enter Grace Period" : "Exit Grace Period"} — {selectedTenant?.name}
              </span>
            </DialogTitle>
            <DialogDescription className="text-xs text-slate-400">
              {gracePeriodModal.mode === "enter"
                ? "Temporarily maintain service access while payment or invoice issues are resolved."
                : "Restore active status or finalize expiration."}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 py-2">
            {gracePeriodModal.mode === "enter" ? (
              <div className="space-y-1.5">
                <Label className="text-xs text-slate-300">Grace Period Days (1-90)</Label>
                <Input
                  type="number"
                  min="1"
                  max="90"
                  value={gracePeriodModal.days}
                  onChange={(e) => setGracePeriodModal({ ...gracePeriodModal, days: e.target.value })}
                  className="bg-slate-950 border-slate-700 text-xs"
                />
              </div>
            ) : (
              <div className="space-y-1.5">
                <Label className="text-xs text-slate-300">Target Subscription Status</Label>
                <Select
                  value={gracePeriodModal.newStatus}
                  onValueChange={(val) => setGracePeriodModal({ ...gracePeriodModal, newStatus: val })}
                >
                  <SelectTrigger className="bg-slate-950 border-slate-700 text-xs text-slate-200">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent className="bg-slate-900 border-slate-800 text-slate-200 text-xs">
                    <SelectItem value="active">Active (Restored)</SelectItem>
                    <SelectItem value="cancelled">Cancelled</SelectItem>
                    <SelectItem value="expired">Expired</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            )}
            <div className="space-y-1.5">
              <Label className="text-xs text-slate-300">Administrative Reason (Required)</Label>
              <Input
                placeholder="e.g. Card expired; awaiting update from customer billing contact"
                value={gracePeriodModal.reason}
                onChange={(e) => setGracePeriodModal({ ...gracePeriodModal, reason: e.target.value })}
                className="bg-slate-950 border-slate-700 text-xs"
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="ghost" size="sm" onClick={() => setGracePeriodModal({ ...gracePeriodModal, open: false })}>
              Cancel
            </Button>
            <Button size="sm" onClick={handleGracePeriod} disabled={submitting} className="bg-amber-600 hover:bg-amber-500 text-white">
              {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : "Save Changes"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Suspend Modal */}
      <Dialog
        open={suspendModal.open}
        onOpenChange={(open) => !submitting && setSuspendModal({ ...suspendModal, open })}
      >
        <DialogContent className="bg-slate-900 border-slate-800 text-slate-100 max-w-md">
          <DialogHeader>
            <DialogTitle className={`text-base font-semibold flex items-center space-x-2 ${suspendModal.action === "suspend" ? "text-rose-400" : "text-emerald-400"}`}>
              {suspendModal.action === "suspend" ? <PauseCircle className="h-5 w-5" /> : <PlayCircle className="h-5 w-5" />}
              <span>
                {suspendModal.action === "suspend" ? "Suspend Subscription" : "Lift Subscription Suspension"} — {selectedTenant?.name}
              </span>
            </DialogTitle>
            <DialogDescription className="text-xs text-slate-400">
              {suspendModal.action === "suspend"
                ? "Immediately halts quota admission for new downloads, clip extractions, and jobs while preserving existing media files."
                : "Restores commercial service operations for the tenant."}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 py-2">
            <Label className="text-xs text-slate-300">Administrative Reason (Required)</Label>
            <Input
              placeholder={suspendModal.action === "suspend" ? "e.g. Unresolved account security dispute" : "e.g. Dispute resolved with tenant admin"}
              value={suspendModal.reason}
              onChange={(e) => setSuspendModal({ ...suspendModal, reason: e.target.value })}
              className="bg-slate-950 border-slate-700 text-xs"
            />
          </div>
          <DialogFooter>
            <Button variant="ghost" size="sm" onClick={() => setSuspendModal({ open: false, action: "suspend", reason: "" })}>
              Cancel
            </Button>
            <Button
              size="sm"
              onClick={handleSuspendToggle}
              disabled={submitting}
              className={suspendModal.action === "suspend" ? "bg-rose-600 hover:bg-rose-500 text-white" : "bg-emerald-600 hover:bg-emerald-500 text-white"}
            >
              {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : suspendModal.action === "suspend" ? "Confirm Suspension" : "Lift Suspension"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
