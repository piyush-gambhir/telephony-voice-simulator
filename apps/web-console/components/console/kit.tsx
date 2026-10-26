"use client";

import { useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import Link from "next/link";
import { CircleAlert, Pause, Play, RefreshCw, X } from "lucide-react";

import { useConsole } from "@/components/console/console-provider";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { VERDICT_LABEL, type Verdict } from "@/lib/results";
import { cn } from "@/lib/utils";

/* ---------- formatting ---------- */

export const MISSING = "–";

export function formatWhen(iso: string | null | undefined, withSeconds = false): string {
  if (!iso) return MISSING;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return MISSING;
  const sameYear = date.getFullYear() === new Date().getFullYear();
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    ...(sameYear ? {} : { year: "numeric" }),
    hour: "2-digit",
    minute: "2-digit",
    ...(withSeconds ? { second: "2-digit" } : {}),
    hour12: false,
  });
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) return MISSING;
  if (seconds < 60) return `${Math.round(seconds)}s`;
  return `${Math.floor(seconds / 60)}m ${String(Math.round(seconds % 60)).padStart(2, "0")}s`;
}

/** Group North American numbers for scanning; leave every other address as written. */
export function formatAddress(value: string | null | undefined): string {
  if (!value) return MISSING;
  const nanp = /^\+1(\d{3})(\d{3})(\d{4})$/.exec(value);
  return nanp ? `+1 ${nanp[1]} ${nanp[2]} ${nanp[3]}` : value;
}

export function shortId(value: string): string {
  return value.length > 14 ? `${value.slice(0, 8)}…${value.slice(-4)}` : value;
}

/* ---------- page frame ---------- */

export function PageFrame({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cn("mx-auto flex w-full max-w-[1400px] flex-col gap-4 px-[var(--page-gutter)] pb-12 pt-5 md:pt-6", className)}>
      {children}
    </div>
  );
}

export function PageHeader({
  title,
  count,
  children,
  freshness = true,
}: {
  title: string;
  count?: ReactNode;
  children?: ReactNode;
  freshness?: boolean;
}) {
  return (
    <header className="flex flex-wrap items-center gap-x-3 gap-y-3">
      <div className="flex min-w-0 items-baseline gap-2.5">
        <h1 className="truncate text-xl font-semibold tracking-tight">{title}</h1>
        {count != null && <span className="t-num text-[13px] text-muted-foreground">{count}</span>}
      </div>
      {freshness && <Freshness />}
      {children && <div className="ml-auto flex flex-wrap items-center gap-2">{children}</div>}
    </header>
  );
}

/** "Updated 21:31" with refresh and pause, stamped only after a successful read. */
export function Freshness() {
  const { lastUpdatedAt, refreshing, refresh, autoRefresh, setAutoRefresh, online, busy } = useConsole();
  return (
    <div className="flex items-center gap-0.5 text-muted-foreground">
      <span className="t-num mr-1 text-xs" aria-live="polite">
        {online === false
          ? "API offline"
          : lastUpdatedAt
            ? `Updated ${lastUpdatedAt.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", hour12: false })}`
            : "Connecting"}
      </span>
      <Button
        variant="ghost"
        size="icon-xs"
        aria-label="Refresh now"
        disabled={refreshing || busy}
        onClick={() => void refresh(true)}
      >
        <RefreshCw className={cn(refreshing && "animate-spin")} />
      </Button>
      <Button
        variant="ghost"
        size="icon-xs"
        aria-label={autoRefresh ? "Pause auto-refresh" : "Resume auto-refresh"}
        aria-pressed={!autoRefresh}
        title={autoRefresh ? "Auto-refresh every 10s" : "Auto-refresh paused"}
        onClick={() => setAutoRefresh(!autoRefresh)}
      >
        {autoRefresh ? <Pause /> : <Play />}
      </Button>
    </div>
  );
}

export function Finding({ lead, detail }: { lead: string; detail?: string | null }) {
  return (
    <p className="text-[13px] leading-5">
      {lead}
      {detail && <span className="text-muted-foreground"> {detail}</span>}
    </p>
  );
}

/** Mutation and read failures stay inline until the next attempt. */
export function ConsoleErrors() {
  const { error, refreshError, clearError, online } = useConsole();
  const message = error ?? refreshError;
  if (!message) return null;
  return (
    <div role="alert" className="flex items-start gap-3 rounded-xl bg-danger-soft px-4 py-3 text-[13px] text-danger">
      <CircleAlert className="mt-0.5 size-4 shrink-0" />
      <p className="min-w-0 flex-1 leading-5">
        {message}
        {online === false && " Start the backend, then refresh."}
      </p>
      {error && (
        <Button variant="ghost" size="icon-xs" className="text-danger hover:text-danger" aria-label="Dismiss" onClick={clearError}>
          <X />
        </Button>
      )}
    </div>
  );
}

/* ---------- surfaces ---------- */

export function Panel({ className, children }: { className?: string; children: ReactNode }) {
  return <section className={cn("rounded-xl bg-card p-4 md:p-5", className)}>{children}</section>;
}

export function PanelTitle({ children, aside }: { children: ReactNode; aside?: ReactNode }) {
  return (
    <div className="mb-3 flex items-center justify-between gap-3">
      <h2 className="text-sm font-semibold">{children}</h2>
      {aside && <div className="t-num text-xs text-muted-foreground">{aside}</div>}
    </div>
  );
}

export function GroupLabel({ children }: { children: ReactNode }) {
  return <p className="t-micro text-subtle-foreground">{children}</p>;
}

export function EmptyStrip({ title, body, action }: { title: string; body?: string; action?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-4 rounded-xl bg-surface-1 px-5 py-4">
      <div className="min-w-0 flex-1">
        <p className="text-[13px] font-medium">{title}</p>
        {body && <p className="mt-0.5 text-[13px] text-muted-foreground">{body}</p>}
      </div>
      {action}
    </div>
  );
}

export function Field({
  id,
  label,
  help,
  children,
  className,
}: {
  id: string;
  label: string;
  help?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("grid gap-1.5", className)}>
      <Label htmlFor={id}>{label}</Label>
      {children}
      {help && <div className="text-xs leading-5 text-muted-foreground">{help}</div>}
    </div>
  );
}

/* ---------- status ---------- */

const VERDICT_TONE: Record<Verdict, { pill: string; dot: string }> = {
  passed: { pill: "bg-success-soft text-success", dot: "bg-success" },
  failed: { pill: "bg-danger-soft text-danger", dot: "bg-danger" },
  error: { pill: "bg-danger-soft text-danger", dot: "bg-danger" },
  no_verdict: { pill: "bg-idle-soft text-muted-foreground", dot: "bg-subtle-foreground" },
  queued: { pill: "bg-accent-soft text-accent-text", dot: "bg-accent" },
  running: { pill: "bg-accent-soft text-accent-text", dot: "bg-accent animate-pulse" },
  cancelled: { pill: "bg-idle-soft text-muted-foreground", dot: "bg-subtle-foreground" },
  ran: { pill: "bg-idle-soft text-muted-foreground", dot: "bg-subtle-foreground" },
};

export function VerdictPill({ verdict, className }: { verdict: Verdict; className?: string }) {
  const tone = VERDICT_TONE[verdict];
  return (
    <span className={cn("t-micro inline-flex h-[22px] shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full px-2", tone.pill, className)}>
      <span className={cn("size-1.5 rounded-full", tone.dot)} aria-hidden />
      {VERDICT_LABEL[verdict]}
    </span>
  );
}

export function VerdictDot({ verdict, className }: { verdict: Verdict; className?: string }) {
  return (
    <span
      role="img"
      aria-label={VERDICT_LABEL[verdict]}
      title={VERDICT_LABEL[verdict]}
      className={cn("inline-block size-2 shrink-0 rounded-full", VERDICT_TONE[verdict].dot, className)}
    />
  );
}

export function TonePill({ tone, children }: { tone: "success" | "warning" | "neutral" | "accent" | "danger"; children: ReactNode }) {
  const classes = {
    success: "bg-success-soft text-success",
    warning: "bg-warning-soft text-warning",
    neutral: "bg-idle-soft text-muted-foreground",
    accent: "bg-accent-soft text-accent-text",
    danger: "bg-danger-soft text-danger",
  }[tone];
  return <span className={cn("t-micro inline-flex h-[22px] shrink-0 items-center whitespace-nowrap rounded-full px-2", classes)}>{children}</span>;
}

/* ---------- tables ---------- */

export function DataTable({ children, className, label }: { children: ReactNode; className?: string; label: string }) {
  return (
    <div className={cn("overflow-hidden rounded-xl bg-card", className)}>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[720px] border-collapse text-[13px]" aria-label={label}>
          {children}
        </table>
      </div>
    </div>
  );
}

export function Th({ children, className }: { children?: ReactNode; className?: string }) {
  return (
    <th scope="col" className={cn("h-9 bg-surface-1 px-3 text-left text-xs font-medium text-muted-foreground first:pl-4 last:pr-4", className)}>
      {children}
    </th>
  );
}

export function Td({ children, className }: { children?: ReactNode; className?: string }) {
  return <td className={cn("h-[var(--h-row)] px-3 first:pl-4 last:pr-4", className)}>{children}</td>;
}

/** A row that opens its detail: the primary cell holds the link, the whole row is a target. */
export function LinkRow({ href, children, selected }: { href: string; children: ReactNode; selected?: boolean }) {
  return (
    <tr
      className={cn(
        "group relative cursor-pointer transition-colors duration-[var(--motion-fast)] even:bg-control/40 hover:bg-control-hover",
        selected && "bg-accent-soft even:bg-accent-soft hover:bg-accent-soft",
      )}
      data-href={href}
    >
      {children}
    </tr>
  );
}

export function RowLink({ href, children, className }: { href: string; children: ReactNode; className?: string }) {
  return (
    <Link
      href={href}
      className={cn(
        "font-medium outline-none after:absolute after:inset-0 after:content-[''] focus-visible:after:rounded-lg focus-visible:after:ring-2 focus-visible:after:ring-ring/40",
        className,
      )}
    >
      {children}
    </Link>
  );
}

/* ---------- confirmation ---------- */

export function useConfirm() {
  const { error, clearError } = useConsole();
  const [request, setRequest] = useState<{
    title: string;
    body: string;
    confirm: string;
    onConfirm: () => Promise<boolean | void>;
  } | null>(null);
  const [pending, setPending] = useState(false);

  const dialog = (
    <Dialog open={request !== null} onOpenChange={(open) => { if (!open && !pending) setRequest(null); }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{request?.title}</DialogTitle>
          <DialogDescription>{request?.body}</DialogDescription>
          {error && <p role="alert" className="mt-2 rounded-lg bg-danger-soft px-3 py-2 text-xs leading-5 text-danger">{error}</p>}
        </DialogHeader>
        <DialogFooter>
          <Button variant="ghost" size="sm" disabled={pending} onClick={() => setRequest(null)}>Cancel</Button>
          <Button
            variant="danger"
            size="sm"
            disabled={pending}
            onClick={async () => {
              if (!request) return;
              setPending(true);
              const outcome = await request.onConfirm();
              setPending(false);
              if (outcome !== false) setRequest(null);
            }}
          >
            {pending && <RefreshCw className="animate-spin" />}
            {request?.confirm}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );

  return {
    ask: (next: NonNullable<typeof request>) => { clearError(); setRequest(next); },
    dialog,
  };
}

/* ---------- segmented radio group ---------- */

/** A radio group with roving focus: Tab enters once, arrows move and select. */
export function Segmented<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: Array<{ id: T; label: string; count?: number }>;
  onChange: (value: T) => void;
}) {
  const refs = useRef<Array<HTMLButtonElement | null>>([]);
  const current = Math.max(0, options.findIndex((option) => option.id === value));

  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    const step = event.key === "ArrowRight" || event.key === "ArrowDown" ? 1
      : event.key === "ArrowLeft" || event.key === "ArrowUp" ? -1 : 0;
    if (!step) return;
    event.preventDefault();
    const next = (current + step + options.length) % options.length;
    onChange(options[next].id);
    refs.current[next]?.focus();
  }

  return (
    <div className="flex flex-wrap gap-1 rounded-lg bg-control p-0.5" role="radiogroup" aria-label={label}>
      {options.map((option, index) => {
        const active = index === current;
        return (
          <button
            key={option.id}
            ref={(element) => { refs.current[index] = element; }}
            type="button"
            role="radio"
            aria-checked={active}
            tabIndex={active ? 0 : -1}
            onClick={() => onChange(option.id)}
            onKeyDown={onKeyDown}
            className={cn(
              "flex h-7 items-center gap-1.5 rounded-md px-2.5 text-xs font-medium outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring/40",
              active ? "bg-card text-foreground [box-shadow:var(--shadow-raise)]" : "text-muted-foreground hover:text-foreground",
            )}
          >
            {option.label}
            {option.count != null && <span className="t-num text-[11px] text-subtle-foreground">{option.count}</span>}
          </button>
        );
      })}
    </div>
  );
}

/** Mutation errors inside a sheet: the page banner sits behind the modal overlay. */
export function SheetError() {
  const { error } = useConsole();
  if (!error) return null;
  return <p role="alert" className="mt-5 rounded-lg bg-danger-soft px-3.5 py-2.5 text-xs leading-5 text-danger">{error}</p>;
}
