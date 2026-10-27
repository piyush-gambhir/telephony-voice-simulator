"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ListChecks, Loader2, Play } from "lucide-react";

import {
  DataTable,
  EmptyStrip,
  Field,
  formatAddress,
  formatDuration,
  Panel,
  PanelTitle,
  Segmented,
  Td,
  Th,
  TonePill,
  useConfirm,
} from "@/components/console/kit";
import { OperationPanel, useOperation } from "@/components/console/operation-panel";
import {
  Facts,
  InlineError,
  parseList,
  ToggleChips,
  toggled,
} from "@/components/console/benchmarks-parts";
import { reportHref, SummaryMetrics, type BenchmarkSummary } from "@/components/console/benchmarks-summary";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { useAction, useApi } from "@/hooks/use-api";
import { simulatorApi } from "@/lib/simulator-api";
import { benchmarks, type BenchmarkOptions, type Operation } from "@/lib/workflows-api";

/** `POST /api/benchmarks/plan` (service/benchmarks.py plan). */
type Plan = {
  name: string;
  scenarios: string[];
  repeat: number;
  pace_seconds: number;
  call_timeout_seconds: number;
  sim_numbers: string[];
  concurrency: number;
  calls: number;
  lanes: string[];
  expected_classes: Record<string, string>;
};

/** The operation result of a benchmark run (service/benchmarks.py run). */
type RunResult = {
  report_id: string;
  report_dir: string;
  ok: boolean;
  complete: boolean;
  planned_calls: number;
  summary: BenchmarkSummary;
};

type Source = "suite" | "scenarios";

/** Blank means "use the suite default"; otherwise a whole number of at least `min`. */
function wholeNumber(value: string, min: number): number | null | undefined {
  if (!value.trim()) return undefined;
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed >= min ? parsed : null;
}

export function BenchmarkNewRun({ onFinished }: { onFinished: () => void }) {
  const suites = useApi((signal) => benchmarks.suites(signal), []);
  const scenarios = useApi((signal) => simulatorApi.scenarios(signal), []);
  const amd = useMemo(() => (scenarios.data ?? []).filter((scenario) => scenario.kind === "amd"), [scenarios.data]);

  const [source, setSource] = useState<Source>("suite");
  const [suite, setSuite] = useState("");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [filter, setFilter] = useState("");
  const [repeat, setRepeat] = useState("");
  const [numbers, setNumbers] = useState("");
  const [concurrency, setConcurrency] = useState("");
  const [pace, setPace] = useState("");
  const [audio, setAudio] = useState(true);

  const [plan, setPlan] = useState<{ key: string; plan: Plan } | null>(null);
  const [operation, setOperation] = useState<Operation | null>(null);
  const tracked = useOperation<RunResult>(operation);
  const planAction = useAction();
  const runAction = useAction();
  const { ask, dialog } = useConfirm();

  const chosenSuite = suite || suites.data?.[0]?.name || "";
  const repeatValue = wholeNumber(repeat, 1);
  const concurrencyValue = wholeNumber(concurrency, 1);
  const paceValue = pace.trim() ? Number(pace) : undefined;
  const paceValid = paceValue === undefined || (Number.isFinite(paceValue) && paceValue >= 0);
  const valid = (source === "suite" ? Boolean(chosenSuite) : picked.size > 0)
    && repeatValue !== null && concurrencyValue !== null && paceValid;

  const options: BenchmarkOptions = useMemo(() => {
    const lines = parseList(numbers);
    return {
      ...(source === "suite" ? { suite: chosenSuite } : { scenarios: amd.filter((s) => picked.has(s.name)).map((s) => s.name) }),
      ...(repeatValue ? { repeat: repeatValue } : {}),
      ...(lines.length ? { numbers: lines } : {}),
      ...(concurrencyValue ? { concurrency: concurrencyValue } : {}),
      ...(paceValue !== undefined && paceValid ? { pace: paceValue } : {}),
    };
  }, [source, chosenSuite, amd, picked, repeatValue, numbers, concurrencyValue, paceValue, paceValid]);
  const key = JSON.stringify(options);
  const current = plan?.key === key ? plan.plan : null;
  const runnable = Boolean(current && current.calls > 0 && current.lanes.length > 0);

  useEffect(() => {
    if (tracked?.status === "succeeded") onFinished();
  }, [tracked?.status, onFinished]);

  async function preview() {
    const response = await planAction.run(() => benchmarks.plan(options));
    if (response) setPlan({ key, plan: response as unknown as Plan });
  }

  function start() {
    if (!current) return;
    const lanes = current.lanes.map(formatAddress).join(", ");
    ask({
      title: `Start ${current.name}?`,
      body:
        `Places ${current.calls} real ${current.calls === 1 ? "call" : "calls"} from your agent to ${lanes}, ` +
        `${current.concurrency} at a time, ${current.pace_seconds}s apart on each line. ` +
        `Every call is a real carrier call billed by your carrier.` +
        (audio ? "" : " Simulator-side audio grading is off."),
      confirm: `Place ${current.calls} ${current.calls === 1 ? "call" : "calls"}`,
      tone: "primary",
      onConfirm: async () => {
        const started = await runAction.run(() => benchmarks.run({ ...options, audio }));
        if (started) setOperation(started);
      },
    });
  }

  const shown = amd.filter((scenario) => {
    const needle = filter.trim().toLowerCase();
    return !needle || scenario.name.toLowerCase().includes(needle) || scenario.title.toLowerCase().includes(needle);
  });

  return (
    <div className="grid gap-4 lg:grid-cols-[420px_minmax(0,1fr)]">
      {dialog}
      <Panel className="h-fit">
        <PanelTitle>New run</PanelTitle>
        <form
          className="grid gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (valid && !planAction.pending) void preview();
          }}
        >
          <Segmented<Source>
            label="Run source"
            value={source}
            onChange={setSource}
            options={[
              { id: "suite", label: "Suite" },
              { id: "scenarios", label: "Scenarios", count: picked.size || undefined },
            ]}
          />

          {source === "suite" ? (
            suites.error ? (
              <InlineError message={suites.error} />
            ) : suites.data && !suites.data.length ? (
              <EmptyStrip title="No benchmark suites." body="Add a suite YAML with a scenarios list to the backend config directory (SIMULATOR_UAT_CONFIG_DIR)." />
            ) : (
              <Field id="bench-suite" label="Suite">
                <Select value={chosenSuite} onValueChange={setSuite} disabled={!suites.data}>
                  <SelectTrigger id="bench-suite"><SelectValue placeholder="Loading suites" /></SelectTrigger>
                  <SelectContent>
                    {(suites.data ?? []).map((item) => (
                      <SelectItem key={item.name} value={item.name}>
                        {item.suite} · {item.scenarios} scenarios{item.repeat ? ` × ${item.repeat}` : ""}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
            )
          ) : scenarios.error ? (
            <InlineError message={scenarios.error} />
          ) : (
            <div className="grid gap-2">
              <Field id="bench-filter" label="AMD scenarios">
                <Input id="bench-filter" placeholder="Filter" value={filter} onChange={(event) => setFilter(event.target.value)} />
              </Field>
              <div className="flex flex-wrap gap-1">
                <Button type="button" variant="ghost" size="xs" onClick={() => setPicked((currentSet) => new Set([...currentSet, ...shown.map((s) => s.name)]))}>
                  Select shown ({shown.length})
                </Button>
                <Button type="button" variant="ghost" size="xs" disabled={!picked.size} onClick={() => setPicked(new Set())}>
                  Clear
                </Button>
              </div>
              <div className="max-h-56 overflow-y-auto rounded-lg bg-surface-1 p-2">
                {shown.length ? (
                  <ToggleChips
                    label="AMD scenarios to run"
                    selected={picked}
                    onToggle={(id) => setPicked((currentSet) => toggled(currentSet, id))}
                    options={shown.map((scenario) => ({ id: scenario.name, label: scenario.name, title: scenario.title }))}
                  />
                ) : (
                  <p className="px-1 py-1 text-xs text-muted-foreground">{scenarios.loading ? "Loading scenarios" : "No AMD scenarios match."}</p>
                )}
              </div>
            </div>
          )}

          <div className="grid grid-cols-3 gap-3">
            <Field id="bench-repeat" label="Repeat">
              <Input id="bench-repeat" inputMode="numeric" placeholder="Suite" value={repeat} onChange={(event) => setRepeat(event.target.value)} aria-invalid={repeatValue === null} />
            </Field>
            <Field id="bench-concurrency" label="Concurrency">
              <Input id="bench-concurrency" inputMode="numeric" placeholder="Lines" value={concurrency} onChange={(event) => setConcurrency(event.target.value)} aria-invalid={concurrencyValue === null} />
            </Field>
            <Field id="bench-pace" label="Pace (s)">
              <Input id="bench-pace" inputMode="decimal" placeholder="Suite" value={pace} onChange={(event) => setPace(event.target.value)} aria-invalid={!paceValid} />
            </Field>
          </div>
          {(repeatValue === null || concurrencyValue === null || !paceValid) && (
            <p className="text-xs text-warning">Repeat and concurrency are whole numbers of 1 or more; pace is 0 or more seconds.</p>
          )}

          <Field id="bench-numbers" label="Sim line numbers" help="One per line. Blank uses the suite's lines, then the backend's SIM_NUMBER.">
            <Textarea id="bench-numbers" rows={3} className="font-mono" value={numbers} onChange={(event) => setNumbers(event.target.value)} />
          </Field>

          <div className="flex items-center justify-between gap-3">
            <label htmlFor="bench-audio" className="text-[13px]">Simulator-side audio grading</label>
            <Switch id="bench-audio" checked={audio} onChange={setAudio} />
          </div>

          <div className="flex flex-wrap gap-2">
            <Button type="submit" variant="secondary" size="sm" disabled={!valid || planAction.pending}>
              {planAction.pending ? <Loader2 className="animate-spin" /> : <ListChecks />}
              Preview plan
            </Button>
            <Button type="button" size="sm" disabled={!valid || !runnable || runAction.pending || tracked?.status === "running" || tracked?.status === "queued"} onClick={start}>
              {runAction.pending ? <Loader2 className="animate-spin" /> : <Play />}
              Start run
            </Button>
          </div>
          {plan && !current && valid && <p className="text-xs text-muted-foreground">Inputs changed: preview the plan again before starting.</p>}
          <InlineError message={planAction.error} />
          <InlineError message={runAction.error} />
        </form>
      </Panel>

      <div className="grid h-fit min-w-0 gap-4">
        {tracked && (
          <OperationPanel
            operation={tracked}
            renderResult={(result) => (
              <div className="grid gap-3">
                <div className="flex flex-wrap items-center gap-3">
                  <TonePill tone={result.ok ? "success" : "warning"}>{result.ok ? "Clean run" : result.complete ? "Has failures" : "Incomplete"}</TonePill>
                  <Link href={reportHref(result.report_id)} className="t-num text-xs text-accent-text hover:underline">
                    Open report {result.report_id}
                  </Link>
                </div>
                <SummaryMetrics summary={result.summary} planned={result.planned_calls} />
              </div>
            )}
          />
        )}
        {current ? <PlanView plan={current} /> : !tracked && (
          <EmptyStrip title="No plan previewed." body="The plan lists the call count, lanes and each scenario's expected class. Nothing is dialed." />
        )}
      </div>
    </div>
  );
}

function PlanView({ plan }: { plan: Plan }) {
  const classes = Object.entries(plan.expected_classes);
  return (
    <div className="grid gap-4" aria-live="polite">
      <Panel>
        <PanelTitle aside={`${plan.calls} ${plan.calls === 1 ? "call" : "calls"}`}>Plan: {plan.name}</PanelTitle>
        <Facts
          items={[
            ["Calls", `${plan.scenarios.length} scenarios × ${plan.repeat} = ${plan.calls}`],
            ["Lanes", plan.lanes.length ? plan.lanes.map(formatAddress).join(", ") : "None"],
            ["Concurrency", String(plan.concurrency)],
            ["Pace", `${plan.pace_seconds}s between calls on a line`],
            ["Call timeout", formatDuration(plan.call_timeout_seconds)],
          ]}
        />
        {!plan.lanes.length && (
          <p className="mt-3 text-xs text-warning">No sim lines: enter numbers, add them to the suite, or set SIM_NUMBER on the backend.</p>
        )}
      </Panel>
      {classes.length > 0 && (
        <DataTable label="Expected class per scenario" className="[&_table]:min-w-[360px]">
          <thead>
            <tr>
              <Th>Scenario</Th>
              <Th className="w-[140px]">Expected class</Th>
            </tr>
          </thead>
          <tbody>
            {classes.map(([name, expected]) => (
              <tr key={name} className="even:bg-control/40">
                <Td className="text-xs font-medium">{name}</Td>
                <Td className="text-xs">{expected}</Td>
              </tr>
            ))}
          </tbody>
        </DataTable>
      )}
    </div>
  );
}
