"use client";

import { useState } from "react";
import { Loader2, Play } from "lucide-react";

import { EmptyStrip, Field, Panel, PanelTitle, Segmented, TonePill, useConfirm } from "@/components/console/kit";
import { OperationPanel, useOperation } from "@/components/console/operation-panel";
import { Facts, InlineError, verdictLabel, verdictTone } from "@/components/console/benchmarks-parts";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useAction } from "@/hooks/use-api";
import type { Scenario } from "@/lib/simulator-api";
import { agents, type Operation } from "@/lib/workflows-api";

/** integrations/livekit/runner.py run_scenarios. */
type RoomSummary = {
  total: number;
  passed: number;
  results_dir: string;
  reports: Array<{
    scenario: string;
    call_id: string;
    room: string;
    passed: boolean | null;
    checks: Array<{ check: string; passed: boolean | null; detail: string }>;
  }>;
};

type Mode = "one" | "all";

export function ScenarioSelect({
  id,
  scenarios,
  value,
  onChange,
}: {
  id: string;
  scenarios: Scenario[];
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <Select value={value} onValueChange={onChange} disabled={!scenarios.length}>
      <SelectTrigger id={id}><SelectValue placeholder="No AMD scenarios" /></SelectTrigger>
      <SelectContent>
        {scenarios.map((scenario) => (
          <SelectItem key={scenario.name} value={scenario.name}>{scenario.name}</SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

/** Dispatch the LiveKit agent into rooms with the simulated callee; pstn_only scenarios cannot run there. */
export function AgentRooms({ scenarios, loading }: { scenarios: Scenario[]; loading: boolean }) {
  const roomable = scenarios.filter((scenario) => !scenario.pstn_only);
  const [mode, setMode] = useState<Mode>("one");
  const [scenario, setScenario] = useState("");
  const [filter, setFilter] = useState("");
  const [operation, setOperation] = useState<Operation | null>(null);
  const tracked = useOperation<RoomSummary>(operation);
  const action = useAction();
  const { ask, dialog } = useConfirm();

  const chosen = scenario || roomable[0]?.name || "";
  const running = tracked?.status === "queued" || tracked?.status === "running";
  const valid = mode === "all" || Boolean(chosen);

  function start() {
    const needle = filter.trim();
    ask({
      title: mode === "one" ? `Run ${chosen} in a room?` : "Run every scenario in rooms?",
      body:
        (mode === "one"
          ? `Dispatches your LiveKit agent into one room for ${chosen}.`
          : `Dispatches your LiveKit agent into rooms, one per scenario file${needle ? ` whose name contains “${needle}”` : ""}, one after another. pstn_only scenarios are skipped.`) +
        " Each room runs your agent end to end, so its usual model and speech usage applies.",
      confirm: "Dispatch",
      tone: "primary",
      onConfirm: async () => {
        const started = await action.run(() =>
          agents.room(mode === "one" ? { scenario: chosen } : { all: true, ...(needle ? { filter: needle } : {}) }),
        );
        if (started) setOperation(started);
      },
    });
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[380px_minmax(0,1fr)]">
      {dialog}
      <Panel className="h-fit">
        <PanelTitle>Run in a LiveKit room</PanelTitle>
        <div className="grid gap-4">
          <Segmented<Mode>
            label="Scenarios to run"
            value={mode}
            onChange={setMode}
            options={[
              { id: "one", label: "One scenario" },
              { id: "all", label: "All scenarios" },
            ]}
          />
          {mode === "one" ? (
            <Field id="room-scenario" label="Scenario">
              {loading && !roomable.length ? (
                <p className="text-[13px] text-muted-foreground">Loading scenarios</p>
              ) : (
                <ScenarioSelect id="room-scenario" scenarios={roomable} value={chosen} onChange={setScenario} />
              )}
            </Field>
          ) : (
            <Field id="room-filter" label="File name filter" help="Blank runs every scenario file.">
              <Input id="room-filter" value={filter} onChange={(event) => setFilter(event.target.value)} />
            </Field>
          )}
          <div>
            <Button size="sm" disabled={!valid || action.pending || running} onClick={start}>
              {action.pending ? <Loader2 className="animate-spin" /> : <Play />}
              Run
            </Button>
          </div>
          <InlineError message={action.error} />
          <p className="text-xs leading-5 text-muted-foreground">
            Needs LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET and AGENT_NAME on the backend.
          </p>
        </div>
      </Panel>

      <div className="grid h-fit min-w-0 gap-4">
        {tracked ? (
          <OperationPanel operation={tracked} renderResult={(summary) => <RoomResults summary={summary} />} />
        ) : (
          <EmptyStrip title="No room runs in this session." body="Each scenario's checks appear here when the run finishes." />
        )}
      </div>
    </div>
  );
}

function RoomResults({ summary }: { summary: RoomSummary }) {
  return (
    <div className="grid gap-3">
      <Facts
        items={[
          ["Passed", `${summary.passed} of ${summary.total}`],
          ["Results", <span key="dir" className="font-mono text-xs">{summary.results_dir}</span>],
        ]}
      />
      {summary.reports.map((report) => (
        <div key={report.call_id} className="grid gap-2 rounded-lg bg-surface-1 p-3">
          <div className="flex flex-wrap items-center gap-2">
            <TonePill tone={verdictTone(report.passed)}>{verdictLabel(report.passed)}</TonePill>
            <p className="min-w-0 flex-1 truncate text-[13px] font-medium">{report.scenario}</p>
            <span className="t-num text-xs text-muted-foreground" title={report.call_id}>{report.room}</span>
          </div>
          <ul className="grid gap-1">
            {report.checks.map((check, index) => (
              <li key={`${check.check}-${index}`} className="flex flex-wrap items-baseline gap-x-2 text-xs">
                <span className={check.passed === true ? "text-success" : check.passed === false ? "text-danger" : "text-muted-foreground"}>
                  {check.passed === true ? "ok" : check.passed === false ? "fail" : "?"}
                </span>
                <span className="font-medium">{check.check}</span>
                <span className="min-w-0 break-words text-muted-foreground">{check.detail}</span>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}
