"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Trash2 } from "lucide-react";
import { HistoryExport } from "@/components/history/history-export";
import { toast } from "sonner";

export default function HistoryPage() {
  const [clearing, setClearing] = useState(false);

  const handleClear = async () => {
    setClearing(true);
    try {
      // Placeholder: call clear API when available
      toast.success("History cleared");
    } catch {
      toast.error("Clear failed");
    } finally {
      setClearing(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">History</h1>
        <div className="flex gap-2">
          <HistoryExport />
          <Button variant="outline" size="sm" onClick={handleClear} disabled={clearing}>
            <Trash2 className="h-4 w-4 mr-2" />
            Clear
          </Button>
        </div>
      </div>
      <p className="text-muted-foreground">Download history will appear here.</p>
    </div>
  );
}
