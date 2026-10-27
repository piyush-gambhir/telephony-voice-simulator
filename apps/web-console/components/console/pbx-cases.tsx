"use client";

import { useRef, useState, type ReactNode } from "react";
import { ListChecks, Loader2, Play } from "lucide-react";

import { EmptyStrip, MISSING, Panel, PanelTitle, TonePill } from "@/components/console/kit";
import { ErrorLine, Facts, OutcomePill } from "@/components/console/models-timeline";
import { CallView, TransferView, type DealerCaseRun } from "@/components/console/pbx-results";
import { Button } from "@/components/ui/button";
import { useAction, useApi } from "@/hooks/use-api";
import { cn } from "@/lib/utils";
import { pbx } from "@/lib/workflows-api";

type Suite = Awaited<ReturnType<typeof pbx.suite>>;

export function PbxCases() {
  const cases = useApi((signal) => pbx.cases(signal), []);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [run, setRun] = useState<DealerCaseRun | null>(null);
  const [suite, setSuite] = useState<Suite | null>(null);
  const caseAction = useAction();
  // Only the most recently clicked case may show its result.
  const latest = useRef<string | null>(null);
  const suiteAction = useAction();

  const list = cases.data ?? [];
  const selected = list.find((item) => item.id === selectedId) ?? null;
  const suiteById = new Map((suite?.cases ?? []).map((item) => [item.id, item]));

  async function runCase(id: string) {
    latest.current = id;
    setSelectedId(id);
    setRun(null);
    const result = await caseAction.run(() => pbx.runCase(id));
    if (result && latest.current === id) setRun(result as unknown as DealerCaseRun);
  }

  async function runSuite() {
    const result = await suiteAction.run(() => pbx.suite());
    if (result) setSuite(result);
  }

  if (cases.error) return <ErrorLine message={cases.error} />;
  if (!cases.loading && !list.length) {
    return <EmptyStrip title="No dealer cases returned." body="Named cases appear here once the simulator backend is running." />;
  }

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-center gap-3" aria-live="polite">
        <Button size="sm" variant="secondary" disabled={suiteAction.pending || cases.loading} onClick={() => void runSuite()}>
          {suiteAction.pending ? <Loader2 className="animate-spin" /> : <ListChecks />}
          Run suite
        </Button>
        {suite && (
          <TonePill tone={suite.passed === suite.total ? "success" : "danger"}>
            {suite.passed} of {suite.total} passed
          </TonePill>
        )}
        <ErrorLine message={suiteAction.error} />
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
        <Panel className="h-fit p-2 md:p-2">
          <ul className="grid gap-1" aria-label="Dealer cases">
            {list.map((item) => {
              const outcome = suiteById.get(item.id);
              const active = item.id === selectedId;
              return (
                <li key={item.id}>
                  <button
                    type="button"
                    aria-pressed={active}
                    onClick={() => void runCase(item.id)}
                    className={cn(
                      "grid w-full gap-1 rounded-lg px-3 py-2.5 text-left outline-none transition-colors duration-[var(--motion-fast)] focus-visible:ring-2 focus-visible:ring-ring/40",
                      active ? "bg-accent-soft" : "hover:bg-control-hover",
                    )}
                  >
                    <span className="flex items-start justify-between gap-3">
                      <span className="min-w-0 text-[13px] font-medium">{item.title}</span>
                      {outcome && <TonePill tone={outcome.passed ? "success" : "danger"}>{outcome.passed ? "Pass" : "Fail"}</TonePill>}
                    </span>
                    <span className="text-xs leading-5 text-muted-foreground">{item.teaches}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        </Panel>

        <div className="grid h-fit gap-4" aria-live="polite">
          {!selected ? (
            <EmptyStrip title="No case run yet." body="Pick a case to run it and compare expected and actual outcomes." />
          ) : caseAction.pending || !run ? (
            <Panel>
              <PanelTitle>{selected.title}</PanelTitle>
              {caseAction.error ? (
                <div className="grid gap-3">
                  <ErrorLine message={caseAction.error} />
                  <div>
                    <Button size="sm" variant="secondary" onClick={() => void runCase(selected.id)}>
                      <Play />
                      Run again
                    </Button>
                  </div>
                </div>
              ) : (
                <p className="flex items-center gap-2 text-[13px] text-muted-foreground">
                  <Loader2 className="size-4 animate-spin" /> Running
                </p>
              )}
            </Panel>
          ) : (
            <>
              <Panel>
                <PanelTitle aside={<TonePill tone={run.passed ? "success" : "danger"}>{run.passed ? "Pass" : "Fail"}</TonePill>}>
                  {run.title}
                </PanelTitle>
                <p className="mb-4 text-[13px] leading-5 text-muted-foreground">{run.teaches}</p>
                <Facts
                  items={[
                    ["Expected outcome", <OutcomePill key="eo" outcome={run.expected_outcome} />],
                    ["Actual outcome", <OutcomePill key="ao" outcome={run.actual_outcome} />],
                    ...(run.expected_transfer || run.actual_transfer
                      ? [
                          ["Expected transfer", run.expected_transfer ? <OutcomePill key="et" outcome={run.expected_transfer} /> : MISSING] as [string, ReactNode],
                          ["Actual transfer", run.actual_transfer ? <OutcomePill key="at" outcome={run.actual_transfer} /> : MISSING] as [string, ReactNode],
                        ]
                      : []),
                  ]}
                />
              </Panel>
              <Panel>
                <PanelTitle aside={run.call.site ?? undefined}>Call</PanelTitle>
                <CallView call={run.call} />
              </Panel>
              {run.transfer && (
                <Panel>
                  <PanelTitle>Transfer</PanelTitle>
                  <TransferView transfer={run.transfer} />
                </Panel>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
