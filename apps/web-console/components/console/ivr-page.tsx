"use client";

import { useState, type FormEvent } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Loader2, PhoneIncoming } from "lucide-react";

import { useConsole } from "@/components/console/console-provider";
import {
  ConsoleErrors,
  DataTable,
  EmptyStrip,
  Field,
  formatAddress,
  formatDuration,
  formatWhen,
  LinkRow,
  MISSING,
  PageFrame,
  PageHeader,
  Panel,
  PanelTitle,
  RowLink,
  Td,
  Th,
  TonePill,
} from "@/components/console/kit";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

type Disposition = "connected" | "busy" | "no_answer" | "failed" | "abandoned";

const DISPOSITIONS: Array<{ id: Disposition; label: string }> = [
  { id: "connected", label: "Answers" },
  { id: "busy", label: "Busy" },
  { id: "no_answer", label: "No answer" },
  { id: "failed", label: "Fails" },
  { id: "abandoned", label: "Caller abandons" },
];

function outcomeTone(outcome: string): "success" | "warning" | "danger" | "neutral" {
  if (["connected", "bridged", "completed"].includes(outcome)) return "success";
  if (["busy", "no_answer"].includes(outcome)) return "warning";
  if (["failed", "abandoned"].includes(outcome)) return "danger";
  return "neutral";
}

export function IvrPage() {
  const router = useRouter();
  const { data, results, busy, loading, actions } = useConsole();
  const usable = data.endpoints.filter((endpoint) => endpoint.enabled);
  const [endpointId, setEndpointId] = useState("");
  const [caller, setCaller] = useState("+14085550131");
  const [extension, setExtension] = useState(data.directory[0]?.extension ?? "1501");
  const [disposition, setDisposition] = useState<Disposition>("connected");
  const endpoint = usable.find((item) => item.id === endpointId)
    ?? usable.find((item) => item.provider === "mock") ?? usable[0];
  const connection = data.connections.find((item) => item.id === endpoint?.connection_id);
  const available = Boolean(endpoint && connection?.enabled !== false);
  const valid = /^[0-9]{4}$/.test(extension) && caller.trim().length >= 5;

  const recent = results.filter((result) => result.run && (result.run.extension || result.run.result.extension));

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!endpoint) return;
    const runId = await actions.createIvrSimulation({
      endpoint_id: endpoint.id,
      caller_number: caller,
      extension,
      disposition,
    });
    if (runId) router.push(`/console/result/?id=${encodeURIComponent(runId)}`);
  }

  return (
    <PageFrame>
      <PageHeader title="IVR lab" count={loading ? null : recent.length} />
      <ConsoleErrors />
      <p className="max-w-2xl text-[13px] leading-5 text-muted-foreground">
        Dial any extension in the directory and force how its destination responds. The same directory drives the
        live Twilio IVR at <code className="t-num text-xs">/twilio/ivr</code>.
      </p>

      <div className="grid gap-4 lg:grid-cols-[360px_minmax(0,1fr)]">
        <Panel className="h-fit">
          <PanelTitle>Simulate an inbound call</PanelTitle>
          {usable.length ? (
            <form className="grid gap-4" onSubmit={submit}>
              <Field id="ivr-line" label="Line">
                <Select value={endpoint?.id} onValueChange={setEndpointId}>
                  <SelectTrigger id="ivr-line"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {usable.map((item) => (
                      <SelectItem key={item.id} value={item.id}>{item.name} · {formatAddress(item.address)}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
              <Field id="ivr-caller" label="Caller number">
                <Input id="ivr-caller" required minLength={5} maxLength={40} value={caller} onChange={(event) => setCaller(event.target.value)} className="font-mono" />
              </Field>
              <div className="grid grid-cols-2 gap-3">
                <Field id="ivr-extension" label="Extension">
                  <Select value={extension} onValueChange={setExtension}>
                    <SelectTrigger id="ivr-extension"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {data.directory.map((entry) => (
                        <SelectItem key={entry.id} value={entry.extension}>{entry.extension} · {entry.name}</SelectItem>
                      ))}
                      {!data.directory.some((entry) => entry.extension === "9999") && (
                        <SelectItem value="9999">9999 · Unknown</SelectItem>
                      )}
                    </SelectContent>
                  </Select>
                </Field>
                <Field id="ivr-disposition" label="Destination">
                  <Select value={disposition} onValueChange={(value) => setDisposition(value as Disposition)}>
                    <SelectTrigger id="ivr-disposition"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {DISPOSITIONS.map((item) => <SelectItem key={item.id} value={item.id}>{item.label}</SelectItem>)}
                    </SelectContent>
                  </Select>
                </Field>
              </div>
              {!available && <p className="text-xs text-warning">Enable this line and its provider first.</p>}
              <div>
                <Button size="sm" disabled={busy || !available || !valid}>
                  {busy ? <Loader2 className="animate-spin" /> : <PhoneIncoming />}
                  Simulate call
                </Button>
              </div>
            </form>
          ) : (
            <p className="text-[13px] text-muted-foreground">
              Add an enabled line in <Link className="text-accent-text hover:underline" href="/console/lines">Lines</Link> first.
            </p>
          )}
        </Panel>

        {recent.length ? (
          <DataTable label="Recent IVR calls">
            <thead>
              <tr>
                <Th>Caller</Th>
                <Th className="w-[90px]">Extension</Th>
                <Th>Destination</Th>
                <Th className="w-[120px]">Outcome</Th>
                <Th className="w-[70px] text-right">Length</Th>
                <Th className="w-[118px]">When</Th>
              </tr>
            </thead>
            <tbody>
              {recent.map((result) => {
                const run = result.run!;
                const outcome = run.outcome ?? run.result.call_outcome ?? run.result.outcome ?? run.status;
                const href = `/console/result/?id=${encodeURIComponent(result.id)}`;
                return (
                  <LinkRow key={result.id} href={href}>
                    <Td><RowLink href={href} className="t-num text-xs">{formatAddress(run.caller_number ?? run.result.caller_number)}</RowLink></Td>
                    <Td className="t-num text-xs">{run.extension ?? run.result.extension ?? MISSING}</Td>
                    <Td className="t-num text-xs text-muted-foreground">{formatAddress(run.destination ?? run.result.destination)}</Td>
                    <Td><TonePill tone={outcomeTone(outcome)}>{outcome.replaceAll("_", " ")}</TonePill></Td>
                    <Td className="t-num text-right text-xs text-muted-foreground">{formatDuration(result.durationS)}</Td>
                    <Td className="t-num whitespace-nowrap text-xs text-muted-foreground">{formatWhen(result.at)}</Td>
                  </LinkRow>
                );
              })}
            </tbody>
          </DataTable>
        ) : (
          <div className="h-fit">
            <EmptyStrip title="No IVR calls yet." body="Simulated calls appear here with their route and outcome." />
          </div>
        )}
      </div>
    </PageFrame>
  );
}
