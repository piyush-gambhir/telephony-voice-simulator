import type { SimulationRun } from "@/lib/simulator-api";

export function timelineStepLabel(step: SimulationRun["timeline"][number]): string {
  if (step.label?.trim()) return step.label;
  return (step.event ?? step.state ?? step.kind ?? "Call event")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase())
    .replace(/\b(Pstn|Amd|Ivr|Dtmf|Pbx)\b/g, (term) => term.toUpperCase());
}
