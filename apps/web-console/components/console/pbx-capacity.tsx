"use client";

import { useState, type FormEvent } from "react";
import { Loader2 } from "lucide-react";

import { DataTable, Field, MISSING, Panel, PanelTitle, Td, Th } from "@/components/console/kit";
import { ErrorLine, Facts, humanize } from "@/components/console/models-timeline";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useApi } from "@/hooks/use-api";
import { pbx, type DealerMap } from "@/lib/workflows-api";

/** `capacity_report` from domains/pbx/dealer/capacity.py. */
type CapacityReport = {
  site: string;
  platform: string;
  carrier_channels: number;
  rows: Array<{
    ingress: string;
    label: string;
    inside_dial_plan: boolean;
    channels_per_call: number;
    channels_per_call_with_handoff: number;
    concurrent_agent_calls: number | null;
    concurrent_agent_calls_with_handoff: number | null;
    limited_by: string;
    explanation: string;
  }>;
  headline: {
    forwarding_concurrent_calls: number;
    peering_concurrent_calls: number;
    capacity_multiplier: number;
    forwarding_concurrent_calls_with_handoff: number;
    handoff_multiplier: number;
  };
};

const ALL = "all";

const count = (value: number | null) => (value == null ? "No trunk limit" : String(value));

export function PbxCapacity({ map }: { map: DealerMap }) {
  const [site, setSite] = useState(ALL);
  const [channelsInput, setChannelsInput] = useState("");
  const [channels, setChannels] = useState<number | undefined>(undefined);
  const reports = useApi(
    (signal) =>
      pbx.capacity(site === ALL ? undefined : site, channels, signal).then((value) =>
        (Array.isArray(value) ? value : [value]) as unknown as CapacityReport[],
      ),
    [site, channels],
  );
  const channelsValid = channelsInput.trim() === "" || /^[1-9]\d*$/.test(channelsInput.trim());

  function apply(event: FormEvent) {
    event.preventDefault();
    if (!channelsValid) return;
    setChannels(channelsInput.trim() ? Number(channelsInput.trim()) : undefined);
  }

  return (
    <div className="grid gap-4">
      <form className="flex flex-wrap items-end gap-3" onSubmit={apply}>
        <Field id="pbx-capacity-site" label="Site" className="w-full sm:w-64">
          <Select value={site} onValueChange={setSite}>
            <SelectTrigger id="pbx-capacity-site"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>All sites</SelectItem>
              {map.sites.map((item) => <SelectItem key={item.id} value={item.id}>{item.label}</SelectItem>)}
            </SelectContent>
          </Select>
        </Field>
        <Field id="pbx-capacity-channels" label="Carrier channels" className="w-40">
          <Input
            id="pbx-capacity-channels"
            inputMode="numeric"
            className="t-num"
            placeholder="Modelled"
            value={channelsInput}
            onChange={(event) => setChannelsInput(event.target.value)}
          />
        </Field>
        <Button size="sm" variant="secondary" disabled={!channelsValid || reports.loading}>
          {reports.loading && <Loader2 className="animate-spin" />}
          Apply
        </Button>
        {!channelsValid && <p className="text-xs text-warning">Channels must be a whole number of at least 1.</p>}
      </form>

      <div aria-live="polite" className="grid gap-4">
        <ErrorLine message={reports.error} />
        {(reports.data ?? []).map((report) => (
          <Panel key={report.site}>
            <PanelTitle aside={`${report.carrier_channels} carrier channels`}>{report.site}</PanelTitle>
            <Facts
              className="mb-4"
              items={[
                ["Platform", report.platform],
                ["Forwarding", <span key="f" className="t-num">{report.headline.forwarding_concurrent_calls} concurrent calls ({report.headline.forwarding_concurrent_calls_with_handoff} with handoff)</span>],
                ["Trunk peering", <span key="p" className="t-num">{report.headline.peering_concurrent_calls} concurrent calls</span>],
                ["Peering gain", <span key="m" className="t-num">{report.headline.capacity_multiplier}x, {report.headline.handoff_multiplier}x with handoff</span>],
              ]}
            />
            <DataTable label={`Capacity at ${report.site}`} className="bg-surface-1">
              <thead>
                <tr>
                  <Th>Ingress pattern</Th>
                  <Th>Dial plan</Th>
                  <Th className="text-right">Channels per call</Th>
                  <Th className="text-right">With handoff</Th>
                  <Th className="text-right">Concurrent calls</Th>
                  <Th className="text-right">With handoff</Th>
                  <Th>Limited by</Th>
                </tr>
              </thead>
              <tbody>
                {report.rows.map((row) => (
                  <tr key={row.ingress} className="align-top even:bg-control/40">
                    <Td className="py-2">
                      <span className="font-medium">{row.label}</span>
                      <span className="t-num block text-xs text-subtle-foreground">{humanize(row.ingress)}</span>
                      <span className="mt-1 block max-w-[420px] text-xs leading-5 text-muted-foreground">{row.explanation}</span>
                    </Td>
                    <Td className="py-2 text-xs">{row.inside_dial_plan ? "Inside" : "Outside"}</Td>
                    <Td className="t-num py-2 text-right text-xs">{row.channels_per_call ?? MISSING}</Td>
                    <Td className="t-num py-2 text-right text-xs">{row.channels_per_call_with_handoff ?? MISSING}</Td>
                    <Td className="t-num py-2 text-right text-xs">{count(row.concurrent_agent_calls)}</Td>
                    <Td className="t-num py-2 text-right text-xs">{count(row.concurrent_agent_calls_with_handoff)}</Td>
                    <Td className="py-2 text-xs">{row.limited_by}</Td>
                  </tr>
                ))}
              </tbody>
            </DataTable>
          </Panel>
        ))}
      </div>
    </div>
  );
}
