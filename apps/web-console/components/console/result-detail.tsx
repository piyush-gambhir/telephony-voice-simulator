"use client";

import { useEffect, useMemo, type ReactNode } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { BookOpen, ChevronDown, ChevronLeft, ChevronUp, RotateCcw, Trash2, X } from "lucide-react";

import { useConsole } from "@/components/console/console-provider";
import {
  ConsoleErrors,
  EmptyStrip,
  formatAddress,
  formatDuration,
  formatWhen,
  MISSING,
  PageFrame,
  Panel,
  PanelTitle,
  shortId,
  useConfirm,
  VerdictPill,
} from "@/components/console/kit";
import { MessageTimeline } from "@/components/console/message-timeline";
import { lastResultsHref } from "@/components/console/results-state";
import { RecordingPlayer } from "@/components/console/recording-player";
import { timelineStepLabel } from "@/components/console/console-status";
import { Button } from "@/components/ui/button";
import { describeCheck, findResult, parseTiming, type Result } from "@/lib/results";
import { content } from "@/lib/scenarios";
import { cn } from "@/lib/utils";

function Facts({ rows }: { rows: Array<[string, ReactNode]> }) {
  return (
    <dl className="grid grid-cols-[92px_minmax(0,1fr)] gap-x-3 gap-y-2 text-[13px]">
      {rows.map(([label, value]) => (
        <div key={label} className="contents">
          <dt className="text-muted-foreground">{label}</dt>
          <dd className="t-num min-w-0 truncate text-xs leading-5">{value ?? MISSING}</dd>
        </div>
      ))}
    </dl>
  );
}

function CheckRow({ name, passed, detail }: { name: string; passed: boolean | null; detail?: string }) {
  const verdict = passed === true ? "passed" : passed === false ? "failed" : "no_verdict";
  return (
    <li className={cn("rounded-lg px-3.5 py-3", passed === false ? "bg-danger-soft" : "bg-surface-1")}>
      <div className="flex items-center justify-between gap-3">
        <span className="t-num truncate text-xs font-medium">{name}</span>
        <VerdictPill verdict={verdict} />
      </div>
      {detail && <p className="mt-1.5 text-[13px] leading-5">{describeCheck(detail)}</p>}
      {detail && describeCheck(detail).toLowerCase() !== detail.toLowerCase() && (
        <p className="t-num mt-1 text-[11px] text-muted-foreground">{detail}</p>
      )}
    </li>
  );
}

function ScenarioSteps({ name }: { name: string }) {
  const scenario = content.scenarios[name];
  const main = scenario?.machine.sequences.find((sequence) => sequence.name === "main");
  if (!scenario || !main) return null;
  const greeting = main.steps.find((step) => step.transcript)?.transcript;
  return (
    <Panel>
      <PanelTitle aside={<Link href={`/docs/${name}/`} className="inline-flex items-center gap-1 hover:text-foreground"><BookOpen className="size-3.5" /> Reference</Link>}>
        What the line does
      </PanelTitle>
      <ol className="flex flex-wrap items-center gap-1.5">
        {main.steps.map((step, index) => (
          <li
            key={index}
            className={cn(
              "t-num rounded-md px-2 py-1 text-[11px]",
              step.kind === "hold" || step.kind === "record" ? "bg-accent-soft text-accent-text" : "bg-surface-1 text-muted-foreground",
            )}
            title={step.detail}
          >
            {step.label}
          </li>
        ))}
      </ol>
      {greeting && (
        <p className="mt-3 rounded-lg bg-surface-1 px-3.5 py-3 text-[13px] leading-6 text-muted-foreground">
          <span className="font-medium text-foreground">Greeting: </span>
          {greeting}
        </p>
      )}
    </Panel>
  );
}

export function ResultDetail() {
  const params = useSearchParams();
  const router = useRouter();
  const id = params.get("id") ?? "";
  const { results, loading, data, busy, actions } = useConsole();
  const confirm = useConfirm();
  const result = useMemo(() => findResult(results, id), [results, id]);
  const index = result ? results.indexOf(result) : -1;
  const previous = index > 0 ? results[index - 1] : null;
  const next = index >= 0 && index < results.length - 1 ? results[index + 1] : null;
  const back = lastResultsHref();

  const open = (target: Result | null) => {
    if (target) router.push(`/console/result/?id=${encodeURIComponent(target.id)}`, { scroll: false });
  };

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target?.closest("input, textarea, select, [role=slider], [role=dialog]") || event.metaKey || event.ctrlKey) return;
      if (event.key === "k" && previous) open(previous);
      if (event.key === "j" && next) open(next);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  if (!result) {
    return (
      <PageFrame>
        <Link href={back} className="flex w-fit items-center gap-1 text-[13px] text-muted-foreground hover:text-foreground">
          <ChevronLeft className="size-4" /> Results
        </Link>
        {loading ? (
          <div className="h-64 rounded-xl bg-card" />
        ) : (
          <EmptyStrip
            title="This result is not in the latest 100 runs and calls."
            body="It may have been deleted, or it is older than the window the API returns."
            action={<Button asChild variant="secondary" size="sm"><Link href={back}>Back to results</Link></Button>}
          />
        )}
      </PageFrame>
    );
  }

  const { run, call } = result;
  const endpoint = data.endpoints.find((item) => item.id === run?.endpoint_id)
    ?? data.endpoints.find((item) => item.address === call?.to_address);
  const recordings = call?.recordings ?? [];
  const messages = recordings.filter((recording) => recording.kind === "message");
  const message = messages[0] ?? null;
  const others = recordings.filter((recording) => recording !== message && recording.local_path);
  const timingCheck = result.checks.find((check) => (check.check ?? check.name) === "message_start_after");
  // The grader picks among several messages by execution window, which the API does not expose,
  // so timing is only drawn when there is exactly one message to draw it on.
  const timing = messages.length <= 1 ? parseTiming(timingCheck?.detail) : null;
  const voicemail = Boolean(message || timingCheck || content.scenarios[result.scenario]?.machine.sequences.some(
    (sequence) => sequence.steps.some((step) => step.kind === "hold"),
  ));
  const canRerun = Boolean(
    run && endpoint?.enabled && result.verdict !== "queued" && data.scenarios.some((item) => item.name === result.scenario),
  );

  return (
    <PageFrame>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <Link href={back} className="flex items-center gap-0.5 text-[13px] text-muted-foreground hover:text-foreground">
          <ChevronLeft className="size-4" /> Results
        </Link>
        <h1 className="min-w-0 truncate text-xl font-semibold tracking-tight">{result.title}</h1>
        <VerdictPill verdict={result.verdict} />
        <span className="t-num hidden text-xs text-subtle-foreground lg:inline">{result.scenario}</span>
        <div className="ml-auto flex items-center gap-1.5">
          <div className="flex rounded-lg bg-control">
            <Button variant="ghost" size="icon-sm" aria-label="Previous result" title="Previous (K)" disabled={!previous} onClick={() => open(previous)}>
              <ChevronUp />
            </Button>
            <Button variant="ghost" size="icon-sm" aria-label="Next result" title="Next (J)" disabled={!next} onClick={() => open(next)}>
              <ChevronDown />
            </Button>
          </div>
          {call && (
            <Button
              variant="destructive"
              size="sm"
              disabled={busy}
              onClick={() => confirm.ask({
                title: "Delete this call?",
                body: "This removes the simulator's record of the call and its local recording files. Twilio and other provider copies follow provider retention and must be deleted there. This cannot be undone.",
                confirm: "Delete call",
                onConfirm: async () => {
                  const done = await actions.deleteCall(call.id);
                  if (done && !run) router.push(back);
                  return done;
                },
              })}
            >
              <Trash2 /> Delete call
            </Button>
          )}
          {result.verdict === "queued" && run ? (
            <Button size="sm" disabled={busy} onClick={() => void actions.cancelRun(run.id)}>
              <X /> Cancel queued run
            </Button>
          ) : canRerun ? (
            <Button
              size="sm"
              disabled={busy}
              onClick={async () => {
                const runId = await actions.createRun(run!.endpoint_id, result.scenario);
                if (runId) router.push(`/console/result/?id=${encodeURIComponent(runId)}`);
              }}
            >
              <RotateCcw /> Run again
            </Button>
          ) : null}
        </div>
      </div>
      <ConsoleErrors />
      <p className="text-[13px] leading-5">
        {result.reason.check && (
          <span className={cn("t-num mr-1.5 text-xs", result.verdict === "failed" ? "text-danger" : "")}>{result.reason.check}</span>
        )}
        <span className={result.reason.check ? "text-muted-foreground" : ""}>{result.reason.text}</span>
      </p>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_300px]">
        <div className="flex min-w-0 flex-col gap-4">
          {voicemail && result.verdict !== "queued" && (
            <Panel>
              <PanelTitle aside={messages.length > 1
                ? `${messages.length} messages; timing is in the checks`
                : message?.duration_s ? `${message.duration_s.toFixed(1)}s message` : null}>
                Message after the tone
              </PanelTitle>
              <MessageTimeline
                key={message?.id ?? result.id}
                recording={message}
                recordingStatus={call?.recording_status}
                timing={timing}
                passed={timingCheck ? timingCheck.passed ?? null : null}
              />
            </Panel>
          )}

          {others.map((recording) => (
            <RecordingPlayer
              key={recording.id}
              recordingId={recording.id}
              label={recording.kind === "full_call" ? "Full call recording" : "Recording"}
            />
          ))}

          {result.checks.length > 0 && (
            <Panel>
              <PanelTitle aside={`${result.checks.filter((check) => check.passed === true).length} of ${result.checks.length} passed`}>
                Checks
              </PanelTitle>
              <ul className="flex flex-col gap-2">
                {result.checks.map((check, position) => (
                  <CheckRow
                    key={position}
                    name={check.check ?? check.name ?? `Check ${position + 1}`}
                    passed={check.passed ?? null}
                    detail={check.detail}
                  />
                ))}
              </ul>
            </Panel>
          )}

          {run && run.timeline.length > 0 && (
            <Panel>
              <PanelTitle aside={`${run.timeline.length} events`}>{call ? "Run events" : "Timeline"}</PanelTitle>
              <ol className="flex flex-col gap-1">
                {run.timeline.map((step, position) => (
                  <li key={position} className="flex items-center gap-3 rounded-lg px-1 py-1.5 text-[13px]">
                    <span className="t-num w-6 text-right text-[11px] text-subtle-foreground">{position + 1}</span>
                    <span>{timelineStepLabel(step)}</span>
                  </li>
                ))}
              </ol>
            </Panel>
          )}

          <ScenarioSteps name={result.scenario} />
        </div>

        <aside className="flex flex-col gap-4">
          {call && (
            <Panel>
              <PanelTitle>Call</PanelTitle>
              <Facts rows={[
                ["From", formatAddress(call.from_address)],
                ["To", formatAddress(call.to_address)],
                ["Line", endpoint?.name ?? MISSING],
                ["Provider", call.provider],
                ["Status", call.status.replaceAll("-", " ")],
                ["Started", formatWhen(call.started_at, true)],
                ["Length", formatDuration(call.duration_s)],
                ["Call id", <span key="id" title={call.id}>{shortId(call.id)}</span>],
              ]} />
            </Panel>
          )}
          {run && (
            <Panel>
              <PanelTitle>Run</PanelTitle>
              <Facts rows={[
                ["Run id", <span key="id" title={run.id}>{shortId(run.id)}</span>],
                ["Created", formatWhen(run.created_at, true)],
                ["Completed", formatWhen(run.completed_at, true)],
                ["Mode", run.result.mode ?? run.provider],
                ...(!call ? [["Line", endpoint ? `${endpoint.name} · ${formatAddress(endpoint.address)}` : MISSING] as [string, ReactNode]] : []),
                ...(run.caller_number || run.result.caller_number ? [["Caller", formatAddress(run.caller_number ?? run.result.caller_number)] as [string, ReactNode]] : []),
                ...(run.extension || run.result.extension ? [["Extension", run.extension ?? run.result.extension] as [string, ReactNode]] : []),
              ]} />
              {run.result.summary && <p className="mt-3 text-xs leading-5 text-muted-foreground">{run.result.summary}</p>}
            </Panel>
          )}
          {result.skipped.length > 0 && (
            <Panel>
              <PanelTitle>Checked elsewhere</PanelTitle>
              <p className="text-xs leading-5 text-muted-foreground">
                These come from the agent&apos;s own records, not this call:
              </p>
              <ul className="mt-2 flex flex-wrap gap-1.5">
                {result.skipped.map((name) => (
                  <li key={name} className="t-num rounded-md bg-surface-1 px-2 py-1 text-[11px] text-muted-foreground">{name}</li>
                ))}
              </ul>
            </Panel>
          )}
        </aside>
      </div>
      {confirm.dialog}
    </PageFrame>
  );
}
