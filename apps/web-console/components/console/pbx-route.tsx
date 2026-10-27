"use client";

import { useState, type FormEvent } from "react";
import { Loader2, PhoneIncoming, Plus, X } from "lucide-react";

import { EmptyStrip, Field, formatAddress, Panel, PanelTitle } from "@/components/console/kit";
import { ErrorLine, humanize } from "@/components/console/models-timeline";
import { CallView, TransferView, type DealerCall, type DealerTransfer } from "@/components/console/pbx-results";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { useAction } from "@/hooks/use-api";
import {
  DEALER_DAYS,
  DEALER_PRESENCE,
  DEALER_TRANSFERS,
  pbx,
  type DealerCallRequest,
  type DealerMap,
} from "@/lib/workflows-api";

const TYPED = "typed";
const NO_TRANSFER = "none";

type PresenceRow = { key: number; extension: string; state: string };

const parseDigits = (value: string) => value.split(/[\s,]+/).filter(Boolean);

export function PbxRoute({ map }: { map: DealerMap }) {
  const [didChoice, setDidChoice] = useState(map.dids[0]?.number ?? TYPED);
  const [typedNumber, setTypedNumber] = useState("");
  const [fromNumber, setFromNumber] = useState("+14085550131");
  const [day, setDay] = useState("tue");
  const [time, setTime] = useState("10:30");
  const [date, setDate] = useState("");
  const [digits, setDigits] = useState("");
  const [intent, setIntent] = useState("");
  const [patience, setPatience] = useState("600");
  const [presence, setPresence] = useState<PresenceRow[]>([]);
  const [occupied, setOccupied] = useState<Record<string, string>>({});
  const [glare, setGlare] = useState<string[]>([]);
  const [method, setMethod] = useState(NO_TRANSFER);
  const [target, setTarget] = useState("");
  const [agent, setAgent] = useState(map.agents.find((item) => item.id === "bdc-overflow")?.id ?? map.agents[0]?.id ?? "");
  const [result, setResult] = useState<{ call: DealerCall; transfer?: DealerTransfer } | null>(null);
  const action = useAction();

  const toNumber = didChoice === TYPED ? typedNumber.trim() : didChoice;
  const digitList = parseDigits(digits);
  const occupiedEntries = Object.entries(occupied).filter(([, value]) => value.trim() !== "");
  const problems = [
    toNumber.length < 3 && "Enter the dialed number.",
    fromNumber.trim().length < 3 && "Enter the caller number.",
    !/^\d{1,2}:\d{2}$/.test(time) && "Time must be HH:MM.",
    date.trim() && !/^\d{2}-\d{2}$/.test(date.trim()) && "Date must be MM-DD.",
    digitList.some((entry) => !/^[0-9*#]+$/.test(entry)) && "Menu digits may only contain 0-9, * and #.",
    !/^\d+$/.test(patience.trim()) && "Patience must be whole seconds.",
    presence.some((row) => !/^\d+$/.test(row.extension.trim())) && "Each presence override needs an extension number.",
    occupiedEntries.some(([, value]) => !/^\d+$/.test(value.trim())) && "Occupied channels must be whole numbers.",
    method !== NO_TRANSFER && !target.trim() && "A transfer needs a target, for example ext:1200.",
  ].filter(Boolean) as string[];

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (problems.length) return;
    const body: DealerCallRequest = {
      to_number: toNumber,
      from_number: fromNumber.trim(),
      day,
      time,
      date: date.trim() || null,
      digits: digitList,
      intent: intent.trim(),
      patience_seconds: Number(patience),
      presence: Object.fromEntries(presence.map((row) => [row.extension.trim(), row.state])),
      occupied_channels: Object.fromEntries(
        occupiedEntries.map(([trunk, value]) => [trunk, Number(value)] as const).filter(([, count]) => count > 0),
      ),
      glare_trunks: glare,
      transfer_method: method === NO_TRANSFER ? null : method,
      transfer_target: method === NO_TRANSFER ? null : target.trim(),
      ...(method !== NO_TRANSFER && agent ? { transfer_agent: agent } : {}),
    };
    const routed = await action.run(() => pbx.route(body));
    if (routed) setResult(routed as unknown as { call: DealerCall; transfer?: DealerTransfer });
  }

  const glareTrunks = map.trunks.filter((trunk) => trunk.glare_prone);

  return (
    <div className="grid gap-4 lg:grid-cols-[400px_minmax(0,1fr)]">
      <Panel className="h-fit">
        <PanelTitle>Route a call</PanelTitle>
        <form className="grid gap-4" onSubmit={submit}>
          <Field id="pbx-to" label="Dialed number">
            <Select value={didChoice} onValueChange={setDidChoice}>
              <SelectTrigger id="pbx-to"><SelectValue /></SelectTrigger>
              <SelectContent>
                {map.dids.map((did) => (
                  <SelectItem key={did.number} value={did.number}>{formatAddress(did.number)} · {did.label}</SelectItem>
                ))}
                <SelectItem value={TYPED}>Another number</SelectItem>
              </SelectContent>
            </Select>
          </Field>
          {didChoice === TYPED && (
            <Field id="pbx-to-typed" label="Number to dial">
              <Input id="pbx-to-typed" value={typedNumber} onChange={(event) => setTypedNumber(event.target.value)} className="font-mono" />
            </Field>
          )}
          <Field id="pbx-from" label="From number">
            <Input id="pbx-from" value={fromNumber} onChange={(event) => setFromNumber(event.target.value)} className="font-mono" />
          </Field>
          <div className="grid grid-cols-3 gap-3">
            <Field id="pbx-day" label="Day">
              <Select value={day} onValueChange={setDay}>
                <SelectTrigger id="pbx-day"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {DEALER_DAYS.map((item) => <SelectItem key={item} value={item}>{item}</SelectItem>)}
                </SelectContent>
              </Select>
            </Field>
            <Field id="pbx-time" label="Time">
              <Input id="pbx-time" type="time" value={time} onChange={(event) => setTime(event.target.value)} />
            </Field>
            <Field id="pbx-date" label="Date">
              <Input id="pbx-date" value={date} onChange={(event) => setDate(event.target.value)} placeholder="MM-DD" className="font-mono" />
            </Field>
          </div>
          <Field id="pbx-digits" label="Menu digits" help="One entry per menu level, separated by spaces or commas.">
            <Input id="pbx-digits" value={digits} onChange={(event) => setDigits(event.target.value)} className="font-mono" placeholder="2 1200" />
          </Field>
          <div className="grid grid-cols-[minmax(0,1fr)_110px] gap-3">
            <Field id="pbx-intent" label="Intent">
              <Input id="pbx-intent" value={intent} onChange={(event) => setIntent(event.target.value)} />
            </Field>
            <Field id="pbx-patience" label="Patience (s)">
              <Input id="pbx-patience" inputMode="numeric" value={patience} onChange={(event) => setPatience(event.target.value)} className="t-num" />
            </Field>
          </div>

          <div className="grid gap-2">
            <div className="flex items-center justify-between gap-3">
              <h3 className="text-[13px] font-medium">Presence overrides</h3>
              <Button
                type="button"
                variant="ghost"
                size="xs"
                onClick={() => setPresence((rows) => [...rows, { key: Date.now(), extension: "", state: "busy" }])}
              >
                <Plus />
                Add extension
              </Button>
            </div>
            {presence.map((row, index) => (
              <div key={row.key} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] items-end gap-2">
                <Field id={`pbx-presence-ext-${row.key}`} label={`Extension ${index + 1}`}>
                  <Input
                    id={`pbx-presence-ext-${row.key}`}
                    inputMode="numeric"
                    value={row.extension}
                    placeholder="1200"
                    className="font-mono"
                    onChange={(event) => setPresence((rows) => rows.map((item) => item.key === row.key ? { ...item, extension: event.target.value } : item))}
                  />
                </Field>
                <Field id={`pbx-presence-state-${row.key}`} label="State">
                  <Select
                    value={row.state}
                    onValueChange={(state) => setPresence((rows) => rows.map((item) => item.key === row.key ? { ...item, state } : item))}
                  >
                    <SelectTrigger id={`pbx-presence-state-${row.key}`}><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {DEALER_PRESENCE.map((state) => <SelectItem key={state} value={state}>{humanize(state)}</SelectItem>)}
                    </SelectContent>
                  </Select>
                </Field>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  aria-label={`Remove extension ${index + 1}`}
                  onClick={() => setPresence((rows) => rows.filter((item) => item.key !== row.key))}
                >
                  <X />
                </Button>
              </div>
            ))}
          </div>

          <div className="grid gap-2">
            <h3 className="text-[13px] font-medium">Occupied trunk channels</h3>
            <div className="grid grid-cols-2 gap-3">
              {map.trunks.map((trunk) => (
                <Field key={trunk.id} id={`pbx-occupied-${trunk.id}`} label={`${trunk.id} (of ${trunk.channels})`}>
                  <Input
                    id={`pbx-occupied-${trunk.id}`}
                    inputMode="numeric"
                    className="t-num"
                    placeholder="0"
                    value={occupied[trunk.id] ?? ""}
                    onChange={(event) => setOccupied((current) => ({ ...current, [trunk.id]: event.target.value }))}
                  />
                </Field>
              ))}
            </div>
          </div>

          {glareTrunks.length > 0 && (
            <div className="grid gap-2">
              <h3 className="text-[13px] font-medium">Glare on seize</h3>
              {glareTrunks.map((trunk) => (
                <div key={trunk.id} className="flex items-center justify-between gap-3">
                  <Label htmlFor={`pbx-glare-${trunk.id}`} className="font-normal">{trunk.label}</Label>
                  <Switch
                    id={`pbx-glare-${trunk.id}`}
                    checked={glare.includes(trunk.id)}
                    onChange={(on) => setGlare((current) => on ? [...current, trunk.id] : current.filter((id) => id !== trunk.id))}
                  />
                </div>
              ))}
            </div>
          )}

          <div className="grid gap-3">
            <h3 className="text-[13px] font-medium">Agent transfer</h3>
            <div className="grid grid-cols-2 gap-3">
              <Field id="pbx-method" label="Method">
                <Select value={method} onValueChange={setMethod}>
                  <SelectTrigger id="pbx-method"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value={NO_TRANSFER}>No transfer</SelectItem>
                    {DEALER_TRANSFERS.map((item) => <SelectItem key={item} value={item}>{humanize(item)}</SelectItem>)}
                  </SelectContent>
                </Select>
              </Field>
              <Field id="pbx-target" label="Target">
                <Input
                  id="pbx-target"
                  value={target}
                  disabled={method === NO_TRANSFER}
                  onChange={(event) => setTarget(event.target.value)}
                  placeholder="ext:1200"
                  className="font-mono"
                />
              </Field>
            </div>
            {method !== NO_TRANSFER && map.agents.length > 0 && (
              <Field id="pbx-agent" label="Agent">
                <Select value={agent} onValueChange={setAgent}>
                  <SelectTrigger id="pbx-agent"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {map.agents.map((item) => <SelectItem key={item.id} value={item.id}>{item.label}</SelectItem>)}
                  </SelectContent>
                </Select>
              </Field>
            )}
          </div>

          {problems.length > 0 && <p className="text-xs text-warning">{problems[0]}</p>}
          <ErrorLine message={action.error} />
          <div>
            <Button size="sm" disabled={action.pending || problems.length > 0}>
              {action.pending ? <Loader2 className="animate-spin" /> : <PhoneIncoming />}
              Route call
            </Button>
          </div>
        </form>
      </Panel>

      <div className="grid h-fit gap-4" aria-live="polite">
        {result ? (
          <>
            <Panel>
              <PanelTitle aside={result.call.site ?? undefined}>Routed call</PanelTitle>
              <CallView call={result.call} />
            </Panel>
            {result.transfer && (
              <Panel>
                <PanelTitle>Transfer</PanelTitle>
                <TransferView transfer={result.transfer} />
              </Panel>
            )}
          </>
        ) : (
          <EmptyStrip title="No call routed yet." body="The outcome, call path and every switch event appear here." />
        )}
      </div>
    </div>
  );
}
