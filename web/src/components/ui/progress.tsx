import { cn } from "@/lib/utils";

interface ProgressProps { value: number; className?: string; }

export function Progress({ value, className }: ProgressProps) {
  const pct = Math.min(100, Math.max(0, value));
  return (
    <div role="progressbar" aria-valuenow={value} className={cn("w-full overflow-hidden rounded-full bg-secondary", className)}>
      <div className="h-full bg-primary transition-all" style={{ width: `${pct}%` }} />
    </div>
  );
}
