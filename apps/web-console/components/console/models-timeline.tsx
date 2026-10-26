"use client";

import type { ReactNode } from "react";

import { MISSING, TonePill } from "@/components/console/kit";
import { cn } from "@/lib/utils";

/** One step of a model timeline as the backend returns it (IVR, PBX story or dealer call). */
export type ModelStep = {
  index?: number;
  at?: string;
  actor?: string;
  kind: string;
  label?: string;
  detail?: string;
  outcome?: string;
  leg?: string;
  destination?: string;
  ref?: string;
  [key: string]: unknown;
};

const SUCCESS = new Set([
  "bridged",
  "connected",
  "answered_human",
  "answered_agent",
  "transferred_answered",
  "recovered_by_agent",
  "ANSWERED",
]);
const WARNING = new Set([
  "overflow",
  "voicemail",
  "mailbox_full",
  "answering_service",
  "busy",
  "no_answer",
  "dial_busy",
  "dial_no_answer",
  "transferred_voicemail",
]);
const DANGER = new Set([
  "dial_failed",
  "failed",
  "abandoned",
  "congestion",
  "unassigned_number",
  "loop_detected",
  "hop_limit",
  "rejected",
  "transfer_rejected",
  "caller_stranded",
  "digits_lost",
]);

export function outcomeTone(outcome: string | null | undefined): "success" | "warning" | "danger" | "neutral" {
  if (!outcome) return "neutral";
  if (SUCCESS.has(outcome)) return "success";
  if (WARNING.has(outcome)) return "warning";
  if (DANGER.has(outcome)) return "danger";
  return "neutral";
}

export function humanize(value: string): string {
  return value.replaceAll("_", " ");
}

export function OutcomePill({ outcome }: { outcome: string | null | undefined }) {
  return <TonePill tone={outcomeTone(outcome)}>{outcome ? humanize(outcome) : MISSING}</TonePill>;
}

/** A compact label/value list for the key facts of a run. */
export function Facts({ items, className }: { items: Array<[string, ReactNode]>; className?: string }) {
  return (
    <dl className={cn("grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-[13px]", className)}>
      {items.map(([label, value]) => (
        <div key={label} className="contents">
          <dt className="text-muted-foreground">{label}</dt>
          <dd className="min-w-0 break-words">{value ?? MISSING}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Steps in order: time, kind, label, detail, actor and where the step points. */
export function StepList({ steps, label }: { steps: ModelStep[]; label: string }) {
  if (!steps.length) return <p className="text-[13px] text-muted-foreground">No steps recorded.</p>;
  return (
    <ol className="grid gap-1" aria-label={label}>
      {steps.map((step, position) => {
        const where = step.destination ?? step.ref;
        const terminal = step.outcome && step.outcome !== "in_progress";
        return (
          <li
            key={step.index ?? position}
            className="grid grid-cols-[44px_minmax(0,1fr)] gap-3 rounded-lg px-2.5 py-2 odd:bg-surface-1"
          >
            <span className="t-num pt-0.5 text-xs text-muted-foreground">{step.at ?? MISSING}</span>
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <span className="text-[13px] font-medium">{step.label || humanize(step.kind)}</span>
                <TonePill tone="neutral">{humanize(step.kind)}</TonePill>
                {terminal && <OutcomePill outcome={step.outcome} />}
              </div>
              {step.detail && <p className="mt-0.5 text-[13px] leading-5 text-muted-foreground">{step.detail}</p>}
              <p className="t-num mt-0.5 text-xs text-subtle-foreground">
                {[step.actor, step.leg, where ? String(where) : null].filter(Boolean).join(" · ")}
              </p>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

export function ErrorLine({ message }: { message: string | null | undefined }) {
  if (!message) return null;
  return (
    <p role="alert" className="rounded-lg bg-danger-soft px-3 py-2 text-xs leading-5 text-danger">
      {message}
    </p>
  );
}
