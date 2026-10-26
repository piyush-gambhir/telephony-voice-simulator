"use client";

import { useMemo, useState, type FormEvent, type ReactNode } from "react";
import { Loader2, PhoneOutgoing, Search, Timer } from "lucide-react";

import {
  Field,
  formatAddress,
  formatDuration,
  MISSING,
  PageFrame,
  PageHeader,
  Panel,
  PanelTitle,
  Segmented,
  TonePill,
  useConfirm,
} from "@/components/console/kit";
import { OperationPanel, useOperation } from "@/components/console/operation-panel";
import { Facts, InlineError, seconds } from "@/components/console/benchmarks-parts";
import { AgentRooms, ScenarioSelect } from "@/components/console/agents-room";
import { JsonBlock } from "@/components/json-block";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useAction, useApi } from "@/hooks/use-api";
import { simulatorApi, type Scenario } from "@/lib/simulator-api";
import { agents, type Operation } from "@/lib/workflows-api";

/** integrations/agent_api.py extract_outcome. */
type Outcome = {
  status: string;
  ended_reason: string;
  detection_layer: string;
  amd: Record<string, unknown> | null;
  is_machine: boolean | null;
  detection_delay: number | null;
};

/** service/agents.py call_and_wait. */
type CallResult = { call_id: string; scenario: string; number: string; completed: boolean; outcome: Outcome };

/** The agent's default scenario in service/agents.py. */
const DEFAULT_SCENARIO = "stock_voicemail_beep_1000";

type Area = "rest" | "room";

export function AgentsPage() {
  const [area, setArea] = useState<Area>("rest");
  const scenarios = useApi((signal) => simulatorApi.scenarios(signal), []);
  const amd = useMemo(() => (scenarios.data ?? []).filter((scenario) => scenario.kind === "amd"), [scenarios.data]);

  return (
    <PageFrame>
      <PageHeader title="Agents" freshness={false}>
        <Segmented<Area>
          label="Agent area"
          value={area}
          onChange={setArea}
          options={[
            { id: "rest", label: "REST agent" },
            { id: "room", label: "LiveKit room" },
          ]}
        />
      </PageHeader>
      <InlineError message={scenarios.error} />
      {area === "rest" ? <RestAgent scenarios={amd} /> : <AgentRooms scenarios={amd} loading={scenarios.loading} />}
    </PageFrame>
  );
}

/* ---------- REST agent ---------- */

function RestAgent({ scenarios }: { scenarios: Scenario[] }) {
  const [scenario, setScenario] = useState("");
  const [number, setNumber] = useState("");
  const [timeout, setTimeoutValue] = useState("300");
  const [triggered, setTriggered] = useState<{ call_id: string; scenario: string; number: string; response: unknown } | null>(null);
  const [operation, setOperation] = useState<Operation | null>(null);
  const tracked = useOperation<CallResult>(operation);
  const triggerAction = useAction();
  const callAction = useAction();
  const { ask, dialog } = useConfirm();

  const [lookupId, setLookupId] = useState("");
  const [record, setRecord] = useState<{ id: string; record: Record<string, unknown>; outcome: Outcome } | null>(null);
  const lookupAction = useAction();

  const chosen = scenario || (scenarios.some((item) => item.name === DEFAULT_SCENARIO) ? DEFAULT_SCENARIO : scenarios[0]?.name ?? "");
  const target = number.trim();
  const timeoutValue = Number(timeout);
  const numberValid = !target || target.length >= 3;
  const timeoutValid = Number.isFinite(timeoutValue) && timeoutValue > 0;
  const running = tracked?.status === "queued" || tracked?.status === "running";
  const destination = target ? formatAddress(target) : "the line in the backend's SIM_NUMBER";
  const body = { scenario: chosen, ...(target ? { number: target } : {}) };

  function trigger() {
    ask({
      title: "Trigger a real call?",
      body: `Your agent will place a real call to ${destination} for ${chosen}. The call is billed by your carrier.`,
      confirm: "Place call",
      tone: "primary",
      onConfirm: async () => {
        const result = await triggerAction.run(() => agents.trigger(body));
        if (result) setTriggered(result);
      },
    });
  }

  function callAndWait() {
    ask({
      title: "Place a call and wait?",
      body:
        `Your agent will place a real call to ${destination} for ${chosen}, then the console polls its record ` +
        `for up to ${formatDuration(timeoutValue)}. The call is billed by your carrier.`,
      confirm: "Place call",
      tone: "primary",
      onConfirm: async () => {
        const started = await callAction.run(() => agents.call({ ...body, timeout: timeoutValue }));
        if (started) setOperation(started);
      },
    });
  }

  async function lookUp(id: string) {
    const result = await lookupAction.run(() => agents.record(id));
    setRecord(result ? { id, record: result.record, outcome: result.outcome as unknown as Outcome } : null);
  }

  function submitLookup(event: FormEvent) {
    event.preventDefault();
    if (lookupId.trim()) void lookUp(lookupId.trim());
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[380px_minmax(0,1fr)]">
      {dialog}
      <div className="grid h-fit gap-4">
        <Panel>
          <PanelTitle>Place a call</PanelTitle>
          <div className="grid gap-4">
            <Field id="agent-scenario" label="Scenario">
              <ScenarioSelect id="agent-scenario" scenarios={scenarios} value={chosen} onChange={setScenario} />
            </Field>
            <Field id="agent-number" label="Sim number" help="Blank uses the backend's SIM_NUMBER.">
              <Input id="agent-number" className="font-mono" value={number} onChange={(event) => setNumber(event.target.value)} aria-invalid={!numberValid} />
            </Field>
            <Field id="agent-timeout" label="Wait timeout (s)" help="Only for call and wait.">
              <Input id="agent-timeout" inputMode="numeric" value={timeout} onChange={(event) => setTimeoutValue(event.target.value)} aria-invalid={!timeoutValid} />
            </Field>
            <div className="flex flex-wrap gap-2">
              <Button variant="secondary" size="sm" disabled={!chosen || !numberValid || triggerAction.pending} onClick={trigger}>
                {triggerAction.pending ? <Loader2 className="animate-spin" /> : <PhoneOutgoing />}
                Trigger call
              </Button>
              <Button size="sm" disabled={!chosen || !numberValid || !timeoutValid || callAction.pending || running} onClick={callAndWait}>
                {callAction.pending ? <Loader2 className="animate-spin" /> : <Timer />}
                Call and wait
              </Button>
            </div>
            <InlineError message={triggerAction.error} />
            <InlineError message={callAction.error} />
            <p className="text-xs leading-5 text-muted-foreground">
              Needs AGENT_API_TRIGGER_URL and AGENT_API_BODY on the backend; reading outcomes also needs AGENT_API_RECORD_URL.
            </p>
          </div>
        </Panel>

        <Panel>
          <PanelTitle>Look up a call</PanelTitle>
          <form className="grid gap-3" onSubmit={submitLookup}>
            <Field id="agent-call-id" label="Call id">
              <Input id="agent-call-id" className="font-mono" value={lookupId} onChange={(event) => setLookupId(event.target.value)} />
            </Field>
            <div>
              <Button type="submit" variant="secondary" size="sm" disabled={!lookupId.trim() || lookupAction.pending}>
                {lookupAction.pending ? <Loader2 className="animate-spin" /> : <Search />}
                Look up
              </Button>
            </div>
            <InlineError message={lookupAction.error} />
          </form>
        </Panel>
      </div>

      <div className="grid h-fit min-w-0 gap-4" aria-live="polite">
        {tracked && (
          <OperationPanel
            operation={tracked}
            renderResult={(result) => (
              <div className="grid gap-3">
                <div className="flex flex-wrap items-center gap-2">
                  <TonePill tone={result.completed ? "success" : "warning"}>{result.completed ? "Completed" : "Timed out"}</TonePill>
                  <span className="t-num text-xs text-muted-foreground">{result.call_id}</span>
                </div>
                <OutcomeFacts outcome={result.outcome} extra={[["Scenario", result.scenario], ["Dialed", formatAddress(result.number)]]} />
              </div>
            )}
          />
        )}

        {triggered && (
          <Panel>
            <PanelTitle aside={formatAddress(triggered.number)}>Triggered {triggered.scenario}</PanelTitle>
            <div className="grid gap-3">
              <div className="flex flex-wrap items-center gap-3">
                <span className="t-num text-[13px]">{triggered.call_id}</span>
                <Button
                  variant="ghost"
                  size="xs"
                  disabled={lookupAction.pending}
                  onClick={() => {
                    setLookupId(triggered.call_id);
                    void lookUp(triggered.call_id);
                  }}
                >
                  Look up record
                </Button>
              </div>
              <JsonBlock value={triggered.response} />
            </div>
          </Panel>
        )}

        {record && (
          <Panel>
            <PanelTitle aside={record.id}>Call record</PanelTitle>
            <div className="grid gap-4">
              <OutcomeFacts outcome={record.outcome} />
              <JsonBlock value={record.record} />
            </div>
          </Panel>
        )}
      </div>
    </div>
  );
}

function OutcomeFacts({ outcome, extra = [] }: { outcome: Outcome; extra?: Array<[string, string]> }) {
  return (
    <Facts
      items={[
        ...extra,
        ["Status", outcome.status || MISSING],
        ["Ended reason", outcome.ended_reason || MISSING],
        ["Detection layer", outcome.detection_layer || MISSING],
        ["Machine", outcome.is_machine == null ? MISSING : outcome.is_machine ? "Yes" : "No"],
        ["Detection delay", seconds(outcome.detection_delay)],
        ...(outcome.amd ? ([["AMD", <JsonBlock key="amd" value={outcome.amd} />]] as Array<[string, ReactNode]>) : []),
      ]}
    />
  );
}
