"use client";

import React, { createContext, useContext, useState } from "react";
import { cn } from "@/lib/utils";

export interface SelectOption {
  value: string;
  label: string;
}

interface SelectContextType {
  value: string;
  onValueChange: (val: string) => void;
  open: boolean;
  setOpen: (open: boolean) => void;
}

const SelectContext = createContext<SelectContextType | null>(null);

export interface SelectProps {
  value?: string;
  onValueChange?: (value: string) => void;
  onChange?: (e: React.ChangeEvent<HTMLSelectElement>) => void;
  options?: SelectOption[];
  children?: React.ReactNode;
  className?: string;
  disabled?: boolean;
}

export function Select(props: SelectProps) {
  const { value = "", onValueChange, onChange, options, children, className, disabled } = props;
  const [open, setOpen] = useState(false);

  // If options array is provided, render native HTML select for backward compatibility
  if (options && !children) {
    const handleNativeChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
      if (onChange) onChange(e);
      if (onValueChange) onValueChange(e.target.value);
    };
    return (
      <select
        value={value}
        onChange={handleNativeChange}
        disabled={disabled}
        className={cn(
          "flex h-9 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-1.5 text-xs text-slate-100 focus:outline-none focus:ring-1 focus:ring-blue-500 disabled:opacity-50",
          className
        )}
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    );
  }

  return (
    <SelectContext.Provider
      value={{
        value,
        onValueChange: (val) => {
          if (onValueChange) onValueChange(val);
          setOpen(false);
        },
        open,
        setOpen,
      }}
    >
      <div className={cn("relative w-full", className)}>{children}</div>
    </SelectContext.Provider>
  );
}

export function SelectTrigger({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  const ctx = useContext(SelectContext);
  return (
    <button
      type="button"
      onClick={() => ctx?.setOpen(!ctx?.open)}
      className={cn(
        "flex h-9 w-full items-center justify-between rounded-md border border-slate-800 bg-slate-950 px-3 py-1.5 text-xs text-slate-200 focus:outline-none focus:ring-1 focus:ring-blue-500",
        className
      )}
    >
      {children}
      <span className="text-[10px] text-slate-400">▼</span>
    </button>
  );
}

export function SelectValue({ placeholder }: { placeholder?: string }) {
  const ctx = useContext(SelectContext);
  return <span>{ctx?.value || placeholder || "Select..."}</span>;
}

export function SelectContent({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  const ctx = useContext(SelectContext);
  if (!ctx?.open) return null;

  return (
    <div
      className={cn(
        "absolute top-full left-0 mt-1 z-50 w-full min-w-[8rem] rounded-md border border-slate-800 bg-slate-900 p-1 shadow-lg max-h-60 overflow-y-auto text-xs",
        className
      )}
    >
      {children}
    </div>
  );
}

export function SelectItem({
  value,
  children,
  className,
}: {
  value: string;
  children: React.ReactNode;
  className?: string;
}) {
  const ctx = useContext(SelectContext);
  const isSelected = ctx?.value === value;

  return (
    <div
      onClick={() => ctx?.onValueChange(value)}
      className={cn(
        "relative flex cursor-pointer select-none items-center rounded-sm px-2 py-1.5 text-xs outline-none transition-colors hover:bg-slate-800 hover:text-white",
        isSelected && "bg-blue-600/20 text-blue-400 font-medium",
        className
      )}
    >
      {children}
    </div>
  );
}
