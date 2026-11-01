"use client";

import { useEffect, useState } from "react";
import {
  CalendarClock,
  RefreshCw,
  Clock,
  XCircle,
  CheckCircle2,
  AlertTriangle,
  Loader2,
  Filter,
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

export default function ScheduledTransitionsPage() {
  const [transitions, setTransitions] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState("all");
  const [total, setTotal] = useState(0);

  // Cancellation modal
  const [cancelModal, setCancelModal] = useState<{
    open: boolean;
    item: any | null;
    reason: string;
  }>({
    open: false,
    item: null,
    reason: "",
  });

  const [submitting, setSubmitting] = useState(false);
  const [scanning, setScanning] = useState(false);

  const fetchTransitions = async () => {
    try {
      const res = await adminApi.listTransitions({
        status: statusFilter === "all" ? undefined : statusFilter,
        limit: 50,
      });
      setTransitions(res.items || []);
      setTotal(res.total || 0);
    } catch (err: any) {
      toast.error(err.message || "Failed to load scheduled transitions");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchTransitions();
  }, [statusFilter]);

  const handleCancelTransition = async () => {
    if (!cancelModal.item || !cancelModal.reason.trim()) {
      toast.error("Administrative reason is mandatory for cancellation");
      return;
    }
    setSubmitting(true);
    try {
      await adminApi.cancelTransition(
        cancelModal.item.tenant_id,
        cancelModal.item.id,
        cancelModal.reason.trim()
      );
      toast.success("Scheduled transition cancelled");
      setCancelModal({ open: false, item: null, reason: "" });
      await fetchTransitions();
    } catch (err: any) {
      toast.error(err.message || "Failed to cancel scheduled transition");
    } finally {
      setSubmitting(false);
    }
  };

  const handleManualScan = async () => {
    setScanning(true);
    try {
      const res = await adminApi.triggerSchedulerScan();
      toast.success(res.message || "Processed due transitions");
      await fetchTransitions();
    } catch (err: any) {
      toast.error(err.message || "Failed to trigger scan");
    } finally {
      setScanning(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-white">Scheduled Subscription Transitions</h1>
          <p className="text-xs text-slate-400 mt-1">
            Queued plan changes, downgrades, cancellations, and contract renewals.
          </p>
        </div>
        <div className="flex items-center space-x-3">
          <Button
            variant="outline"
            size="sm"
            onClick={fetchTransitions}
            className="border-slate-800 bg-slate-900 text-slate-300 hover:text-white"
          >
            <RefreshCw className="h-4 w-4 mr-1.5" />
            Refresh
          </Button>
          <Button
            size="sm"
            onClick={handleManualScan}
            disabled={scanning}
            className="bg-blue-600 hover:bg-blue-500 text-white"
          >
            {scanning ? <Loader2 className="h-4 w-4 animate-spin mr-1.5" /> : <Clock className="h-4 w-4 mr-1.5" />}
            Scan & Process Due Now
          </Button>
        </div>
      </div>

      {/* Filter Bar */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 flex items-center justify-between">
        <div className="flex items-center space-x-2">
          <Filter className="h-4 w-4 text-slate-500" />
          <Select value={statusFilter} onValueChange={setStatusFilter}>
            <SelectTrigger className="w-[180px] bg-slate-950 border-slate-800 text-xs text-slate-200">
              <SelectValue placeholder="All Transitions" />
            </SelectTrigger>
            <SelectContent className="bg-slate-900 border-slate-800 text-slate-200 text-xs">
              <SelectItem value="all">All Transitions</SelectItem>
              <SelectItem value="pending">Pending</SelectItem>
              <SelectItem value="executed">Executed</SelectItem>
              <SelectItem value="cancelled">Cancelled</SelectItem>
              <SelectItem value="failed">Failed</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className="text-xs text-slate-400">Total: {total} transitions</div>
      </div>

      {/* Table */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden shadow-sm">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="bg-slate-950/70 border-b border-slate-800 text-slate-400 uppercase tracking-wider font-semibold">
              <tr>
                <th className="py-3 px-4">Transition ID & Type</th>
                <th className="py-3 px-4">Tenant ID</th>
                <th className="py-3 px-4">Target Plan</th>
                <th className="py-3 px-4">Status</th>
                <th className="py-3 px-4">Effective Scheduled Time</th>
                <th className="py-3 px-4">Reason / Notes</th>
                <th className="py-3 px-4 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60 text-slate-200">
              {loading ? (
                <tr>
                  <td colSpan={7} className="py-12 text-center text-slate-500">
                    <Loader2 className="h-6 w-6 animate-spin mx-auto mb-2 text-blue-500" />
                    Loading transitions...
                  </td>
                </tr>
              ) : transitions.length === 0 ? (
                <tr>
                  <td colSpan={7} className="py-12 text-center text-slate-500">
                    No scheduled transitions found.
                  </td>
                </tr>
              ) : (
                transitions.map((item) => {
                  let statusBadge = (
                    <Badge variant="outline" className="border-slate-700 bg-slate-800 text-slate-300 uppercase text-[10px]">
                      {item.status}
                    </Badge>
                  );
                  if (item.status === "pending") {
                    statusBadge = (
                      <Badge variant="outline" className="border-indigo-500/40 bg-indigo-500/10 text-indigo-400 uppercase text-[10px]">
                        PENDING
                      </Badge>
                    );
                  } else if (item.status === "executed") {
                    statusBadge = (
                      <Badge variant="outline" className="border-emerald-500/40 bg-emerald-500/10 text-emerald-400 uppercase text-[10px]">
                        EXECUTED
                      </Badge>
                    );
                  } else if (item.status === "failed") {
                    statusBadge = (
                      <Badge variant="outline" className="border-rose-500/40 bg-rose-500/10 text-rose-400 uppercase text-[10px]">
                        FAILED
                      </Badge>
                    );
                  }

                  return (
                    <tr key={item.id} className="hover:bg-slate-800/30 transition-colors">
                      <td className="py-3 px-4">
                        <div className="font-semibold text-white uppercase text-[11px]">{item.transition_type}</div>
                        <div className="text-[10px] font-mono text-slate-400">{item.id.slice(0, 13)}...</div>
                      </td>
                      <td className="py-3 px-4 font-mono text-slate-300">{item.tenant_id}</td>
                      <td className="py-3 px-4">
                        {item.target_plan_name ? (
                          <div>
                            <span className="font-medium text-slate-200">{item.target_plan_name}</span>
                            <div className="text-[10px] font-mono text-slate-400">{item.target_plan_code}</div>
                          </div>
                        ) : (
                          <span className="text-slate-500">None (Cancellation)</span>
                        )}
                      </td>
                      <td className="py-3 px-4">{statusBadge}</td>
                      <td className="py-3 px-4 font-mono text-slate-300 text-[11px]">
                        {new Date(item.scheduled_at).toLocaleString()}
                      </td>
                      <td className="py-3 px-4 text-slate-400 max-w-xs truncate" title={item.reason}>
                        {item.reason}
                      </td>
                      <td className="py-3 px-4 text-right">
                        {item.status === "pending" && (
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => setCancelModal({ open: true, item, reason: "" })}
                            className="text-xs text-rose-400 hover:text-rose-300 hover:bg-slate-800"
                          >
                            <XCircle className="h-3.5 w-3.5 mr-1" />
                            Cancel
                          </Button>
                        )}
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Cancel Modal */}
      <Dialog
        open={cancelModal.open}
        onOpenChange={(open) => !submitting && setCancelModal({ ...cancelModal, open })}
      >
        <DialogContent className="bg-slate-900 border-slate-800 text-slate-100 max-w-md">
          <DialogHeader>
            <DialogTitle className="text-base font-semibold flex items-center space-x-2 text-rose-400">
              <XCircle className="h-5 w-5" />
              <span>Cancel Scheduled Transition</span>
            </DialogTitle>
            <DialogDescription className="text-xs text-slate-400">
              Cancelling transition <span className="font-mono text-white">{cancelModal.item?.id}</span> for tenant{" "}
              <span className="font-mono text-white">{cancelModal.item?.tenant_id}</span> will abort the scheduled plan change.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 py-2">
            <Label className="text-xs text-slate-300">Administrative Cancellation Reason (Required)</Label>
            <Input
              placeholder="e.g. Customer decided to remain on current plan"
              value={cancelModal.reason}
              onChange={(e) => setCancelModal({ ...cancelModal, reason: e.target.value })}
              className="bg-slate-950 border-slate-700 text-xs"
            />
          </div>
          <DialogFooter>
            <Button variant="ghost" size="sm" onClick={() => setCancelModal({ open: false, item: null, reason: "" })}>
              Keep Transition
            </Button>
            <Button size="sm" onClick={handleCancelTransition} disabled={submitting} className="bg-rose-600 hover:bg-rose-500 text-white">
              {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : "Confirm Cancellation"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
