import {
  CheckCircle2,
  ChevronDown,
  FileCode2,
  KeyRound,
  Settings2,
} from "lucide-react";

import { JsonBlock } from "@/components/json-block";
import { StepFlow } from "@/components/step-flow";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { Check, Scenario } from "@/lib/scenarios";

const CHECK_TITLES: Record<string, string> = {
  webhook_received: "Call completion is reported",
  ended_reason: "Final call disposition",
  ended_reason_not: "Disallowed dispositions",
  ended_reason_any_of: "Accepted dispositions",
  detection_layer_prefix_any_of: "Accepted detection path",
  detection_layer_absent: "Machine detection stays inactive",
  message_start_after: "Message starts at the right time",
  first_agent_speech_after: "Agent responds within the allowed window",
  agent_speech_after: "Agent responds after the call changes state",
  message_content: "Voicemail message is complete",
  max_overlap_with_playback: "Agent does not talk over the prompt",
  max_overlap_between_marks: "Agent stays quiet during the tone",
  dtmf_received: "Correct keypress is sent",
  dtmf_press_count_max: "Keypress attempts stay within limit",
  agent_spoke: "Agent speaks on the call",
  expected_outcome: "Terminal outcome",
};

function parseValue(value: Check["value"]): unknown {
  if (typeof value !== "string") return value;
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}

function words(value: string) {
  return value
    .replace(/^call\.(ending|in-progress)\./, "")
    .replaceAll("-", " ")
    .replaceAll("_", " ");
}

function markName(value: unknown) {
  return typeof value === "string" ? words(value) : "the expected event";
}

function CheckValue({ check }: { check: Check }) {
  const value = parseValue(check.value);

  if (check.key === "message_content" && value && typeof value === "object") {
    const message = value as { expected?: string; min_recall?: number };
    return (
      <div className="mt-2 space-y-2">
        {message.expected ? (
          <blockquote className="rounded-xl bg-muted/55 px-4 py-3 text-sm leading-6 text-foreground/90">
            “{message.expected}”
          </blockquote>
        ) : null}
        {typeof message.min_recall === "number" ? (
          <p className="text-xs text-muted-foreground">
            Minimum transcript match: {Math.round(message.min_recall * 100)}%
          </p>
        ) : null}
      </div>
    );
  }

  if (
    (check.key === "message_start_after" ||
      check.key === "first_agent_speech_after" ||
      check.key === "agent_speech_after") &&
    value &&
    typeof value === "object"
  ) {
    const window = value as { mark?: string; min?: number; max?: number };
    return (
      <p className="mt-1 text-sm text-muted-foreground">
        {window.min ?? 0}–{window.max ?? "∞"} seconds after {markName(window.mark)}
      </p>
    );
  }

  if (check.key === "max_overlap_with_playback" && value && typeof value === "object") {
    const overlap = value as { asset?: string; max_s?: number };
    return (
      <p className="mt-1 text-sm text-muted-foreground">
        At most {overlap.max_s ?? 0}s over{" "}
        <span className="font-mono text-xs text-foreground/80">{overlap.asset}</span>
      </p>
    );
  }

  if (check.key === "max_overlap_between_marks" && value && typeof value === "object") {
    const overlap = value as { start?: string; end?: string; max_s?: number };
    return (
      <p className="mt-1 text-sm text-muted-foreground">
        At most {overlap.max_s ?? 0}s between {markName(overlap.start)} and{" "}
        {markName(overlap.end)}
      </p>
    );
  }

  if (check.key === "dtmf_received" && value && typeof value === "object") {
    const keypress = value as { digit?: string };
    return (
      <div className="mt-2 flex items-center gap-2 text-sm text-muted-foreground">
        <KeyRound className="size-4 text-warning" />
        Send key <Badge variant="warning">{keypress.digit ?? "any"}</Badge>
      </div>
    );
  }

  if (Array.isArray(value)) {
    return (
      <div className="mt-2 flex flex-wrap gap-1.5">
        {value.map((item) => (
          <Badge key={String(item)} variant="outline" className="font-mono">
            {words(String(item))}
          </Badge>
        ))}
      </div>
    );
  }

  if (typeof value === "boolean") {
    return (
      <p className="mt-1 text-sm text-muted-foreground">
        {check.key === "detection_layer_absent"
          ? value
            ? "No detection layer may activate"
            : "Detection is allowed"
          : value
            ? "Required"
            : "Must not occur"}
      </p>
    );
  }

  if (typeof value === "number") {
    return (
      <p className="mt-1 text-sm text-muted-foreground">
        {check.key.endsWith("_max") ? `No more than ${value}` : value}
      </p>
    );
  }

  return (
    <p className="mt-1 font-mono text-xs leading-5 text-muted-foreground">
      {words(String(value))}
    </p>
  );
}

export function ScenarioReference({ scenario }: { scenario: Scenario }) {
  const { machine, expect } = scenario;

  return (
    <div className="space-y-4">
      <div className="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-4 xl:grid-cols-[minmax(0,1.1fr)_minmax(22rem,0.9fr)]">
        <Card className="min-w-0 bg-muted/30">
          <CardHeader>
            <CardTitle className="text-base">What the simulated callee does</CardTitle>
          </CardHeader>
          <CardContent className="space-y-5">
            {machine.sequences.map((sequence) => (
              <div key={sequence.name}>
                {machine.sequences.length > 1 ? (
                  <div className="mb-3 flex items-center gap-2">
                    <Badge variant="outline">{words(sequence.name)}</Badge>
                    <span className="text-xs text-muted-foreground">call branch</span>
                  </div>
                ) : null}
                <StepFlow steps={sequence.steps} />
              </div>
            ))}

            {machine.onDtmf ? (
              <div className="rounded-xl bg-warning-soft px-4 py-3 text-sm">
                {machine.onDtmf.mode === "ignore" ? (
                  <p>The callee ignores every keypress; the gate never advances.</p>
                ) : (
                  <p>
                    Pressing{" "}
                    <span className="font-mono font-semibold">
                      {machine.onDtmf.digits.join(", ") || "any key"}
                    </span>{" "}
                    switches the call to{" "}
                    <span className="font-mono">{words(machine.onDtmf.switchTo)}</span>.
                  </p>
                )}
              </div>
            ) : null}

            {machine.maxDuration ? (
              <p className="text-xs text-muted-foreground">
                Safety timeout: the simulator ends the call after {machine.maxDuration}s.
              </p>
            ) : null}
          </CardContent>
        </Card>

        <Card className="min-w-0 bg-success-soft/45">
          <CardHeader>
            <CardTitle className="text-base">What counts as a pass</CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="space-y-4">
              {expect.map((check) => (
                <li key={check.key} className="flex items-start gap-3">
                  <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-success" />
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium">
                      {CHECK_TITLES[check.key] ?? check.label}
                    </p>
                    <CheckValue check={check} />
                  </div>
                </li>
              ))}
              {expect.length === 0 ? (
                <li className="text-sm text-muted-foreground">
                  This scenario is reviewed manually; it has no automated assertions.
                </li>
              ) : null}
            </ul>
          </CardContent>
        </Card>
      </div>

      <details className="group rounded-2xl bg-muted/30">
        <summary className="flex cursor-pointer list-none items-center gap-3 px-5 py-4 text-sm font-medium md:px-6">
          <Settings2 className="size-4 text-muted-foreground" />
          Technical fixture and configuration
          <ChevronDown className="ml-auto size-4 text-muted-foreground transition-transform group-open:rotate-180" />
        </summary>
        <div className="grid gap-5 px-5 pb-5 md:px-6 md:pb-6 xl:grid-cols-[minmax(16rem,0.4fr)_minmax(0,1fr)]">
          <dl className="space-y-3 text-sm">
            <div>
              <dt className="text-xs text-muted-foreground">Scenario ID</dt>
              <dd className="mt-1 font-mono text-xs">{scenario.name}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">Fixture source</dt>
              <dd className="mt-1 flex items-center gap-2 font-mono text-xs">
                <FileCode2 className="size-3.5 text-muted-foreground" />
                {scenario.file}
              </dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">Simulator path</dt>
              <dd className="mt-1 uppercase">{scenario.kind}</dd>
            </div>
          </dl>

          <div>
            <p className="mb-2 text-xs font-medium text-muted-foreground">
              Configuration overrides
            </p>
            {scenario.configOverrides ? (
              <JsonBlock value={scenario.configOverrides} />
            ) : (
              <p className="rounded-xl bg-background/60 px-4 py-3 text-sm text-muted-foreground">
                Uses the simulator defaults.
              </p>
            )}
          </div>
        </div>
      </details>
    </div>
  );
}
