"use client";

import { useEffect, useState } from "react";
import { ListChecks, Loader2, Play } from "lucide-react";

import {
  EmptyStrip,
  formatDuration,
  formatWhen,
  PageFrame,
  PageHeader,
  Panel,
  PanelTitle,
  TonePill,
  useConfirm,
} from "@/components/console/kit";
import { OperationPanel, useOperation } from "@/components/console/operation-panel";
import { Facts, InlineError, ToggleChips, toggled } from "@/components/console/benchmarks-parts";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useAction, useApi } from "@/hooks/use-api";
import { uat, type Operation } from "@/lib/workflows-api";

/** `POST /api/uat/check` (evaluation/uat.py check_config). */
type Check = { valid: boolean; config: string; scenarios: string[]; steps: string[] };
type PlannedCase = { scenario: string; steps: Array<{ name: string; action: string }> };

/** evaluation/uat.py run_matrix and _run_workflow. */
type StepReport = {
  name: string;
  run_if: string;
  skipped: string | null;
  attempts: number;
  captured_keys: string[];
  result: {
    adapter: string;
    ok: boolean;
    status: string;
    duration_s: number;
    detail: string;
    stdout: string;
    stderr: string;
    output_redacted: boolean;
  } | null;
};
type UatReport = {
  generated_at: string;
  config: string;
  total: number;
  passed: number;
  report: string;
  results: Array<{ scenario: string; run_id: string; passed: boolean; artifact_dir: string; steps: StepReport[] }>;
};

export function UatPage() {
  const configs = useApi((signal) => uat.configs(signal), []);
  const [config, setConfig] = useState("");
  const chosen = config || configs.data?.[0]?.name || "";

  return (
    <PageFrame>
      <PageHeader title="UAT" count={configs.data?.length} freshness={false}>
        {configs.data && configs.data.length > 0 && (
          <div className="w-[260px] max-w-full">
            <Select value={chosen} onValueChange={setConfig}>
              <SelectTrigger aria-label="UAT config"><SelectValue /></SelectTrigger>
              <SelectContent>
                {configs.data.map((item) => <SelectItem key={item.name} value={item.name}>{item.name}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
        )}
      </PageHeader>
      {configs.error ? (
        <InlineError message={configs.error} />
      ) : configs.loading && !configs.data ? (
        <p className="text-[13px] text-muted-foreground" aria-live="polite">Loading configs</p>
      ) : !chosen ? (
        <EmptyStrip
          title="No UAT configs."
          body="Add a YAML file with version: 1, a matrix and steps to the backend config directory (SIMULATOR_UAT_CONFIG_DIR)."
        />
      ) : (
        <UatConfig key={chosen} config={chosen} />
      )}
    </PageFrame>
  );
}

function UatConfig({ config }: { config: string }) {
  const [check, setCheck] = useState<Check | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [plan, setPlan] = useState<{ key: string; cases: PlannedCase[] } | null>(null);
  const [operation, setOperation] = useState<Operation | null>(null);
  const tracked = useOperation<UatReport>(operation);
  const checkAction = useAction();
  const planAction = useAction();
  const runAction = useAction();
  const { ask, dialog } = useConfirm();
  const { run: runCheck } = checkAction;

  useEffect(() => {
    void runCheck(() => uat.check(config) as Promise<Check>).then((result) => { if (result) setCheck(result); });
  }, [config, runCheck]);

  const scenarios = [...new Set(check?.scenarios ?? [])];
  const scope = scenarios.filter((name) => selected.has(name));
  const scopeKey = scope.join("\n");
  const caseCount = scope.length ? (check?.scenarios ?? []).filter((name) => selected.has(name)).length : check?.scenarios.length ?? 0;
  const running = tracked?.status === "queued" || tracked?.status === "running";

  async function previewPlan() {
    const response = await planAction.run(() => uat.plan(config, scope.length ? scope : undefined));
    if (response) setPlan({ key: scopeKey, cases: response.cases });
  }

  function start() {
    if (!check) return;
    const steps = check.steps.length;
    ask({
      title: scope.length ? `Run ${scope.length} selected ${scope.length === 1 ? "scenario" : "scenarios"}?` : "Run every scenario?",
      body:
        `Executes the ${steps} ${steps === 1 ? "step" : "steps"} of ${config} for ${caseCount} matrix ${caseCount === 1 ? "case" : "cases"}. ` +
        "Steps can place real calls and run commands defined in that config file.",
      confirm: "Run",
      tone: "primary",
      onConfirm: async () => {
        const started = await runAction.run(() => uat.run(config, scope.length ? scope : undefined));
        if (started) setOperation(started);
      },
    });
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[400px_minmax(0,1fr)]">
      {dialog}
      <Panel className="h-fit">
        <PanelTitle aside={check ? <TonePill tone={check.valid ? "success" : "danger"}>{check.valid ? "Valid" : "Invalid"}</TonePill> : null}>
          {config}
        </PanelTitle>
        <div className="grid gap-4" aria-live="polite">
          {checkAction.pending && !check && <p className="text-[13px] text-muted-foreground">Checking config</p>}
          <InlineError message={checkAction.error} />
          {check && (
            <>
              <div className="grid gap-1.5">
                <p className="text-[13px] font-medium">
                  Scenarios <span className="t-num text-muted-foreground">{scope.length ? `${scope.length} of ` : ""}{scenarios.length}</span>
                </p>
                <ToggleChips
                  label="Scenarios to run"
                  selected={selected}
                  onToggle={(id) => setSelected((current) => toggled(current, id))}
                  options={scenarios.map((name) => ({ id: name, label: name }))}
                />
                {!scope.length && <p className="text-xs text-muted-foreground">None selected runs all.</p>}
              </div>
              <div className="grid gap-1.5">
                <p className="text-[13px] font-medium">Steps</p>
                <ol className="grid gap-1 text-[13px]">
                  {check.steps.map((step, index) => (
                    <li key={`${step}-${index}`} className="flex gap-2">
                      <span className="t-num w-5 shrink-0 text-right text-muted-foreground">{index + 1}</span>
                      <span className="min-w-0 break-words">{step}</span>
                    </li>
                  ))}
                </ol>
              </div>
              <div className="flex flex-wrap gap-2">
                <Button variant="secondary" size="sm" disabled={planAction.pending} onClick={previewPlan}>
                  {planAction.pending ? <Loader2 className="animate-spin" /> : <ListChecks />}
                  Plan
                </Button>
                <Button size="sm" disabled={!check.valid || runAction.pending || running} onClick={start}>
                  {runAction.pending ? <Loader2 className="animate-spin" /> : <Play />}
                  {scope.length ? `Run selected (${scope.length})` : "Run all"}
                </Button>
              </div>
              <InlineError message={planAction.error} />
              <InlineError message={runAction.error} />
            </>
          )}
        </div>
      </Panel>

      <div className="grid h-fit min-w-0 gap-4">
        {tracked && <OperationPanel operation={tracked} renderResult={(report) => <RunReport report={report} />} />}
        {plan ? (
          <PlanView cases={plan.cases} stale={plan.key !== scopeKey} />
        ) : !tracked && (
          <EmptyStrip title="No plan yet." body="The plan renders every step's action per scenario without executing anything." />
        )}
      </div>
    </div>
  );
}

function PlanView({ cases, stale }: { cases: PlannedCase[]; stale: boolean }) {
  return (
    <Panel>
      <PanelTitle aside={`${cases.length} ${cases.length === 1 ? "case" : "cases"}`}>Plan</PanelTitle>
      {stale && <p className="mb-3 text-xs text-muted-foreground">Selection changed since this plan.</p>}
      <div className="grid gap-4">
        {cases.map((item, caseIndex) => (
          <div key={`${item.scenario}-${caseIndex}`} className="grid gap-2">
            <p className="text-[13px] font-semibold">{item.scenario}</p>
            <ol className="grid gap-1.5">
              {item.steps.map((step, index) => (
                <li key={`${step.name}-${index}`} className="grid gap-1 rounded-lg bg-surface-1 px-3 py-2">
                  <p className="text-xs font-medium">
                    <span className="t-num mr-2 text-muted-foreground">{index + 1}</span>
                    {step.name}
                  </p>
                  <code className="block whitespace-pre-wrap break-all font-mono text-[11.5px] leading-5 text-muted-foreground">{step.action}</code>
                </li>
              ))}
            </ol>
          </div>
        ))}
      </div>
    </Panel>
  );
}

function RunReport({ report }: { report: UatReport }) {
  return (
    <div className="grid gap-4">
      <Facts
        items={[
          ["Passed", `${report.passed} of ${report.total}`],
          ["Generated", formatWhen(report.generated_at, true)],
          ["Report", <span key="path" className="font-mono text-xs">{report.report}</span>],
        ]}
      />
      {report.results.map((result) => (
        <div key={result.run_id} className="grid gap-2 rounded-lg bg-surface-1 p-3">
          <div className="flex flex-wrap items-center gap-2">
            <TonePill tone={result.passed ? "success" : "danger"}>{result.passed ? "Passed" : "Failed"}</TonePill>
            <p className="min-w-0 flex-1 truncate text-[13px] font-medium">{result.scenario}</p>
            <span className="t-num text-xs text-muted-foreground" title={result.artifact_dir}>{result.run_id.slice(0, 8)}</span>
          </div>
          <ol className="grid gap-1.5">
            {result.steps.map((step, index) => <StepRow key={`${step.name}-${index}`} step={step} />)}
          </ol>
        </div>
      ))}
    </div>
  );
}

function StepRow({ step }: { step: StepReport }) {
  const result = step.result;
  const output = result ? [result.detail, result.stdout, result.stderr].filter(Boolean).join("\n") : "";
  return (
    <li className="grid gap-1 rounded-md bg-card px-3 py-2">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
        {step.skipped ? (
          <TonePill tone="neutral">Skipped</TonePill>
        ) : (
          <TonePill tone={result?.ok ? "success" : "danger"}>{result?.status ?? "No result"}</TonePill>
        )}
        <span className="min-w-0 flex-1 font-medium">{step.name}</span>
        {result && <span className="t-num text-muted-foreground">{result.adapter}</span>}
        {result && <span className="t-num text-muted-foreground">{formatDuration(result.duration_s)}</span>}
        {step.attempts > 1 && <span className="t-num text-muted-foreground">{step.attempts} attempts</span>}
      </div>
      {step.skipped && <p className="text-xs text-muted-foreground">{step.skipped.replaceAll("_", " ")}</p>}
      {result?.output_redacted && <p className="text-xs text-muted-foreground">Output redacted by the config.</p>}
      {output && (
        <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all rounded bg-surface-1 px-2.5 py-1.5 font-mono text-[11.5px] leading-5 text-muted-foreground">{output}</pre>
      )}
      {step.captured_keys.length > 0 && (
        <p className="t-num text-xs text-muted-foreground">Captured {step.captured_keys.join(", ")}</p>
      )}
    </li>
  );
}
