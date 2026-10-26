"use client";

import { useState, type FormEvent, type ReactNode } from "react";
import { CloudDownload, History, Link2, Loader2, Plus, Trash2 } from "lucide-react";

import { useConsole } from "@/components/console/console-provider";
import {
  ConsoleErrors,
  DataTable,
  EmptyStrip,
  Field,
  formatAddress,
  MISSING,
  PageFrame,
  PageHeader,
  SheetError,
  Td,
  Th,
  TonePill,
  useConfirm,
} from "@/components/console/kit";
import { EndpointEditor } from "@/components/console/resource-editors";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
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
import type { Endpoint, ManagedNumber } from "@/lib/simulator-api";
import { cn } from "@/lib/utils";

const SELECTION: Record<Endpoint["routing_mode"], string> = {
  queued: "Queue, then default",
  fixed: "Always default",
};

function SheetSection({ title, description, children }: { title: string; description?: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-3">
      <div>
        <h3 className="text-[13px] font-semibold">{title}</h3>
        {description && <p className="mt-0.5 text-xs leading-5 text-muted-foreground">{description}</p>}
      </div>
      {children}
    </section>
  );
}

function Toggle({ id, checked, onChange, label }: { id: string; checked: boolean; onChange: (value: boolean) => void; label: string }) {
  return (
    <button
      id={id}
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={() => onChange(!checked)}
      className={cn(
        "relative h-5 w-9 shrink-0 rounded-full outline-none transition-colors duration-[var(--motion-fast)] focus-visible:ring-2 focus-visible:ring-ring/40",
        checked ? "bg-accent" : "bg-control-active",
      )}
    >
      <span className={cn(
        "absolute left-0 top-0.5 size-4 rounded-full bg-card [box-shadow:var(--shadow-raise)] transition-transform duration-[var(--motion-fast)]",
        checked ? "translate-x-[18px]" : "translate-x-0.5",
      )} />
    </button>
  );
}

function ManagedNumberForm({ number, onDone }: { number: ManagedNumber; onDone: () => void }) {
  const { data, busy, actions } = useConsole();
  const scenarios = data.scenarios.filter((scenario) => scenario.kind === "amd");
  const [friendlyName, setFriendlyName] = useState(number.friendly_name);
  const [defaultScenario, setDefaultScenario] = useState(number.endpoint.default_scenario ?? "");
  const [routingMode, setRoutingMode] = useState(number.endpoint.routing_mode);
  const [recordFullCalls, setRecordFullCalls] = useState(number.configuration.record_full_calls === true);
  const [nextScenario, setNextScenario] = useState(number.endpoint.default_scenario ?? scenarios[0]?.name ?? "");
  const queueBlocked = !number.endpoint.enabled
    ? "Enable the line to queue a call."
    : number.endpoint.routing_mode !== "queued"
      ? "Save call selection as Queue, then default to queue a call."
      : !number.attached_to_runtime
        ? "Connect the number to the local runtime to queue a call."
        : null;

  return (
    <div className="flex flex-col gap-7">
      <SheetSection title="Configuration" description="Default behavior and recording for every call to this number.">
        <Field id={`friendly-${number.id}`} label="Twilio name">
          <Input id={`friendly-${number.id}`} value={friendlyName} maxLength={64} onChange={(event) => setFriendlyName(event.target.value)} />
        </Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field id={`default-${number.id}`} label="Default scenario">
            <Select value={defaultScenario} onValueChange={setDefaultScenario}>
              <SelectTrigger id={`default-${number.id}`}><SelectValue placeholder="Select scenario" /></SelectTrigger>
              <SelectContent>
                {scenarios.map((scenario) => (
                  <SelectItem key={scenario.name} value={scenario.name}>{scenarioTitle(scenario.name, scenarios)}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          <Field id={`routing-${number.id}`} label="Call selection">
            <Select value={routingMode} onValueChange={(value) => setRoutingMode(value as Endpoint["routing_mode"])}>
              <SelectTrigger id={`routing-${number.id}`}><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="queued">{SELECTION.queued}</SelectItem>
                <SelectItem value="fixed">{SELECTION.fixed}</SelectItem>
              </SelectContent>
            </Select>
          </Field>
        </div>
        <div className="flex items-start gap-3 rounded-lg bg-surface-1 px-3.5 py-3">
          <div className="min-w-0 flex-1">
            <label htmlFor={`record-${number.id}`} className="text-[13px] font-medium">Record the full call</label>
            <p className="mt-0.5 text-xs leading-5 text-muted-foreground">
              Both sides, for playback and debugging. Voicemail scenarios always save the message after the tone.
            </p>
          </div>
          <Toggle id={`record-${number.id}`} label="Record the full call" checked={recordFullCalls} onChange={setRecordFullCalls} />
        </div>
        <div>
          <Button
            size="sm"
            disabled={busy || !friendlyName.trim() || !defaultScenario}
            onClick={async () => {
              const saved = await actions.updateAmdNumber(number.id, {
                friendly_name: friendlyName,
                default_scenario: defaultScenario,
                routing_mode: routingMode,
                record_full_calls: recordFullCalls,
              });
              if (saved) onDone();
            }}
          >
            {busy && <Loader2 className="animate-spin" />} Save number
          </Button>
        </div>
      </SheetSection>

      <SheetSection title="Next call" description="Run a one-time scenario on the next call to this number, then fall back to the default.">
        <Field id={`queue-${number.id}`} label="Scenario" help={queueBlocked}>
          <Select value={nextScenario} onValueChange={setNextScenario}>
            <SelectTrigger id={`queue-${number.id}`}><SelectValue placeholder="Select scenario" /></SelectTrigger>
            <SelectContent>
              {scenarios.map((scenario) => (
                <SelectItem key={scenario.name} value={scenario.name}>{scenarioTitle(scenario.name, scenarios)}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>
        <div>
          <Button
            size="sm"
            variant="secondary"
            disabled={busy || !nextScenario || Boolean(queueBlocked)}
            onClick={() => void actions.queueAmdNumber(number.id, nextScenario)}
          >
            Queue next call{number.pending_runs > 0 ? ` (${number.pending_runs} waiting)` : ""}
          </Button>
        </div>
      </SheetSection>

      <SheetSection title="Webhook" description={number.voice_url || "No Twilio Voice URL configured."}>
        {number.attached_to_runtime ? (
          <div className="flex flex-wrap items-center gap-2">
            <TonePill tone="success">Local runtime</TonePill>
            {number.configuration.previous_voice_url && (
              <Button size="sm" variant="ghost" disabled={busy} onClick={() => void actions.restoreAmdNumber(number.id)}>
                <History /> Restore hosted Function
              </Button>
            )}
          </div>
        ) : (
          <div className="flex flex-col items-start gap-2">
            <Button size="sm" variant="secondary" disabled={busy || !number.target_voice_url} onClick={() => void actions.attachAmdNumber(number.id)}>
              <Link2 /> Connect to local runtime
            </Button>
            {!number.target_voice_url && (
              <p className="text-xs text-warning">Start the HTTPS ingress before connecting this number.</p>
            )}
          </div>
        )}
      </SheetSection>
    </div>
  );
}

function AddLineForm({ onDone }: { onDone: () => void }) {
  const { data, actions } = useConsole();
  const { connections, catalog, scenarios } = data;
  const [connectionId, setConnectionId] = useState(connections[0]?.id ?? "");
  const connection = connections.find((item) => item.id === connectionId) ?? connections[0];
  const kinds = catalog.find((item) => item.key === connection?.provider)?.endpoint_kinds ?? ["phone_number"];
  const compatible = scenariosForProvider(scenarios, connection?.provider, catalog);
  const [name, setName] = useState("Test line");
  const [kind, setKind] = useState<Endpoint["kind"]>(kinds[0]);
  const [address, setAddress] = useState("+15550100001");
  const [scenario, setScenario] = useState(compatible[0]?.name ?? "");
  const [routing, setRouting] = useState<Endpoint["routing_mode"]>("fixed");
  const chosenKind = kinds.includes(kind) ? kind : kinds[0];
  const chosenScenario = compatible.some((item) => item.name === scenario) ? scenario : compatible[0]?.name ?? "";

  const [validation, setValidation] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!connection) return;
    if (!name.trim() || !address.trim()) return setValidation("Give the line a name and an address.");
    if (connection.provider !== "telnyx" && !chosenScenario) return setValidation("Choose a default scenario.");
    setValidation(null);
    const created = await actions.createEndpoint({
      connection_id: connection.id,
      name,
      kind: chosenKind,
      address,
      routing_mode: routing,
      default_scenario: chosenScenario,
    });
    if (created) onDone();
  }

  if (!connections.length) {
    return <p className="rounded-lg bg-surface-1 px-3.5 py-3 text-[13px] text-muted-foreground">Add a provider in Settings first. Every line belongs to one.</p>;
  }

  return (
    <form id="add-line" className="grid gap-5" onSubmit={submit}>
      <Field id="line-connection" label="Provider">
        <Select value={connection?.id} onValueChange={setConnectionId}>
          <SelectTrigger id="line-connection"><SelectValue /></SelectTrigger>
          <SelectContent>
            {connections.map((item) => <SelectItem key={item.id} value={item.id}>{item.name} · {item.provider}</SelectItem>)}
          </SelectContent>
        </Select>
      </Field>
      <Field id="line-name" label="Name">
        <Input id="line-name" value={name} onChange={(event) => setName(event.target.value)} />
      </Field>
      <div className="grid gap-3 sm:grid-cols-[140px_minmax(0,1fr)]">
        <Field id="line-kind" label="Kind">
          <Select value={chosenKind} onValueChange={(value) => setKind(value as Endpoint["kind"])}>
            <SelectTrigger id="line-kind"><SelectValue /></SelectTrigger>
            <SelectContent>
              {kinds.map((item) => <SelectItem key={item} value={item}>{item.replace("_", " ")}</SelectItem>)}
            </SelectContent>
          </Select>
        </Field>
        <Field id="line-address" label="Address">
          <Input id="line-address" value={address} onChange={(event) => setAddress(event.target.value)} className="font-mono" />
        </Field>
      </div>
      <Field id="line-scenario" label="Default scenario">
        <Select value={chosenScenario} onValueChange={setScenario}>
          <SelectTrigger id="line-scenario"><SelectValue placeholder="Select scenario" /></SelectTrigger>
          <SelectContent>
            {compatible.map((item) => <SelectItem key={item.name} value={item.name}>{scenarioTitle(item.name, scenarios)}</SelectItem>)}
          </SelectContent>
        </Select>
      </Field>
      <Field id="line-routing" label="Call selection" help="Always default answers every call with the default. Queue, then default runs a queued scenario first.">
        <Select value={routing} onValueChange={(value) => setRouting(value as Endpoint["routing_mode"])}>
          <SelectTrigger id="line-routing"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="fixed">{SELECTION.fixed}</SelectItem>
            <SelectItem value="queued">{SELECTION.queued}</SelectItem>
          </SelectContent>
        </Select>
      </Field>
      {validation && <p role="alert" className="text-xs text-danger">{validation}</p>}
    </form>
  );
}

export function LinesPage() {
  const { data, results, loading, busy, actions, clearError } = useConsole();
  const confirm = useConfirm();
  const [editing, setEditing] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const managedByEndpoint = new Map(data.amdNumbers.map((number) => [number.endpoint_id, number]));
  const queuedByEndpoint = new Map<string, number>();
  results.forEach((result) => {
    if (result.verdict === "queued" && result.run) {
      queuedByEndpoint.set(result.run.endpoint_id, (queuedByEndpoint.get(result.run.endpoint_id) ?? 0) + 1);
    }
  });
  const endpoint = data.endpoints.find((item) => item.id === editing);
  const managed = endpoint ? managedByEndpoint.get(endpoint.id) : undefined;

  return (
    <PageFrame>
      <PageHeader title="Lines" count={loading ? null : data.endpoints.length}>
        <Button
          variant="secondary"
          size="sm"
          disabled={busy}
          title="Import the numbers attached to the hosted AMD Function, creating the Twilio provider if needed"
          onClick={() => void actions.syncAmdNumbers()}
        >
          <CloudDownload /> {data.amdNumbers.length ? "Sync from Twilio" : "Import from Twilio"}
        </Button>
        <Button size="sm" onClick={() => { clearError(); setAdding(true); }}><Plus /> Add line</Button>
      </PageHeader>
      <ConsoleErrors />

      {data.endpoints.length ? (
        <DataTable label="Lines">
          <thead>
            <tr>
              <Th className="w-[22%]">Line</Th>
              <Th className="w-[170px]">Address</Th>
              <Th className="w-[14%]">Provider</Th>
              <Th className="w-[160px]">Call selection</Th>
              <Th>Default scenario</Th>
              <Th className="w-[70px] text-right">Queued</Th>
              <Th className="w-[176px]">Status</Th>
            </tr>
          </thead>
          <tbody>
            {data.endpoints.map((item) => {
              const number = managedByEndpoint.get(item.id);
              // Managed numbers report every pending run; others count queued results in the window.
              const queued = number?.pending_runs ?? queuedByEndpoint.get(item.id) ?? 0;
              return (
                <tr key={item.id} className="relative transition-colors even:bg-control/40 hover:bg-control-hover">
                  <Td className="max-w-0">
                    <button
                      type="button"
                      onClick={() => { clearError(); setEditing(item.id); }}
                      className="block w-full truncate text-left font-medium outline-none after:absolute after:inset-0 after:content-[''] focus-visible:after:rounded-lg focus-visible:after:ring-2 focus-visible:after:ring-ring/40"
                    >
                      {item.name}
                    </button>
                  </Td>
                  <Td className="t-num whitespace-nowrap text-xs">{formatAddress(item.address)}</Td>
                  <Td className="max-w-0"><span className="block truncate text-muted-foreground">{item.connection_name}</span></Td>
                  <Td className="text-muted-foreground">{SELECTION[item.routing_mode]}</Td>
                  <Td className="max-w-0">
                    <span className="block truncate text-muted-foreground">
                      {item.default_scenario ? scenarioTitle(item.default_scenario, data.scenarios) : MISSING}
                    </span>
                  </Td>
                  <Td className="t-num text-right text-xs">{queued || <span className="text-subtle-foreground">0</span>}</Td>
                  <Td>
                    <span className="flex gap-1">
                      {item.enabled ? <TonePill tone="success">Enabled</TonePill> : <TonePill tone="neutral">Disabled</TonePill>}
                      {number && (number.attached_to_runtime
                        ? <TonePill tone="accent">Local</TonePill>
                        : <TonePill tone="warning">Hosted</TonePill>)}
                    </span>
                  </Td>
                </tr>
              );
            })}
          </tbody>
        </DataTable>
      ) : loading ? (
        <div className="h-64 rounded-xl bg-card" />
      ) : (
        <EmptyStrip
          title="No lines yet."
          body="A line is a phone number, SIP URI or extension the simulator answers. Start with the local simulator: it needs no credentials."
          action={<Button size="sm" onClick={() => setAdding(true)}><Plus /> Add line</Button>}
        />
      )}

      <Sheet open={Boolean(endpoint)} onOpenChange={(open) => { if (!open) setEditing(null); }}>
        <SheetContent className="sm:max-w-lg">
          {endpoint && (
            <>
              <SheetHeader>
                <SheetTitle>{endpoint.name}</SheetTitle>
                <SheetDescription className="t-num text-xs">{formatAddress(endpoint.address)} · {endpoint.connection_name}</SheetDescription>
              </SheetHeader>
              <div className="flex-1 overflow-y-auto px-6 pb-6">
                {managed ? (
                  <ManagedNumberForm key={`${managed.id}:${managed.updated_at}`} number={managed} onDone={() => setEditing(null)} />
                ) : (
                  <EndpointEditor
                    key={endpoint.id}
                    endpoint={endpoint}
                    connections={data.connections}
                    catalog={data.catalog}
                    scenarios={data.scenarios}
                    busy={busy}
                    onCancel={() => setEditing(null)}
                    onSave={(values) => actions.saveEndpoint(endpoint.id, values)}
                  />
                )}
                <SheetError />
              </div>
              <SheetFooter className="sm:justify-between">
                <Button
                  variant="destructive"
                  size="sm"
                  disabled={busy}
                  onClick={() => confirm.ask({
                    title: `Delete ${endpoint.name}?`,
                    body: "This deletes the line and its simulation runs. It does not detach or reconfigure the carrier number. This cannot be undone.",
                    confirm: "Delete line",
                    onConfirm: async () => {
                      const done = await actions.deleteEndpoint(endpoint.id);
                      if (done) setEditing(null);
                      return done;
                    },
                  })}
                >
                  <Trash2 /> Delete
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={busy}
                  onClick={() => void (managed
                    ? actions.updateAmdNumber(managed.id, { enabled: !endpoint.enabled })
                    : actions.saveEndpoint(endpoint.id, { enabled: !endpoint.enabled }))}
                >
                  {endpoint.enabled ? "Disable line" : "Enable line"}
                </Button>
              </SheetFooter>
            </>
          )}
        </SheetContent>
      </Sheet>

      <Sheet open={adding} onOpenChange={setAdding}>
        <SheetContent>
          <SheetHeader>
            <SheetTitle>Add line</SheetTitle>
            <SheetDescription>Attach a callable address to a provider.</SheetDescription>
          </SheetHeader>
          <div className="flex-1 overflow-y-auto px-6 pb-6">
            {adding && <AddLineForm onDone={() => setAdding(false)} />}
            <SheetError />
          </div>
          <SheetFooter>
            <Button variant="ghost" size="sm" onClick={() => setAdding(false)}>Cancel</Button>
            <Button size="sm" type="submit" form="add-line" disabled={busy || !data.connections.length}>
              {busy && <Loader2 className="animate-spin" />} Add line
            </Button>
          </SheetFooter>
        </SheetContent>
      </Sheet>
      {confirm.dialog}
    </PageFrame>
  );
}
