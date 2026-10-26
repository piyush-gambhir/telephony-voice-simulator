"use client";

import { useState, type FormEvent, type ReactNode } from "react";
import { Loader2, Plus, Trash2 } from "lucide-react";

import { useConsole } from "@/components/console/console-provider";
import {
  ConsoleErrors,
  DataTable,
  EmptyStrip,
  Field,
  formatAddress,
  PageFrame,
  PageHeader,
  SheetError,
  Td,
  Th,
  TonePill,
  useConfirm,
} from "@/components/console/kit";
import {
  DirectoryEntryEditor,
  parseSettingsJson,
  ProviderConnectionEditor,
} from "@/components/console/resource-editors";
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
import type { ProviderConnection } from "@/lib/simulator-api";

const STATUS_TONE: Record<ProviderConnection["status"], "success" | "warning" | "neutral"> = {
  ready: "success",
  needs_setup: "warning",
  disabled: "neutral",
};

function OpenCell({ onOpen, children, className }: { onOpen: () => void; children: ReactNode; className?: string }) {
  return (
    <button
      type="button"
      onClick={onOpen}
      className={`block w-full truncate text-left font-medium outline-none after:absolute after:inset-0 after:content-[''] focus-visible:after:rounded-lg focus-visible:after:ring-2 focus-visible:after:ring-ring/40 ${className ?? ""}`}
    >
      {children}
    </button>
  );
}

function SideSheet({
  open,
  onClose,
  title,
  description,
  children,
  footer,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string;
  children: ReactNode;
  footer?: ReactNode;
}) {
  return (
    <Sheet open={open} onOpenChange={(value) => { if (!value) onClose(); }}>
      <SheetContent className="sm:max-w-lg">
        <SheetHeader>
          <SheetTitle>{title}</SheetTitle>
          {description && <SheetDescription>{description}</SheetDescription>}
        </SheetHeader>
        <div className="flex-1 overflow-y-auto px-6 pb-6">{children}<SheetError /></div>
        {footer && <SheetFooter className="sm:justify-between">{footer}</SheetFooter>}
      </SheetContent>
    </Sheet>
  );
}

/* ---------- providers ---------- */

function AddProviderForm({ onDone }: { onDone: () => void }) {
  const { data, actions } = useConsole();
  const [provider, setProvider] = useState(data.catalog[0]?.key ?? "mock");
  const [name, setName] = useState("Local simulator");
  const [status, setStatus] = useState<ProviderConnection["status"]>("ready");
  const [description, setDescription] = useState("Local deterministic call execution.");
  const [settings, setSettings] = useState("{}");
  const [validation, setValidation] = useState<string | null>(null);
  const descriptor = data.catalog.find((item) => item.key === provider);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setValidation(null);
    let parsed: Record<string, unknown>;
    try {
      parsed = parseSettingsJson(settings);
    } catch (cause) {
      setValidation(cause instanceof Error ? cause.message : "Settings JSON is invalid.");
      return;
    }
    if (!name.trim()) {
      setValidation("Give the provider a name.");
      return;
    }
    if (await actions.createConnection({ provider, name, status, description, settings: parsed })) onDone();
  }

  return (
    <form id="add-provider" className="grid gap-5" onSubmit={submit}>
      <Field id="provider-type" label="Provider" help={descriptor?.description}>
        <Select value={provider} onValueChange={setProvider}>
          <SelectTrigger id="provider-type"><SelectValue /></SelectTrigger>
          <SelectContent>
            {data.catalog.map((item) => (
              <SelectItem key={item.key} value={item.key}>{item.name}{item.status === "preview" ? " (preview)" : ""}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </Field>
      <Field id="provider-name" label="Name">
        <Input id="provider-name" value={name} onChange={(event) => setName(event.target.value)} />
      </Field>
      <div className="grid gap-3 sm:grid-cols-[150px_minmax(0,1fr)]">
        <Field id="provider-status" label="Status">
          <Select value={status} onValueChange={(value) => setStatus(value as ProviderConnection["status"])}>
            <SelectTrigger id="provider-status"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="ready">Ready</SelectItem>
              <SelectItem value="needs_setup">Needs setup</SelectItem>
              <SelectItem value="disabled">Disabled</SelectItem>
            </SelectContent>
          </Select>
        </Field>
        <Field id="provider-description" label="Description">
          <Input id="provider-description" value={description} onChange={(event) => setDescription(event.target.value)} />
        </Field>
      </div>
      <Field id="provider-settings" label="Settings JSON" help="Non-secret adapter options. Credentials stay in the simulator's environment.">
        <textarea
          id="provider-settings"
          value={settings}
          spellCheck={false}
          onChange={(event) => setSettings(event.target.value)}
          className="min-h-24 w-full resize-y rounded-lg bg-control px-3 py-2 font-mono text-xs outline-none transition-colors hover:bg-control-hover focus-visible:ring-2 focus-visible:ring-ring/40"
        />
      </Field>
      {validation && <p role="alert" className="text-xs text-danger">{validation}</p>}
    </form>
  );
}

export function ProvidersPage() {
  const { data, busy, loading, actions, clearError } = useConsole();
  const confirm = useConfirm();
  const [editing, setEditing] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const connection = data.connections.find((item) => item.id === editing);

  return (
    <PageFrame>
      <PageHeader title="Providers" count={loading ? null : data.connections.length}>
        <Button size="sm" onClick={() => { clearError(); setAdding(true); }}><Plus /> Add provider</Button>
      </PageHeader>
      <ConsoleErrors />

      {data.connections.length ? (
        <DataTable label="Providers">
          <thead>
            <tr>
              <Th className="w-[24%]">Name</Th>
              <Th className="w-[150px]">Adapter</Th>
              <Th className="w-[130px]">Status</Th>
              <Th className="w-[70px] text-right">Lines</Th>
              <Th>Notes</Th>
            </tr>
          </thead>
          <tbody>
            {data.connections.map((item) => {
              const descriptor = data.catalog.find((entry) => entry.key === item.provider);
              const lines = data.endpoints.filter((endpoint) => endpoint.connection_id === item.id).length;
              return (
                <tr key={item.id} className="relative transition-colors even:bg-control/40 hover:bg-control-hover">
                  <Td className="max-w-0"><OpenCell onOpen={() => { clearError(); setEditing(item.id); }}>{item.name}</OpenCell></Td>
                  <Td className="text-muted-foreground">
                    <span className="flex items-center gap-1.5">
                      {descriptor?.name ?? item.provider}
                      {descriptor?.status === "preview" && <TonePill tone="warning">Preview</TonePill>}
                    </span>
                  </Td>
                  <Td><TonePill tone={STATUS_TONE[item.status]}>{item.status.replaceAll("_", " ")}</TonePill></Td>
                  <Td className="t-num text-right text-xs">{lines}</Td>
                  <Td className="max-w-0">
                    <span className="block truncate text-muted-foreground">
                      {item.runtime.ready ? item.description || descriptor?.description : item.runtime.message ?? item.description}
                    </span>
                  </Td>
                </tr>
              );
            })}
          </tbody>
        </DataTable>
      ) : loading ? (
        <div className="h-48 rounded-xl bg-card" />
      ) : (
        <EmptyStrip
          title="No providers yet."
          body="Start with the local simulator. It needs no credentials and exercises the whole control plane."
          action={<Button size="sm" onClick={() => { clearError(); setAdding(true); }}><Plus /> Add provider</Button>}
        />
      )}

      <SideSheet
        open={Boolean(connection)}
        onClose={() => setEditing(null)}
        title={connection?.name ?? ""}
        description="The operator-facing profile and non-secret adapter settings."
        footer={connection && (
          <Button
            variant="destructive"
            size="sm"
            disabled={busy}
            onClick={() => confirm.ask({
              title: `Delete ${connection.name}?`,
              body: "This deletes the provider with its lines, directory routes and run history. This cannot be undone.",
              confirm: "Delete provider",
              onConfirm: async () => {
                const done = await actions.deleteConnection(connection.id);
                if (done) setEditing(null);
                return done;
              },
            })}
          >
            <Trash2 /> Delete provider
          </Button>
        )}
      >
        {connection && (
          <ProviderConnectionEditor
            key={connection.id}
            connection={connection}
            busy={busy}
            onCancel={() => setEditing(null)}
            onSave={(values) => actions.saveConnection(connection.id, values)}
          />
        )}
      </SideSheet>

      <Sheet open={adding} onOpenChange={setAdding}>
        <SheetContent className="sm:max-w-lg">
          <SheetHeader>
            <SheetTitle>Add provider</SheetTitle>
            <SheetDescription>Credentials stay in the simulator&apos;s environment.</SheetDescription>
          </SheetHeader>
          <div className="flex-1 overflow-y-auto px-6 pb-6">{adding && <AddProviderForm onDone={() => setAdding(false)} />}<SheetError /></div>
          <SheetFooter>
            <Button variant="ghost" size="sm" onClick={() => setAdding(false)}>Cancel</Button>
            <Button size="sm" type="submit" form="add-provider" disabled={busy}>
              {busy && <Loader2 className="animate-spin" />} Add provider
            </Button>
          </SheetFooter>
        </SheetContent>
      </Sheet>
      {confirm.dialog}
    </PageFrame>
  );
}

/* ---------- directory ---------- */

function AddExtensionForm({ onDone }: { onDone: () => void }) {
  const { data, actions } = useConsole();
  const [connectionId, setConnectionId] = useState(data.connections[0]?.id ?? "");
  const [extension, setExtension] = useState("1501");
  const [name, setName] = useState("Front desk");
  const [destination, setDestination] = useState("+15550101501");
  const [department, setDepartment] = useState("Reception");
  const [timeout, setTimeoutValue] = useState("25");

  const [validation, setValidation] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!connectionId) return setValidation("Choose a provider.");
    if (!/^[0-9]{4}$/.test(extension)) return setValidation("The extension must be four digits.");
    if (!destination.trim()) return setValidation("Add a destination.");
    setValidation(null);
    const created = await actions.createDirectoryEntry({
      connection_id: connectionId,
      extension,
      name,
      destination,
      department,
      ring_timeout: Number(timeout),
    });
    if (created) onDone();
  }

  return (
    <form id="add-extension" className="grid gap-5" onSubmit={submit}>
      <Field id="extension-provider" label="Provider">
        <Select value={connectionId} onValueChange={setConnectionId}>
          <SelectTrigger id="extension-provider"><SelectValue placeholder="Select provider" /></SelectTrigger>
          <SelectContent>
            {data.connections.map((item) => <SelectItem key={item.id} value={item.id}>{item.name} · {item.provider}</SelectItem>)}
          </SelectContent>
        </Select>
      </Field>
      <div className="grid grid-cols-2 gap-3">
        <Field id="extension-number" label="Extension" help="Four digits.">
          <Input id="extension-number" inputMode="numeric" minLength={4} maxLength={4} pattern="[0-9]{4}" value={extension}
            onChange={(event) => setExtension(event.target.value)} className="font-mono" />
        </Field>
        <Field id="extension-timeout" label="Ring timeout" help="Seconds, 5 to 120.">
          <Input id="extension-timeout" type="number" min="5" max="120" value={timeout} onChange={(event) => setTimeoutValue(event.target.value)} />
        </Field>
      </div>
      <Field id="extension-name" label="Display name">
        <Input id="extension-name" value={name} onChange={(event) => setName(event.target.value)} />
      </Field>
      <Field id="extension-destination" label="Destination">
        <Input id="extension-destination" value={destination} onChange={(event) => setDestination(event.target.value)} className="font-mono" />
      </Field>
      <Field id="extension-department" label="Department">
        <Input id="extension-department" value={department} onChange={(event) => setDepartment(event.target.value)} />
      </Field>
      {validation && <p role="alert" className="text-xs text-danger">{validation}</p>}
    </form>
  );
}

export function DirectoryPage() {
  const { data, busy, loading, actions, clearError } = useConsole();
  const confirm = useConfirm();
  const [editing, setEditing] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const entry = data.directory.find((item) => item.id === editing);

  return (
    <PageFrame>
      <PageHeader title="Directory" count={loading ? null : data.directory.length}>
        <Button size="sm" disabled={!data.connections.length} onClick={() => { clearError(); setAdding(true); }}><Plus /> Add extension</Button>
      </PageHeader>
      <ConsoleErrors />
      <p className="max-w-2xl text-[13px] leading-5 text-muted-foreground">
        Extensions route both the IVR lab and the live Twilio IVR.
      </p>

      {data.directory.length ? (
        <DataTable label="Directory">
          <thead>
            <tr>
              <Th className="w-[100px]">Extension</Th>
              <Th className="w-[24%]">Name</Th>
              <Th>Destination</Th>
              <Th className="w-[14%]">Provider</Th>
              <Th className="w-[80px] text-right">Timeout</Th>
              <Th className="w-[110px]">Status</Th>
            </tr>
          </thead>
          <tbody>
            {data.directory.map((item) => (
              <tr key={item.id} className="relative transition-colors even:bg-control/40 hover:bg-control-hover">
                <Td><OpenCell onOpen={() => { clearError(); setEditing(item.id); }} className="t-num text-xs">{item.extension}</OpenCell></Td>
                <Td className="max-w-0">
                  <span className="block truncate">{item.name}</span>
                </Td>
                <Td className="t-num text-xs text-muted-foreground">{formatAddress(item.destination)}</Td>
                <Td className="max-w-0"><span className="block truncate text-muted-foreground">{item.department || item.connection_name}</span></Td>
                <Td className="t-num text-right text-xs">{item.ring_timeout}s</Td>
                <Td>{item.enabled ? <TonePill tone="success">Enabled</TonePill> : <TonePill tone="neutral">Disabled</TonePill>}</Td>
              </tr>
            ))}
          </tbody>
        </DataTable>
      ) : loading ? (
        <div className="h-48 rounded-xl bg-card" />
      ) : (
        <EmptyStrip
          title="No extensions yet."
          body={data.connections.length ? "Map four digits to a destination to simulate inbound routing." : "Add a provider first; every destination belongs to one."}
          action={data.connections.length ? <Button size="sm" onClick={() => { clearError(); setAdding(true); }}><Plus /> Add extension</Button> : null}
        />
      )}

      <SideSheet
        open={Boolean(entry)}
        onClose={() => setEditing(null)}
        title={entry ? `Extension ${entry.extension}` : ""}
        description={entry ? `${entry.name} · ${entry.connection_name}` : undefined}
        footer={entry && (
          <>
            <Button
              variant="destructive"
              size="sm"
              disabled={busy}
              onClick={() => confirm.ask({
                title: `Delete extension ${entry.extension}?`,
                body: "Callers dialing it will hear the unknown-extension path. This cannot be undone.",
                confirm: "Delete extension",
                onConfirm: async () => {
                  const done = await actions.deleteDirectoryEntry(entry.id);
                  if (done) setEditing(null);
                  return done;
                },
              })}
            >
              <Trash2 /> Delete
            </Button>
            <Button variant="secondary" size="sm" disabled={busy} onClick={() => void actions.saveDirectoryEntry(entry.id, { enabled: !entry.enabled })}>
              {entry.enabled ? "Disable" : "Enable"}
            </Button>
          </>
        )}
      >
        {entry && (
          <DirectoryEntryEditor
            key={entry.id}
            entry={entry}
            connections={data.connections}
            busy={busy}
            onCancel={() => setEditing(null)}
            onSave={(values) => actions.saveDirectoryEntry(entry.id, values)}
          />
        )}
      </SideSheet>

      <Sheet open={adding} onOpenChange={setAdding}>
        <SheetContent>
          <SheetHeader>
            <SheetTitle>Add extension</SheetTitle>
            <SheetDescription>Map digits to a provider-backed destination.</SheetDescription>
          </SheetHeader>
          <div className="flex-1 overflow-y-auto px-6 pb-6">{adding && <AddExtensionForm onDone={() => setAdding(false)} />}<SheetError /></div>
          <SheetFooter>
            <Button variant="ghost" size="sm" onClick={() => setAdding(false)}>Cancel</Button>
            <Button size="sm" type="submit" form="add-extension" disabled={busy}>
              {busy && <Loader2 className="animate-spin" />} Add extension
            </Button>
          </SheetFooter>
        </SheetContent>
      </Sheet>
      {confirm.dialog}
    </PageFrame>
  );
}
