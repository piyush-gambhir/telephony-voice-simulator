"use client";

import { useState, type FormEvent, type ReactNode } from "react";
import { Loader2, X } from "lucide-react";

import { ActionError } from "@/components/console/hosted-parts";
import { Field, formatAddress, formatWhen, MISSING, Panel, PanelTitle, Segmented, useConfirm } from "@/components/console/kit";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useAction } from "@/hooks/use-api";
import { accountNumbers, type AccountNumber } from "@/lib/workflows-api";

type Ask = ReturnType<typeof useConfirm>["ask"];
type Backup = { name: string; modified: number };

/** Same rule as the API: HTTPS, or a local URL for development. */
const WEBHOOK_URL = /^(https:\/\/\S+|http:\/\/(localhost|127\.0\.0\.1):\S+)$/;

function text(value: unknown): string {
  return typeof value === "string" && value ? value : MISSING;
}

/** Old and new value side by side; unchanged rows are muted. */
function ChangeRow({ label, before, after }: { label: string; before: string; after: string }) {
  const changed = before !== after;
  return (
    <div className="grid gap-1 rounded-md bg-card px-3 py-2 text-xs sm:grid-cols-[120px_minmax(0,1fr)]">
      <span className="text-muted-foreground">{label}</span>
      {changed ? (
        <div className="grid min-w-0 gap-0.5 font-mono">
          <span className="break-all text-muted-foreground line-through">{before}</span>
          <span className="break-all">{after}</span>
        </div>
      ) : (
        <span className="break-all font-mono text-muted-foreground">{after} (unchanged)</span>
      )}
    </div>
  );
}

function Plan({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="grid gap-1.5 rounded-lg bg-surface-1 p-3" aria-live="polite">
      <p className="text-xs font-medium">{title}</p>
      {children}
    </div>
  );
}

/* ---------- point ---------- */

type PointPlan = Awaited<ReturnType<typeof accountNumbers.point>>;

export function PointForm({ number, ask, onApplied, onClose }: { number: AccountNumber; ask: Ask; onApplied: () => void; onClose: () => void }) {
  const [mode, setMode] = useState<"route" | "url">("route");
  const [route, setRoute] = useState<"ivr" | "amd">("ivr");
  const [baseUrl, setBaseUrl] = useState("");
  const [voiceUrl, setVoiceUrl] = useState("");
  const [statusCallback, setStatusCallback] = useState("");
  const [plan, setPlan] = useState<PointPlan | null>(null);
  const [applied, setApplied] = useState<PointPlan | null>(null);
  const action = useAction();

  const valid = (mode === "route" ? baseUrl === "" || WEBHOOK_URL.test(baseUrl) : WEBHOOK_URL.test(voiceUrl))
    && (statusCallback === "" || WEBHOOK_URL.test(statusCallback));

  function edit<T>(setter: (value: T) => void) {
    return (value: T) => {
      setter(value);
      setPlan(null);
      setApplied(null);
    };
  }

  function body(dryRun: boolean) {
    return {
      number: number.phone_number,
      ...(mode === "route" ? { route, base_url: baseUrl || undefined } : { voice_url: voiceUrl }),
      status_callback_url: statusCallback || undefined,
      dry_run: dryRun,
    };
  }

  async function preview(event: FormEvent) {
    event.preventDefault();
    const result = await action.run(() => accountNumbers.point(body(true)));
    if (result) setPlan(result);
  }

  function apply() {
    if (!plan) return;
    const url = String(plan.change.VoiceUrl ?? "");
    ask({
      title: `Rewire ${formatAddress(number.phone_number)}?`,
      body: `This rewires the live number: inbound calls to it go to ${url} immediately. A backup of the current settings is saved as ${plan.backup_path}.`,
      confirm: "Apply",
      tone: "primary",
      onConfirm: async () => {
        const result = await action.run(() => accountNumbers.point(body(false)));
        if (result) {
          setApplied(result);
          setPlan(null);
          onApplied();
        }
      },
    });
  }

  return (
    <Panel>
      <PanelTitle aside={<CloseButton onClose={onClose} />}>Point {formatAddress(number.phone_number)}</PanelTitle>
      <form className="grid gap-4" onSubmit={preview}>
        <Segmented
          label="Target"
          value={mode}
          onChange={edit(setMode)}
          options={[{ id: "route", label: "Simulator route" }, { id: "url", label: "Explicit voice URL" }]}
        />
        {mode === "route" ? (
          <div className="grid gap-3 sm:grid-cols-[160px_minmax(0,1fr)]">
            <Field id="point-route" label="Route">
              <Select value={route} onValueChange={(value) => edit(setRoute)(value as "ivr" | "amd")}>
                <SelectTrigger id="point-route"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="amd">AMD (/twilio/voice)</SelectItem>
                  <SelectItem value="ivr">IVR (/twilio/ivr)</SelectItem>
                </SelectContent>
              </Select>
            </Field>
            <Field id="point-base" label="Base URL" help="Leave empty to use PUBLIC_BASE_URL on the API host.">
              <Input
                id="point-base"
                value={baseUrl}
                placeholder="https://"
                onChange={(event) => edit(setBaseUrl)(event.target.value.trim())}
                aria-invalid={baseUrl !== "" && !WEBHOOK_URL.test(baseUrl)}
                className="font-mono"
              />
            </Field>
          </div>
        ) : (
          <Field id="point-voice" label="Voice URL">
            <Input
              id="point-voice"
              value={voiceUrl}
              placeholder="https://"
              onChange={(event) => edit(setVoiceUrl)(event.target.value.trim())}
              aria-invalid={voiceUrl !== "" && !WEBHOOK_URL.test(voiceUrl)}
              className="font-mono"
            />
          </Field>
        )}
        <Field id="point-status" label="Status callback" help="Optional. Left empty, the current status callback stays.">
          <Input
            id="point-status"
            value={statusCallback}
            placeholder="https://"
            onChange={(event) => edit(setStatusCallback)(event.target.value.trim())}
            aria-invalid={statusCallback !== "" && !WEBHOOK_URL.test(statusCallback)}
            className="font-mono"
          />
        </Field>
        <ActionError message={action.error} />
        {plan && (
          <Plan title="Planned change (dry run, nothing written)">
            <ChangeRow label="Voice URL" before={text(plan.backup.voice_url)} after={text(plan.change.VoiceUrl)} />
            <ChangeRow label="Friendly name" before={text(plan.backup.friendly_name)} after={text(plan.change.FriendlyName)} />
            <ChangeRow label="Status callback" before={text(plan.backup.status_callback)} after={text(plan.change.StatusCallback ?? plan.backup.status_callback)} />
            <p className="break-all px-1 pt-1 text-xs text-muted-foreground">Backup: <span className="font-mono">{plan.backup_path}</span></p>
          </Plan>
        )}
        {applied && (
          <p className="rounded-lg bg-success-soft px-3 py-2 text-xs leading-5 text-success" aria-live="polite">
            Applied. Voice URL is now {text(applied.change.VoiceUrl)}; backup saved as {applied.backup_path}.
          </p>
        )}
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant={plan ? "secondary" : "default"} disabled={!valid || action.pending}>
            {action.pending && !plan && <Loader2 className="animate-spin" />} Dry run
          </Button>
          {plan && (
            <Button type="button" size="sm" disabled={action.pending} onClick={apply}>
              Apply
            </Button>
          )}
        </div>
      </form>
    </Panel>
  );
}

/* ---------- revert ---------- */

type RevertPlan = Awaited<ReturnType<typeof accountNumbers.revert>>;

export function RevertForm({
  number,
  backups,
  ask,
  onApplied,
  onClose,
}: {
  number: AccountNumber;
  backups: Backup[];
  ask: Ask;
  onApplied: () => void;
  onClose: () => void;
}) {
  // Point names its backup after the number SID; fall back to every backup when none match.
  const matching = backups.filter((item) => item.name.includes(number.sid));
  const options = [...(matching.length ? matching : backups)].sort((a, b) => b.modified - a.modified);
  const [backup, setBackup] = useState(options[0]?.name ?? "");
  const [plan, setPlan] = useState<RevertPlan | null>(null);
  const [applied, setApplied] = useState(false);
  const action = useAction();

  async function preview(event: FormEvent) {
    event.preventDefault();
    const result = await action.run(() => accountNumbers.revert({ number: number.phone_number, backup, dry_run: true }));
    if (result) setPlan(result);
  }

  function apply() {
    if (!plan) return;
    ask({
      title: `Restore ${formatAddress(number.phone_number)}?`,
      body: `This rewires the live number from backup ${backup}: inbound calls to it go to ${text(plan.restore.VoiceUrl)} immediately.`,
      confirm: "Restore",
      tone: "primary",
      onConfirm: async () => {
        const result = await action.run(() => accountNumbers.revert({ number: number.phone_number, backup, dry_run: false }));
        if (result) {
          setApplied(true);
          setPlan(null);
          onApplied();
        }
      },
    });
  }

  return (
    <Panel>
      <PanelTitle aside={<CloseButton onClose={onClose} />}>Revert {formatAddress(number.phone_number)}</PanelTitle>
      {options.length ? (
        <form className="grid gap-4" onSubmit={preview}>
          <Field
            id="revert-backup"
            label="Backup"
            help={matching.length ? undefined : "No backup is named after this number's SID; the API refuses a backup from another number."}
          >
            <Select value={backup} onValueChange={(value) => { setBackup(value); setPlan(null); setApplied(false); }}>
              <SelectTrigger id="revert-backup"><SelectValue placeholder="Select a backup" /></SelectTrigger>
              <SelectContent>
                {options.map((item) => (
                  <SelectItem key={item.name} value={item.name}>
                    {item.name} · {formatWhen(new Date(item.modified * 1000).toISOString())}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          <ActionError message={action.error} />
          {plan && (
            <Plan title="Planned restore (dry run, nothing written)">
              <ChangeRow label="Voice URL" before={text(number.voice_url)} after={text(plan.restore.VoiceUrl)} />
              <ChangeRow label="Friendly name" before={text(number.friendly_name)} after={text(plan.restore.FriendlyName)} />
              <ChangeRow label="Status callback" before={text(number.status_callback)} after={text(plan.restore.StatusCallback)} />
            </Plan>
          )}
          {applied && (
            <p className="rounded-lg bg-success-soft px-3 py-2 text-xs leading-5 text-success" aria-live="polite">
              Restored from {backup}.
            </p>
          )}
          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant={plan ? "secondary" : "default"} disabled={!backup || action.pending}>
              {action.pending && !plan && <Loader2 className="animate-spin" />} Dry run
            </Button>
            {plan && (
              <Button type="button" size="sm" disabled={action.pending} onClick={apply}>
                Restore
              </Button>
            )}
          </div>
        </form>
      ) : (
        <p className="text-[13px] text-muted-foreground">No backups yet. Pointing a number saves one before it changes anything.</p>
      )}
    </Panel>
  );
}

function CloseButton({ onClose }: { onClose: () => void }) {
  return (
    <Button type="button" variant="ghost" size="icon-xs" aria-label="Close" onClick={onClose}>
      <X />
    </Button>
  );
}
