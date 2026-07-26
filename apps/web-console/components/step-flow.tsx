import {
  ArrowDownUp,
  Bell,
  Clock,
  PhoneForwarded,
  PhoneOff,
  Play,
  Repeat,
} from "lucide-react";

import { cn } from "@/lib/utils";
import type { Step } from "@/lib/scenarios";

const ICONS: Record<string, typeof Play> = {
  wait: Clock,
  hold: Clock,
  play: Play,
  tone: Bell,
  loop: Repeat,
  hangup: PhoneOff,
  dial: PhoneForwarded,
  unknown: ArrowDownUp,
};

function isAudioFixture(step: Step) {
  return Boolean(
    step.transcript ||
      step.audioUrl ||
      /\.(wav|mp3|m4a|ogg)$/i.test(step.detail)
  );
}

function audioRole(step: Step) {
  const asset = step.detail.toLowerCase();
  if (
    asset.includes("human") ||
    asset.includes("pickup") ||
    asset.includes("gatekeeper") ||
    asset.includes("receptionist")
  ) {
    return {
      title: "Human speaks",
      summary: "The live person speaks to the agent",
    };
  }
  if (
    asset.includes("screen") ||
    asset.includes("assistant") ||
    asset.includes("connecting")
  ) {
    return {
      title: "Screening prompt plays",
      summary: "The screening service speaks to the agent",
    };
  }
  if (asset.includes("not_in_service") || asset.includes("disconnected")) {
    return {
      title: "Carrier intercept plays",
      summary: "The carrier announces that the destination is unreachable",
    };
  }
  if (asset.includes("gate_") || asset.includes("press")) {
    return {
      title: "Keypress prompt plays",
      summary: "The connect-gate tells the agent which key to send",
    };
  }
  return {
    title: "Mailbox prompt plays",
    summary: "The voicemail or answering-device prompt plays to the agent",
  };
}

function stepTitle(step: Step, index: number) {
  if (step.kind === "wait" && step.label.startsWith("wait ")) {
    return index === 0 ? "Initial silence" : "Processing pause";
  }
  if (step.kind === "play" && isAudioFixture(step)) return audioRole(step).title;
  if (step.kind === "tone" && step.label.startsWith("beep ")) return "Record tone";
  if (step.kind === "hold" && step.label.startsWith("hold ")) return "Agent response window";
  if (step.kind === "hangup" && step.label === "hang up") return "Call ends";
  if (step.kind === "dial" && step.label === "bridge to a real phone") {
    return "Call bridges";
  }
  if (step.kind === "loop" && step.label.startsWith("loop to step ")) {
    return "Prompt repeats";
  }
  return step.label;
}

function stepSummary(step: Step) {
  if (step.kind === "wait" && step.label.startsWith("wait ")) {
    return `${step.label.replace("wait ", "")} of silence`;
  }
  if (step.kind === "hold" && step.label.startsWith("hold ")) {
    return `${step.label.replace("hold ", "")} available for the agent response or recording`;
  }
  if (step.kind === "play" && isAudioFixture(step)) {
    return audioRole(step).summary;
  }
  if (step.kind === "hangup" && step.label === "hang up") {
    return "The simulated callee ends the call";
  }
  if (step.kind === "tone" && step.label.startsWith("beep ")) {
    return `${step.label.replace("beep ", "")} for ${step.detail}`;
  }
  return step.detail;
}

export function StepFlow({ steps }: { steps: Step[] }) {
  return (
    <ol>
      {steps.map((s, i) => {
        const Icon = ICONS[s.kind] ?? ArrowDownUp;
        return (
          <li key={i} className="grid grid-cols-[2rem_minmax(0,1fr)] gap-3">
            <div className="flex flex-col items-center">
              <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-semibold tabular-nums text-muted-foreground">
                {i + 1}
              </span>
              {i < steps.length - 1 ? (
                <span className="my-1 min-h-5 w-px flex-1 bg-muted" />
              ) : null}
            </div>
            <div className={cn("min-w-0 pb-5", i === steps.length - 1 && "pb-0")}>
              <div className="flex items-center gap-2">
                <Icon
                  className={cn(
                    "size-4 shrink-0 text-muted-foreground",
                    s.kind === "hangup" && "text-destructive",
                    s.kind === "dial" && "text-warning",
                    s.kind === "tone" && "text-info"
                  )}
                />
                <p className="text-sm font-semibold">{stepTitle(s, i)}</p>
              </div>
              {stepSummary(s) ? (
                <p className="mt-1 text-sm leading-6 text-muted-foreground">
                  {stepSummary(s)}
                </p>
              ) : null}
              {s.transcript ? (
                <blockquote className="mt-3 rounded-xl bg-muted/55 px-4 py-3 text-sm leading-6 text-foreground/90">
                  “{s.transcript}”
                </blockquote>
              ) : null}
              {s.kind === "play" && isAudioFixture(s) ? (
                <p className="mt-2 font-mono text-[11px] text-subtle-foreground">
                  {s.detail}
                </p>
              ) : null}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
