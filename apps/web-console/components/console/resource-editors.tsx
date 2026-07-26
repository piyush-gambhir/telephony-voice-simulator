"use client";

import { FormEvent, useMemo, useState } from "react";
import { Loader2, Save, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  DirectoryEntry,
  Endpoint,
  ProviderConnection,
  ProviderDescriptor,
  Scenario,
} from "@/lib/simulator-api";

const NO_SCENARIO = "__none__";

export type ProviderConnectionUpdate = Pick<
  ProviderConnection,
  "name" | "status" | "description" | "settings"
>;

export type EndpointUpdate = Pick<
  Endpoint,
  | "connection_id"
  | "name"
  | "kind"
  | "address"
  | "routing_mode"
  | "default_scenario"
  | "enabled"
>;

export type DirectoryEntryUpdate = Pick<
  DirectoryEntry,
  | "connection_id"
  | "extension"
  | "name"
  | "destination"
  | "department"
  | "ring_timeout"
  | "enabled"
>;

export function parseSettingsJson(value: string): Record<string, unknown> {
  const parsed: unknown = JSON.parse(value || "{}");
  if (parsed === null || Array.isArray(parsed) || typeof parsed !== "object") {
    throw new Error("Settings must be a JSON object.");
  }
  return parsed as Record<string, unknown>;
}

function EditorActions({
  busy,
  disabled,
  onCancel,
}: {
  busy: boolean;
  disabled?: boolean;
  onCancel: () => void;
}) {
  return (
    <div className="flex justify-end gap-2">
      <Button type="button" variant="ghost" disabled={busy} onClick={onCancel}>
        <X />
        Cancel
      </Button>
      <Button disabled={busy || disabled}>
        {busy ? <Loader2 className="animate-spin" /> : <Save />}
        Save
      </Button>
    </div>
  );
}

export function ProviderConnectionEditor({
  connection,
  busy,
  onCancel,
  onSave,
}: {
  connection: ProviderConnection;
  busy: boolean;
  onCancel: () => void;
  onSave: (values: ProviderConnectionUpdate) => Promise<boolean>;
}) {
  const [name, setName] = useState(connection.name);
  const [status, setStatus] = useState(connection.status);
  const [description, setDescription] = useState(connection.description);
  const [settingsJson, setSettingsJson] = useState(
    JSON.stringify(connection.settings, null, 2)
  );
  const [validation, setValidation] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setValidation(null);
    try {
      const settings = parseSettingsJson(settingsJson);
      if (
        await onSave({
          name: name.trim(),
          status,
          description: description.trim(),
          settings,
        })
      ) {
        onCancel();
      }
    } catch (cause) {
      setValidation(cause instanceof Error ? cause.message : "Settings JSON is invalid.");
    }
  }

  return (
    <form className="grid gap-4 rounded-xl bg-background/55 p-4" onSubmit={submit}>
      <div className="grid gap-3 md:grid-cols-2">
        <div className="grid gap-2">
          <Label htmlFor={`provider-edit-name-${connection.id}`}>Connection name</Label>
          <Input
            id={`provider-edit-name-${connection.id}`}
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </div>
        <div className="grid gap-2">
          <Label htmlFor={`provider-edit-status-${connection.id}`}>Status</Label>
          <Select
            value={status}
            onValueChange={(value) => setStatus(value as ProviderConnection["status"])}
          >
            <SelectTrigger id={`provider-edit-status-${connection.id}`}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="ready">Ready</SelectItem>
              <SelectItem value="needs_setup">Needs setup</SelectItem>
              <SelectItem value="disabled">Disabled</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>
      <div className="grid gap-2">
        <Label htmlFor={`provider-edit-description-${connection.id}`}>Description</Label>
        <Input
          id={`provider-edit-description-${connection.id}`}
          value={description}
          onChange={(event) => setDescription(event.target.value)}
        />
      </div>
      <div className="grid gap-2">
        <Label htmlFor={`provider-edit-settings-${connection.id}`}>Settings JSON</Label>
        <textarea
          id={`provider-edit-settings-${connection.id}`}
          className="min-h-28 w-full resize-y rounded-xl bg-background/80 px-3 py-2 font-mono text-xs outline-none transition-colors focus:bg-muted/70"
          value={settingsJson}
          onChange={(event) => setSettingsJson(event.target.value)}
          spellCheck={false}
        />
        <p className="text-xs text-muted-foreground">
          Use this for non-secret adapter options. Keep carrier credentials in environment
          variables.
        </p>
        {validation && <p className="text-xs text-destructive">{validation}</p>}
      </div>
      <EditorActions busy={busy} disabled={!name.trim()} onCancel={onCancel} />
    </form>
  );
}

function scenariosForProvider(scenarios: Scenario[], provider?: string): Scenario[] {
  if (provider === "twilio") {
    return scenarios.filter((scenario) => scenario.kind === "amd");
  }
  if (provider === "telnyx") return [];
  return scenarios;
}

export function EndpointEditor({
  endpoint,
  connections,
  catalog,
  scenarios,
  busy,
  onCancel,
  onSave,
}: {
  endpoint: Endpoint;
  connections: ProviderConnection[];
  catalog: ProviderDescriptor[];
  scenarios: Scenario[];
  busy: boolean;
  onCancel: () => void;
  onSave: (values: EndpointUpdate) => Promise<boolean>;
}) {
  const [connectionId, setConnectionId] = useState(endpoint.connection_id);
  const [name, setName] = useState(endpoint.name);
  const [kind, setKind] = useState(endpoint.kind);
  const [address, setAddress] = useState(endpoint.address);
  const [routingMode, setRoutingMode] = useState(endpoint.routing_mode);
  const [defaultScenario, setDefaultScenario] = useState(
    endpoint.default_scenario ?? NO_SCENARIO
  );
  const [enabled, setEnabled] = useState(endpoint.enabled);

  const selectedConnection = connections.find((item) => item.id === connectionId);
  const kinds =
    catalog.find((item) => item.key === selectedConnection?.provider)?.endpoint_kinds ??
    ["phone_number"];
  const compatibleScenarios = useMemo(
    () => scenariosForProvider(scenarios, selectedConnection?.provider),
    [scenarios, selectedConnection?.provider]
  );

  function selectConnection(value: string) {
    setConnectionId(value);
    const connection = connections.find((item) => item.id === value);
    const nextKinds =
      catalog.find((item) => item.key === connection?.provider)?.endpoint_kinds ??
      ["phone_number"];
    if (!nextKinds.includes(kind)) setKind(nextKinds[0]);
    const nextScenarios = scenariosForProvider(scenarios, connection?.provider);
    if (
      defaultScenario !== NO_SCENARIO &&
      !nextScenarios.some((scenario) => scenario.name === defaultScenario)
    ) {
      setDefaultScenario(nextScenarios[0]?.name ?? NO_SCENARIO);
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (
      await onSave({
        connection_id: connectionId,
        name: name.trim(),
        kind,
        address: address.trim(),
        routing_mode: routingMode,
        default_scenario: defaultScenario === NO_SCENARIO ? null : defaultScenario,
        enabled,
      })
    ) {
      onCancel();
    }
  }

  return (
    <form className="grid gap-4 rounded-xl bg-background/55 p-4" onSubmit={submit}>
      <div className="grid gap-3 md:grid-cols-2">
        <div className="grid gap-2">
          <Label htmlFor={`endpoint-edit-connection-${endpoint.id}`}>Connection</Label>
          <Select value={connectionId} onValueChange={selectConnection}>
            <SelectTrigger id={`endpoint-edit-connection-${endpoint.id}`}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {connections.map((connection) => (
                <SelectItem value={connection.id} key={connection.id}>
                  {connection.name} · {connection.provider}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="grid gap-2">
          <Label htmlFor={`endpoint-edit-name-${endpoint.id}`}>Name</Label>
          <Input
            id={`endpoint-edit-name-${endpoint.id}`}
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </div>
        <div className="grid gap-2">
          <Label htmlFor={`endpoint-edit-kind-${endpoint.id}`}>Kind</Label>
          <Select value={kind} onValueChange={(value) => setKind(value as Endpoint["kind"])}>
            <SelectTrigger id={`endpoint-edit-kind-${endpoint.id}`}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {kinds.map((item) => (
                <SelectItem value={item} key={item}>
                  {item.replace("_", " ")}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="grid gap-2">
          <Label htmlFor={`endpoint-edit-address-${endpoint.id}`}>Address</Label>
          <Input
            id={`endpoint-edit-address-${endpoint.id}`}
            value={address}
            onChange={(event) => setAddress(event.target.value)}
          />
        </div>
        <div className="grid gap-2">
          <Label htmlFor={`endpoint-edit-routing-${endpoint.id}`}>Routing mode</Label>
          <Select
            value={routingMode}
            onValueChange={(value) => setRoutingMode(value as Endpoint["routing_mode"])}
          >
            <SelectTrigger id={`endpoint-edit-routing-${endpoint.id}`}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="fixed">Fixed default</SelectItem>
              <SelectItem value="queued">Queued override</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className="grid gap-2">
          <Label htmlFor={`endpoint-edit-enabled-${endpoint.id}`}>Status</Label>
          <Select
            value={enabled ? "enabled" : "disabled"}
            onValueChange={(value) => setEnabled(value === "enabled")}
          >
            <SelectTrigger id={`endpoint-edit-enabled-${endpoint.id}`}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="enabled">Enabled</SelectItem>
              <SelectItem value="disabled">Disabled</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>
      <div className="grid gap-2">
        <Label htmlFor={`endpoint-edit-scenario-${endpoint.id}`}>Default scenario</Label>
        <Select value={defaultScenario} onValueChange={setDefaultScenario}>
          <SelectTrigger id={`endpoint-edit-scenario-${endpoint.id}`}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={NO_SCENARIO}>No default</SelectItem>
            {compatibleScenarios.map((scenario) => (
              <SelectItem value={scenario.name} key={scenario.name}>
                {scenario.title}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <p className="text-xs text-muted-foreground">
          Fixed uses the endpoint default when configured. Queued accepts a per-run override,
          then falls back to the default.
        </p>
      </div>
      <EditorActions
        busy={busy}
        disabled={!connectionId || !name.trim() || !address.trim()}
        onCancel={onCancel}
      />
    </form>
  );
}

export function DirectoryEntryEditor({
  entry,
  connections,
  busy,
  onCancel,
  onSave,
}: {
  entry: DirectoryEntry;
  connections: ProviderConnection[];
  busy: boolean;
  onCancel: () => void;
  onSave: (values: DirectoryEntryUpdate) => Promise<boolean>;
}) {
  const [connectionId, setConnectionId] = useState(entry.connection_id);
  const [extension, setExtension] = useState(entry.extension);
  const [name, setName] = useState(entry.name);
  const [destination, setDestination] = useState(entry.destination);
  const [department, setDepartment] = useState(entry.department);
  const [ringTimeout, setRingTimeout] = useState(String(entry.ring_timeout));
  const [enabled, setEnabled] = useState(entry.enabled);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (
      await onSave({
        connection_id: connectionId,
        extension: extension.trim(),
        name: name.trim(),
        destination: destination.trim(),
        department: department.trim(),
        ring_timeout: Number(ringTimeout),
        enabled,
      })
    ) {
      onCancel();
    }
  }

  return (
    <form className="grid gap-4 rounded-xl bg-background/60 p-4" onSubmit={submit}>
      <div className="grid gap-3">
        <div className="grid gap-2">
          <Label htmlFor={`directory-edit-connection-${entry.id}`}>Connection</Label>
          <Select value={connectionId} onValueChange={setConnectionId}>
            <SelectTrigger id={`directory-edit-connection-${entry.id}`}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {connections.map((connection) => (
                <SelectItem value={connection.id} key={connection.id}>
                  {connection.name} · {connection.provider}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="grid gap-2">
          <Label htmlFor={`directory-edit-extension-${entry.id}`}>Extension</Label>
          <Input
            id={`directory-edit-extension-${entry.id}`}
            inputMode="numeric"
            minLength={4}
            maxLength={4}
            pattern="[0-9]{4}"
            value={extension}
            onChange={(event) => setExtension(event.target.value)}
          />
        </div>
        <div className="grid gap-2">
          <Label htmlFor={`directory-edit-timeout-${entry.id}`}>Ring timeout</Label>
          <Input
            id={`directory-edit-timeout-${entry.id}`}
            type="number"
            min="5"
            max="120"
            value={ringTimeout}
            onChange={(event) => setRingTimeout(event.target.value)}
          />
        </div>
        <div className="grid gap-2">
          <Label htmlFor={`directory-edit-name-${entry.id}`}>Display name</Label>
          <Input
            id={`directory-edit-name-${entry.id}`}
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </div>
        <div className="grid gap-2">
          <Label htmlFor={`directory-edit-destination-${entry.id}`}>Destination</Label>
          <Input
            id={`directory-edit-destination-${entry.id}`}
            value={destination}
            onChange={(event) => setDestination(event.target.value)}
          />
        </div>
        <div className="grid gap-2">
          <Label htmlFor={`directory-edit-department-${entry.id}`}>Department</Label>
          <Input
            id={`directory-edit-department-${entry.id}`}
            value={department}
            onChange={(event) => setDepartment(event.target.value)}
          />
        </div>
        <div className="grid gap-2">
          <Label htmlFor={`directory-edit-enabled-${entry.id}`}>Status</Label>
          <Select
            value={enabled ? "enabled" : "disabled"}
            onValueChange={(value) => setEnabled(value === "enabled")}
          >
            <SelectTrigger id={`directory-edit-enabled-${entry.id}`}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="enabled">Enabled</SelectItem>
              <SelectItem value="disabled">Disabled</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>
      <EditorActions
        busy={busy}
        disabled={
          !connectionId ||
          !/^[0-9]{4}$/.test(extension.trim()) ||
          !name.trim() ||
          !destination.trim() ||
          !department.trim() ||
          !Number.isInteger(Number(ringTimeout)) ||
          Number(ringTimeout) < 5 ||
          Number(ringTimeout) > 120
        }
        onCancel={onCancel}
      />
    </form>
  );
}
