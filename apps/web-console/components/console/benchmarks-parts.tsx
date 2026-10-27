"use client";

import type { ReactNode } from "react";

import { MISSING } from "@/components/console/kit";
import { cn } from "@/lib/utils";

/* Small pieces shared by the Benchmarks, UAT and Agents pages. */

/** A multi-select as toggle buttons: each option is pressed or not. */
export function ToggleChips({
  label,
  options,
  selected,
  onToggle,
}: {
  label: string;
  options: Array<{ id: string; label: string; title?: string }>;
  selected: ReadonlySet<string>;
  onToggle: (id: string) => void;
}) {
  return (
    <div role="group" aria-label={label} className="flex flex-wrap gap-1.5">
      {options.map((option) => {
        const on = selected.has(option.id);
        return (
          <button
            key={option.id}
            type="button"
            aria-pressed={on}
            title={option.title}
            onClick={() => onToggle(option.id)}
            className={cn(
              "h-7 max-w-full truncate rounded-md px-2.5 text-xs font-medium outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring/40",
              on ? "bg-accent-soft text-accent-text" : "bg-control text-muted-foreground hover:bg-control-hover hover:text-foreground",
            )}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

/** Toggle membership of `id` in a set, returning a new set. */
export function toggled(set: ReadonlySet<string>, id: string): Set<string> {
  const next = new Set(set);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  return next;
}

export function InlineError({ message, className }: { message: string | null | undefined; className?: string }) {
  if (!message) return null;
  return (
    <p role="alert" className={cn("rounded-lg bg-danger-soft px-3 py-2 text-xs leading-5 text-danger", className)}>
      {message}
    </p>
  );
}

/** One headline number. */
export function Metric({ label, value, detail }: { label: string; value: ReactNode; detail?: ReactNode }) {
  return (
    <div className="min-w-0 rounded-lg bg-surface-1 px-3.5 py-3">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="t-num mt-1 truncate text-lg font-semibold tracking-tight">{value}</p>
      {detail != null && <p className="t-num mt-0.5 truncate text-xs text-muted-foreground">{detail}</p>}
    </div>
  );
}

export function MetricGrid({ children }: { children: ReactNode }) {
  return <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-6">{children}</div>;
}

/** Key/value facts in a compact two-column list. */
export function Facts({ items }: { items: Array<[string, ReactNode]> }) {
  return (
    <dl className="grid grid-cols-[minmax(0,auto)_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-[13px]">
      {items.map(([label, value]) => (
        <div key={label} className="contents">
          <dt className="text-muted-foreground">{label}</dt>
          <dd className="t-num min-w-0 break-words">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Long text output (markdown reports, step output) kept as written. */
export function Preformatted({ text, label, className }: { text: string; label: string; className?: string }) {
  return (
    <pre
      aria-label={label}
      tabIndex={0}
      className={cn(
        "max-h-[32rem] overflow-auto whitespace-pre rounded-lg bg-surface-1 px-3.5 py-3 font-mono text-[12px] leading-5 text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring/40",
        className,
      )}
    >
      {text}
    </pre>
  );
}

export function percent(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value) ? `${Math.round(value * 1000) / 10}%` : MISSING;
}

export function count(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value) ? String(value) : MISSING;
}

export function seconds(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value) ? `${Math.round(value * 100) / 100}s` : MISSING;
}

/** Phone numbers or SIP addresses typed one per line or separated by commas. */
export function parseList(value: string): string[] {
  return value.split(/[\s,]+/).map((item) => item.trim()).filter(Boolean);
}

/** A tri-state verdict: true passed, false failed, null undecided. */
export function verdictTone(passed: boolean | null | undefined): "success" | "danger" | "neutral" {
  return passed === true ? "success" : passed === false ? "danger" : "neutral";
}

export function verdictLabel(passed: boolean | null | undefined): string {
  return passed === true ? "Passed" : passed === false ? "Failed" : "Indeterminate";
}
