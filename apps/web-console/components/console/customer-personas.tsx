"use client";

import { useEffect, useState, type ReactNode } from "react";
import { Check, X } from "lucide-react";

import { EmptyStrip, formatAddress, formatWhen, Panel, PanelTitle, TonePill, useConfirm } from "@/components/console/kit";
import { OperationPanel, useOperation } from "@/components/console/operation-panel";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useApi } from "@/hooks/use-api";
import {
  inbox,
  type CustomerGrade,
  type Operation,
  type Persona,
  type PersonaCheck,
  type QueuedPersona,
} from "@/lib/workflows-api";
import { cn } from "@/lib/utils";

function message(cause: unknown, fallback: string): string {
  return cause instanceof Error ? cause.message : fallback;
}

export function EnginePill({ engine }: { engine: Persona["engine"] }) {
  return <TonePill tone={engine === "realtime" ? "accent" : "neutral"}>{engine === "realtime" ? "Realtime" : "Chained"}</TonePill>;
}

/** The persona catalog with its queue: queue a persona for a line's next call, or check it. */
export function PersonaCatalog({ lines, refreshKey }: { lines: string[]; refreshKey: number }) {
  const personas = useApi((signal) => inbox.personas(signal), [refreshKey]);
  // Calls consume queued personas, so the page's Refresh reloads the queue too.
  const queue = useApi((signal) => inbox.queue(signal), [refreshKey]);
  const [line, setLine] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [check, setCheck] = useState<Operation | null>(null);
  const tracked = useOperation<PersonaCheck>(check);
  const { ask, dialog } = useConfirm();
  const target = line ?? lines[0] ?? null;

  async function enqueue(persona: Persona) {
    if (!target) return;
    setError(null);
    try {
      const queued = await inbox.enqueue({ number: target, persona_id: persona.id });
      queue.setData((current) => [...(current ?? []), queued]);
    } catch (cause) {
      setError(message(cause, "Could not queue the persona"));
    }
  }

  async function remove(entry: QueuedPersona) {
    setError(null);
    try {
      await inbox.unqueue(entry.id);
      queue.setData((current) => current?.filter((item) => item.id !== entry.id) ?? current);
    } catch (cause) {
      setError(message(cause, "Could not remove the queued persona"));
    }
  }

  function runCheck(persona: Persona) {
    ask({
      title: `Check ${persona.id}?`,
      body: "Runs the persona's fidelity test in ElevenLabs: a simulated dealership calls the persona agent and an evaluator judges whether the customer stayed in character. One run costs about 300 ElevenLabs credits.",
      confirm: "Run check",
      tone: "primary",
      onConfirm: async () => {
        try {
          setCheck(await inbox.checkPersona(persona.id));
        } catch (cause) {
          return message(cause, "Could not start the check");
        }
      },
    });
  }

  const queued = queue.data ?? [];
  return (
    <Panel>
      {dialog}
      <PanelTitle aside={personas.data?.length}>Persona catalog</PanelTitle>
      {error && <p role="alert" className="mb-3 rounded-lg bg-danger-soft px-3 py-2 text-xs leading-5 text-danger">{error}</p>}
      {personas.error && <p role="alert" className="mb-3 text-xs text-danger">{personas.error}</p>}
      {lines.length > 1 && (
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <span className="text-xs text-muted-foreground">Queue on</span>
          <Select value={target ?? undefined} onValueChange={setLine}>
            <SelectTrigger className="h-8 w-auto min-w-[170px]" aria-label="Persona line"><SelectValue /></SelectTrigger>
            <SelectContent>
              {lines.map((number) => <SelectItem key={number} value={number}>{formatAddress(number)}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
      )}
      <ul className="grid gap-2">
        {(personas.data ?? []).map((persona) => (
          <li key={persona.id} className="grid gap-2 rounded-lg bg-surface-1 px-3 py-2.5">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-[12px] font-medium">{persona.id}</span>
              <EnginePill engine={persona.engine} />
              {persona.language && <TonePill tone="neutral">{persona.language}</TonePill>}
              <span className="ml-auto flex gap-1.5">
                <Button size="xs" variant="secondary" disabled={!target} onClick={() => void enqueue(persona)}>Queue</Button>
                <Button size="xs" variant="ghost" onClick={() => runCheck(persona)}>Check</Button>
              </span>
            </div>
            {persona.description && <p className="text-[13px] leading-5 text-muted-foreground">{persona.description}</p>}
            {persona.expectations.length > 0 && (
              <ul className="grid gap-0.5 text-xs leading-5 text-muted-foreground">
                {persona.expectations.map((expectation) => <li key={expectation}>{expectation}</li>)}
              </ul>
            )}
          </li>
        ))}
      </ul>
      {!lines.length && <p className="mt-3 text-xs text-muted-foreground">Add a persona line to PSTN_CUSTOMER_PROFILES to queue personas.</p>}
      {tracked && (
        <OperationPanel
          className="mt-3"
          operation={tracked}
          renderResult={(result) => (
            <p className="text-[13px]">
              {result.passed} of {result.runs.length} run{result.runs.length === 1 ? "" : "s"} passed, {result.credits} credits.
              {result.runs.flatMap((run) => run.reasons).map((reason) => (
                <span key={reason} className="mt-1 block text-xs leading-5 text-muted-foreground">{reason}</span>
              ))}
            </p>
          )}
        />
      )}
      <h3 className="mb-2 mt-4 text-[13px] font-medium">Queued for the next call</h3>
      {queued.length ? (
        <ul className="grid gap-1.5" aria-live="polite">
          {queued.map((entry) => (
            <li key={entry.id} className="flex flex-wrap items-center gap-2 rounded-lg bg-surface-1 px-3 py-2 text-[13px]">
              <span className="font-mono text-[12px]">{entry.persona.id}</span>
              <span className="t-num text-muted-foreground">on {formatAddress(entry.number)}</span>
              {entry.label && <span className="text-xs text-muted-foreground">{entry.label}</span>}
              <span className="t-num ml-auto text-xs text-muted-foreground">{formatWhen(entry.created_at)}</span>
              <Button size="xs" variant="ghost" aria-label={`Remove ${entry.persona.id}`} onClick={() => void remove(entry)}>
                <X />
              </Button>
            </li>
          ))}
        </ul>
      ) : (
        <EmptyStrip title="Nothing queued" body="Without a queued persona, each line answers as its default persona." />
      )}
    </Panel>
  );
}

function Verdict({ passed, children }: { passed: boolean; children: ReactNode }) {
  return (
    <li className="flex gap-2">
      <span className={cn("mt-0.5 grid size-4 shrink-0 place-items-center rounded-full", passed ? "bg-success-soft text-success" : "bg-danger-soft text-danger")}>
        {passed ? <Check className="size-3" /> : <X className="size-3" />}
      </span>
      <div className="grid min-w-0 gap-0.5">{children}</div>
    </li>
  );
}

/** A call's grade: one verdict per expectation with the evaluator's reasons, then each check. */
export function GradeView({ grade }: { grade: CustomerGrade }) {
  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <TonePill tone={grade.passed ? "success" : "danger"}>{grade.passed ? "Passed" : "Failed"}</TonePill>
        <span className="text-xs text-muted-foreground">
          against {grade.persona_id}
          {grade.evaluation_model && `, judged by ${grade.evaluation_model}`}
          {grade.credits != null && `, ${grade.credits} credits`}
        </span>
      </div>
      {(grade.expectations.length > 0 || grade.checks.length > 0) && (
        <ul className="grid gap-2 text-[13px] leading-5">
          {grade.expectations.map((item) => (
            <Verdict key={item.expectation} passed={item.passed}>
              <span>{item.expectation}</span>
              {item.reasons.map((reason) => <span key={reason} className="text-xs text-muted-foreground">{reason}</span>)}
            </Verdict>
          ))}
          {grade.checks.map((item) => (
            <Verdict key={item.check} passed={item.passed}><span>{item.check}</span></Verdict>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Grade a finished call (an operation of a few ElevenLabs test runs), then show the result. */
export function GradeSection({
  callId,
  grade,
  ready,
  onGraded,
}: {
  callId: string;
  grade: CustomerGrade | null;
  ready: boolean;
  onGraded: (grade: CustomerGrade) => void;
}) {
  const [operation, setOperation] = useState<Operation | null>(null);
  const tracked = useOperation<CustomerGrade>(operation?.params.call_id === callId ? operation : null);
  const { ask, dialog } = useConfirm();
  const result = tracked?.status === "succeeded" ? tracked.result : null;

  useEffect(() => {
    if (result) onGraded(result);
    // Report each finished grading once; onGraded is a fresh closure every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [result]);

  function start() {
    ask({
      title: "Grade this call?",
      body: "Runs one ElevenLabs judge test per persona expectation against this transcript, plus the persona's checks. Each expectation costs about 100 ElevenLabs credits.",
      confirm: grade ? "Grade again" : "Grade",
      tone: "primary",
      onConfirm: async () => {
        try {
          setOperation(await inbox.grade(callId));
        } catch (cause) {
          return message(cause, "Could not start grading");
        }
      },
    });
  }

  const running = tracked != null && (tracked.status === "queued" || tracked.status === "running");
  return (
    <div className="mt-4 grid gap-3">
      {dialog}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-[13px] font-medium">Grade</h3>
        <Button size="xs" variant="secondary" disabled={!ready || running} onClick={start}>
          {grade ? "Grade again" : "Grade"}
        </Button>
      </div>
      {grade ? <GradeView grade={grade} /> : (
        <p className="text-xs text-muted-foreground">{ready ? "Not graded yet." : "Grading needs a finished transcript."}</p>
      )}
      {tracked && tracked.status !== "succeeded" && <OperationPanel operation={tracked} />}
    </div>
  );
}
