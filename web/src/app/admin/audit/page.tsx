"use client";

import { useEffect, useState } from "react";
import {
  FileSpreadsheet,
  Search,
  Filter,
  RefreshCw,
  Clock,
  ShieldAlert,
  Loader2,
  FileText,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import adminApi from "@/lib/admin-api";
import { toast } from "sonner";

export default function AdminAuditPage() {
  const [logs, setLogs] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [tenantFilter, setTenantFilter] = useState("");
  const [actionFilter, setActionFilter] = useState("");
  const [selectedDetails, setSelectedDetails] = useState<any | null>(null);

  const fetchLogs = async () => {
    try {
      const data = await adminApi.getAuditLogs({
        tenant_id: tenantFilter.trim() || undefined,
        action: actionFilter.trim() || undefined,
        limit: 100,
      });
      setLogs(data || []);
    } catch (err: any) {
      toast.error(err.message || "Failed to load audit logs");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchLogs();
  }, []);

  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    fetchLogs();
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-white">Commercial Audit Trail</h1>
          <p className="text-xs text-slate-400 mt-1">
            Tamper-evident record of all administrative subscription mutations, plan assignments, and policy changes.
          </p>
        </div>
        <div className="flex items-center space-x-2">
          <Button
            variant="outline"
            size="sm"
            onClick={fetchLogs}
            className="border-slate-800 bg-slate-900 text-slate-300 hover:text-white"
          >
            <RefreshCw className="h-4 w-4 mr-1.5" />
            Refresh
          </Button>
        </div>
      </div>

      {/* Filter and Search Bar */}
      <form onSubmit={handleSearchSubmit} className="bg-slate-900 border border-slate-800 rounded-xl p-4 flex flex-col sm:flex-row items-center gap-3">
        <div className="flex-1 w-full">
          <Input
            placeholder="Filter by Tenant ID..."
            value={tenantFilter}
            onChange={(e) => setTenantFilter(e.target.value)}
            className="bg-slate-950 border-slate-800 text-xs"
          />
        </div>
        <div className="flex-1 w-full">
          <Input
            placeholder="Filter by Action (e.g. plan_assigned, grace_period_entered)..."
            value={actionFilter}
            onChange={(e) => setActionFilter(e.target.value)}
            className="bg-slate-950 border-slate-800 text-xs"
          />
        </div>
        <Button type="submit" size="sm" variant="secondary" className="bg-slate-800 text-slate-200 text-xs shrink-0 w-full sm:w-auto">
          <Filter className="h-3.5 w-3.5 mr-1" />
          Filter Logs
        </Button>
      </form>

      {/* Audit Log Table */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden shadow-sm">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="bg-slate-950/70 border-b border-slate-800 text-slate-400 uppercase tracking-wider font-semibold">
              <tr>
                <th className="py-3 px-4">Action</th>
                <th className="py-3 px-4">Tenant ID</th>
                <th className="py-3 px-4">Actor</th>
                <th className="py-3 px-4">Mandatory Reason</th>
                <th className="py-3 px-4">Timestamp (UTC)</th>
                <th className="py-3 px-4 text-right">Details</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60 text-slate-200">
              {loading ? (
                <tr>
                  <td colSpan={6} className="py-12 text-center text-slate-500">
                    <Loader2 className="h-6 w-6 animate-spin mx-auto mb-2 text-blue-500" />
                    Loading audit trail...
                  </td>
                </tr>
              ) : logs.length === 0 ? (
                <tr>
                  <td colSpan={6} className="py-12 text-center text-slate-500">
                    No commercial audit logs found matching criteria.
                  </td>
                </tr>
              ) : (
                logs.map((log) => (
                  <tr key={log.id} className="hover:bg-slate-800/30 transition-colors">
                    <td className="py-3 px-4">
                      <Badge variant="outline" className="border-blue-500/40 bg-blue-500/10 text-blue-400 font-mono text-[10px]">
                        {log.action}
                      </Badge>
                    </td>
                    <td className="py-3 px-4 font-mono text-slate-300">
                      {log.tenant_id ? (
                        <span>{log.tenant_id}</span>
                      ) : (
                        <span className="text-slate-500 italic">System Scope</span>
                      )}
                    </td>
                    <td className="py-3 px-4">
                      <div className="text-white font-medium">{log.actor_role}</div>
                      <div className="text-[10px] font-mono text-slate-400">{log.actor_user_id || "System"}</div>
                    </td>
                    <td className="py-3 px-4 max-w-sm truncate text-slate-300" title={log.reason}>
                      {log.reason || "—"}
                    </td>
                    <td className="py-3 px-4 font-mono text-slate-400 text-[11px]">
                      {new Date(log.created_at).toLocaleString()}
                    </td>
                    <td className="py-3 px-4 text-right">
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => setSelectedDetails(log)}
                        className="text-xs text-slate-400 hover:text-white hover:bg-slate-800 p-1 h-auto"
                      >
                        <FileText className="h-3.5 w-3.5 mr-1" />
                        JSON
                      </Button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* JSON Details Modal */}
      <Dialog open={!!selectedDetails} onOpenChange={(open) => !open && setSelectedDetails(null)}>
        <DialogContent className="bg-slate-900 border-slate-800 text-slate-100 max-w-lg">
          <DialogHeader>
            <DialogTitle className="text-base font-semibold flex items-center space-x-2">
              <FileSpreadsheet className="h-5 w-5 text-blue-400" />
              <span>Audit Event Details — {selectedDetails?.action}</span>
            </DialogTitle>
          </DialogHeader>
          <div className="py-2">
            <pre className="p-3 bg-slate-950 border border-slate-800 rounded-lg text-xs font-mono text-slate-300 overflow-x-auto max-h-96">
              {JSON.stringify(selectedDetails?.details_json || {}, null, 2)}
            </pre>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
