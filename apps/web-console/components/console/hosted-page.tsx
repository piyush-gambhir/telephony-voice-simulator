"use client";

import { useState, type FormEvent } from "react";
import { Loader2, Plus, X } from "lucide-react";

import { useConsole } from "@/components/console/console-provider";
import {
  ActionError,
  E164,
  GradedCallView,
  PassPill,
  ScenarioSelect,
  ToggleRow,
  type GradedCall,
} from "@/components/console/hosted-parts";
import { Field, formatAddress, MISSING, PageFrame, PageHeader, Panel, PanelTitle, Segmented, useConfirm } from "@/components/console/kit";
import { OperationPanel, useOperation } from "@/components/console/operation-panel";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { Scenario } from "@/lib/simulator-api";
import { hosted, type Operation } from "@/lib/workflows-api";
import { useAction } from "@/hooks/use-api";

const DEFAULT_SCENARIO = "stock_voicemail_beep_1000";
const NUMBERS_LIST = "hosted-number-options";

type Ask = ReturnType<typeof useConfirm>["ask"];

function pickDefault(scenarios: Scenario[]): string {
  return scenarios.some((item) => item.name === DEFAULT_SCENARIO) ? DEFAULT_SCENARIO : scenarios[0]?.name ?? DEFAULT_SCENARIO;
}

function NumberInput({ id, value, onChange, optional }: { id: string; value: string; onChange: (value: string) => void; optional?: boolean }) {
  return (
    <Input
      id={id}
      list={NUMBERS_LIST}
      inputMode="tel"
      placeholder={optional ? "TWILIO_FROM_NUMBER" : "+15555550100"}
      value={value}
      onChange={(event) => onChange(event.target.value.trim())}
      aria-invalid={value !== "" && !E164.test(value)}
      className="font-mono"
    />
  );
}

/* ---------- queue ---------- */

function QueuePanel({ scenarios }: { scenarios: Scenario[] }) {
  const [number, setNumber] = useState("");
  const [scenario, setScenario] = useState(() => pickDefault(scenarios));
  const action = useAction();
  const [queued, setQueued] = useState<{ number: string; scenario: string; queue: string[] } | null>(null);
  const valid = E164.test(number) && scenario.trim() !== "";

  async function submit(event: FormEvent) {
    event.preventDefault();
    const result = await action.run(() => hosted.queue({ number, scenario: scenario.trim() }));
    if (result) setQueued(result);
  }

  return (
    <Panel className="h-fit">
      <PanelTitle>Queue a scenario</PanelTitle>
      <form className="grid gap-4" onSubmit={submit}>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field id="queue-number" label="Hosted number">
            <NumberInput id="queue-number" value={number} onChange={setNumber} />
          </Field>
          <Field id="queue-scenario" label="Scenario">
            <ScenarioSelect id="queue-scenario" value={scenario} onChange={setScenario} scenarios={scenarios} />
          </Field>
        </div>
        <ActionError message={action.error} />
        <div>
          <Button size="sm" disabled={!valid || action.pending}>
            {action.pending && <Loader2 className="animate-spin" />} Queue
          </Button>
        </div>
      </form>
      {queued && (
        <div className="mt-4 grid gap-2 rounded-lg bg-surface-1 p-3.5" aria-live="polite">
          <p className="text-xs text-muted-foreground">
            Queue on <span className="t-num text-foreground">{formatAddress(queued.number)}</span>, next call first
          </p>
          {queued.queue.length ? (
            <ol className="grid gap-1 text-xs">
              {queued.queue.map((item, index) => (
                <li key={`${item}-${index}`} className="flex gap-2.5">
                  <span className="t-num w-4 text-right text-subtle-foreground">{index + 1}</span>
                  <span className="font-mono">{item}</span>
                </li>
              ))}
            </ol>
          ) : (
            <p className="text-xs">{MISSING}</p>
          )}
        </div>
      )}
    </Panel>
  );
}

/* ---------- smoke call ---------- */

function SmokePanel({ scenarios, ask }: { scenarios: Scenario[]; ask: Ask }) {
  const [number, setNumber] = useState("");
  const [scenario, setScenario] = useState(() => pickDefault(scenarios));
  const [from, setFrom] = useState("");
  const action = useAction();
  const [op, setOp] = useState<Operation | null>(null);
  const tracked = useOperation<{ passed: boolean; calls: GradedCall[] }>(op);
  const valid = E164.test(number) && scenario.trim() !== "" && (from === "" || E164.test(from));

  function submit(event: FormEvent) {
    event.preventDefault();
    ask({
      title: "Place a smoke call?",
      body: `Places one real Twilio call from ${from ? formatAddress(from) : "TWILIO_FROM_NUMBER"} to ${formatAddress(number)} running ${scenario}, then grades it. The call is billed to the Twilio account.`,
      confirm: "Place call",
      tone: "primary",
      onConfirm: async () => {
        const started = await action.run(() => hosted.smoke({ number, scenario: scenario.trim(), from_number: from || undefined }));
        if (started) setOp(started);
      },
    });
  }

  return (
    <div className="grid gap-2">
      <Panel>
        <PanelTitle>Smoke call</PanelTitle>
        <form className="grid gap-4" onSubmit={submit}>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field id="smoke-number" label="Hosted number">
              <NumberInput id="smoke-number" value={number} onChange={setNumber} />
            </Field>
            <Field id="smoke-scenario" label="Scenario">
              <ScenarioSelect id="smoke-scenario" value={scenario} onChange={setScenario} scenarios={scenarios} />
            </Field>
          </div>
          <Field id="smoke-from" label="From number" help="Optional. Defaults to TWILIO_FROM_NUMBER on the API host.">
            <NumberInput id="smoke-from" value={from} onChange={setFrom} optional />
          </Field>
          <ActionError message={action.error} />
          <div>
            <Button size="sm" disabled={!valid || action.pending || tracked?.status === "queued" || tracked?.status === "running"}>
              {action.pending && <Loader2 className="animate-spin" />} Place smoke call
            </Button>
          </div>
        </form>
      </Panel>
      {tracked && (
        <OperationPanel
          operation={tracked}
          renderResult={(result) => (
            <div className="grid gap-2">
              {result.calls.map((call) => <GradedCallView key={call.call_sid} call={call} />)}
            </div>
          )}
        />
      )}
    </div>
  );
}

/* ---------- grade ---------- */

function GradePanel() {
  const [mode, setMode] = useState<"sid" | "number">("sid");
  const [sid, setSid] = useState("");
  const [number, setNumber] = useState("");
  const [last, setLast] = useState("3");
  const action = useAction();
  const [op, setOp] = useState<Operation | null>(null);
  const tracked = useOperation<{ passed: boolean; calls: GradedCall[]; skipped: string[] }>(op);
  const lastCount = Number(last);
  const valid = mode === "sid"
    ? /^CA[0-9a-fA-F]{32}$/.test(sid)
    : E164.test(number) && Number.isInteger(lastCount) && lastCount >= 1 && lastCount <= 50;

  async function submit(event: FormEvent) {
    event.preventDefault();
    const started = await action.run(() =>
      hosted.grade(mode === "sid" ? { call_sid: sid } : { number, last: lastCount }),
    );
    if (started) setOp(started);
  }

  return (
    <div className="grid gap-2">
      <Panel>
        <PanelTitle>Grade calls</PanelTitle>
        <form className="grid gap-4" onSubmit={submit}>
          <Segmented
            label="Grade by"
            value={mode}
            onChange={setMode}
            options={[{ id: "sid", label: "Call SID" }, { id: "number", label: "Recent calls to a number" }]}
          />
          {mode === "sid" ? (
            <Field id="grade-sid" label="Call SID">
              <Input
                id="grade-sid"
                value={sid}
                placeholder="CA…"
                onChange={(event) => setSid(event.target.value.trim())}
                aria-invalid={sid !== "" && !/^CA[0-9a-fA-F]{32}$/.test(sid)}
                className="font-mono"
              />
            </Field>
          ) : (
            <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_120px]">
              <Field id="grade-number" label="Hosted number">
                <NumberInput id="grade-number" value={number} onChange={setNumber} />
              </Field>
              <Field id="grade-last" label="Last calls">
                <Input id="grade-last" type="number" min={1} max={50} value={last} onChange={(event) => setLast(event.target.value)} className="t-num" />
              </Field>
            </div>
          )}
          <ActionError message={action.error} />
          <div>
            <Button size="sm" disabled={!valid || action.pending || tracked?.status === "queued" || tracked?.status === "running"}>
              {action.pending && <Loader2 className="animate-spin" />} Grade
            </Button>
          </div>
        </form>
      </Panel>
      {tracked && (
        <OperationPanel
          operation={tracked}
          renderResult={(result) => (
            <div className="grid gap-2">
              <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                <PassPill passed={result.calls.length ? result.passed : null} />
                <span className="t-num">{result.calls.length} graded</span>
              </div>
              {result.calls.map((call) => <GradedCallView key={call.call_sid} call={call} />)}
              {result.skipped.length > 0 && (
                <div className="rounded-lg bg-surface-1 p-3.5 text-xs">
                  <p className="text-muted-foreground">Skipped (no simulator state on these legs)</p>
                  <ul className="mt-1.5 grid gap-0.5 font-mono">
                    {result.skipped.map((item) => <li key={item} className="break-all">{item}</li>)}
                  </ul>
                </div>
              )}
            </div>
          )}
        />
      )}
    </div>
  );
}

/* ---------- LiveKit agent call ---------- */

type AgentCallResult = {
  agent_name: string;
  room: string;
  call_id: string;
  sim_number: string;
  scenario: string;
  queued: boolean;
  passed: boolean | null;
  call: GradedCall | null;
};

function AgentCallPanel({ scenarios, ask }: { scenarios: Scenario[]; ask: Ask }) {
  const [scenario, setScenario] = useState(() => pickDefault(scenarios));
  const [queueFirst, setQueueFirst] = useState(true);
  const [wait, setWait] = useState(true);
  const action = useAction();
  const [op, setOp] = useState<Operation | null>(null);
  const tracked = useOperation<AgentCallResult>(op);
  const valid = scenario.trim() !== "";

  function submit(event: FormEvent) {
    event.preventDefault();
    ask({
      title: "Dispatch the LiveKit agent?",
      body: `Dispatches the deployed LiveKit agent to place one real call to the hosted sim number${queueFirst ? ` after queuing ${scenario}` : ""}. The call is billed to the LiveKit and Twilio accounts.${wait ? " Waits up to about 4 minutes for the call, then grades it." : ""}`,
      confirm: "Dispatch agent",
      tone: "primary",
      onConfirm: async () => {
        const started = await action.run(() => hosted.agentCall({ scenario: scenario.trim(), queue: queueFirst, wait }));
        if (started) setOp(started);
      },
    });
  }

  return (
    <div className="grid gap-2">
      <Panel>
        <PanelTitle>LiveKit agent call</PanelTitle>
        <form className="grid gap-4" onSubmit={submit}>
          <Field id="agent-scenario" label="Scenario">
            <ScenarioSelect id="agent-scenario" value={scenario} onChange={setScenario} scenarios={scenarios} />
          </Field>
          <div className="grid gap-2">
            <ToggleRow id="agent-queue" label="Queue the scenario first" checked={queueFirst} onChange={setQueueFirst} />
            <ToggleRow id="agent-wait" label="Wait for the call and grade it" checked={wait} onChange={setWait} />
          </div>
          <ActionError message={action.error} />
          <div>
            <Button size="sm" disabled={!valid || action.pending || tracked?.status === "queued" || tracked?.status === "running"}>
              {action.pending && <Loader2 className="animate-spin" />} Dispatch agent
            </Button>
          </div>
        </form>
      </Panel>
      {tracked && (
        <OperationPanel
          operation={tracked}
          renderResult={(result) => (
            <div className="grid gap-2">
              <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 rounded-lg bg-surface-1 p-3.5 text-xs">
                <dt className="text-muted-foreground">Agent</dt>
                <dd className="break-all font-mono">{result.agent_name}</dd>
                <dt className="text-muted-foreground">Room</dt>
                <dd className="break-all font-mono">{result.room}</dd>
                <dt className="text-muted-foreground">Call ID</dt>
                <dd className="break-all font-mono">{result.call_id}</dd>
                <dt className="text-muted-foreground">Sim number</dt>
                <dd className="t-num">{formatAddress(result.sim_number)}</dd>
                <dt className="text-muted-foreground">Scenario</dt>
                <dd className="font-mono">{result.scenario}{result.queued ? " (queued)" : ""}</dd>
              </dl>
              {result.call ? (
                <GradedCallView call={result.call} />
              ) : result.passed === false ? (
                <p className="rounded-lg bg-danger-soft px-3 py-2 text-xs text-danger">No completed inbound leg reached the sim number.</p>
              ) : null}
            </div>
          )}
        />
      )}
    </div>
  );
}

/* ---------- deploy ---------- */

type MapRow = { key: number; number: string; scenario: string };

function DeployPanel({ scenarios, ask }: { scenarios: Scenario[]; ask: Ask }) {
  const [defaultScenario, setDefaultScenario] = useState(() => pickDefault(scenarios));
  const [rows, setRows] = useState<MapRow[]>([]);
  const [nextKey, setNextKey] = useState(0);
  const [acknowledged, setAcknowledged] = useState(false);
  const [allowlist, setAllowlist] = useState("");
  const action = useAction();
  const [op, setOp] = useState<Operation | null>(null);
  const tracked = useOperation<{ domain: string; machine_url: string }>(op);
  const numbers = rows.map((row) => row.number);
  const rowsValid = rows.every((row) => E164.test(row.number) && row.scenario.trim() !== "")
    && new Set(numbers).size === numbers.length;
  const valid = defaultScenario.trim() !== "" && rowsValid && acknowledged && allowlist.trim() !== "";

  function updateRow(key: number, patch: Partial<MapRow>) {
    setRows((current) => current.map((row) => (row.key === key ? { ...row, ...patch } : row)));
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    ask({
      title: "Deploy the hosted callee?",
      body: "Uploads the callee and its scenario audio to Twilio Serverless as PUBLIC Twilio Assets (anyone with the URL can fetch them) and replaces the current deployment. Calls to hosted numbers use the new build as soon as it is live.",
      confirm: "Deploy",
      tone: "primary",
      onConfirm: async () => {
        const started = await action.run(() =>
          hosted.deploy({
            default_scenario: defaultScenario.trim(),
            static_map: Object.fromEntries(rows.map((row) => [row.number, row.scenario.trim()])),
            acknowledge_public_audio: acknowledged,
            audio_deployment_allowlist: allowlist.trim(),
          }),
        );
        if (started) setOp(started);
      },
    });
  }

  return (
    <div className="grid gap-2">
      <Panel>
        <PanelTitle>Deploy</PanelTitle>
        <form className="grid gap-4" onSubmit={submit}>
          <Field id="deploy-default" label="Default scenario" help="Used when a number has no queued or mapped scenario.">
            <ScenarioSelect id="deploy-default" value={defaultScenario} onChange={setDefaultScenario} scenarios={scenarios} />
          </Field>

          <div className="grid gap-2">
            <p className="text-sm font-medium">Fixed scenario per number</p>
            {rows.map((row, index) => (
              <div key={row.key} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] items-end gap-2">
                <Field id={`map-number-${row.key}`} label={`Number ${index + 1}`}>
                  <NumberInput id={`map-number-${row.key}`} value={row.number} onChange={(value) => updateRow(row.key, { number: value })} />
                </Field>
                <Field id={`map-scenario-${row.key}`} label={`Scenario ${index + 1}`}>
                  <ScenarioSelect
                    id={`map-scenario-${row.key}`}
                    value={row.scenario}
                    onChange={(value) => updateRow(row.key, { scenario: value })}
                    scenarios={scenarios}
                  />
                </Field>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  aria-label={`Remove number ${index + 1}`}
                  onClick={() => setRows((current) => current.filter((item) => item.key !== row.key))}
                >
                  <X />
                </Button>
              </div>
            ))}
            {!rowsValid && rows.length > 0 && (
              <p className="text-xs text-warning">Each row needs a distinct E.164 number and a scenario.</p>
            )}
            <div>
              <Button
                type="button"
                variant="secondary"
                size="xs"
                onClick={() => {
                  setRows((current) => [...current, { key: nextKey, number: "", scenario: defaultScenario }]);
                  setNextKey((key) => key + 1);
                }}
              >
                <Plus /> Add number
              </Button>
            </div>
          </div>

          <Field id="deploy-allowlist" label="Deployment allowlist" help="Path on the API host to the reviewed audio allowlist JSON.">
            <Input
              id="deploy-allowlist"
              value={allowlist}
              placeholder="/absolute/path/to/deployment_allowlist.json"
              onChange={(event) => setAllowlist(event.target.value)}
              className="font-mono"
            />
          </Field>
          <ToggleRow
            id="deploy-ack"
            label="Acknowledge public audio"
            help="The scenario audio becomes publicly fetchable. Confirm it passed the rights and privacy review."
            checked={acknowledged}
            onChange={setAcknowledged}
          />
          <ActionError message={action.error} />
          <div>
            <Button size="sm" disabled={!valid || action.pending || tracked?.status === "queued" || tracked?.status === "running"}>
              {action.pending && <Loader2 className="animate-spin" />} Deploy
            </Button>
          </div>
        </form>
      </Panel>
      {tracked && (
        <OperationPanel
          operation={tracked}
          renderResult={(result) => (
            <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 rounded-lg bg-surface-1 p-3.5 text-xs">
              <dt className="text-muted-foreground">Domain</dt>
              <dd className="break-all font-mono">{result.domain}</dd>
              <dt className="text-muted-foreground">Machine URL</dt>
              <dd className="break-all font-mono">
                <a href={result.machine_url} target="_blank" rel="noreferrer" className="text-accent-text hover:underline">{result.machine_url}</a>
              </dd>
            </dl>
          )}
        />
      )}
    </div>
  );
}

/* ---------- page ---------- */

export function HostedPage() {
  const { data } = useConsole();
  const { ask, dialog } = useConfirm();
  const scenarios = data.scenarios.filter((item) => item.kind === "amd");
  // Scenario defaults are picked once on mount; remount the forms when the catalog first arrives.
  const catalogKey = scenarios.length ? "loaded" : "empty";

  return (
    <PageFrame>
      <PageHeader title="Hosted callee" freshness={false} />
      <p className="max-w-2xl text-[13px] leading-5 text-muted-foreground">
        The always-on Twilio Serverless copy of the AMD callee. These actions call Twilio directly; no local simulator is involved.
      </p>
      <datalist id={NUMBERS_LIST}>
        {data.amdNumbers.map((item) => <option key={item.id} value={item.phone_number}>{item.friendly_name}</option>)}
      </datalist>
      <div key={catalogKey} className="grid items-start gap-4 lg:grid-cols-2">
        <div className="grid gap-4">
          <QueuePanel scenarios={scenarios} />
          <SmokePanel scenarios={scenarios} ask={ask} />
          <GradePanel />
        </div>
        <div className="grid gap-4">
          <AgentCallPanel scenarios={scenarios} ask={ask} />
          <DeployPanel scenarios={scenarios} ask={ask} />
        </div>
      </div>
      {dialog}
    </PageFrame>
  );
}
