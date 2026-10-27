"use client";

import { formatDuration, MISSING } from "@/components/console/kit";
import { Facts, humanize, OutcomePill, StepList, type ModelStep } from "@/components/console/models-timeline";

/** `CallResult.as_dict()` from domains/pbx/dealer/router.py. */
export type DealerCall = {
  outcome: string;
  answered_by: string | null;
  answered_by_label: string | null;
  site: string | null;
  path: string[];
  duration_seconds: number;
  notes: string[];
  cdr: Record<string, unknown>;
  timeline: ModelStep[];
};

/** `TransferResult.as_dict()` from domains/pbx/dealer/transfer.py. */
export type DealerTransfer = {
  outcome: string;
  accepted: boolean;
  reason: string;
  method: string;
  target: string;
  recoverable: boolean;
  notes: string[];
  alternatives: string[];
  timeline: ModelStep[];
  target_leg: DealerCall | null;
};

/** `run_case` from domains/pbx/dealer/cases.py. */
export type DealerCaseRun = {
  id: string;
  title: string;
  teaches: string;
  expected_outcome: string;
  actual_outcome: string;
  expected_transfer: string | null;
  actual_transfer: string | null;
  passed: boolean;
  call: DealerCall;
  transfer: DealerTransfer | null;
};

function Notes({ notes }: { notes: string[] }) {
  if (!notes.length) return null;
  return (
    <ul className="grid gap-1 rounded-lg bg-surface-1 px-3 py-2.5 text-[13px] leading-5">
      {notes.map((note) => <li key={note}>{note}</li>)}
    </ul>
  );
}

export function CallView({ call, label = "Call timeline" }: { call: DealerCall; label?: string }) {
  const cdr = call.cdr;
  const trunks = Array.isArray(cdr.trunks) ? (cdr.trunks as string[]).join(", ") : null;
  return (
    <div className="grid gap-4">
      <Facts
        items={[
          ["Outcome", <OutcomePill key="o" outcome={call.outcome} />],
          ["Answered by", call.answered_by_label ? `${call.answered_by_label}${call.answered_by ? ` (${call.answered_by})` : ""}` : MISSING],
          ["Site", call.site ?? MISSING],
          ["Duration", <span key="d" className="t-num">{formatDuration(call.duration_seconds)}</span>],
          ["Path", call.path.length ? <span key="p" className="t-num text-xs">{call.path.join(" → ")}</span> : MISSING],
          ["Trunks", trunks || MISSING],
          ["Carrier channels", <span key="c" className="t-num">{String(cdr.carrier_channels_used ?? MISSING)}</span>],
          ["Hops", <span key="h" className="t-num">{String(cdr.hops ?? MISSING)}</span>],
          ["Disposition", String(cdr.disposition ?? MISSING)],
          ...(cdr.tracking_source ? [["Tracking source", String(cdr.tracking_source)] as [string, string]] : []),
        ]}
      />
      <Notes notes={call.notes} />
      <StepList steps={call.timeline} label={label} />
    </div>
  );
}

export function TransferView({ transfer }: { transfer: DealerTransfer }) {
  return (
    <div className="grid gap-4">
      <Facts
        items={[
          ["Outcome", <OutcomePill key="o" outcome={transfer.outcome} />],
          ["Method", humanize(transfer.method)],
          ["Target", <span key="t" className="t-num text-xs">{transfer.target}</span>],
          ["Accepted", transfer.accepted ? "Yes" : "No"],
          ["Recoverable", transfer.recoverable ? "Yes" : "No"],
          ["Reason", transfer.reason || MISSING],
        ]}
      />
      {transfer.alternatives.length > 0 && (
        <div className="grid gap-1.5">
          <h4 className="text-[13px] font-medium">Alternatives</h4>
          <ul className="grid list-disc gap-1 pl-5 text-[13px] leading-5 text-muted-foreground">
            {transfer.alternatives.map((item) => <li key={item}>{item}</li>)}
          </ul>
        </div>
      )}
      <StepList steps={transfer.timeline} label="Transfer timeline" />
      {transfer.target_leg && (
        <div className="grid gap-3">
          <h4 className="text-[13px] font-medium">Target leg</h4>
          <CallView call={transfer.target_leg} label="Target leg timeline" />
        </div>
      )}
    </div>
  );
}
