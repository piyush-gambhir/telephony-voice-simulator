"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { X } from "lucide-react";

import { ActionError } from "@/components/console/hosted-parts";
import {
  DataTable,
  EmptyStrip,
  formatDuration,
  formatWhen,
  LinkRow,
  MISSING,
  PageFrame,
  PageHeader,
  Panel,
  PanelTitle,
  RowLink,
  Segmented,
  Td,
  Th,
} from "@/components/console/kit";
import { isSettled, OperationLog, OperationStatusPill, useOperation } from "@/components/console/operation-panel";
import { JsonBlock } from "@/components/json-block";
import { Button } from "@/components/ui/button";
import { useApi } from "@/hooks/use-api";
import { operations, type Operation } from "@/lib/workflows-api";

const POLL_MS = 3000;
const ALL = "all";

function durationOf(operation: Operation): number | null {
  if (!operation.started_at) return null;
  const end = operation.finished_at ? Date.parse(operation.finished_at) : Date.now();
  return Math.max(0, (end - Date.parse(operation.started_at)) / 1000);
}

function hrefFor(id: string): string {
  return `/console/operations/?id=${encodeURIComponent(id)}`;
}

function OperationDetail({ id }: { id: string }) {
  const loaded = useApi((signal) => operations.get(id, signal), [id]);
  // Polls until the operation settles, so a running job's log stays live.
  const operation = useOperation(loaded.data);

  if (loaded.error) {
    return (
      <Panel>
        <PanelTitle aside={<CloseLink />}>Operation</PanelTitle>
        <ActionError message={loaded.error} />
      </Panel>
    );
  }
  if (!operation || operation.id !== id) return null;

  const params = Object.entries(operation.params ?? {});
  return (
    <Panel className="grid gap-4">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <OperationStatusPill status={operation.status} />
        <h2 className="min-w-0 flex-1 text-sm font-semibold">{operation.title}</h2>
        <CloseLink />
      </div>
      <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-5 gap-y-1 text-xs sm:grid-cols-[auto_minmax(0,1fr)_auto_minmax(0,1fr)]">
        <dt className="text-muted-foreground">Kind</dt>
        <dd className="font-mono">{operation.kind}</dd>
        <dt className="text-muted-foreground">ID</dt>
        <dd className="break-all font-mono">{operation.id}</dd>
        <dt className="text-muted-foreground">Queued</dt>
        <dd className="t-num">{formatWhen(operation.created_at, true)}</dd>
        <dt className="text-muted-foreground">Started</dt>
        <dd className="t-num">{formatWhen(operation.started_at, true)}</dd>
        <dt className="text-muted-foreground">Finished</dt>
        <dd className="t-num">{formatWhen(operation.finished_at, true)}</dd>
        <dt className="text-muted-foreground">Duration</dt>
        <dd className="t-num">{formatDuration(durationOf(operation))}</dd>
      </dl>

      <div className="grid gap-1.5">
        <h3 className="text-xs font-medium text-muted-foreground">Parameters</h3>
        {params.length ? (
          <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-5 gap-y-1 rounded-lg bg-surface-1 px-3 py-2.5 text-xs">
            {params.map(([key, value]) => (
              <div key={key} className="contents">
                <dt className="text-muted-foreground">{key}</dt>
                <dd className="break-all font-mono">
                  {value == null || value === "" ? MISSING : typeof value === "string" ? value : JSON.stringify(value)}
                </dd>
              </div>
            ))}
          </dl>
        ) : (
          <p className="text-xs text-muted-foreground">None</p>
        )}
      </div>

      <div className="grid gap-1.5" aria-live="polite">
        <h3 className="text-xs font-medium text-muted-foreground">Log</h3>
        {operation.log.length ? (
          <OperationLog lines={operation.log} className="max-h-[480px]" />
        ) : (
          <p className="text-xs text-muted-foreground">{isSettled(operation.status) ? "No log lines." : "Waiting for output."}</p>
        )}
      </div>

      {operation.error && (
        <div className="grid gap-1.5">
          <h3 className="text-xs font-medium text-muted-foreground">Error</h3>
          <p role="alert" className="whitespace-pre-wrap break-words rounded-lg bg-danger-soft px-3 py-2 text-xs leading-5 text-danger">
            {operation.error}
          </p>
        </div>
      )}

      {operation.result != null && (
        <div className="grid gap-1.5">
          <h3 className="text-xs font-medium text-muted-foreground">Result</h3>
          <JsonBlock value={operation.result} />
        </div>
      )}
    </Panel>
  );
}

function CloseLink() {
  return (
    <Button variant="ghost" size="icon-xs" asChild aria-label="Close details">
      <Link href="/console/operations/">
        <X />
      </Link>
    </Button>
  );
}

export function OperationsPage() {
  const selectedId = useSearchParams().get("id");
  const list = useApi((signal) => operations.list(undefined, signal), []);
  const [kind, setKind] = useState<string>(ALL);
  const all = [...(list.data ?? [])].sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at));
  const active = all.some((item) => !isSettled(item.status));
  const { reload, setData } = list;

  // Poll quietly while anything is queued or running; each new list re-arms the timer.
  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      operations.list(undefined, controller.signal).then(setData, () => {
        if (!controller.signal.aborted) setData((current) => (current ? [...current] : current));
      });
    }, POLL_MS);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [active, list.data, setData]);

  const kinds = [...new Set(all.map((item) => item.kind))].sort();
  const shown = kind === ALL ? all : all.filter((item) => item.kind === kind);

  return (
    <PageFrame>
      <PageHeader title="Operations" count={list.data ? all.length : null} freshness={false}>
        <Button variant="secondary" size="sm" disabled={list.loading} onClick={reload}>Refresh</Button>
      </PageHeader>
      <ActionError message={list.error} />

      {selectedId && <OperationDetail key={selectedId} id={selectedId} />}

      {kinds.length > 1 && (
        <Segmented
          label="Filter by kind"
          value={kinds.includes(kind) ? kind : ALL}
          onChange={setKind}
          options={[
            { id: ALL, label: "All", count: all.length },
            ...kinds.map((item) => ({ id: item, label: item, count: all.filter((op) => op.kind === item).length })),
          ]}
        />
      )}

      {shown.length ? (
        <DataTable label="Operations">
          <thead>
            <tr>
              <Th>Title</Th>
              <Th className="w-[150px]">Kind</Th>
              <Th className="w-[110px]">Status</Th>
              <Th className="w-[130px]">Started</Th>
              <Th className="w-[90px] text-right">Duration</Th>
            </tr>
          </thead>
          <tbody>
            {shown.map((item) => {
              const href = hrefFor(item.id);
              return (
                <LinkRow key={item.id} href={href} selected={item.id === selectedId}>
                  <Td className="max-w-[420px] truncate"><RowLink href={href} className="text-[13px]">{item.title}</RowLink></Td>
                  <Td className="font-mono text-xs text-muted-foreground">{item.kind}</Td>
                  <Td><OperationStatusPill status={item.status} /></Td>
                  <Td className="t-num whitespace-nowrap text-xs text-muted-foreground">{formatWhen(item.started_at ?? item.created_at)}</Td>
                  <Td className="t-num text-right text-xs text-muted-foreground">{formatDuration(durationOf(item))}</Td>
                </LinkRow>
              );
            })}
          </tbody>
        </DataTable>
      ) : list.data && !list.error ? (
        <EmptyStrip title="No operations yet." body="Deploys, smoke calls, grading, benchmarks and other long jobs appear here as they run." />
      ) : null}
    </PageFrame>
  );
}
