import { content } from "@/lib/scenarios";
import { identifierLabel } from "@/lib/display-text";
import type {
  CallAnalysis,
  CheckResult,
  Endpoint,
  IncomingCall,
  Scenario,
  SimulationRun,
} from "@/lib/simulator-api";

export type Verdict =
  | "passed"
  | "failed"
  | "no_verdict"
  | "queued"
  | "running"
  | "cancelled"
  | "error"
  | "ran";

export const VERDICT_LABEL: Record<Verdict, string> = {
  passed: "Passed",
  failed: "Failed",
  no_verdict: "No verdict",
  queued: "Queued",
  running: "Running",
  cancelled: "Cancelled",
  error: "Error",
  ran: "Ran",
};

/** One row of evidence: a run, its call, or a call that arrived without a queued run. */
export type Result = {
  id: string;
  run: SimulationRun | null;
  call: IncomingCall | null;
  scenario: string;
  title: string;
  kind: Scenario["kind"] | null;
  verdict: Verdict;
  reason: Reason;
  checks: CheckResult[];
  skipped: string[];
  provider: string;
  line: string | null;
  lineName: string | null;
  from: string | null;
  durationS: number | null;
  at: string;
};

export type Reason = { check: string | null; text: string };

const ACTIVE_CALL = new Set(["queued", "initiated", "ringing", "in-progress"]);

export function scenarioTitle(name: string, scenarios: Scenario[] = []): string {
  if (name === "ivr_custom") return "IVR lab call";
  return (
    content.scenarios[name]?.title ??
    scenarios.find((scenario) => scenario.name === name)?.title ??
    identifierLabel(name)
  );
}

function checkName(check: CheckResult, index: number): string {
  return check.check ?? check.name ?? `check ${index + 1}`;
}

/** Turn the grader's terse detail into a sentence, keeping unknown formats verbatim. */
export function describeCheck(detail: string | undefined): string {
  if (!detail) return "";
  const timing = parseTiming(detail);
  if (timing) {
    const after = timing.basis === "recording" ? "after the recording started" : "after the tone";
    return `Agent started ${timing.start.toFixed(2)}s ${after}; allowed ${timing.min} to ${timing.max}s.`;
  }
  const dtmf = /^want=(\S+) got=\[(.*)\]$/.exec(detail);
  if (dtmf) {
    const got = dtmf[2].replaceAll("'", "").trim();
    return `Expected digit ${dtmf[1] === "any" ? "any key" : dtmf[1]}, received ${got || "none"}.`;
  }
  return detail.charAt(0).toUpperCase() + detail.slice(1);
}

export type Timing = {
  start: number;
  min: number;
  max: number;
  basis: "recording" | "mark";
  /** Seconds from the mark to the start of the recording, when the grader reported it. */
  offset: number | null;
};

/** Window and start on the recording's own axis, or null when they cannot be placed. */
export function onRecordingAxis(timing: Timing | null): { start: number; min: number; max: number } | null {
  if (!timing) return null;
  if (timing.basis === "recording") return timing;
  if (timing.offset == null) return null;
  return { start: timing.start - timing.offset, min: timing.min - timing.offset, max: timing.max - timing.offset };
}

/** Timing evidence written by pstn/analyze.py for message_start_after. */
export function parseTiming(detail: string | undefined): Timing | null {
  if (!detail) return null;
  const match = /(leading_silence|delay_after_mark)=(-?[\d.]+)s window=\[(-?[\d.]+),(-?[\d.]+)\]/.exec(detail);
  if (!match) return null;
  const offset = /record_offset=(-?[\d.]+)s/.exec(detail);
  return {
    start: Number(match[2]),
    min: Number(match[3]),
    max: Number(match[4]),
    basis: match[1] === "leading_silence" ? "recording" : "mark",
    offset: offset ? Number(offset[1]) : null,
  };
}

function reasonFor(verdict: Verdict, checks: CheckResult[], run: SimulationRun | null): Reason {
  if (verdict === "failed" || verdict === "no_verdict") {
    const wanted = verdict === "failed" ? false : null;
    const index = checks.findIndex((check) => (check.passed ?? null) === wanted);
    if (index >= 0) {
      const check = checks[index];
      return { check: checkName(check, index), text: describeCheck(check.detail) };
    }
    return {
      check: null,
      text: verdict === "failed" ? "Checks failed." : "Evidence is incomplete, so no verdict yet.",
    };
  }
  if (verdict === "queued") return { check: null, text: run?.result.summary ?? "Waiting for the next call." };
  if (verdict === "cancelled") return { check: null, text: "Removed from the queue before a call arrived." };
  if (verdict === "error") {
    return {
      check: null,
      text: run?.result.error ?? run?.result.summary
        ?? (run?.result.carrier_status ? `Call ended: ${run.result.carrier_status.replaceAll("-", " ")}.` : "The run could not complete."),
    };
  }
  if (verdict === "running") return { check: null, text: run?.result.summary ?? "Call in progress." };
  if (verdict === "passed") {
    return { check: null, text: checks.length === 1 ? "1 check passed." : `${checks.length} checks passed.` };
  }
  const outcome = run?.outcome ?? run?.result.call_outcome ?? run?.result.outcome;
  return {
    check: null,
    text: outcome ? `Outcome: ${outcome.replaceAll("_", " ")}.` : run?.result.summary ?? "Timeline recorded.",
  };
}

/**
 * The checks that decide a call: AMD checks on the call, or a synthetic-customer grade
 * (each persona expectation and transcript check becomes one check), or the run's copy.
 */
function evidenceFor(run: SimulationRun | null, call: IncomingCall | null): CallAnalysis | undefined {
  if (call?.analysis?.checks) return call.analysis;
  const grade = call?.analysis?.grade;
  if (grade) {
    return {
      passed: grade.passed,
      checks: [
        ...grade.expectations.map((item) => ({
          check: item.expectation, passed: item.passed, detail: item.reasons.join(" "),
        })),
        ...grade.checks.map((item) => ({ check: item.check, passed: item.passed })),
      ],
    };
  }
  return run?.result.analysis;
}

function verdictFor(run: SimulationRun | null, call: IncomingCall | null): Verdict {
  if (run?.status === "queued") return "queued";
  if (run?.status === "cancelled") return "cancelled";
  if (run?.result.error) return "error";
  if (call && ACTIVE_CALL.has(call.status)) return "running";
  if (run && !call && !run.completed_at && !["completed", "failed"].includes(run.status)) return "running";
  const analysis = evidenceFor(run, call);
  // Checks decide whenever they exist, whether or not the call is still in the loaded window.
  const keptVerdict = run?.result.call_deleted && run.result.passed != null;
  if (analysis?.checks?.length || run?.result.graded || keptVerdict) {
    const passed = analysis?.passed ?? run?.result.passed ?? null;
    return passed === true ? "passed" : passed === false ? "failed" : "no_verdict";
  }
  if (run?.status === "failed") return "error";
  if (call) return call.status === "completed" ? "no_verdict" : "error";
  // A completed carrier run without checks is waiting on, or lost, its evidence.
  if (run?.result.mode === "pstn" && run.status === "completed") return "no_verdict";
  return "ran";
}

export function buildResults(
  runs: SimulationRun[],
  calls: IncomingCall[],
  endpoints: Endpoint[],
  scenarios: Scenario[],
): Result[] {
  const callsById = new Map(calls.map((call) => [call.id, call]));
  // The backend links in both directions; either side may land first while a call is claimed.
  const callsByRun = new Map(
    calls.filter((call) => call.analysis?.run_id).map((call) => [call.analysis.run_id as string, call]),
  );
  const usedCalls = new Set<string>();
  const results: Result[] = [];

  const lineFor = (endpointId: string | null, address: string | null) => {
    const endpoint =
      (endpointId && endpoints.find((item) => item.id === endpointId)) ||
      (address && endpoints.find((item) => item.address === address)) ||
      null;
    return { line: endpoint?.address ?? address, lineName: endpoint?.name ?? null };
  };

  const make = (run: SimulationRun | null, call: IncomingCall | null): Result => {
    const scenario = run?.scenario ?? call?.scenario ?? "unknown";
    const analysis = evidenceFor(run, call);
    const checks = analysis?.checks ?? [];
    const verdict = verdictFor(run, call);
    const { line, lineName } = lineFor(run?.endpoint_id ?? null, call?.to_address ?? null);
    return {
      id: run?.id ?? call!.id,
      run,
      call,
      scenario,
      title: scenarioTitle(scenario, scenarios),
      kind: scenarios.find((item) => item.name === scenario)?.kind ?? content.scenarios[scenario]?.kind ?? null,
      verdict,
      reason: reasonFor(verdict, checks, run),
      checks,
      skipped: analysis?.skipped_webhook_side ?? [],
      provider: run?.provider ?? call?.provider ?? "unknown",
      line,
      lineName,
      from: call?.from_address || run?.caller_number || run?.result.caller_number || null,
      durationS:
        call?.duration_s ?? run?.result.duration_seconds ?? run?.duration_seconds ?? null,
      at: call?.started_at ?? run?.created_at ?? new Date(0).toISOString(),
    };
  };

  for (const run of runs) {
    const call = (run.result.call_id && callsById.get(run.result.call_id)) || callsByRun.get(run.id) || null;
    if (call) usedCalls.add(call.id);
    results.push(make(run, call));
  }
  // Calls answered with a line's default scenario have no queued run; keep them as evidence too.
  for (const call of calls) {
    if (!usedCalls.has(call.id)) results.push(make(null, call));
  }
  return results.sort((left, right) => right.at.localeCompare(left.at));
}

export function findResult(results: Result[], id: string): Result | undefined {
  return results.find((result) => result.id === id || result.call?.id === id || result.run?.id === id);
}

/** One sentence about the visible rows, or null when there is nothing worth saying. */
export function findingLine(results: Result[]): { lead: string; detail: string | null } | null {
  const failed = results.filter((result) => result.verdict === "failed");
  const queued = results.filter((result) => result.verdict === "queued").length;
  const graded = results.filter((result) =>
    ["passed", "failed", "no_verdict"].includes(result.verdict)
  ).length;
  if (!failed.length) {
    if (!graded) return null;
    return {
      lead: `All ${graded} graded results passed or are awaiting evidence.`,
      detail: queued ? `${queued} queued for the next call.` : null,
    };
  }
  const byCheck = new Map<string, Set<string>>();
  for (const result of failed) {
    const check = result.reason.check;
    if (!check) continue;
    const scenarios = byCheck.get(check) ?? new Set<string>();
    scenarios.add(result.scenario);
    byCheck.set(check, scenarios);
  }
  const counts = [...byCheck.keys()].map((check) => ({
    check,
    rows: failed.filter((result) => result.reason.check === check).length,
    scenarios: byCheck.get(check)!.size,
  }));
  counts.sort((left, right) => right.rows - left.rows);
  const top = counts[0];
  return {
    lead: `${failed.length} of ${results.length} failed.`,
    detail: top
      ? `${top.check} accounts for ${top.rows} of them across ${top.scenarios} ${top.scenarios === 1 ? "scenario" : "scenarios"}.`
      : null,
  };
}

export type Coverage = {
  scenario: Scenario;
  title: string;
  recent: Verdict[];
  passRate: number | null;
  graded: number;
  lastAt: string | null;
  lastFailure: Reason | null;
};

export function buildCoverage(results: Result[], scenarios: Scenario[]): Coverage[] {
  return scenarios.map((scenario) => {
    const rows = results.filter(
      (result) => result.scenario === scenario.name && result.verdict !== "queued" && result.verdict !== "cancelled"
    );
    const graded = rows.filter((result) => result.verdict === "passed" || result.verdict === "failed");
    const passed = graded.filter((result) => result.verdict === "passed").length;
    return {
      scenario,
      title: scenarioTitle(scenario.name, scenarios),
      recent: rows.slice(0, 5).map((result) => result.verdict),
      passRate: graded.length ? passed / graded.length : null,
      graded: graded.length,
      lastAt: rows[0]?.at ?? null,
      lastFailure: rows.find((result) => result.verdict === "failed")?.reason ?? null,
    };
  });
}
