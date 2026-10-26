"use client";

import { Suspense, useMemo, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { ArrowLeft, Loader2, RefreshCw, Stethoscope } from "lucide-react";

import {
  DataTable,
  EmptyStrip,
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
  TonePill,
} from "@/components/console/kit";
import { count, InlineError, percent, Preformatted, seconds } from "@/components/console/benchmarks-parts";
import {
  reportHref,
  SummaryMetrics,
  type BenchmarkReport,
  type BenchmarkSummary,
  type ReportListing,
} from "@/components/console/benchmarks-summary";
import { BenchmarkCompare } from "@/components/console/benchmarks-compare";
import { BenchmarkNewRun } from "@/components/console/benchmarks-run";
import { Button } from "@/components/ui/button";
import { useAction, useApi } from "@/hooks/use-api";
import { benchmarks } from "@/lib/workflows-api";

export function BenchmarksPage() {
  return (
    <Suspense fallback={<PageFrame><PageHeader title="Benchmarks" freshness={false} /></PageFrame>}>
      <BenchmarksView />
    </Suspense>
  );
}

type View = "reports" | "compare" | "run";

function BenchmarksView() {
  const params = useSearchParams();
  const reportId = params.get("report");
  const [view, setView] = useState<View>("reports");
  const reports = useApi((signal) => benchmarks.reports(signal) as Promise<ReportListing[]>, []);

  if (reportId) return <ReportDetail key={reportId} id={reportId} />;

  return (
    <PageFrame>
      <PageHeader title="Benchmarks" count={reports.data?.length} freshness={false}>
        <Segmented<View>
          label="Benchmark view"
          value={view}
          onChange={setView}
          options={[
            { id: "reports", label: "Reports" },
            { id: "compare", label: "Compare" },
            { id: "run", label: "New run" },
          ]}
        />
      </PageHeader>

      {view === "reports" && <ReportList reports={reports} />}
      {view === "compare" && <BenchmarkCompare reports={reports.data ?? []} loading={reports.loading} error={reports.error} />}
      {view === "run" && <BenchmarkNewRun onFinished={reports.reload} />}
    </PageFrame>
  );
}

function ReportList({ reports }: { reports: ReturnType<typeof useApi<ReportListing[]>> }) {
  if (reports.error) return <InlineError message={reports.error} />;
  if (reports.loading && !reports.data) return <p className="text-[13px] text-muted-foreground" aria-live="polite">Loading reports</p>;
  if (!reports.data?.length) {
    return <EmptyStrip title="No benchmark reports yet." body="Finished and partial runs appear here once a run writes its report.json." />;
  }
  return (
    <DataTable label="Benchmark reports">
      <thead>
        <tr>
          <Th>Report</Th>
          <Th>Suite</Th>
          <Th className="w-[118px]">Updated</Th>
          <Th className="w-[90px] text-right">Calls</Th>
          <Th className="w-[70px] text-right">Passed</Th>
          <Th className="w-[70px] text-right">Failed</Th>
          <Th className="w-[70px] text-right">Indet.</Th>
          <Th className="w-[86px] text-right">Pass rate</Th>
          <Th className="w-[96px]">State</Th>
        </tr>
      </thead>
      <tbody>
        {reports.data.map((report) => {
          const href = reportHref(report.report_id);
          return (
            <LinkRow key={report.report_id} href={href}>
              <Td><RowLink href={href} className="t-num whitespace-nowrap text-xs">{report.report_id}</RowLink></Td>
              <Td className="whitespace-nowrap text-xs">{report.suite ?? MISSING}</Td>
              <Td className="t-num whitespace-nowrap text-xs text-muted-foreground">{formatWhen(report.updated_at)}</Td>
              <Td className="t-num text-right text-xs">
                {count(report.calls)}
                {report.planned_calls != null && <span className="text-muted-foreground"> / {report.planned_calls}</span>}
              </Td>
              <Td className="t-num text-right text-xs">{count(report.passed)}</Td>
              <Td className="t-num text-right text-xs">{count(report.failed)}</Td>
              <Td className="t-num text-right text-xs">{count(report.indeterminate)}</Td>
              <Td className="t-num text-right text-xs font-medium">{percent(report.pass_rate)}</Td>
              <Td>{report.complete ? <TonePill tone="success">Complete</TonePill> : <TonePill tone="warning">Partial</TonePill>}</Td>
            </LinkRow>
          );
        })}
      </tbody>
    </DataTable>
  );
}

/* ---------- report detail ---------- */

function ReportDetail({ id }: { id: string }) {
  const report = useApi((signal) => benchmarks.report(id, signal) as Promise<BenchmarkReport>, [id]);
  const markdown = useApi((signal) => benchmarks.reportMarkdown(id, signal), [id]);
  const rescore = useAction();
  const preflight = useAction();
  const [rescored, setRescored] = useState<{ summary: BenchmarkSummary; markdown: string } | null>(null);
  const [health, setHealth] = useState<{ healthy: boolean; detail: string } | null>(null);

  const data = report.data;
  const summary = data?.summary;

  return (
    <PageFrame>
      <div>
        <Button asChild variant="ghost" size="xs" className="-ml-2 text-muted-foreground">
          <Link href="/console/benchmarks/"><ArrowLeft />Benchmarks</Link>
        </Button>
      </div>
      <PageHeader title={id} freshness={false}>
        <Button
          variant="secondary"
          size="sm"
          disabled={preflight.pending}
          onClick={async () => {
            const result = await preflight.run(() => benchmarks.preflight(id));
            if (result) setHealth(result);
          }}
        >
          {preflight.pending ? <Loader2 className="animate-spin" /> : <Stethoscope />}
          Preflight check
        </Button>
        <Button
          variant="secondary"
          size="sm"
          disabled={rescore.pending}
          onClick={async () => {
            const result = await rescore.run(() => benchmarks.rescore(id));
            if (result) setRescored({ summary: result.summary as unknown as BenchmarkSummary, markdown: result.markdown });
          }}
        >
          {rescore.pending ? <Loader2 className="animate-spin" /> : <RefreshCw />}
          Rescore
        </Button>
      </PageHeader>

      <div aria-live="polite" className="grid gap-2">
        <InlineError message={preflight.error} />
        <InlineError message={rescore.error} />
        {health && (
          <div className="flex flex-wrap items-start gap-3 rounded-xl bg-card px-4 py-3">
            <TonePill tone={health.healthy ? "success" : "danger"}>{health.healthy ? "Healthy" : "Broken"}</TonePill>
            <p className="min-w-0 flex-1 text-[13px] leading-5">{health.detail || MISSING}</p>
          </div>
        )}
      </div>

      {report.error ? (
        <InlineError message={report.error} />
      ) : !data ? (
        <p className="text-[13px] text-muted-foreground" aria-live="polite">Loading report</p>
      ) : (
        <>
          <Panel>
            <PanelTitle aside={data.run_id}>{data.suite ?? "Report"}</PanelTitle>
            <div className="mb-4 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-muted-foreground">
              {data.complete ? <TonePill tone="success">Complete</TonePill> : <TonePill tone="warning">Partial</TonePill>}
              {data.sim_numbers?.length ? <span className="t-num">Lines {data.sim_numbers.join(", ")}</span> : null}
            </div>
            {summary ? <SummaryMetrics summary={summary} planned={data.planned_calls} /> : <p className="text-[13px] text-muted-foreground">This report has no summary.</p>}
          </Panel>

          {summary && <ConfusionMatrix matrix={summary.confusion_matrix} />}
          {summary && <ScenarioTable summary={summary} />}

          {rescored && (
            <Panel>
              <PanelTitle aside="Current scoring rules">Rescored</PanelTitle>
              <div className="grid gap-4">
                <SummaryMetrics summary={rescored.summary} planned={data.planned_calls} />
                <Preformatted label="Rescored report" text={rescored.markdown} />
              </div>
            </Panel>
          )}

          <Panel>
            <PanelTitle>Report</PanelTitle>
            {markdown.error ? (
              <InlineError message={markdown.error} />
            ) : markdown.data ? (
              <Preformatted label="Rendered report" text={markdown.data.markdown} />
            ) : (
              <p className="text-[13px] text-muted-foreground">Loading report</p>
            )}
          </Panel>
        </>
      )}
    </PageFrame>
  );
}

function ConfusionMatrix({ matrix }: { matrix: Record<string, Record<string, number>> }) {
  const expected = Object.keys(matrix).sort();
  const observed = [...new Set(expected.flatMap((row) => Object.keys(matrix[row])))].sort();
  if (!expected.length) return null;
  return (
    <DataTable label="Expected against observed class" className="[&_table]:min-w-[420px]">
      <thead>
        <tr>
          <Th>Expected / observed</Th>
          {observed.map((name) => <Th key={name} className="text-right">{name}</Th>)}
        </tr>
      </thead>
      <tbody>
        {expected.map((row) => (
          <tr key={row} className="even:bg-control/40">
            <Td className="text-xs font-medium">{row}</Td>
            {observed.map((name) => (
              <Td key={name} className={`t-num text-right text-xs ${name === row ? "font-semibold" : "text-muted-foreground"}`}>
                {matrix[row][name] ?? 0}
              </Td>
            ))}
          </tr>
        ))}
      </tbody>
    </DataTable>
  );
}

function ScenarioTable({ summary }: { summary: BenchmarkSummary }) {
  const rows = useMemo(() => Object.entries(summary.per_scenario ?? {}), [summary]);
  if (!rows.length) return null;
  return (
    <DataTable label="Results per scenario">
      <thead>
        <tr>
          <Th>Scenario</Th>
          <Th className="w-[80px] text-right">Attempts</Th>
          <Th className="w-[70px] text-right">Passed</Th>
          <Th className="w-[70px] text-right">Failed</Th>
          <Th className="w-[70px] text-right">Indet.</Th>
          <Th className="w-[86px] text-right">Pass rate</Th>
          <Th className="w-[96px] text-right">Delay (med)</Th>
          <Th>First failure</Th>
        </tr>
      </thead>
      <tbody>
        {rows.map(([name, entry]) => {
          const failure = entry.failures[0];
          const reason = failure ? (failure.failed_checks[0] ?? failure.error ?? MISSING) : null;
          return (
            <tr key={name} className="even:bg-control/40">
              <Td className="text-xs font-medium">{name}</Td>
              <Td className="t-num text-right text-xs">{entry.attempts}</Td>
              <Td className="t-num text-right text-xs">{entry.passed}</Td>
              <Td className="t-num text-right text-xs">{entry.failed}</Td>
              <Td className="t-num text-right text-xs">{entry.indeterminate}</Td>
              <Td className="t-num text-right text-xs font-medium">{percent(entry.pass_rate)}</Td>
              <Td className="t-num text-right text-xs text-muted-foreground">{entry.detection_delay ? seconds(entry.detection_delay.median) : MISSING}</Td>
              <Td className="max-w-[360px] text-xs text-muted-foreground">
                {reason ? (
                  <span className="line-clamp-2" title={failure.failed_checks.join("\n") || failure.error || undefined}>
                    Attempt {failure.attempt}: {reason}
                  </span>
                ) : MISSING}
              </Td>
            </tr>
          );
        })}
      </tbody>
    </DataTable>
  );
}

