import { Activity, BarChart3, Cable, CircleAlert, ContactRound, Database, PhoneCall, Timer, Workflow } from "lucide-react";

import { Card, CardContent } from "@/components/ui/card";
import { runDuration, runOutcome } from "@/components/console/console-status";
import type { DirectoryEntry, Endpoint, IncomingCall, ProviderConnection, Scenario, SimulationRun } from "@/lib/simulator-api";

type OverviewData = {
  connections: ProviderConnection[];
  endpoints: Endpoint[];
  directory: DirectoryEntry[];
  scenarios: Scenario[];
  runs: SimulationRun[];
  calls: IncomingCall[];
};

const OVERVIEW_STATS: Array<{
  label: string;
  value: (data: OverviewData) => number;
  icon: typeof Activity;
}> = [
  { label: "Provider connections", value: ({ connections }) => connections.length, icon: Cable },
  {
    label: "Active endpoints",
    value: ({ endpoints }) => endpoints.filter((item) => item.enabled).length,
    icon: PhoneCall,
  },
  { label: "Directory routes", value: ({ directory }) => directory.length, icon: ContactRound },
  { label: "Scenario library", value: ({ scenarios }) => scenarios.length, icon: Workflow },
  { label: "Recorded runs", value: ({ runs }) => runs.length, icon: Database },
  { label: "Incoming calls", value: ({ calls }) => calls.length, icon: PhoneCall },
];

export function OverviewMetrics({ data }: { data: OverviewData }) {
  const { runs } = data;
  const completedSimulationRuns = runs.filter((run) => Boolean(runOutcome(run)));
  const connectedSimulationRuns = completedSimulationRuns.filter((run) =>
    ["connected", "bridged"].includes(runOutcome(run))
  );
  const connectRate = completedSimulationRuns.length
    ? Math.round((connectedSimulationRuns.length / completedSimulationRuns.length) * 100)
    : 0;

  return (
    <>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-6">
        {OVERVIEW_STATS.map(({ label, value, icon: Icon }) => (
          <Card key={label}>
            <CardContent className="p-5 md:p-6">
              <div className="flex items-center justify-between">
                <p className="text-sm text-muted-foreground">{label}</p>
                <Icon className="h-4 w-4 text-muted-foreground" />
              </div>
              <p className="mt-3 text-3xl font-semibold">
                {value(data)}
              </p>
            </CardContent>
          </Card>
        ))}
      </div>

      <div className="grid gap-4 md:grid-cols-3">
        <Card className="bg-success-soft">
          <CardContent className="p-5 md:p-6">
            <div className="flex items-center justify-between">
              <p className="text-sm text-muted-foreground">Simulation connect rate</p>
              <BarChart3 className="size-4 text-success" />
            </div>
            <p className="mt-3 text-3xl font-semibold">{connectRate}%</p>
            <p className="mt-1 text-xs text-muted-foreground">
              {connectedSimulationRuns.length} connected of{" "}
              {completedSimulationRuns.length} outcome-bearing runs
            </p>
          </CardContent>
        </Card>
        <Card className="bg-warning-soft">
          <CardContent className="p-5 md:p-6">
            <div className="flex items-center justify-between">
              <p className="text-sm text-muted-foreground">Not connected</p>
              <CircleAlert className="size-4 text-warning" />
            </div>
            <p className="mt-3 text-3xl font-semibold">
              {completedSimulationRuns.length - connectedSimulationRuns.length}
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              Busy, no-answer, failed, and abandoned outcomes
            </p>
          </CardContent>
        </Card>
        <Card className="bg-info-soft">
          <CardContent className="p-5 md:p-6">
            <div className="flex items-center justify-between">
              <p className="text-sm text-muted-foreground">Recorded duration</p>
              <Timer className="size-4 text-info" />
            </div>
            <p className="mt-3 text-3xl font-semibold">
              {Math.round(
                completedSimulationRuns.reduce(
                  (total, run) => total + runDuration(run),
                  0
                )
              )}
              s
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              Across deterministic IVR call records
            </p>
          </CardContent>
        </Card>
      </div>
    </>
  );
}
