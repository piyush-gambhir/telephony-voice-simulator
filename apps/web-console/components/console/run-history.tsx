"use client";

import { useState } from "react";
import { Check, CircleAlert, Clock3, X } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { HistoryPagination, HistoryToolbar } from "@/components/console/history-toolbar";
import { runDuration, runOutcome, runStatusBadge, timelineStepLabel } from "@/components/console/console-status";
import type { Endpoint, SimulationRun } from "@/lib/simulator-api";

const PAGE_SIZE = 20;

export function RunHistory({ runs, endpoints, busy, onCancel }: {
  runs: SimulationRun[];
  endpoints: Endpoint[];
  busy: boolean;
  onCancel: (runId: string) => Promise<void>;
}) {
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const [provider, setProvider] = useState("all");
  const [pageIndex, setPageIndex] = useState(0);
  const needle = query.trim().toLowerCase();
  const matches = runs.filter((run) => {
    const endpoint = endpoints.find((item) => item.id === run.endpoint_id);
    const matchesStatus = status === "all" || (status === "checks_incomplete"
      ? run.result.graded && run.result.passed == null
      : status === "checks_failed"
      ? run.result.graded && run.result.passed === false
      : status === "checks_passed"
        ? run.result.graded && run.result.passed === true
        : run.status === status);
    return matchesStatus && (provider === "all" || run.provider === provider)
      && [run.id, run.scenario, run.endpoint_id, endpoint?.name, endpoint?.address,
        run.caller_number, run.extension, run.destination, run.result.error, runOutcome(run)]
        .some((value) => value?.toLowerCase().includes(needle));
  });
  const page = Math.min(pageIndex, Math.max(0, Math.ceil(matches.length / PAGE_SIZE) - 1));
  const statuses = [...new Set(runs.map((run) => run.status))].sort();
  if (runs.some((run) => run.result.graded)) statuses.push("checks_passed", "checks_failed", "checks_incomplete");

  return (
    <div className="space-y-4">
      <HistoryToolbar
        label="runs" query={query} onQueryChange={(value) => { setQuery(value); setPageIndex(0); }}
        status={status} statuses={statuses} onStatusChange={(value) => { setStatus(value); setPageIndex(0); }}
        provider={provider} providers={[...new Set(runs.map((run) => run.provider))].sort()}
        onProviderChange={(value) => { setProvider(value); setPageIndex(0); }}
        records={matches} total={runs.length}
      />
      {!matches.length && (
        <Card><CardContent className="p-8 text-center text-sm text-muted-foreground">
          {runs.length ? "No runs match these filters." : "No runs yet. Start a scenario or an IVR simulation from Overview."}
        </CardContent></Card>
      )}
      {matches.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE).map((run) => {
        const endpoint = endpoints.find((item) => item.id === run.endpoint_id);
        const checks = run.result.analysis?.checks ?? [];
        return (
          <Card key={run.id}>
            <CardContent className="p-5 md:p-6">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="font-medium">{run.scenario.replaceAll("_", " ")}</p>
                    {runStatusBadge(run)}
                    <Badge variant="outline">{run.provider}</Badge>
                  </div>
                  <p className="mt-2 text-sm text-muted-foreground">
                    {run.result.error ?? run.result.summary ?? (run.status === "cancelled" ? "Removed from the inbound call queue." : "Waiting for call activity.")}
                  </p>
                  <p className="mt-2 text-xs text-muted-foreground">
                    {endpoint ? `${endpoint.name} · ${endpoint.address}` : run.endpoint_id}
                  </p>
                  <p className="mt-1 break-all font-mono text-xs text-muted-foreground">{run.id}</p>
                </div>
                <div className="flex flex-wrap items-center gap-3">
                  <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
                    <Clock3 className="size-3.5" />{new Date(run.created_at).toLocaleString()}
                  </span>
                  {run.status === "queued" && (
                    <Button variant="outline" size="sm" disabled={busy} onClick={() => void onCancel(run.id)}>
                      <X /> Cancel queued run
                    </Button>
                  )}
                </div>
              </div>
              {runOutcome(run) && (
                <dl className="mt-4 flex flex-wrap gap-x-6 gap-y-3 text-xs">
                  {[
                    ["Caller", run.caller_number ?? run.result.caller_number ?? "—"],
                    ["Extension", run.extension ?? run.result.extension ?? "—"],
                    ["Destination", run.destination ?? run.result.destination ?? "—"],
                    ["Outcome", runOutcome(run).replaceAll("_", " ")],
                    ["Duration", `${runDuration(run)}s`],
                  ].map(([label, value]) => (
                    <div key={label}><dt className="text-muted-foreground">{label}</dt><dd className="mt-1 font-medium">{value}</dd></div>
                  ))}
                </dl>
              )}
              {(run.timeline.length > 0 || checks.length > 0) && (
                <details className="mt-5">
                  <summary className="cursor-pointer text-sm font-medium">
                    Timeline ({run.timeline.length}) and checks ({checks.length})
                  </summary>
                  <div className="mt-3 space-y-3">
                    {run.timeline.length > 0 && (
                      <ol className="flex flex-wrap gap-2">
                        {run.timeline.map((step, index) => (
                          <li className="flex items-center gap-2 rounded-lg bg-muted/60 px-3 py-2 text-xs" key={index}>
                            <span className="text-muted-foreground">{index + 1}.</span>
                            <span>{timelineStepLabel(step)}</span>
                          </li>
                        ))}
                      </ol>
                    )}
                    {checks.length > 0 && (
                      <div className="grid gap-2 md:grid-cols-2">
                        {checks.map((check, index) => (
                          <div className="flex min-w-0 items-start gap-2 rounded-lg bg-muted/40 px-3 py-2 text-xs" key={index}>
                            {check.passed === true ? <Check className="mt-0.5 size-3.5 shrink-0 text-success" />
                              : check.passed === false ? <CircleAlert className="mt-0.5 size-3.5 shrink-0 text-destructive" />
                                : <Clock3 className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />}
                            <div className="min-w-0">
                              <p className="font-medium">{(check.check ?? check.name ?? `Check ${index + 1}`).replaceAll("_", " ")}</p>
                              {check.passed == null && <p className="text-muted-foreground">Not graded</p>}
                              {check.detail && <p className="mt-0.5 text-muted-foreground">{check.detail}</p>}
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </details>
              )}
            </CardContent>
          </Card>
        );
      })}
      <HistoryPagination page={page} count={matches.length} pageSize={PAGE_SIZE} onPageChange={setPageIndex} />
    </div>
  );
}
