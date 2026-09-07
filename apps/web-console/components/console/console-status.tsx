import { Badge } from "@/components/ui/badge";
import type { SimulationRun } from "@/lib/simulator-api";

export function statusBadge(status: string) {
  const label = status.replace(/^dial_/, "").replaceAll("_", " ");
  if (["completed", "connected", "bridged"].includes(status)) {
    return <Badge variant="success">{label}</Badge>;
  }
  if (["failed", "abandoned", "dial_failed"].includes(status)) {
    return <Badge variant="destructive">{label}</Badge>;
  }
  if (["queued", "busy", "no_answer", "dial_busy", "dial_no_answer"].includes(status)) {
    return <Badge variant="warning">{label}</Badge>;
  }
  return <Badge variant="secondary">{label}</Badge>;
}

export function runStatusBadge(run: SimulationRun) {
  if (run.result.graded && run.result.passed === true) {
    return <Badge variant="success">Checks passed</Badge>;
  }
  if (run.result.graded && run.result.passed === false) {
    return <Badge variant="destructive">Checks failed</Badge>;
  }
  if (run.result.graded) return <Badge variant="warning">Checks incomplete</Badge>;
  return statusBadge(run.status);
}

export function timelineStepLabel(step: SimulationRun["timeline"][number]): string {
  if (step.label?.trim()) return step.label;
  return (step.event ?? step.state ?? step.kind ?? "Call event")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase())
    .replace(/\b(Pstn|Amd|Ivr|Dtmf|Pbx)\b/g, (term) => term.toUpperCase());
}

export function runOutcome(run: SimulationRun): string {
  return run.outcome ?? run.result.call_outcome ?? run.result.outcome ?? "";
}

export function runDuration(run: SimulationRun): number {
  return run.duration_seconds ?? run.result.duration_seconds ?? 0;
}
