"use client";

import { useState } from "react";
import { Loader2, Play, RotateCcw } from "lucide-react";

import {
  EmptyStrip,
  Field,
  formatAddress,
  MISSING,
  PageFrame,
  PageHeader,
  Panel,
  PanelTitle,
  Segmented,
} from "@/components/console/kit";
import { ErrorLine, Facts, humanize, OutcomePill, StepList } from "@/components/console/models-timeline";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useAction, useApi } from "@/hooks/use-api";
import { cn } from "@/lib/utils";
import { models, type ModelResult } from "@/lib/workflows-api";

type Kind = "ivr" | "pbx";

const DEFAULT_STATE = "default";

const asList = (value: ModelResult | ModelResult[]) => (Array.isArray(value) ? value : [value]);

export function ModelsPage() {
  const [kind, setKind] = useState<Kind>("ivr");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [overrides, setOverrides] = useState<Record<string, string>>({});
  const runs = useApi(() => models.simulate({ kind }).then(asList), [kind]);
  const extensions = useApi((signal) => models.pbxExtensions(signal), []);
  const rerun = useAction();

  const list = runs.data ?? [];
  const selected = list.find((item) => item.scenario_id === selectedId) ?? list[0] ?? null;
  const activeOverrides = Object.fromEntries(Object.entries(overrides).filter(([, state]) => state !== DEFAULT_STATE));
  const overrideCount = Object.keys(activeOverrides).length;
  const applied = list[0]?.overrides ?? {};
  const appliedCount = Object.keys(applied).length;

  async function runWithOverrides(next: Record<string, string>) {
    const result = await rerun.run(() => models.simulate({ kind: "pbx", overrides: next }));
    if (result) runs.setData(asList(result));
  }

  return (
    <PageFrame>
      <PageHeader title="Built-in models" count={runs.loading ? null : list.length} freshness={false}>
        <Segmented<Kind>
          label="Model kind"
          value={kind}
          onChange={(next) => {
            setKind(next);
            setSelectedId(null);
            rerun.setError(null);
          }}
          options={[
            { id: "ivr", label: "IVR journeys" },
            { id: "pbx", label: "PBX stories" },
          ]}
        />
        <Button variant="secondary" size="sm" disabled={runs.loading || rerun.pending} onClick={runs.reload}>
          {runs.loading ? <Loader2 className="animate-spin" /> : <Play />}
          Run all
        </Button>
      </PageHeader>

      {runs.error && <ErrorLine message={runs.error} />}

      {kind === "pbx" && (
        <Panel>
          <PanelTitle aside={appliedCount ? `${appliedCount} applied` : "Story defaults"}>Extension states</PanelTitle>
          {extensions.error && <ErrorLine message={extensions.error} />}
          {extensions.data && (
            <div className="grid gap-4">
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                {extensions.data.extensions.map((extension) => (
                  <Field key={extension.id} id={`model-ext-${extension.id}`} label={extension.name}>
                    <Select
                      value={overrides[extension.id] ?? DEFAULT_STATE}
                      onValueChange={(state) => setOverrides((current) => ({ ...current, [extension.id]: state }))}
                    >
                      <SelectTrigger id={`model-ext-${extension.id}`}><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value={DEFAULT_STATE}>Story default</SelectItem>
                        {extensions.data!.states.map((state) => (
                          <SelectItem key={state} value={state}>{humanize(state)}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </Field>
                ))}
              </div>
              <div className="flex flex-wrap items-center gap-2" aria-live="polite">
                <Button size="sm" disabled={rerun.pending || runs.loading || !overrideCount} onClick={() => void runWithOverrides(activeOverrides)}>
                  {rerun.pending ? <Loader2 className="animate-spin" /> : <Play />}
                  Re-run with {overrideCount || "no"} override{overrideCount === 1 ? "" : "s"}
                </Button>
                {(overrideCount > 0 || appliedCount > 0) && (
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={rerun.pending || runs.loading}
                    onClick={() => {
                      setOverrides({});
                      void runWithOverrides({});
                    }}
                  >
                    <RotateCcw />
                    Reset to defaults
                  </Button>
                )}
                <ErrorLine message={rerun.error} />
              </div>
            </div>
          )}
        </Panel>
      )}

      {!runs.loading && !runs.error && !list.length ? (
        <EmptyStrip title="No scenarios returned." body="Built-in scenarios appear here once the simulator backend is running." />
      ) : (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
          <Panel className="h-fit p-2 md:p-2">
            <ul className="grid gap-1" aria-label={kind === "ivr" ? "IVR journeys" : "PBX stories"}>
              {list.map((item) => {
                const active = item.scenario_id === selected?.scenario_id;
                return (
                  <li key={item.scenario_id} className="min-w-0">
                    <button
                      type="button"
                      aria-pressed={active}
                      onClick={() => setSelectedId(item.scenario_id)}
                      className={cn(
                        "grid w-full min-w-0 grid-cols-1 gap-1 rounded-lg px-3 py-2.5 text-left outline-none transition-colors duration-[var(--motion-fast)] focus-visible:ring-2 focus-visible:ring-ring/40",
                        active ? "bg-accent-soft" : "hover:bg-control-hover",
                      )}
                    >
                      <span className="flex min-w-0 items-center justify-between gap-3">
                        <span className="min-w-0 truncate text-[13px] font-medium">{item.title}</span>
                        <span className="shrink-0"><OutcomePill outcome={item.outcome} /></span>
                      </span>
                      <span className="t-num truncate text-xs text-muted-foreground">{summary(item)}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </Panel>

          {selected && (
            <Panel className="h-fit">
              <PanelTitle aside={<OutcomePill outcome={selected.outcome} />}>{selected.title}</PanelTitle>
              <Facts
                className="mb-4"
                items={[
                  ["Scenario", <span key="id" className="t-num text-xs">{selected.scenario_id}</span>],
                  ...(selected.caller ? [["Caller", selected.caller] as [string, string]] : []),
                  ["Caller number", <span key="cn" className="t-num">{formatAddress(selected.caller_number)}</span>],
                  ["Dialed", <span key="dn" className="t-num">{formatAddress(selected.dialed_number ?? selected.public_number)}</span>],
                  ...(selected.intent ? [["Intent", selected.intent] as [string, string]] : []),
                  ...(kind === "ivr"
                    ? [
                        ["Connected to", selected.connected_to ?? MISSING] as [string, string],
                        ["Menu attempts", String(selected.attempts ?? MISSING)] as [string, string],
                      ]
                    : []),
                  ...(selected.expected_summary ? [["Expected", selected.expected_summary] as [string, string]] : []),
                  ...(kind === "pbx"
                    ? [[
                        "Overrides",
                        appliedCount
                          ? Object.entries(applied).map(([id, state]) => `${extensionName(id)}: ${humanize(state)}`).join(", ")
                          : "None",
                      ] as [string, string]]
                    : []),
                ]}
              />
              <StepList steps={selected.timeline} label={`${selected.title} timeline`} />
            </Panel>
          )}
        </div>
      )}
    </PageFrame>
  );

  function extensionName(id: string) {
    return extensions.data?.extensions.find((extension) => extension.id === id)?.name ?? id;
  }
}

function summary(item: ModelResult): string {
  const dialed = formatAddress(item.dialed_number ?? item.public_number);
  const parts = [item.caller ?? formatAddress(item.caller_number), `to ${dialed}`];
  if (item.connected_to) parts.push(`connected to ${item.connected_to}`);
  if (item.attempts != null) parts.push(`${item.attempts} attempt${item.attempts === 1 ? "" : "s"}`);
  return parts.join(" · ");
}
