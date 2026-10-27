"use client";

import { useState, type ReactNode } from "react";
import Link from "next/link";
import { RefreshCw } from "lucide-react";

import {
  DataTable,
  EmptyStrip,
  formatAddress,
  formatDuration,
  formatWhen,
  MISSING,
  PageFrame,
  PageHeader,
  Panel,
  PanelTitle,
  Segmented,
  Td,
  Th,
  TonePill,
  useConfirm,
} from "@/components/console/kit";
import { EnginePill, GradeSection, PersonaCatalog } from "@/components/console/customer-personas";
import { Button } from "@/components/ui/button";
import { useApi } from "@/hooks/use-api";
import { inbox, type CustomerGrade, type CustomerSession } from "@/lib/workflows-api";
import { cn } from "@/lib/utils";

type Area = "sms" | "customer";

export function InboxPage() {
  const [area, setArea] = useState<Area>("sms");
  return (
    <PageFrame>
      <PageHeader title="Inbox" freshness={false}>
        <Segmented
          label="Inbox area"
          value={area}
          options={[{ id: "sms", label: "SMS" }, { id: "customer", label: "Synthetic customer" }]}
          onChange={setArea}
        />
      </PageHeader>
      {area === "sms" ? <SmsArea /> : <CustomerArea />}
    </PageFrame>
  );
}

/* ---------- shared ---------- */

function InlineError({ message }: { message: string | null }) {
  if (!message) return null;
  return <p role="alert" className="rounded-xl bg-danger-soft px-4 py-3 text-[13px] leading-5 text-danger">{message}</p>;
}

function RefreshButton({ loading, onClick, label }: { loading: boolean; onClick: () => void; label: string }) {
  return (
    <Button variant="secondary" size="sm" disabled={loading} onClick={onClick}>
      <RefreshCw className={cn(loading && "animate-spin")} />
      {label}
    </Button>
  );
}

function Stat({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid min-w-0 gap-1 rounded-lg bg-surface-1 px-3 py-2.5">
      <p className="text-xs text-muted-foreground">{label}</p>
      <div className="min-w-0 text-[13px]">{children}</div>
    </div>
  );
}

function Presence({ present }: { present: boolean }) {
  return <TonePill tone={present ? "success" : "warning"}>{present ? "Present" : "Missing"}</TonePill>;
}

function AddressList({ values }: { values: string[] }) {
  if (!values.length) return <span className="text-muted-foreground">None</span>;
  return (
    <ul className="flex flex-wrap gap-1.5">
      {values.map((value) => (
        <li key={value} className="t-num rounded-md bg-control px-2 py-0.5 text-xs">{formatAddress(value)}</li>
      ))}
    </ul>
  );
}

/* ---------- SMS ---------- */

function SmsArea() {
  const status = useApi((signal) => inbox.smsStatus(signal), []);
  const messages = useApi((signal) => inbox.sms(50, signal), []);
  const loading = status.loading || messages.loading;
  const rows = messages.data ?? [];

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-sm font-semibold">Receiver</h2>
        <RefreshButton loading={loading} label="Refresh" onClick={() => { status.reload(); messages.reload(); }} />
      </div>
      <InlineError message={status.error} />
      {status.data && (
        <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-5" aria-live="polite">
          <Stat label="Inbox">
            <TonePill tone={status.data.inbox_configured ? (status.data.inbox_exists ? "success" : "accent") : "warning"}>
              {status.data.inbox_configured ? (status.data.inbox_exists ? "Configured" : "Configured, no messages yet") : "Not configured"}
            </TonePill>
          </Stat>
          <Stat label="Numbers"><AddressList values={status.data.numbers} /></Stat>
          <Stat label="Allowed senders"><AddressList values={status.data.allowed_senders} /></Stat>
          <Stat label="Twilio account"><Presence present={status.data.account_configured} /></Stat>
          <Stat label="Request signing"><Presence present={status.data.signing_configured} /></Stat>
        </div>
      )}

      <InlineError message={messages.error} />
      {messages.data && (rows.length ? (
        <DataTable label="SMS messages">
          <thead>
            <tr><Th>Received</Th><Th>From</Th><Th>To</Th><Th>Body</Th></tr>
          </thead>
          <tbody>
            {rows.map((message) => (
              <tr key={message.message_sid} className="even:bg-control/40">
                <Td className="t-num whitespace-nowrap text-muted-foreground">{formatWhen(message.received_at, true)}</Td>
                <Td className="t-num whitespace-nowrap">{formatAddress(message.sender)}</Td>
                <Td className="t-num whitespace-nowrap">{formatAddress(message.recipient)}</Td>
                <Td className="py-2 leading-5"><span className="whitespace-pre-wrap break-words">{message.body}</span></Td>
              </tr>
            ))}
          </tbody>
        </DataTable>
      ) : (
        <EmptyStrip
          title="No messages"
          body="Texts sent to an allowed number from an allowed sender appear here. Set PSTN_SMS_DB, PSTN_SMS_NUMBERS and PSTN_SMS_ALLOWED_SENDERS on the API server."
        />
      ))}
    </div>
  );
}

/* ---------- synthetic customer ---------- */

function stopReason(session: CustomerSession): string {
  if (session.error_type) return session.error_type.replace(/_/g, " ");
  if (typeof session.stopped === "string" && session.stopped) return session.stopped.replace(/_/g, " ");
  if (session.stopped === true) return "stopped";
  return MISSING;
}

function CustomerArea() {
  const profiles = useApi((signal) => inbox.customerProfiles(signal), []);
  const sessions = useApi((signal) => inbox.customerSessions(20, signal), []);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const loading = profiles.loading || sessions.loading;
  const list = sessions.data ?? [];
  const selected = list.find((session) => session.call_id === selectedId) ?? null;
  const lines = Object.entries(profiles.data?.profiles ?? {});
  const configured = Object.entries(profiles.data?.configured ?? {});
  // Fetch state is per session: one session's slow or failed fetch never shows on another.
  const [fetching, setFetching] = useState<string | null>(null);
  const [fetchErrors, setFetchErrors] = useState<Record<string, string>>({});
  const { ask, dialog } = useConfirm();
  const [synced, setSynced] = useState<string[] | null>(null);

  async function fetchTranscript(callId: string) {
    setFetching(callId);
    setFetchErrors(({ [callId]: _cleared, ...rest }) => rest);
    try {
      const session = await inbox.customerRefresh(callId);
      sessions.setData((current) => current?.map((item) => (item.call_id === callId ? session : item)) ?? current);
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "Could not fetch the transcript";
      setFetchErrors((current) => ({ ...current, [callId]: message }));
    } finally {
      setFetching((current) => (current === callId ? null : current));
    }
  }

  function graded(callId: string, grade: CustomerGrade) {
    sessions.setData((current) => current?.map((item) => (item.call_id === callId ? { ...item, grade } : item)) ?? current);
  }

  function open(session: CustomerSession) {
    const next = session.call_id === selectedId ? null : session.call_id;
    setSelectedId(next);
    if (next && session.transcript_status && session.transcript_status !== "done") void fetchTranscript(next);
  }

  function pushAgentSettings() {
    ask({
      title: "Update the ElevenLabs persona agents?",
      body: "Overwrites the prompt, the mu-law 8 kHz audio formats, the 4-minute call limit, the model and the per-call override permissions on the agents named by ELEVENLABS_CUSTOMER_AGENT_ID and ELEVENLABS_CUSTOMER_REALTIME_AGENT_ID. Their voices stay as set in ElevenLabs.",
      confirm: "Update agents",
      tone: "primary",
      onConfirm: async () => {
        try {
          setSynced(Object.values(await inbox.customerSyncAgent()).map((agent) => agent.agent_id));
        } catch (cause) {
          return cause instanceof Error ? cause.message : "Could not update the agent";
        }
      },
    });
  }

  return (
    <div className="grid gap-4">
      {dialog}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-sm font-semibold">Configuration</h2>
        <RefreshButton loading={loading} label="Refresh" onClick={() => { profiles.reload(); sessions.reload(); }} />
      </div>
      <InlineError message={profiles.error} />
      {profiles.data && (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,320px)] lg:items-start">
          <Panel>
            <PanelTitle aside={lines.length}>Persona lines</PanelTitle>
            {lines.length ? (
              <ul className="grid gap-2">
                {lines.map(([number, profile]) => (
                  <li key={number} className="grid gap-1 rounded-lg bg-surface-1 px-3 py-2.5 sm:grid-cols-[150px_1fr] sm:gap-3">
                    <span className="t-num text-[13px] font-medium">{formatAddress(number)}</span>
                    <span className="whitespace-pre-wrap break-words text-[13px] leading-5 text-muted-foreground">
                      {typeof profile === "string" ? profile : JSON.stringify(profile)}
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <EmptyStrip title="No persona lines" body="Set PSTN_CUSTOMER_PROFILES to a JSON file mapping each number to its default persona id." />
            )}
            <div className="mt-4 grid gap-2">
              <h3 className="text-[13px] font-medium">Allowed callers</h3>
              <AddressList values={profiles.data.allowed_callers} />
            </div>
          </Panel>
          <Panel>
            <PanelTitle>Environment</PanelTitle>
            <ul className="grid grid-cols-1 gap-1.5">
              {configured.map(([key, present]) => (
                <li key={key} className="flex items-center justify-between gap-3">
                  <span className="min-w-0 truncate font-mono text-[11.5px]">{key}</span>
                  <Presence present={present} />
                </li>
              ))}
            </ul>
            <div className="mt-4 grid gap-2" aria-live="polite">
              <Button size="sm" variant="secondary" className="justify-self-start" onClick={pushAgentSettings}>
                Push agent settings
              </Button>
              {synced && <p className="text-xs text-muted-foreground">Updated <span className="font-mono">{synced.join(", ")}</span>.</p>}
            </div>
          </Panel>
        </div>
      )}

      {profiles.data && <PersonaCatalog lines={lines.map(([number]) => number)} />}

      <h2 className="mt-2 text-sm font-semibold">Sessions</h2>
      <InlineError message={sessions.error} />
      {sessions.data && (list.length ? (
        <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,460px)] xl:items-start">
          <DataTable label="Synthetic customer sessions">
            <thead>
              <tr><Th>When</Th><Th>From</Th><Th>To</Th><Th>Persona</Th><Th>Status</Th><Th>Duration</Th><Th>Grade</Th><Th>Stop reason</Th></tr>
            </thead>
            <tbody>
              {list.map((session) => {
                const active = session.call_id === selectedId;
                return (
                  <tr
                    key={session.call_id}
                    className={cn(
                      "relative cursor-pointer transition-colors duration-[var(--motion-fast)] even:bg-control/40 hover:bg-control-hover",
                      active && "bg-accent-soft even:bg-accent-soft hover:bg-accent-soft",
                    )}
                  >
                    <Td className="t-num whitespace-nowrap">
                      <button
                        type="button"
                        aria-pressed={active}
                        onClick={() => open(session)}
                        className="font-medium outline-none after:absolute after:inset-0 after:content-[''] focus-visible:after:rounded-lg focus-visible:after:ring-2 focus-visible:after:ring-ring/40"
                      >
                        {formatWhen(session.started_at)}
                      </button>
                    </Td>
                    <Td className="t-num whitespace-nowrap">{formatAddress(session.from)}</Td>
                    <Td className="t-num whitespace-nowrap">{formatAddress(session.to)}</Td>
                    <Td className="whitespace-nowrap font-mono text-[12px]">{session.persona_id ?? MISSING}</Td>
                    <Td className="whitespace-nowrap">{session.status.replace(/_/g, " ")}</Td>
                    <Td className="t-num">{formatDuration(session.duration_s)}</Td>
                    <Td>{session.grade ? <TonePill tone={session.grade.passed ? "success" : "danger"}>{session.grade.passed ? "Passed" : "Failed"}</TonePill> : MISSING}</Td>
                    <Td className="whitespace-nowrap text-muted-foreground">{stopReason(session)}</Td>
                  </tr>
                );
              })}
            </tbody>
          </DataTable>
          {selected ? (
            <Dialogue
              session={selected}
              fetching={fetching === selected.call_id}
              error={fetchErrors[selected.call_id] ?? null}
              onFetch={() => void fetchTranscript(selected.call_id)}
              onGraded={(grade) => graded(selected.call_id, grade)}
            />
          ) : (
            <EmptyStrip title="No session selected" body="Select a session to read its dialogue." />
          )}
        </div>
      ) : (
        <EmptyStrip title="No sessions" body="Calls answered or placed by the synthetic customer appear here with their dialogue." />
      ))}
    </div>
  );
}

function turnText(turn: CustomerSession["dialogue"][number]): string {
  return turn.content;
}

/** The dealership's speech is stored as role "user", the customer's replies as "assistant". */
function Dialogue({
  session,
  fetching,
  error,
  onFetch,
  onGraded,
}: {
  session: CustomerSession;
  fetching: boolean;
  error: string | null;
  onFetch: () => void;
  onGraded: (grade: CustomerGrade) => void;
}) {
  const pending = session.transcript_status != null && session.transcript_status !== "done";
  return (
    <Panel>
      <PanelTitle aside={
        <Link href={`/console/result/?id=${encodeURIComponent(session.call_id)}`} className="text-accent-text hover:underline">
          Call result
        </Link>
      }>
        {formatAddress(session.from)} to {formatAddress(session.to)}
      </PanelTitle>
      {session.persona_id && (
        <div className="mb-3 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <span className="font-mono text-foreground">{session.persona_id}</span>
          {session.engine && <EnginePill engine={session.engine} />}
          {session.persona_source && "queue_id" in session.persona_source
            ? `queued${session.persona_source.label ? `, ${session.persona_source.label}` : ""}`
            : "line default"}
        </div>
      )}
      {session.dialogue.length ? (
        <ol className="grid gap-2" aria-label="Dialogue">
          {session.dialogue.map((turn, index) => {
            const customer = turn.role === "assistant";
            return (
              <li key={index} className={cn("flex", customer ? "justify-end" : "justify-start")}>
                <div className={cn("grid max-w-[85%] gap-0.5 rounded-xl px-3 py-2", customer ? "bg-accent-soft" : "bg-surface-1")}>
                  <span className={cn("text-xs font-medium", customer ? "text-accent-text" : "text-muted-foreground")}>
                    {customer ? "Customer" : "Agent"}
                  </span>
                  <p className="whitespace-pre-wrap break-words text-[13px] leading-5">{turnText(turn)}</p>
                </div>
              </li>
            );
          })}
        </ol>
      ) : (
        <p className="text-[13px] text-muted-foreground">
          {pending ? "ElevenLabs has not finished this transcript yet." : "No turns were recorded for this call."}
        </p>
      )}
      {(pending || error) && (
        <div className="mt-3 flex flex-wrap items-center gap-3" aria-live="polite">
          <Button size="xs" variant="secondary" disabled={fetching} onClick={onFetch}>
            <RefreshCw className={cn(fetching && "animate-spin")} />
            Fetch transcript
          </Button>
          {error && <span role="alert" className="text-xs text-danger">{error}</span>}
        </div>
      )}
      {session.persona_id && (
        <GradeSection
          key={session.call_id}
          callId={session.call_id}
          grade={session.grade}
          ready={session.transcript_status === "done"}
          onGraded={onGraded}
        />
      )}
    </Panel>
  );
}
