"use client";

import { useState, type DragEvent } from "react";
import { FileUp, Loader2 } from "lucide-react";

import {
  DataTable,
  EmptyStrip,
  PageFrame,
  PageHeader,
  Panel,
  PanelTitle,
  Td,
  Th,
} from "@/components/console/kit";
import { Button } from "@/components/ui/button";
import { useAction } from "@/hooks/use-api";
import { analysis, type SilenceSummary } from "@/lib/workflows-api";
import { cn } from "@/lib/utils";

const MAX_ROWS = 50_000;

const BUCKET_LABEL: Record<string, string> = {
  carrier_vm_stock: "Carrier voicemail (stock greeting)",
  business_vm: "Business voicemail",
  personal_vm_firstperson: "Personal voicemail",
  ivr_menu_press: "IVR menu",
  screener_saynamer: "Call screener",
  sit_not_in_service: "Not in service (SIT)",
  mailbox_full_unset: "Mailbox full or not set up",
  spanish: "Spanish",
  human_greet_then_silent: "Human greets, then silent",
  repeat_confusion: "Human can't hear or asks to repeat",
  true_silence_no_audio: "True silence (no audio)",
  unclassified: "Unclassified",
};

function bucketLabel(bucket: string): string {
  return BUCKET_LABEL[bucket] ?? bucket.replace(/_/g, " ");
}

type Row = { call_uid: string; user_text: string };
type Parsed = { name: string; rows: Row[]; malformed: number; overCap: boolean };
type Figure = { count: number; share: number | null };

/** One JSON object per line with call_uid and user_text; anything else counts as malformed. */
function parseJsonl(text: string): { rows: Row[]; malformed: number } {
  const rows: Row[] = [];
  let malformed = 0;
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line) continue;
    try {
      const value: unknown = JSON.parse(line);
      if (!value || typeof value !== "object" || Array.isArray(value)) { malformed += 1; continue; }
      const record = value as Record<string, unknown>;
      const text = record.user_text;
      if (text != null && typeof text !== "string") { malformed += 1; continue; }
      rows.push({ call_uid: String(record.call_uid ?? ""), user_text: text ?? "" });
    } catch {
      malformed += 1;
    }
  }
  return { rows, malformed };
}

/** Headline figures arrive as {count, share_percent}; accept a bare count too. */
function figure(value: unknown, total: number): Figure {
  if (typeof value === "number") return { count: value, share: total ? Math.round((value / total) * 1000) / 10 : null };
  if (value && typeof value === "object") {
    const record = value as { count?: unknown; share_percent?: unknown };
    return {
      count: typeof record.count === "number" ? record.count : 0,
      share: typeof record.share_percent === "number" ? record.share_percent : null,
    };
  }
  return { count: 0, share: null };
}

function examplesOf(summary: SilenceSummary): Array<[string, string[]]> {
  return Object.entries(summary.examples ?? {})
    .map(([bucket, value]): [string, string[]] => [bucket, Array.isArray(value) ? value.map(String) : []])
    .filter(([, items]) => items.length > 0);
}

export function AnalysisPage() {
  const [parsed, setParsed] = useState<Parsed | null>(null);
  const [readError, setReadError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [summary, setSummary] = useState<SilenceSummary | null>(null);
  const action = useAction();

  async function load(file: File | undefined) {
    if (!file) return;
    setReadError(null);
    setSummary(null);
    action.setError(null);
    try {
      const { rows, malformed } = parseJsonl(await file.text());
      setParsed({ name: file.name, rows: rows.slice(0, MAX_ROWS), malformed, overCap: rows.length > MAX_ROWS });
    } catch (cause) {
      setParsed(null);
      setReadError(cause instanceof Error ? cause.message : "Could not read the file");
    }
  }

  function onDrop(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    setDragging(false);
    void load(event.dataTransfer.files[0]);
  }

  async function classify() {
    if (!parsed?.rows.length) return;
    const next = await action.run(() => analysis.silence(parsed.rows));
    if (next) setSummary(next);
  }

  const canClassify = Boolean(parsed?.rows.length) && !action.pending;

  return (
    <PageFrame>
      <PageHeader title="Analysis" freshness={false} />
      <Panel>
        <PanelTitle>Silence-timeout transcripts</PanelTitle>
        <div className="grid gap-3">
          <label
            htmlFor="analysis-file"
            onDragOver={(event) => { event.preventDefault(); setDragging(true); }}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
            className={cn(
              "flex cursor-pointer flex-col items-center gap-2 rounded-xl px-4 py-8 text-center transition-colors duration-[var(--motion-fast)] focus-within:ring-2 focus-within:ring-ring/40",
              dragging ? "bg-accent-soft" : "bg-surface-1 hover:bg-control-hover",
            )}
          >
            <FileUp className="size-5 text-muted-foreground" aria-hidden />
            <span className="text-[13px] font-medium">Drop a JSONL file or choose one</span>
            <span className="text-xs text-muted-foreground">
              One {"{call_uid, user_text}"} object per line, exported from an agent&apos;s call log. Parsed in this browser and sent only to the local API.
            </span>
            <input
              id="analysis-file"
              type="file"
              accept=".jsonl,.ndjson,.json,.txt,application/x-ndjson,application/json,text/plain"
              className="sr-only"
              onChange={(event) => { void load(event.target.files?.[0]); event.target.value = ""; }}
            />
          </label>

          {readError && <p role="alert" className="rounded-lg bg-danger-soft px-3 py-2 text-xs leading-5 text-danger">{readError}</p>}

          {parsed && (
            <div className="flex flex-wrap items-center gap-x-4 gap-y-2" aria-live="polite">
              <p className="min-w-0 truncate text-[13px] font-medium">{parsed.name}</p>
              <p className="t-num text-[13px] text-muted-foreground">
                {parsed.rows.length.toLocaleString()} rows
                {parsed.malformed > 0 && `, ${parsed.malformed.toLocaleString()} malformed lines skipped`}
              </p>
              <Button size="sm" className="ml-auto" disabled={!canClassify} onClick={() => void classify()}>
                {action.pending && <Loader2 className="animate-spin" />}
                Classify
              </Button>
            </div>
          )}
          {parsed?.overCap && (
            <p className="rounded-lg bg-warning-soft px-3 py-2 text-xs leading-5 text-warning">
              The file has more than {MAX_ROWS.toLocaleString()} valid rows. Only the first {MAX_ROWS.toLocaleString()} are classified; split the file to cover the rest.
            </p>
          )}
          {parsed && parsed.rows.length === 0 && (
            <p className="rounded-lg bg-warning-soft px-3 py-2 text-xs leading-5 text-warning">No valid rows in this file.</p>
          )}
          {action.error && <p role="alert" className="rounded-lg bg-danger-soft px-3 py-2 text-xs leading-5 text-danger">{action.error}</p>}
        </div>
      </Panel>

      {summary ? (
        <Summary summary={summary} />
      ) : (
        <EmptyStrip title="No classification yet" body="Bucket counts, shares and example transcripts appear here after you classify a file." />
      )}
    </PageFrame>
  );
}

function Summary({ summary }: { summary: SilenceSummary }) {
  const total = summary.total;
  const headline: Array<{ label: string; value: Figure }> = [
    { label: "Calls", value: { count: total, share: null } },
    { label: "Voicemail-like", value: figure(summary.voicemail_like, total) },
    { label: "Any machine or system", value: figure(summary.any_machine_or_system, total) },
    { label: "True silence", value: figure(summary.true_silence, total) },
  ];
  const maxShare = Math.max(...summary.buckets.map((bucket) => bucket.share_percent), 0);
  const examples = examplesOf(summary);

  return (
    <div className="grid gap-4" aria-live="polite">
      <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
        {headline.map(({ label, value }) => (
          <div key={label} className="grid gap-1 rounded-xl bg-card px-4 py-3">
            <p className="text-xs text-muted-foreground">{label}</p>
            <p className="t-num text-xl font-semibold tracking-tight">
              {value.count.toLocaleString()}
              {value.share != null && <span className="ml-1.5 text-[13px] font-normal text-muted-foreground">{value.share}%</span>}
            </p>
          </div>
        ))}
      </div>

      <DataTable label="Silence-timeout buckets">
        <thead>
          <tr><Th>Bucket</Th><Th className="text-right">Count</Th><Th className="text-right">Share</Th><Th className="w-[40%]"><span className="sr-only">Proportion</span></Th></tr>
        </thead>
        <tbody>
          {summary.buckets.map((bucket) => (
            <tr key={bucket.bucket} className="even:bg-control/40">
              <Td className="font-medium">{bucketLabel(bucket.bucket)}</Td>
              <Td className="t-num text-right">{bucket.count.toLocaleString()}</Td>
              <Td className="t-num text-right text-muted-foreground">{bucket.share_percent}%</Td>
              <Td>
                <div className="h-2 w-full rounded-full bg-control" aria-hidden>
                  <div
                    className="h-2 rounded-full bg-accent"
                    style={{ width: `${maxShare ? (bucket.share_percent / maxShare) * 100 : 0}%` }}
                  />
                </div>
              </Td>
            </tr>
          ))}
        </tbody>
      </DataTable>

      {examples.length > 0 && (
        <Panel>
          <PanelTitle>Examples</PanelTitle>
          <div className="grid gap-4 md:grid-cols-2">
            {examples.map(([bucket, items]) => (
              <section key={bucket} className="grid content-start gap-2">
                <h3 className="text-[13px] font-medium">{bucketLabel(bucket)}</h3>
                <ul className="grid gap-1.5">
                  {items.map((text, index) => (
                    <li key={index} className="rounded-lg bg-surface-1 px-3 py-2 text-xs leading-5 text-muted-foreground">{text}</li>
                  ))}
                </ul>
              </section>
            ))}
          </div>
        </Panel>
      )}
    </div>
  );
}
