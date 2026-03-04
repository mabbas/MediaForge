"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { FileDown, Loader2 } from "lucide-react";
import { toast } from "sonner";
import api from "@/lib/api-client";

export function HistoryExport() {
  const [loading, setLoading] = useState(false);

  const exportData = async (format: "csv" | "json") => {
    setLoading(true);
    try {
      const res = await api.history({ page_size: 1000 });
      const entries = (res.entries || []) as Array<Record<string, unknown>>;

      if (entries.length === 0) {
        toast.error("No history to export");
        return;
      }

      let content: string;
      let mimeType: string;
      let filename: string;

      if (format === "csv") {
        const headers = ["job_id", "url", "title", "provider", "status", "media_type", "quality", "file_size", "created_at", "completed_at"];
        const rows = entries.map((e) => {
          const url = String(e.url ?? "").replace(/"/g, '""');
          const title = String(e.title ?? "").replace(/"/g, '""');
          return [e.job_id, `"${url}"`, `"${title}"`, e.provider ?? "", e.status, e.media_type ?? "", e.quality ?? "", e.file_size_human ?? "", e.created_at ?? "", e.completed_at ?? ""].join(",");
        });
        content = [headers.join(","), ...rows].join("\n");
        mimeType = "text/csv";
        filename = "grabitdown-history.csv";
      } else {
        content = JSON.stringify(entries, null, 2);
        mimeType = "application/json";
        filename = "grabitdown-history.json";
      }

      const blob = new Blob([content], { type: mimeType });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
      toast.success("Exported " + entries.length + " entries as " + format.toUpperCase());
    } catch {
      toast.error("Export failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex gap-2">
      <Button variant="outline" size="sm" onClick={() => exportData("csv")} disabled={loading}>
        {loading ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : <FileDown className="h-4 w-4 mr-1" />}
        Export CSV
      </Button>
      <Button variant="outline" size="sm" onClick={() => exportData("json")} disabled={loading}>
        <FileDown className="h-4 w-4 mr-1" />
        Export JSON
      </Button>
    </div>
  );
}
