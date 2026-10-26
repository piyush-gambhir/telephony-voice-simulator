"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";

import { useConsole } from "@/components/console/console-provider";
import { Field, formatAddress } from "@/components/console/kit";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { scenarioTitle } from "@/lib/results";
import { scenariosForProvider } from "@/lib/scenario-compatibility";

/**
 * Starts a run on a line. Local lines play the scenario immediately; carrier lines
 * queue it for the next inbound call, so the result opens in its queued state.
 */
export function QueueRunSheet({
  open,
  onOpenChange,
  scenario: initialScenario,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  scenario?: string;
}) {
  const router = useRouter();
  const { data, busy, error, actions } = useConsole();
  const { endpoints, connections, scenarios, catalog } = data;
  const [endpointId, setEndpointId] = useState("");
  const [scenarioName, setScenarioName] = useState(initialScenario ?? "");

  const usable = endpoints.filter((endpoint) => endpoint.enabled);
  const fallback =
    (initialScenario &&
      usable.find((endpoint) =>
        scenariosForProvider(scenarios, endpoint.provider, catalog).some((item) => item.name === initialScenario)
        && endpoint.routing_mode === "queued")) ||
    usable.find((endpoint) => endpoint.provider === "mock") ||
    usable[0];
  const endpoint = endpoints.find((item) => item.id === endpointId) ?? fallback;
  const connection = connections.find((item) => item.id === endpoint?.connection_id);
  const compatible = scenariosForProvider(scenarios, endpoint?.provider, catalog);
  const fixed = endpoint?.routing_mode === "fixed" ? endpoint.default_scenario : null;
  const scenario =
    fixed ?? (compatible.some((item) => item.name === scenarioName) ? scenarioName : compatible[0]?.name ?? "");
  const available = Boolean(endpoint?.enabled && connection?.enabled !== false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!endpoint || !scenario) return;
    const runId = await actions.createRun(endpoint.id, scenario);
    if (runId) {
      onOpenChange(false);
      router.push(`/console/result/?id=${encodeURIComponent(runId)}`);
    }
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent>
        <form onSubmit={submit} className="flex h-full flex-col">
          <SheetHeader>
            <SheetTitle>Queue a run</SheetTitle>
            <SheetDescription>
              The local simulator plays the scenario now. A carrier line runs it on its next inbound call.
            </SheetDescription>
          </SheetHeader>
          <div className="flex flex-1 flex-col gap-5 overflow-y-auto px-6 pb-6">
            {usable.length ? (
              <>
                <Field id="queue-line" label="Line">
                  <Select
                    value={endpoint?.id ?? ""}
                    onValueChange={(value) => {
                      setEndpointId(value);
                      const next = endpoints.find((item) => item.id === value);
                      const options = scenariosForProvider(scenarios, next?.provider, catalog);
                      if (!options.some((item) => item.name === scenarioName)) setScenarioName(options[0]?.name ?? "");
                    }}
                  >
                    <SelectTrigger id="queue-line"><SelectValue placeholder="Select a line" /></SelectTrigger>
                    <SelectContent>
                      {usable.map((item) => (
                        <SelectItem key={item.id} value={item.id}>
                          {item.name} · {formatAddress(item.address)}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </Field>
                <Field
                  id="queue-scenario"
                  label="Scenario"
                  help={fixed
                    ? "This line always answers with its default scenario. Switch it to Queue, then default in Lines to choose another."
                    : `${compatible.length} scenarios run on ${endpoint?.provider ?? "this provider"}.`}
                >
                  <Select value={scenario} onValueChange={setScenarioName} disabled={Boolean(fixed)}>
                    <SelectTrigger id="queue-scenario"><SelectValue placeholder="Select a scenario" /></SelectTrigger>
                    <SelectContent>
                      {compatible.map((item) => (
                        <SelectItem key={item.name} value={item.name}>{scenarioTitle(item.name, scenarios)}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </Field>
                {endpoint?.provider === "twilio" && (
                  <p className="rounded-lg bg-surface-1 px-3 py-2.5 text-xs leading-5 text-muted-foreground">
                    Twilio allows one Voice URL per number: use a separate number for the shared
                    <code> /twilio/ivr</code> directory, or reattach this number when switching modes. The hosted
                    <code> /machine</code> Function keeps its own Sync queue, controlled from the CLI; this console
                    queues only the unified backend.
                  </p>
                )}
                {endpoint?.provider === "telnyx" && (
                  <p className="rounded-lg bg-warning-soft px-3 py-2.5 text-xs leading-5 text-warning">
                    Telnyx is a preview adapter and cannot place calls yet.
                  </p>
                )}
                {!available && (
                  <p className="rounded-lg bg-warning-soft px-3 py-2.5 text-xs leading-5 text-warning">
                    Enable this line and its provider before running.
                  </p>
                )}
                {error && <p role="alert" className="text-xs text-danger">{error}</p>}
              </>
            ) : (
              <p className="rounded-lg bg-surface-1 px-3 py-3 text-[13px] text-muted-foreground">
                Add an enabled line in Lines first. The local simulator needs no credentials.
              </p>
            )}
          </div>
          <SheetFooter>
            <Button type="button" variant="ghost" size="sm" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button size="sm" disabled={busy || !available || !scenario || !compatible.some((item) => item.name === scenario)}>
              {busy && <Loader2 className="animate-spin" />}
              {endpoint?.provider === "mock" ? "Run now" : "Queue for next call"}
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}
