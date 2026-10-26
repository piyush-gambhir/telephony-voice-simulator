"use client";

import { useMemo, useState, type ReactNode } from "react";
import Link from "next/link";
import { BookOpen, Plus, Search } from "lucide-react";

import { useConsole } from "@/components/console/console-provider";
import {
  ConsoleErrors,
  DataTable,
  EmptyStrip,
  Finding,
  formatWhen,
  LinkRow,
  MISSING,
  PageFrame,
  PageHeader,
  RowLink,
  Segmented,
  Td,
  Th,
  VerdictDot,
} from "@/components/console/kit";
import { QueueRunSheet } from "@/components/console/queue-run-sheet";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { buildCoverage, type Coverage } from "@/lib/results";
import type { Scenario } from "@/lib/simulator-api";
import { cn } from "@/lib/utils";

const KINDS: Array<{ id: "all" | Scenario["kind"]; label: string }> = [
  { id: "all", label: "All" },
  { id: "amd", label: "AMD" },
  { id: "ivr", label: "IVR" },
  { id: "pbx", label: "PBX" },
];

/** Worst first: recent failures, then missing verdicts, then passing, then never run. */
function attention(row: Coverage): number {
  if (!row.recent.length) return 4;
  if (row.recent.includes("failed")) return (row.passRate ?? 0) - 1;
  if (row.recent.includes("no_verdict")) return 2;
  return 3;
}

function TickMeter({ parts }: { parts: Array<{ value: number; className: string }> }) {
  const total = 30;
  const sum = parts.reduce((acc, part) => acc + part.value, 0) || 1;
  const ticks = parts.flatMap((part) =>
    Array.from({ length: Math.round((part.value / sum) * total) }, () => part.className));
  while (ticks.length < total) ticks.push("bg-control-active");
  return (
    <div className="flex h-3.5 gap-[2px]" aria-hidden>
      {ticks.slice(0, total).map((className, index) => (
        <span key={index} className={cn("flex-1 rounded-[2px]", className)} />
      ))}
    </div>
  );
}

function Kpi({ label, value, unit, children }: { label: string; value: string; unit?: string; children?: ReactNode }) {
  return (
    <div className="rounded-xl bg-card px-4 py-3.5">
      <p className="text-xs font-medium text-muted-foreground">{label}</p>
      <p className="mb-2.5 mt-1.5 flex items-baseline gap-1.5">
        <span className="text-2xl font-semibold tabular-nums tracking-tight">{value}</span>
        {unit && <span className="text-xs text-muted-foreground">{unit}</span>}
      </p>
      {children}
    </div>
  );
}

export function ScenariosPage() {
  const { results, data, loading } = useConsole();
  const [kind, setKind] = useState<(typeof KINDS)[number]["id"]>("all");
  const [query, setQuery] = useState("");
  const [queueFor, setQueueFor] = useState<string | null>(null);

  const coverage = useMemo(() => buildCoverage(results, data.scenarios), [results, data.scenarios]);
  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return coverage
      .filter((row) => kind === "all" || row.scenario.kind === kind)
      .filter((row) => !needle || [row.title, row.scenario.name, row.scenario.description]
        .some((value) => value.toLowerCase().includes(needle)))
      .sort((left, right) => attention(left) - attention(right) || left.title.localeCompare(right.title));
  }, [coverage, kind, query]);

  const graded = results.filter((result) => result.verdict === "passed" || result.verdict === "failed");
  const passed = graded.filter((result) => result.verdict === "passed").length;
  const failed = graded.length - passed;
  const noVerdict = results.filter((result) => result.verdict === "no_verdict").length;
  const tested = coverage.filter((row) => row.recent.length > 0);
  const failing = tested.filter((row) => row.recent.includes("failed"));
  const neverRun = coverage.length - tested.length;

  const checkCounts = new Map<string, number>();
  failing.forEach((row) => {
    const check = row.lastFailure?.check;
    if (check) checkCounts.set(check, (checkCounts.get(check) ?? 0) + 1);
  });
  const topCheck = [...checkCounts.entries()].sort((left, right) => right[1] - left[1])[0];

  return (
    <PageFrame>
      <PageHeader title="Scenarios" count={loading ? null : visible.length}>
        <Button size="sm" onClick={() => setQueueFor("")}><Plus /> Queue a run</Button>
      </PageHeader>
      <ConsoleErrors />

      {!loading && (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <Kpi label="Pass rate" value={graded.length ? `${Math.round((passed / graded.length) * 100)}%` : MISSING} unit={`${passed} of ${graded.length} graded`}>
            <TickMeter parts={[{ value: passed, className: "bg-success" }, { value: failed, className: "bg-danger" }]} />
          </Kpi>
          <Kpi label="Failing scenarios" value={String(failing.length)} unit={`of ${tested.length} tested`}>
            <TickMeter parts={[{ value: failing.length, className: "bg-danger" }, { value: tested.length - failing.length, className: "bg-control-active" }]} />
          </Kpi>
          <Kpi label="No verdict" value={String(noVerdict)} unit="evidence missing">
            <TickMeter parts={[{ value: noVerdict, className: "bg-subtle-foreground" }, { value: Math.max(0, results.length - noVerdict), className: "bg-control-active" }]} />
          </Kpi>
          <Kpi label="Never run" value={String(neverRun)} unit={`of ${coverage.length}`}>
            <TickMeter parts={[{ value: neverRun, className: "bg-accent" }, { value: tested.length, className: "bg-control-active" }]} />
          </Kpi>
        </div>
      )}

      {!loading && failing.length > 0 && (
        <Finding
          lead={`${failing.length} of ${tested.length} tested scenarios failed recently.`}
          detail={topCheck ? `${topCheck[0]} is the latest failure on ${topCheck[1]} of them.` : null}
        />
      )}

      <div className="flex flex-wrap items-center gap-2">
        <div className="relative w-full sm:w-72">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-subtle-foreground" />
          <Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search scenarios" aria-label="Search scenarios" className="h-8 pl-9" />
        </div>
        <Segmented
          label="Kind"
          value={kind}
          onChange={setKind}
          options={KINDS.map((item) => ({
            id: item.id,
            label: item.label,
            count: item.id === "all" ? coverage.length : coverage.filter((row) => row.scenario.kind === item.id).length,
          }))}
        />
      </div>

      {visible.length ? (
        <DataTable label="Scenario coverage">
          <thead>
            <tr>
              <Th className="w-[34%]">Scenario</Th>
              <Th className="w-[70px]">Kind</Th>
              <Th className="w-[120px]">Last 5</Th>
              <Th className="w-[90px] text-right">Pass rate</Th>
              <Th>Latest failure</Th>
              <Th className="w-[118px]">Last run</Th>
              <Th className="w-[112px]" />
            </tr>
          </thead>
          <tbody>
            {visible.map((row) => {
              const href = `/console?q=${encodeURIComponent(row.scenario.name)}`;
              return (
                <LinkRow key={row.scenario.name} href={href}>
                  <Td className="max-w-0">
                    <RowLink href={href} className="block truncate">{row.title}</RowLink>
                  </Td>
                  <Td className="t-micro text-muted-foreground">{row.scenario.kind}</Td>
                  <Td>
                    <span className="flex items-center gap-1.5">
                      {Array.from({ length: 5 }, (_, index) => row.recent[index]
                        ? <VerdictDot key={index} verdict={row.recent[index]} className="size-2.5" />
                        : <span key={index} className="size-2.5 rounded-full bg-control-active" aria-hidden />)}
                    </span>
                  </Td>
                  <Td className="t-num text-right text-xs">
                    {row.passRate == null ? <span className="text-subtle-foreground">{MISSING}</span> : `${Math.round(row.passRate * 100)}%`}
                  </Td>
                  <Td className="max-w-0">
                    <span className="block truncate text-xs text-muted-foreground">
                      {row.lastFailure ? (
                        <>
                          {row.lastFailure.check && <span className="t-num mr-1.5 text-danger">{row.lastFailure.check}</span>}
                          {row.lastFailure.text}
                        </>
                      ) : row.recent.length ? "" : "Never run on a line"}
                    </span>
                  </Td>
                  <Td className="t-num whitespace-nowrap text-xs text-muted-foreground">{formatWhen(row.lastAt)}</Td>
                  <Td>
                    <span className="relative z-10 flex justify-end gap-1">
                      <Button asChild variant="ghost" size="icon-xs" aria-label={`${row.title} reference`} title="Reference">
                        <Link href={`/docs/${row.scenario.name}/`}><BookOpen /></Link>
                      </Button>
                      <Button variant="secondary" size="xs" onClick={() => setQueueFor(row.scenario.name)}>Queue</Button>
                    </span>
                  </Td>
                </LinkRow>
              );
            })}
          </tbody>
        </DataTable>
      ) : !loading ? (
        <EmptyStrip title="No scenarios match." body="Try another kind or search." />
      ) : (
        <div className="h-96 rounded-xl bg-card" />
      )}

      <QueueRunSheet
        key={queueFor ?? "closed"}
        open={queueFor !== null}
        onOpenChange={(open) => { if (!open) setQueueFor(null); }}
        scenario={queueFor || undefined}
      />
    </PageFrame>
  );
}
