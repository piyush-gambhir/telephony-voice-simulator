"use client";

import { useState } from "react";
import { GitCompare, Loader2 } from "lucide-react";

import { EmptyStrip, Field, formatWhen, Panel, PanelTitle } from "@/components/console/kit";
import { InlineError, percent, Preformatted, ToggleChips, toggled } from "@/components/console/benchmarks-parts";
import type { ReportListing } from "@/components/console/benchmarks-summary";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useAction } from "@/hooks/use-api";
import { benchmarks } from "@/lib/workflows-api";

/** Side-by-side markdown for two or more finished reports, each optionally labelled. */
export function BenchmarkCompare({
  reports,
  loading,
  error,
}: {
  reports: ReportListing[];
  loading: boolean;
  error: string | null;
}) {
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [labels, setLabels] = useState<Record<string, string>>({});
  const [result, setResult] = useState<{ labels: string[]; markdown: string } | null>(null);
  const action = useAction();

  // Keep the order the reports are listed in, newest first.
  const chosen = reports.filter((report) => selected.has(report.report_id));
  const badLabel = chosen.some((report) => (labels[report.report_id] ?? "").includes("="));
  const valid = chosen.length >= 2 && !badLabel;

  async function compare() {
    const items = chosen.map((report) => {
      const label = (labels[report.report_id] ?? "").trim();
      return label ? `${label}=${report.report_id}` : report.report_id;
    });
    const response = await action.run(() => benchmarks.compare(items));
    if (response) setResult({ labels: response.labels, markdown: response.markdown });
  }

  if (error) return <InlineError message={error} />;
  if (loading && !reports.length) return <p className="text-[13px] text-muted-foreground" aria-live="polite">Loading reports</p>;
  if (reports.length < 2) {
    return <EmptyStrip title="Compare needs at least two reports." body="Finished benchmark runs appear here to pick from." />;
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[400px_minmax(0,1fr)]">
      <Panel className="h-fit">
        <PanelTitle aside={`${chosen.length} selected`}>Reports</PanelTitle>
        <div className="grid gap-4">
          <ToggleChips
            label="Reports to compare"
            selected={selected}
            onToggle={(id) => setSelected((current) => toggled(current, id))}
            options={reports.map((report) => ({
              id: report.report_id,
              label: report.report_id,
              title: `${report.suite ?? "Report"}, ${formatWhen(report.updated_at)}, pass rate ${percent(report.pass_rate)}`,
            }))}
          />
          {chosen.length > 0 && (
            <div className="grid gap-3">
              {chosen.map((report) => {
                const id = `compare-label-${report.report_id}`;
                return (
                  <Field key={report.report_id} id={id} label={`Label for ${report.report_id}`}>
                    <Input
                      id={id}
                      maxLength={60}
                      placeholder={report.report_id}
                      value={labels[report.report_id] ?? ""}
                      onChange={(event) => setLabels((current) => ({ ...current, [report.report_id]: event.target.value }))}
                    />
                  </Field>
                );
              })}
            </div>
          )}
          {badLabel && <p className="text-xs text-warning">Labels cannot contain “=”.</p>}
          <div className="grid gap-2">
            <div>
              <Button size="sm" disabled={!valid || action.pending} onClick={compare}>
                {action.pending ? <Loader2 className="animate-spin" /> : <GitCompare />}
                Compare
              </Button>
            </div>
            <InlineError message={action.error} />
          </div>
        </div>
      </Panel>

      <Panel className="min-w-0">
        <PanelTitle aside={result?.labels.join(", ")}>Comparison</PanelTitle>
        <div aria-live="polite">
          {result ? (
            <Preformatted label="Comparison report" text={result.markdown} />
          ) : (
            <p className="text-[13px] text-muted-foreground">Pick two or more reports to see them side by side.</p>
          )}
        </div>
      </Panel>
    </div>
  );
}
