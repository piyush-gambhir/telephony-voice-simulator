"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import Link from "next/link";
import { CheckCircle2, CircleAlert, Loader2 } from "lucide-react";

import { formatWhen } from "@/components/console/kit";
import { operations, type Operation, type OperationStatus } from "@/lib/workflows-api";
import { cn } from "@/lib/utils";

const POLL_MS = 1000;

export function isSettled(status: OperationStatus): boolean {
  return status === "succeeded" || status === "failed";
}

/**
 * Track one operation: start with the 202 response, then poll until it settles.
 * Pass null to clear.
 */
export function useOperation<R = unknown>(initial: Operation | null) {
  const [polled, setPolled] = useState<Operation<R> | null>(null);
  const id = initial?.id ?? null;
  const settled = initial ? isSettled(initial.status) : true;

  useEffect(() => {
    if (!id || settled) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const next = (await operations.get(id, controller.signal)) as Operation<R>;
        if (controller.signal.aborted) return;
        setPolled(next);
        if (!isSettled(next.status)) timer = setTimeout(poll, POLL_MS);
      } catch {
        if (!controller.signal.aborted) timer = setTimeout(poll, POLL_MS * 3);
      }
    };
    timer = setTimeout(poll, POLL_MS);
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [id, settled]);

  // A newer 202 response replaces whatever was polled for the previous operation.
  return polled && polled.id === id ? polled : (initial as Operation<R> | null);
}

const STATUS_LABEL: Record<OperationStatus, string> = {
  queued: "Queued",
  running: "Running",
  succeeded: "Done",
  failed: "Failed",
};

export function OperationStatusPill({ status }: { status: OperationStatus }) {
  return (
    <span
      className={cn(
        "inline-flex h-6 items-center gap-1.5 rounded-full px-2.5 text-xs font-medium",
        status === "succeeded" && "bg-success-soft text-success",
        status === "failed" && "bg-danger-soft text-danger",
        (status === "running" || status === "queued") && "bg-accent-soft text-accent-text",
      )}
    >
      {status === "succeeded" ? <CheckCircle2 className="size-3.5" />
        : status === "failed" ? <CircleAlert className="size-3.5" />
        : <Loader2 className="size-3.5 animate-spin" />}
      {STATUS_LABEL[status]}
    </span>
  );
}

function elapsed(operation: Operation): string | null {
  if (!operation.started_at) return null;
  const end = operation.finished_at ? Date.parse(operation.finished_at) : Date.now();
  const seconds = Math.max(0, Math.round((end - Date.parse(operation.started_at)) / 1000));
  return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

/** Log lines, newest at the bottom, following new output unless the reader scrolled up. */
export function OperationLog({ lines, className }: { lines: string[]; className?: string }) {
  const ref = useRef<HTMLPreElement>(null);
  const follow = useRef(true);
  useEffect(() => {
    const element = ref.current;
    if (element && follow.current) element.scrollTop = element.scrollHeight;
  }, [lines.length]);
  if (!lines.length) return null;
  return (
    <pre
      ref={ref}
      onScroll={(event) => {
        const element = event.currentTarget;
        follow.current = element.scrollHeight - element.scrollTop - element.clientHeight < 24;
      }}
      className={cn("max-h-64 overflow-auto rounded-lg bg-surface-1 px-3 py-2.5 font-mono text-[11.5px] leading-5 text-muted-foreground", className)}
    >
      {lines.join("\n")}
    </pre>
  );
}

/**
 * One operation: status, elapsed time, live log, then the error or the rendered result.
 * `renderResult` receives the result once the operation succeeds.
 */
export function OperationPanel<R>({
  operation,
  renderResult,
  className,
}: {
  operation: Operation<R>;
  renderResult?: (result: R) => ReactNode;
  className?: string;
}) {
  const time = elapsed(operation);
  return (
    <section className={cn("grid gap-3 rounded-xl bg-card p-4", className)} aria-live="polite">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <OperationStatusPill status={operation.status} />
        <p className="min-w-0 flex-1 truncate text-[13px] font-medium">{operation.title}</p>
        <span className="t-num text-xs text-muted-foreground">
          {time ?? `Queued ${formatWhen(operation.created_at, true)}`}
        </span>
        <Link
          href={`/console/operations/?id=${encodeURIComponent(operation.id)}`}
          className="text-xs text-accent-text hover:underline"
        >
          Details
        </Link>
      </div>
      <OperationLog lines={operation.log} />
      {operation.status === "failed" && operation.error && (
        <p role="alert" className="rounded-lg bg-danger-soft px-3 py-2 text-xs leading-5 text-danger">{operation.error}</p>
      )}
      {operation.status === "succeeded" && operation.result != null && renderResult?.(operation.result)}
    </section>
  );
}
