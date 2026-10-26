"use client";

import { useState } from "react";
import Link from "next/link";

import { PointForm, RevertForm } from "@/components/console/account-numbers-change";
import { ActionError } from "@/components/console/hosted-parts";
import { DataTable, EmptyStrip, formatAddress, MISSING, PageFrame, PageHeader, Td, Th, useConfirm } from "@/components/console/kit";
import { Button } from "@/components/ui/button";
import { useApi } from "@/hooks/use-api";
import { accountNumbers } from "@/lib/workflows-api";
import { cn } from "@/lib/utils";

/** A webhook URL without its scheme, truncated; the full URL is in the title. */
function UrlCell({ url }: { url: string }) {
  if (!url) return <span className="text-subtle-foreground">{MISSING}</span>;
  return (
    <span title={url} className="block max-w-[280px] truncate font-mono text-xs">
      {url.replace(/^https?:\/\//, "")}
    </span>
  );
}

export function AccountNumbersPage() {
  const numbers = useApi((signal) => accountNumbers.list(signal), []);
  const backups = useApi((signal) => accountNumbers.backups(signal), []);
  const { ask, dialog } = useConfirm();
  const [open, setOpen] = useState<{ sid: string; kind: "point" | "revert" } | null>(null);
  const selected = numbers.data?.find((item) => item.sid === open?.sid) ?? null;

  function refresh() {
    numbers.reload();
    backups.reload();
  }

  return (
    <PageFrame>
      <PageHeader title="Account numbers" count={numbers.data?.length} freshness={false}>
        <Button variant="secondary" size="sm" disabled={numbers.loading} onClick={refresh}>Refresh</Button>
      </PageHeader>
      <p className="max-w-2xl text-[13px] leading-5 text-muted-foreground">
        Every number on the Twilio account. The managed AMD numbers are configured on{" "}
        <Link href="/console/lines" className="text-accent-text hover:underline">Lines</Link>.
      </p>
      <ActionError message={numbers.error} />

      {selected && open?.kind === "point" && (
        <PointForm key={`point-${selected.sid}`} number={selected} ask={ask} onApplied={refresh} onClose={() => setOpen(null)} />
      )}
      {selected && open?.kind === "revert" && (
        <RevertForm
          key={`revert-${selected.sid}`}
          number={selected}
          backups={backups.data ?? []}
          ask={ask}
          onApplied={refresh}
          onClose={() => setOpen(null)}
        />
      )}

      {numbers.data?.length ? (
        <DataTable label="Twilio account numbers">
          <thead>
            <tr>
              <Th className="w-[150px]">Number</Th>
              <Th>Friendly name</Th>
              <Th>Voice URL</Th>
              <Th>Status callback</Th>
              <Th className="w-[150px]"><span className="sr-only">Actions</span></Th>
            </tr>
          </thead>
          <tbody>
            {numbers.data.map((item) => {
              const active = open?.sid === item.sid;
              return (
                <tr key={item.sid} className={cn("even:bg-control/40", active && "bg-accent-soft even:bg-accent-soft")}>
                  <Td className="t-num whitespace-nowrap text-xs font-medium">{formatAddress(item.phone_number)}</Td>
                  <Td className="max-w-[200px] truncate text-xs">{item.friendly_name || MISSING}</Td>
                  <Td><UrlCell url={item.voice_url} /></Td>
                  <Td><UrlCell url={item.status_callback} /></Td>
                  <Td>
                    <div className="flex justify-end gap-1">
                      <Button
                        variant="ghost"
                        size="xs"
                        aria-pressed={active && open?.kind === "point"}
                        onClick={() => setOpen({ sid: item.sid, kind: "point" })}
                      >
                        Point
                      </Button>
                      <Button
                        variant="ghost"
                        size="xs"
                        aria-pressed={active && open?.kind === "revert"}
                        disabled={backups.loading && !backups.data}
                        onClick={() => setOpen({ sid: item.sid, kind: "revert" })}
                      >
                        Revert
                      </Button>
                    </div>
                  </Td>
                </tr>
              );
            })}
          </tbody>
        </DataTable>
      ) : numbers.loading ? null : numbers.error ? null : (
        <EmptyStrip
          title="No numbers on the Twilio account."
          body="Numbers appear here once TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN are set on the API host and the account owns at least one."
        />
      )}
      {dialog}
    </PageFrame>
  );
}
