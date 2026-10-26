"use client";

import { DataTable, formatAddress, MISSING, Panel, PanelTitle, Td, Th, TonePill } from "@/components/console/kit";
import { Facts, humanize } from "@/components/console/models-timeline";
import type { DealerMap } from "@/lib/workflows-api";

/** One rooftop's row of `transfer_matrix` (domains/pbx/dealer/transfer.py). */
type MatrixRow = {
  site: string;
  platform: string;
  border_element: string | null;
  blind_refer: boolean;
  attended_refer: boolean;
  bridge: boolean;
  dtmf_redial: boolean;
  external_transfer: boolean;
  bare_extensions_from_trunk: boolean;
};

const MATRIX_COLUMNS: Array<[keyof MatrixRow, string]> = [
  ["blind_refer", "Blind REFER"],
  ["attended_refer", "Attended REFER"],
  ["bridge", "Bridge"],
  ["dtmf_redial", "DTMF redial"],
  ["external_transfer", "External transfer"],
  ["bare_extensions_from_trunk", "Extensions from trunk"],
];

function YesNo({ value }: { value: boolean }) {
  return <TonePill tone={value ? "success" : "danger"}>{value ? "Yes" : "No"}</TonePill>;
}

export function PbxMap({ map }: { map: DealerMap }) {
  const matrix = Object.entries((map.transfer_matrix ?? {}) as Record<string, MatrixRow>);
  return (
    <div className="grid gap-6">
      <p className="t-num text-[13px] text-muted-foreground">
        {map.group} · intersite prefix {map.intersite_prefix}
      </p>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {map.sites.map((site) => (
          <Panel key={site.id}>
            <PanelTitle aside={site.id}>{site.label}</PanelTitle>
            <Facts
              items={[
                ["Platform", site.platform],
                ["Border", site.border_element || MISSING],
                ["Main DID", <span key="d" className="t-num">{formatAddress(site.main_did)}</span>],
                ["Extensions", <span key="e" className="t-num">{site.extension_range}</span>],
                ["Attendant", site.attendant || MISSING],
                ["Night target", <span key="n" className="t-num text-xs">{site.night_target || MISSING}</span>],
                ["Trunks", <span key="t" className="t-num text-xs">{site.trunks.join(", ") || MISSING}</span>],
              ]}
            />
            {site.notes && <p className="mt-3 text-[13px] leading-5 text-muted-foreground">{site.notes}</p>}
          </Panel>
        ))}
      </div>

      <section className="grid gap-2">
        <h2 className="text-sm font-semibold">Transfer matrix</h2>
        <DataTable label="Transfer methods each site supports">
          <thead>
            <tr>
              <Th>Site</Th>
              {MATRIX_COLUMNS.map(([key, label]) => <Th key={key}>{label}</Th>)}
            </tr>
          </thead>
          <tbody>
            {matrix.map(([id, row]) => (
              <tr key={id} className="even:bg-control/40">
                <Td>
                  <span className="font-medium">{row.site}</span>
                  <span className="block text-xs text-muted-foreground">{row.border_element ?? row.platform}</span>
                </Td>
                {MATRIX_COLUMNS.map(([key]) => <Td key={key}><YesNo value={Boolean(row[key])} /></Td>)}
              </tr>
            ))}
          </tbody>
        </DataTable>
      </section>

      <section className="grid gap-2">
        <h2 className="text-sm font-semibold">Trunks</h2>
        <DataTable label="Trunks">
          <thead>
            <tr>
              <Th>Trunk</Th>
              <Th>Site</Th>
              <Th>Kind</Th>
              <Th>Signaling</Th>
              <Th className="text-right">Channels</Th>
              <Th>DTMF</Th>
              <Th>Glare</Th>
            </tr>
          </thead>
          <tbody>
            {map.trunks.map((trunk) => (
              <tr key={trunk.id} className="even:bg-control/40">
                <Td>
                  <span className="font-medium">{trunk.label}</span>
                  <span className="t-num block text-xs text-muted-foreground">{trunk.id}</span>
                </Td>
                <Td className="text-xs">{trunk.peer_site ? `${trunk.site} to ${trunk.peer_site}` : trunk.site}</Td>
                <Td className="text-xs">{trunk.kind}</Td>
                <Td className="text-xs">{humanize(trunk.signaling)}</Td>
                <Td className="t-num text-right text-xs">{trunk.channels}</Td>
                <Td className="text-xs">{trunk.dtmf_mode}</Td>
                <Td>{trunk.glare_prone ? <TonePill tone="warning">Glare prone</TonePill> : <span className="text-xs text-muted-foreground">No</span>}</Td>
              </tr>
            ))}
          </tbody>
        </DataTable>
      </section>

      <section className="grid gap-2">
        <h2 className="text-sm font-semibold">DIDs</h2>
        <DataTable label="DIDs">
          <thead>
            <tr>
              <Th>Number</Th>
              <Th>Label</Th>
              <Th>Site</Th>
              <Th>Target</Th>
              <Th>Tracking source</Th>
            </tr>
          </thead>
          <tbody>
            {map.dids.map((did) => (
              <tr key={did.number} className="even:bg-control/40">
                <Td className="t-num text-xs">{formatAddress(did.number)}</Td>
                <Td className="font-medium">{did.label}</Td>
                <Td className="text-xs">{did.site}</Td>
                <Td className="t-num text-xs">{did.target}</Td>
                <Td className="text-xs text-muted-foreground">{did.tracking_source ?? MISSING}</Td>
              </tr>
            ))}
          </tbody>
        </DataTable>
      </section>

      <section className="grid gap-2">
        <h2 className="text-sm font-semibold">Agents</h2>
        <DataTable label="Agents">
          <thead>
            <tr>
              <Th>Agent</Th>
              <Th>Site</Th>
              <Th>Ingress</Th>
              <Th>Address</Th>
              <Th>Reached via</Th>
              <Th>Diversion</Th>
            </tr>
          </thead>
          <tbody>
            {map.agents.map((agent) => (
              <tr key={agent.id} className="even:bg-control/40 align-top">
                <Td className="py-2">
                  <span className="font-medium">{agent.label}</span>
                  <span className="t-num block text-xs text-muted-foreground">{agent.id}</span>
                  {agent.notes && <span className="mt-1 block max-w-[360px] text-xs leading-5 text-muted-foreground">{agent.notes}</span>}
                </Td>
                <Td className="py-2 text-xs">{agent.site}</Td>
                <Td className="py-2 text-xs">{humanize(agent.ingress)}</Td>
                <Td className="t-num py-2 text-xs">{formatAddress(agent.address)}</Td>
                <Td className="t-num py-2 text-xs">{agent.reached_via}</Td>
                <Td className="py-2 text-xs">{agent.receives_diversion ? "Yes" : "No"}</Td>
              </tr>
            ))}
          </tbody>
        </DataTable>
      </section>
    </div>
  );
}
