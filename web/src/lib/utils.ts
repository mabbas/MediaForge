import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatBytes(bytes?: number | null, decimals = 2): string {
  if (bytes === null || bytes === undefined || bytes === 0) return "0 B";
  const k = 1024;
  const dm = decimals < 0 ? 0 : decimals;
  const sizes = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  if (i === 0) return `${bytes} B`;
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(dm))} ${sizes[i]}`;
}

export function formatDuration(seconds?: number | null): string {
  if (seconds === null || seconds === undefined) return "—";
  const sec = Math.floor(seconds);
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;

  if (h > 0) {
    return `${h}h ${m}m`;
  }
  if (m > 0) {
    return `${m}m ${s}s`;
  }
  return `${s}s`;
}

export function formatSpeed(bytesPerSec?: number | null): string {
  if (!bytesPerSec || bytesPerSec <= 0) return "—";
  return `${formatBytes(bytesPerSec)}/s`;
}

export function truncate(str: string, maxLength = 50): string {
  if (!str || str.length <= maxLength) return str;
  return str.slice(0, maxLength - 3) + "...";
}
