"use client";

import { useState } from "react";
import {
  ArrowRight,
  AudioLines,
  Check,
  CloudDownload,
  History,
  Link2,
  Loader2,
  PhoneCall,
  Save,
  Send,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { ManagedNumber, Scenario } from "@/lib/simulator-api";

type NumberUpdate = {
  friendly_name?: string;
  default_scenario?: string | null;
  routing_mode?: "fixed" | "queued";
  enabled?: boolean;
  record_full_calls?: boolean;
};

function NumberCard({
  number,
  scenarios,
  busy,
  onUpdate,
  onQueue,
  onAttach,
  onRestore,
}: {
  number: ManagedNumber;
  scenarios: Scenario[];
  busy: boolean;
  onUpdate: (numberId: string, update: NumberUpdate) => Promise<void>;
  onQueue: (numberId: string, scenario: string) => Promise<void>;
  onAttach: (numberId: string) => Promise<void>;
  onRestore: (numberId: string) => Promise<void>;
}) {
  const [friendlyName, setFriendlyName] = useState(number.friendly_name);
  const [defaultScenario, setDefaultScenario] = useState(
    number.endpoint.default_scenario ?? ""
  );
  const [routingMode, setRoutingMode] = useState(number.endpoint.routing_mode);
  const [recordFullCalls, setRecordFullCalls] = useState(
    number.configuration.record_full_calls === true
  );
  const [queuedScenario, setQueuedScenario] = useState(
    number.endpoint.default_scenario ?? scenarios[0]?.name ?? ""
  );

  const previousVoiceUrl = number.configuration.previous_voice_url;

  return (
    <Card data-testid={`amd-number-${number.phone_number}`} className="bg-card">
      <CardHeader className="gap-4 pb-4">
        <div className="flex flex-col justify-between gap-4 lg:flex-row lg:items-start">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <PhoneCall className="size-4 text-primary" />
              <CardTitle className="text-base">{number.phone_number}</CardTitle>
              <Badge variant={number.attached_to_runtime ? "success" : "warning"}>
                {number.attached_to_runtime ? "Local runtime" : "Hosted runtime"}
              </Badge>
              {!number.endpoint.enabled && <Badge variant="outline">Disabled</Badge>}
              {number.pending_runs > 0 && (
                <Badge variant="info">{number.pending_runs} queued</Badge>
              )}
            </div>
            <p className="mt-2 break-all text-xs text-muted-foreground">
              {number.voice_url || "No Twilio Voice URL configured"}
            </p>
          </div>
          <Button
            type="button"
            size="sm"
            variant={number.endpoint.enabled ? "outline" : "default"}
            disabled={busy}
            onClick={() => onUpdate(number.id, { enabled: !number.endpoint.enabled })}
          >
            {number.endpoint.enabled ? "Disable line" : "Enable line"}
          </Button>
        </div>
      </CardHeader>

      <CardContent className="grid gap-5 pt-0 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <div className="space-y-4 rounded-xl bg-muted/35 p-4">
          <div>
            <p className="text-sm font-medium">Number configuration</p>
            <p className="mt-1 text-xs text-muted-foreground">
              Saved in MySQL and reconciled with the Twilio number.
            </p>
          </div>

          <div className="space-y-2">
            <Label htmlFor={`friendly-${number.id}`}>Twilio name</Label>
            <Input
              id={`friendly-${number.id}`}
              value={friendlyName}
              maxLength={64}
              onChange={(event) => setFriendlyName(event.target.value)}
            />
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor={`default-${number.id}`}>Default scenario</Label>
              <Select value={defaultScenario} onValueChange={setDefaultScenario}>
                <SelectTrigger id={`default-${number.id}`}>
                  <SelectValue placeholder="Select scenario" />
                </SelectTrigger>
                <SelectContent>
                  {scenarios.map((scenario) => (
                    <SelectItem key={scenario.name} value={scenario.name}>
                      {scenario.title}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label htmlFor={`routing-${number.id}`}>Call selection</Label>
              <Select
                value={routingMode}
                onValueChange={(value) =>
                  setRoutingMode(value as "fixed" | "queued")
                }
              >
                <SelectTrigger id={`routing-${number.id}`}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="queued">Queue, then default</SelectItem>
                  <SelectItem value="fixed">Always default</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="rounded-lg bg-background/55 p-3">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2 text-sm font-medium">
                  <AudioLines className="size-4 text-primary" />
                  Record the full call
                </div>
                <p className="mt-1 max-w-xl text-xs leading-5 text-muted-foreground">
                  Captures both sides for playback and debugging. Voicemail scenarios
                  also save the isolated message after the beep automatically.
                </p>
              </div>
              <Button
                type="button"
                size="sm"
                variant={recordFullCalls ? "default" : "outline"}
                aria-pressed={recordFullCalls}
                onClick={() => setRecordFullCalls((current) => !current)}
              >
                {recordFullCalls && <Check />}
                {recordFullCalls ? "Enabled" : "Disabled"}
              </Button>
            </div>
          </div>

          <Button
            type="button"
            disabled={busy || !friendlyName.trim() || !defaultScenario}
            onClick={() =>
              onUpdate(number.id, {
                friendly_name: friendlyName,
                default_scenario: defaultScenario,
                routing_mode: routingMode,
                record_full_calls: recordFullCalls,
              })
            }
          >
            {busy ? <Loader2 className="animate-spin" /> : <Save />}
            Save number
          </Button>
        </div>

        <div className="space-y-4 rounded-xl bg-muted/35 p-4">
          <div>
            <p className="text-sm font-medium">Next live call</p>
            <p className="mt-1 text-xs text-muted-foreground">
              Queue a one-time AMD scenario for the next call to this number.
            </p>
          </div>

          <div className="space-y-2">
            <Label htmlFor={`queue-${number.id}`}>Scenario</Label>
            <Select value={queuedScenario} onValueChange={setQueuedScenario}>
              <SelectTrigger id={`queue-${number.id}`}>
                <SelectValue placeholder="Select scenario" />
              </SelectTrigger>
              <SelectContent>
                {scenarios.map((scenario) => (
                  <SelectItem key={scenario.name} value={scenario.name}>
                    {scenario.title}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <Button
            type="button"
            disabled={
              busy ||
              !queuedScenario ||
              !number.endpoint.enabled ||
              number.endpoint.routing_mode !== "queued" ||
              !number.attached_to_runtime
            }
            onClick={() => onQueue(number.id, queuedScenario)}
          >
            {busy ? <Loader2 className="animate-spin" /> : <Send />}
            Queue next call
          </Button>

          <div className="space-y-3 pt-2">
            {number.attached_to_runtime ? (
              <div className="flex flex-wrap items-center gap-3">
                <Badge variant="success">Webhook connected</Badge>
                {previousVoiceUrl && (
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    disabled={busy}
                    onClick={() => onRestore(number.id)}
                  >
                    <History />
                    Restore hosted Function
                  </Button>
                )}
              </div>
            ) : (
              <Button
                type="button"
                variant="outline"
                disabled={busy || !number.target_voice_url}
                onClick={() => onAttach(number.id)}
              >
                <Link2 />
                Connect to local runtime
                <ArrowRight />
              </Button>
            )}
            {!number.target_voice_url && (
              <p className="text-xs text-warning">
                Start the HTTPS ingress before connecting this line.
              </p>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

export function AmdNumberManager({
  numbers,
  scenarios,
  busy,
  onSync,
  onUpdate,
  onQueue,
  onAttach,
  onRestore,
}: {
  numbers: ManagedNumber[];
  scenarios: Scenario[];
  busy: boolean;
  onSync: () => Promise<void>;
  onUpdate: (numberId: string, update: NumberUpdate) => Promise<void>;
  onQueue: (numberId: string, scenario: string) => Promise<void>;
  onAttach: (numberId: string) => Promise<void>;
  onRestore: (numberId: string) => Promise<void>;
}) {
  return (
    <div className="space-y-5">
      <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-start">
        <div>
          <h2 className="text-xl font-semibold">AMD simulator numbers</h2>
          <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
            These are the dedicated Twilio lines used by the simulator. Names,
            routing, default behavior, live queues, and webhook ownership are
            managed here and persisted in MySQL.
          </p>
        </div>
        <Button type="button" variant="outline" disabled={busy} onClick={onSync}>
          {busy ? <Loader2 className="animate-spin" /> : <CloudDownload />}
          Sync from Twilio
        </Button>
      </div>

      {numbers.length === 0 ? (
        <Card className="bg-muted/35">
          <CardContent className="flex min-h-72 flex-col items-center justify-center p-8 text-center">
            <PhoneCall className="size-7 text-primary" />
            <p className="mt-4 font-medium">Import the four AMD lines</p>
            <p className="mt-1 max-w-lg text-sm text-muted-foreground">
              The import reads the numbers currently attached to the hosted AMD
              Function, creates their MySQL records, and links each one to a
              provider-neutral endpoint.
            </p>
            <Button type="button" className="mt-5" disabled={busy} onClick={onSync}>
              {busy ? <Loader2 className="animate-spin" /> : <CloudDownload />}
              Import AMD numbers
            </Button>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-4">
          {numbers.map((number) => (
            <NumberCard
              key={`${number.id}:${number.updated_at}:${number.pending_runs}`}
              number={number}
              scenarios={scenarios}
              busy={busy}
              onUpdate={onUpdate}
              onQueue={onQueue}
              onAttach={onAttach}
              onRestore={onRestore}
            />
          ))}
        </div>
      )}
    </div>
  );
}
