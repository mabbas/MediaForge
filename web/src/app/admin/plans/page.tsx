"use client";

import { useEffect, useState } from "react";
import {
  Layers,
  Plus,
  ShieldCheck,
  Archive,
  DollarSign,
  AlertTriangle,
  CheckCircle2,
  Loader2,
  RefreshCw,
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

export default function AdminPlansPage() {
  const [plans, setPlans] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  // Dialog states
  const [publishDialog, setPublishDialog] = useState<{ open: boolean; plan: any | null; reason: string }>({
    open: false,
    plan: null,
    reason: "",
  });
  const [retireDialog, setRetireDialog] = useState<{ open: boolean; plan: any | null; reason: string }>({
    open: false,
    plan: null,
    reason: "",
  });
  const [pricingDialog, setPricingDialog] = useState<{ open: boolean; plan: any | null; priceDollars: string; reason: string }>({
    open: false,
    plan: null,
    priceDollars: "",
    reason: "",
  });
  const [submitting, setSubmitting] = useState(false);

  const fetchPlans = async () => {
    try {
      const data = await adminApi.listPlans();
      setPlans(data || []);
    } catch (err: any) {
      toast.error(err.message || "Failed to load plan catalog");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchPlans();
  }, []);

  const handlePublish = async () => {
    if (!publishDialog.plan || !publishDialog.reason.trim()) {
      toast.error("Administrative reason is mandatory");
      return;
    }
    setSubmitting(true);
    try {
      await adminApi.publishPlan(publishDialog.plan.id, publishDialog.reason.trim());
      toast.success(`Published plan ${publishDialog.plan.name} v${publishDialog.plan.version}`);
      setPublishDialog({ open: false, plan: null, reason: "" });
      await fetchPlans();
    } catch (err: any) {
      toast.error(err.message || "Failed to publish plan");
    } finally {
      setSubmitting(false);
    }
  };

  const handleRetire = async () => {
    if (!retireDialog.plan || !retireDialog.reason.trim()) {
      toast.error("Administrative reason is mandatory");
      return;
    }
    setSubmitting(true);
    try {
      await adminApi.retirePlan(retireDialog.plan.id, retireDialog.reason.trim());
      toast.success(`Retired plan ${retireDialog.plan.name} v${retireDialog.plan.version}`);
      setRetireDialog({ open: false, plan: null, reason: "" });
      await fetchPlans();
    } catch (err: any) {
      toast.error(err.message || "Failed to retire plan");
    } finally {
      setSubmitting(false);
    }
  };

  const handleUpdatePricing = async () => {
    if (!pricingDialog.plan || !pricingDialog.reason.trim()) {
      toast.error("Administrative reason is mandatory");
      return;
    }
    const cents = Math.round(parseFloat(pricingDialog.priceDollars) * 100);
    if (isNaN(cents) || cents < 0) {
      toast.error("Invalid price amount");
      return;
    }
    setSubmitting(true);
    try {
      await adminApi.updatePricing(pricingDialog.plan.id, {
        price_cents: cents,
        pricing_status: "approved",
        reason: pricingDialog.reason.trim(),
      });
      toast.success("Updated and approved plan pricing");
      setPricingDialog({ open: false, plan: null, priceDollars: "", reason: "" });
      await fetchPlans();
    } catch (err: any) {
      toast.error(err.message || "Failed to update pricing");
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20 text-slate-400">
        <Loader2 className="h-8 w-8 animate-spin text-blue-500 mr-3" />
        <span>Loading plan catalog...</span>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-white">Commercial Plan Catalog</h1>
          <p className="text-xs text-slate-400 mt-1">
            Authoritative versioned plan definitions, quota boundaries, and commercial approval states.
          </p>
        </div>
        <div className="flex items-center space-x-3">
          <Button
            variant="outline"
            size="sm"
            onClick={fetchPlans}
            className="border-slate-800 bg-slate-900 text-slate-300 hover:text-white"
          >
            <RefreshCw className="h-4 w-4 mr-1.5" />
            Refresh
          </Button>
        </div>
      </div>

      {/* Plans Table */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden shadow-sm">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="bg-slate-950/70 border-b border-slate-800 text-slate-400 uppercase tracking-wider font-semibold">
              <tr>
                <th className="py-3 px-4">Plan Name & Code</th>
                <th className="py-3 px-4">Version</th>
                <th className="py-3 px-4">Tier Type</th>
                <th className="py-3 px-4">Status</th>
                <th className="py-3 px-4">Commercial Pricing</th>
                <th className="py-3 px-4">Interval</th>
                <th className="py-3 px-4 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60 text-slate-200">
              {plans.map((p) => {
                const isApproved =
                  p.price_cents !== null &&
                  p.metadata_json?.pricing_status !== "unapproved_pending_commercial_review";
                const priceFormatted = isApproved ? `$${(p.price_cents / 100).toFixed(2)}` : "Pricing not configured";

                let statusBadge = (
                  <Badge variant="outline" className="border-slate-700 bg-slate-800 text-slate-300 uppercase">
                    {p.status}
                  </Badge>
                );
                if (p.status === "active") {
                  statusBadge = (
                    <Badge variant="outline" className="border-emerald-500/40 bg-emerald-500/10 text-emerald-400 uppercase">
                      ACTIVE
                    </Badge>
                  );
                } else if (p.status === "draft") {
                  statusBadge = (
                    <Badge variant="outline" className="border-amber-500/40 bg-amber-500/10 text-amber-400 uppercase">
                      DRAFT
                    </Badge>
                  );
                } else if (p.status === "retired") {
                  statusBadge = (
                    <Badge variant="outline" className="border-slate-700 bg-slate-800/60 text-slate-400 uppercase">
                      RETIRED
                    </Badge>
                  );
                }

                return (
                  <tr key={p.id} className="hover:bg-slate-800/30 transition-colors">
                    <td className="py-3 px-4">
                      <div className="font-medium text-white">{p.name}</div>
                      <div className="text-[11px] font-mono text-slate-400">{p.plan_code}</div>
                    </td>
                    <td className="py-3 px-4 font-mono">v{p.version}</td>
                    <td className="py-3 px-4 uppercase text-[11px] font-medium text-slate-300">{p.tier_type}</td>
                    <td className="py-3 px-4">{statusBadge}</td>
                    <td className="py-3 px-4">
                      <span className={isApproved ? "font-medium text-white" : "italic text-amber-400/90"}>
                        {priceFormatted}
                      </span>
                    </td>
                    <td className="py-3 px-4 capitalize text-slate-300">{p.billing_interval}</td>
                    <td className="py-3 px-4 text-right space-x-2">
                      {p.status === "draft" && (
                        <>
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() =>
                              setPricingDialog({
                                open: true,
                                plan: p,
                                priceDollars: p.price_cents ? (p.price_cents / 100).toFixed(2) : "0.00",
                                reason: "",
                              })
                            }
                            className="text-xs text-blue-400 hover:text-blue-300 hover:bg-slate-800"
                          >
                            <DollarSign className="h-3.5 w-3.5 mr-1" />
                            Pricing
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => setPublishDialog({ open: true, plan: p, reason: "" })}
                            className="text-xs text-emerald-400 hover:text-emerald-300 hover:bg-slate-800"
                          >
                            <ShieldCheck className="h-3.5 w-3.5 mr-1" />
                            Publish
                          </Button>
                        </>
                      )}
                      {p.status === "active" && (
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => setRetireDialog({ open: true, plan: p, reason: "" })}
                          className="text-xs text-rose-400 hover:text-rose-300 hover:bg-slate-800"
                        >
                          <Archive className="h-3.5 w-3.5 mr-1" />
                          Retire
                        </Button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* Publish Confirmation Modal */}
      <Dialog open={publishDialog.open} onOpenChange={(open) => !submitting && setPublishDialog({ ...publishDialog, open })}>
        <DialogContent className="bg-slate-900 border-slate-800 text-slate-100 max-w-md">
          <DialogHeader>
            <DialogTitle className="text-base font-semibold flex items-center space-x-2">
              <ShieldCheck className="h-5 w-5 text-emerald-400" />
              <span>Publish Commercial Plan</span>
            </DialogTitle>
            <DialogDescription className="text-xs text-slate-400">
              Publishing will activate plan <span className="text-white font-mono">{publishDialog.plan?.plan_code} v{publishDialog.plan?.version}</span>.
              Once published, plans become strictly immutable. Existing active versions with the same code will be retired automatically.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 py-2">
            <Label className="text-xs text-slate-300">Administrative Reason (Required)</Label>
            <Input
              placeholder="e.g. Approved by Commercial Pricing Committee"
              value={publishDialog.reason}
              onChange={(e) => setPublishDialog({ ...publishDialog, reason: e.target.value })}
              className="bg-slate-950 border-slate-700 text-xs"
            />
          </div>
          <DialogFooter>
            <Button variant="ghost" size="sm" onClick={() => setPublishDialog({ open: false, plan: null, reason: "" })}>
              Cancel
            </Button>
            <Button size="sm" onClick={handlePublish} disabled={submitting} className="bg-emerald-600 hover:bg-emerald-500 text-white">
              {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : "Confirm Publication"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Retire Confirmation Modal */}
      <Dialog open={retireDialog.open} onOpenChange={(open) => !submitting && setRetireDialog({ ...retireDialog, open })}>
        <DialogContent className="bg-slate-900 border-slate-800 text-slate-100 max-w-md">
          <DialogHeader>
            <DialogTitle className="text-base font-semibold flex items-center space-x-2 text-rose-400">
              <Archive className="h-5 w-5" />
              <span>Retire Commercial Plan</span>
            </DialogTitle>
            <DialogDescription className="text-xs text-slate-400">
              Retiring plan <span className="text-white font-mono">{retireDialog.plan?.plan_code} v{retireDialog.plan?.version}</span> prevents new tenant subscriptions. Existing subscribers remain active until migration.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 py-2">
            <Label className="text-xs text-slate-300">Administrative Reason (Required)</Label>
            <Input
              placeholder="e.g. Plan deprecated in favor of v2"
              value={retireDialog.reason}
              onChange={(e) => setRetireDialog({ ...retireDialog, reason: e.target.value })}
              className="bg-slate-950 border-slate-700 text-xs"
            />
          </div>
          <DialogFooter>
            <Button variant="ghost" size="sm" onClick={() => setRetireDialog({ open: false, plan: null, reason: "" })}>
              Cancel
            </Button>
            <Button size="sm" onClick={handleRetire} disabled={submitting} className="bg-rose-600 hover:bg-rose-500 text-white">
              {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : "Confirm Retirement"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Pricing Configuration Modal */}
      <Dialog open={pricingDialog.open} onOpenChange={(open) => !submitting && setPricingDialog({ ...pricingDialog, open })}>
        <DialogContent className="bg-slate-900 border-slate-800 text-slate-100 max-w-md">
          <DialogHeader>
            <DialogTitle className="text-base font-semibold flex items-center space-x-2">
              <DollarSign className="h-5 w-5 text-blue-400" />
              <span>Configure Commercial Pricing</span>
            </DialogTitle>
            <DialogDescription className="text-xs text-slate-400">
              Set and officially approve pricing for draft plan <span className="text-white font-mono">{pricingDialog.plan?.plan_code} v{pricingDialog.plan?.version}</span>.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 py-2">
            <div className="space-y-1.5">
              <Label className="text-xs text-slate-300">Price in USD ($)</Label>
              <Input
                type="number"
                step="0.01"
                min="0"
                placeholder="29.00"
                value={pricingDialog.priceDollars}
                onChange={(e) => setPricingDialog({ ...pricingDialog, priceDollars: e.target.value })}
                className="bg-slate-950 border-slate-700 text-xs"
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs text-slate-300">Administrative Approval Reason (Required)</Label>
              <Input
                placeholder="e.g. Commercial Board Q4 approved rate card"
                value={pricingDialog.reason}
                onChange={(e) => setPricingDialog({ ...pricingDialog, reason: e.target.value })}
                className="bg-slate-950 border-slate-700 text-xs"
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="ghost" size="sm" onClick={() => setPricingDialog({ open: false, plan: null, priceDollars: "", reason: "" })}>
              Cancel
            </Button>
            <Button size="sm" onClick={handleUpdatePricing} disabled={submitting} className="bg-blue-600 hover:bg-blue-500 text-white">
              {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : "Save & Approve Price"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
