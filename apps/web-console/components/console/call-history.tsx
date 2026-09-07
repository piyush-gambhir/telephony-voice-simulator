"use client";

import { useState } from "react";
import { Trash2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { HistoryPagination, HistoryToolbar } from "@/components/console/history-toolbar";
import { RecordingPlayer } from "@/components/console/recording-player";
import { statusBadge } from "@/components/console/console-status";
import type { IncomingCall } from "@/lib/simulator-api";

const PAGE_SIZE = 20;

export function CallHistory({ calls, busy, onDelete }: {
  calls: IncomingCall[];
  busy: boolean;
  onDelete: (callId: string) => Promise<void>;
}) {
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const [provider, setProvider] = useState("all");
  const [pageIndex, setPageIndex] = useState(0);
  const needle = query.trim().toLowerCase();
  const matches = calls.filter((call) =>
    (status === "all" || call.status === status)
    && (provider === "all" || call.provider === provider)
    && [call.id, call.scenario, call.from_address, call.to_address]
      .some((value) => value?.toLowerCase().includes(needle)),
  );
  const page = Math.min(pageIndex, Math.max(0, Math.ceil(matches.length / PAGE_SIZE) - 1));

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Incoming calls</CardTitle>
        <CardDescription>Inspect inbound calls and load recordings when you need to listen.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <HistoryToolbar
          label="calls" query={query} onQueryChange={(value) => { setQuery(value); setPageIndex(0); }}
          status={status} statuses={[...new Set(calls.map((call) => call.status))].sort()}
          onStatusChange={(value) => { setStatus(value); setPageIndex(0); }}
          provider={provider} providers={[...new Set(calls.map((call) => call.provider))].sort()}
          onProviderChange={(value) => { setProvider(value); setPageIndex(0); }}
          records={matches} total={calls.length}
        />
        {!matches.length && (
          <p className="rounded-xl bg-muted/30 p-8 text-center text-sm text-muted-foreground">
            {calls.length ? "No calls match these filters." : "No incoming calls yet. Calls to a configured AMD number and their recordings will appear here."}
          </p>
        )}
        {matches.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE).map((call) => {
          const availableRecordings = call.recordings.filter((recording) => recording.local_path);
          return (
            <div className="rounded-xl bg-muted/30 p-4 md:p-5" key={call.id}>
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="font-medium">{call.from_address || "Unknown caller"}</p>
                    <span className="text-muted-foreground">→</span>
                    <p className="font-medium">{call.to_address || "—"}</p>
                    {statusBadge(call.status)}
                    <Badge variant="outline">{call.provider}</Badge>
                    {call.analysis.passed === null && <Badge variant="warning">Checks incomplete</Badge>}
                    {call.analysis.passed != null && (
                      <Badge variant={call.analysis.passed ? "success" : "destructive"}>
                        {call.analysis.passed ? "Checks passed" : "Checks failed"}
                      </Badge>
                    )}
                  </div>
                  <p className="mt-1 break-all font-mono text-xs text-muted-foreground">{call.id}</p>
                </div>
                <Button type="button" variant="ghost" size="icon" disabled={busy}
                  aria-label={`Delete call ${call.id}`} onClick={() => void onDelete(call.id)}>
                  <Trash2 />
                </Button>
              </div>
              <div className="mt-4 flex flex-wrap gap-x-6 gap-y-2 text-sm">
                <span><span className="text-muted-foreground">Scenario: </span>{call.scenario?.replaceAll("_", " ") ?? "No scenario assigned"}</span>
                <span><span className="text-muted-foreground">Duration: </span>{call.duration_s == null ? "—" : `${Math.round(call.duration_s)}s`}</span>
                <span><span className="text-muted-foreground">Started: </span>{new Date(call.started_at).toLocaleString()}</span>
              </div>
              {availableRecordings.length ? (
                <div className="mt-5 grid gap-4 xl:grid-cols-2">
                  {availableRecordings.map((recording) => (
                    <RecordingPlayer key={recording.id} recordingId={recording.id}
                      label={recording.kind === "full_call" ? "Complete call" : "Voicemail left after the beep"} />
                  ))}
                </div>
              ) : (
                <p className="mt-5 rounded-lg bg-background/45 px-4 py-3 text-sm text-muted-foreground">
                  {call.recording_status === "disabled" ? "Recording was disabled for this call."
                    : call.recording_status === "failed" ? "The provider could not deliver the recording."
                      : call.recording_status === "completed" ? "No local recording is available."
                        : "Waiting for the provider to finish and deliver the recordings."}
                </p>
              )}
            </div>
          );
        })}
        <HistoryPagination page={page} count={matches.length} pageSize={PAGE_SIZE} onPageChange={setPageIndex} />
      </CardContent>
    </Card>
  );
}
