"use client";
import { cn } from "@/lib/utils";

export interface SelectOption { value: string; label: string; }

interface SelectProps {
  value: string;
  onChange: (e: React.ChangeEvent<HTMLSelectElement>) => void;
  options: SelectOption[];
  className?: string;
  disabled?: boolean;
}

export function Select(props: SelectProps) {
  const { value, onChange, options, className, disabled } = props;
  return (
    <select value={value} onChange={onChange} disabled={disabled}
      className={cn("flex h-10 rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring", className)}>
      {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  );
}
