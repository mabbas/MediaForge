import React from 'react';
import { clsx } from 'clsx';

interface StatusBadgeProps {
  status: string;
  className?: string;
}

export const StatusBadge: React.FC<StatusBadgeProps> = ({ status, className }) => {
  const norm = status.toLowerCase();

  let colorClasses = 'bg-slate-100 text-slate-700 border-slate-200';

  if (['ready', 'succeeded', 'completed'].includes(norm)) {
    colorClasses = 'bg-emerald-50 text-emerald-700 border-emerald-200';
  } else if (['running', 'processing'].includes(norm)) {
    colorClasses = 'bg-blue-50 text-blue-700 border-blue-200 animate-pulse';
  } else if (['queued', 'pending'].includes(norm)) {
    colorClasses = 'bg-amber-50 text-amber-700 border-amber-200';
  } else if (['failed', 'error'].includes(norm)) {
    colorClasses = 'bg-rose-50 text-rose-700 border-rose-200';
  } else if (['cancelled'].includes(norm)) {
    colorClasses = 'bg-slate-100 text-slate-500 border-slate-300';
  }

  return (
    <span
      className={clsx(
        'inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium border capitalize',
        colorClasses,
        className
      )}
    >
      {norm}
    </span>
  );
};
