"use client";

import { MISSING } from "@/components/console/kit";
import { count, Metric, MetricGrid, percent, seconds } from "@/components/console/benchmarks-parts";

/** One row of `GET /api/benchmarks/reports` (service/benchmarks.py list_reports). */
export type ReportListing = {
  report_id: string;
  run_id: string | null;
  suite: string | null;
  complete: boolean | null;
  planned_calls: number | null;
  calls: number | null;
  passed: number | null;
  failed: number | null;
  indeterminate: number | null;
  pass_rate: number | null;
  updated_at: string;
};

type DelayStats = { n: number; min: number; median: number; p90: number; max: number } | null;

/** evaluation/benchmark/scoring.py summarize. */
export type BenchmarkSummary = {
  calls: number;
  passed: number;
  failed: number;
  indeterminate: number;
  pass_rate: number | null;
  confusion_matrix: Record<string, Record<string, number>>;
  false_human_rate: number | null;
  false_machine_rate: number | null;
  classification_coverage: number | null;
  detection_delay: DelayStats;
  per_scenario: Record<string, {
    attempts: number;
    passed: number;
    failed: number;
    indeterminate: number;
    pass_rate: number | null;
    detection_delay: DelayStats;
    failures: Array<{ attempt: number; error: string | null; failed_checks: string[] }>;
  }>;
};

export type BenchmarkReport = {
  run_id?: string;
  suite?: string;
  sim_numbers?: string[];
  planned_calls?: number;
  complete?: boolean;
  summary?: BenchmarkSummary;
};

export const reportHref = (id: string) => `/console/benchmarks/?report=${encodeURIComponent(id)}`;

export function SummaryMetrics({ summary, planned }: { summary: BenchmarkSummary; planned?: number | null }) {
  const delay = summary.detection_delay;
  return (
    <MetricGrid>
      <Metric label="Calls" value={count(summary.calls)} detail={planned != null ? `of ${planned} planned` : undefined} />
      <Metric
        label="Pass rate"
        value={percent(summary.pass_rate)}
        detail={`${summary.passed} passed, ${summary.failed} failed, ${summary.indeterminate} indet.`}
      />
      <Metric label="False human" value={percent(summary.false_human_rate)} detail="Mailbox taken as a person" />
      <Metric label="False machine" value={percent(summary.false_machine_rate)} detail="Person taken as a mailbox" />
      <Metric label="Classified" value={percent(summary.classification_coverage)} />
      <Metric
        label="Detection delay"
        value={delay ? seconds(delay.median) : MISSING}
        detail={delay ? `p90 ${seconds(delay.p90)}, n ${delay.n}` : undefined}
      />
    </MetricGrid>
  );
}
