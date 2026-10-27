"use client";

import { type ReactNode } from "react";

import { MISSING, TonePill } from "@/components/console/kit";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import type { CallAnalysis, Scenario } from "@/lib/simulator-api";

/** E.164 as Twilio stores it. */
export const E164 = /^\+[1-9]\d{6,14}$/;

/** A call graded by the hosted callee (`telephony/twilio/hosted/grade.py`). */
export type GradedCall = {
  call_sid: string;
  scenario: string | null;
  digits: string[];
  recording_files: string[];
  analysis_recording_files: string[];
  recordings: Array<{ sid: string | null; source: string; channels: number | null; duration: string | null; file: string }>;
  analysis: CallAnalysis;
  artifacts_dir: string;
};

export function ActionError({ message }: { message: string | null }) {
  if (!message) return null;
  return <p role="alert" className="rounded-lg bg-danger-soft px-3 py-2 text-xs leading-5 text-danger">{message}</p>;
}

/** AMD scenarios from the catalog; a free-text input when the catalog has not loaded. */
export function ScenarioSelect({
  id,
  value,
  onChange,
  scenarios,
}: {
  id: string;
  value: string;
  onChange: (value: string) => void;
  scenarios: Scenario[];
}) {
  if (!scenarios.length) {
    return <Input id={id} value={value} onChange={(event) => onChange(event.target.value)} className="font-mono" />;
  }
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger id={id}><SelectValue placeholder="Select a scenario" /></SelectTrigger>
      <SelectContent>
        {scenarios.map((item) => (
          <SelectItem key={item.name} value={item.name}>{item.title || item.name}</SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

export function ToggleRow({
  id,
  label,
  help,
  checked,
  onChange,
}: {
  id: string;
  label: string;
  help?: ReactNode;
  checked: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <div className="flex items-start gap-3 rounded-lg bg-surface-1 px-3.5 py-3">
      <div className="min-w-0 flex-1">
        <label htmlFor={id} className="text-[13px] font-medium">{label}</label>
        {help && <p className="mt-0.5 text-xs leading-5 text-muted-foreground">{help}</p>}
      </div>
      <Switch id={id} label={label} checked={checked} onChange={onChange} />
    </div>
  );
}

export function PassPill({ passed }: { passed: boolean | null | undefined }) {
  if (passed === true) return <TonePill tone="success">Passed</TonePill>;
  if (passed === false) return <TonePill tone="danger">Failed</TonePill>;
  return <TonePill tone="neutral">No verdict</TonePill>;
}

function CheckPill({ passed }: { passed: boolean | null | undefined }) {
  if (passed === true) return <TonePill tone="success">Pass</TonePill>;
  if (passed === false) return <TonePill tone="danger">Fail</TonePill>;
  return <TonePill tone="neutral">No evidence</TonePill>;
}

/** One graded call: verdict, scenario, digits, then every check with its detail. */
export function GradedCallView({ call }: { call: GradedCall }) {
  const checks = call.analysis?.checks ?? [];
  return (
    <div className="grid gap-3 rounded-lg bg-surface-1 p-3.5">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <PassPill passed={call.analysis?.passed} />
        <span className="t-num min-w-0 break-all font-mono text-xs">{call.call_sid}</span>
        <span className="text-xs text-muted-foreground">{call.scenario ?? MISSING}</span>
      </div>
      <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 text-xs">
        <dt className="text-muted-foreground">Digits</dt>
        <dd className="t-num font-mono">{call.digits?.length ? call.digits.join(" ") : MISSING}</dd>
        <dt className="text-muted-foreground">Recordings</dt>
        <dd className="t-num">
          {call.recording_files?.length ?? 0} saved, {call.analysis_recording_files?.length ?? 0} graded
        </dd>
        <dt className="text-muted-foreground">Artifacts</dt>
        <dd className="break-all font-mono">{call.artifacts_dir || MISSING}</dd>
      </dl>
      {checks.length ? (
        <ul className="grid gap-1.5">
          {checks.map((check, index) => (
            <li key={`${check.check ?? check.name}-${index}`} className="flex items-start gap-2.5 rounded-md bg-card px-3 py-2">
              <CheckPill passed={check.passed} />
              <div className="min-w-0 flex-1 text-xs leading-5">
                <p className="font-medium">{(check.check ?? check.name ?? "check").replaceAll("_", " ")}</p>
                {check.detail && <p className="break-words text-muted-foreground">{check.detail}</p>}
              </div>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-xs text-muted-foreground">The grader returned no checks for this call.</p>
      )}
    </div>
  );
}
