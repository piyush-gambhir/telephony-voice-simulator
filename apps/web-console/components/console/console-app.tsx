"use client";

import { FormEvent, useMemo, useState } from "react";
import {
  Activity,
  BarChart3,
  Cable,
  Check,
  CircleAlert,
  ContactRound,
  Database,
  FlaskConical,
  Loader2,
  Pencil,
  PhoneCall,
  Play,
  Plus,
  RadioTower,
  RefreshCw,
  Route,
  Server,
  Trash2,
  Timer,
  Workflow,
} from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";
import { AmdNumberManager } from "@/components/console/amd-number-manager";
import { RunHistory } from "@/components/console/run-history";
import { CallHistory } from "@/components/console/call-history";
import { runDuration, runOutcome, statusBadge } from "@/components/console/console-status";
import { useConsoleData } from "@/hooks/use-console-data";
import { scenariosForProvider } from "@/lib/scenario-compatibility";
import { PageShell } from "@/components/page-shell";
import {
  DirectoryEntryEditor,
  DirectoryEntryUpdate,
  EndpointEditor,
  EndpointUpdate,
  parseSettingsJson,
  ProviderConnectionEditor,
  ProviderConnectionUpdate,
} from "@/components/console/resource-editors";
import {
  DirectoryEntry,
  Endpoint,
  IncomingCall,
  ManagedNumber,
  ProviderConnection,
  Scenario,
  SIMULATOR_API_URL,
  SimulationRun,
  simulatorApi,
} from "@/lib/simulator-api";

type View =
  | "numbers"
  | "overview"
  | "providers"
  | "endpoints"
  | "directory"
  | "scenarios"
  | "runs"
  | "calls";

const NAV: Array<{ id: View; label: string; icon: typeof Activity }> = [
  { id: "overview", label: "Overview", icon: Activity },
  { id: "numbers", label: "AMD numbers", icon: PhoneCall },
  { id: "scenarios", label: "Scenarios", icon: Workflow },
  { id: "runs", label: "Runs", icon: FlaskConical },
  { id: "calls", label: "Calls & recordings", icon: PhoneCall },
  { id: "providers", label: "Providers", icon: Cable },
  { id: "endpoints", label: "Endpoints", icon: Route },
  { id: "directory", label: "Directory", icon: ContactRound },
];

const OVERVIEW_STATS: Array<{
  label: string;
  value: (data: {
    connections: ProviderConnection[];
    endpoints: Endpoint[];
    directory: DirectoryEntry[];
    scenarios: Scenario[];
    runs: SimulationRun[];
    calls: IncomingCall[];
  }) => number;
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

function EmptyState({
  icon: Icon,
  title,
  body,
}: {
  icon: typeof Activity;
  title: string;
  body: string;
}) {
  return (
    <div className="flex min-h-48 flex-col items-center justify-center rounded-xl bg-muted/40 p-8 text-center">
      <Icon className="mb-3 h-6 w-6 text-muted-foreground" />
      <p className="font-medium">{title}</p>
      <p className="mt-1 max-w-md text-sm text-muted-foreground">{body}</p>
    </div>
  );
}

export function ConsoleApp() {
  const [view, setView] = useState<View>("overview");
  const [busy, setBusy] = useState(false);
  const {
    data: { catalog, amdNumbers, connections, endpoints, directory, scenarios, runs, calls },
    updateData, loading, refreshing, online, refreshError, lastUpdatedAt,
    autoRefresh, setAutoRefresh, refresh: loadConsoleData,
  } = useConsoleData({ paused: busy });
  const [error, setError] = useState<string | null>(null);
  const [providerType, setProviderType] = useState("mock");
  const [providerName, setProviderName] = useState("Local simulator");
  const [providerStatus, setProviderStatus] =
    useState<ProviderConnection["status"]>("ready");
  const [providerDescription, setProviderDescription] = useState(
    "Local deterministic call execution."
  );
  const [providerSettings, setProviderSettings] = useState("{}");
  const [endpointConnectionId, setEndpointConnection] = useState("");
  const [endpointName, setEndpointName] = useState("Test line");
  const [endpointKind, setEndpointKind] = useState<Endpoint["kind"]>("phone_number");
  const [endpointAddress, setEndpointAddress] = useState("+15550100001");
  const [endpointScenarioName, setEndpointScenario] = useState("");
  const [endpointRoutingMode, setEndpointRoutingMode] =
    useState<Endpoint["routing_mode"]>("fixed");
  const [runEndpointId, setRunEndpoint] = useState("");
  const [runScenarioName, setRunScenario] = useState("");
  const [directoryConnectionId, setDirectoryConnection] = useState("");
  const [directoryExtension, setDirectoryExtension] = useState("1501");
  const [directoryName, setDirectoryName] = useState("Front desk");
  const [directoryDestination, setDirectoryDestination] = useState("+15550101501");
  const [directoryDepartment, setDirectoryDepartment] = useState("Reception");
  const [directoryTimeout, setDirectoryTimeout] = useState("25");
  const [ivrCaller, setIvrCaller] = useState("+14085550131");
  const [ivrExtension, setIvrExtension] = useState("1501");
  const [ivrDisposition, setIvrDisposition] =
    useState<"connected" | "busy" | "no_answer" | "failed" | "abandoned">("connected");
  const [editingConnectionId, setEditingConnectionId] = useState<string | null>(null);
  const [editingEndpointId, setEditingEndpointId] = useState<string | null>(null);
  const [editingDirectoryId, setEditingDirectoryId] = useState<string | null>(null);

  const endpointConnection = connections.some((item) => item.id === endpointConnectionId)
    ? endpointConnectionId : connections[0]?.id ?? "";
  const directoryConnection = connections.some((item) => item.id === directoryConnectionId)
    ? directoryConnectionId : connections[0]?.id ?? "";
  const runEndpoint = endpoints.some((item) => item.id === runEndpointId)
    ? runEndpointId : endpoints.find((item) => item.enabled && item.provider === "mock")?.id
      ?? endpoints.find((item) => item.enabled)?.id ?? "";

  const selectedDescriptor = useMemo(
    () => catalog.find((provider) => provider.key === providerType),
    [catalog, providerType]
  );
  const selectedConnection = useMemo(
    () => connections.find((connection) => connection.id === endpointConnection),
    [connections, endpointConnection]
  );
  const endpointKinds =
    catalog.find((provider) => provider.key === selectedConnection?.provider)?.endpoint_kinds ??
    ["phone_number"];
  const selectedRunEndpoint = useMemo(
    () => endpoints.find((endpoint) => endpoint.id === runEndpoint),
    [endpoints, runEndpoint]
  );
  const runScenarios = useMemo(
    () => scenariosForProvider(scenarios, selectedRunEndpoint?.provider, catalog),
    [scenarios, selectedRunEndpoint?.provider, catalog],
  );
  const endpointScenarios = useMemo(
    () => scenariosForProvider(scenarios, selectedConnection?.provider, catalog),
    [scenarios, selectedConnection?.provider, catalog],
  );
  const fixedRunScenario = selectedRunEndpoint?.routing_mode === "fixed"
    ? selectedRunEndpoint.default_scenario : null;
  const runScenario = fixedRunScenario ?? (runScenarios.some((item) => item.name === runScenarioName)
    ? runScenarioName : runScenarios[0]?.name ?? "");
  const endpointScenario = endpointScenarios.some((item) => item.name === endpointScenarioName)
    ? endpointScenarioName : endpointScenarios[0]?.name ?? "";
  const runConnection = connections.find((item) => item.id === selectedRunEndpoint?.connection_id);
  const runAvailable = Boolean(selectedRunEndpoint?.enabled && runConnection?.enabled);
  const editingConnection = connections.find(
    (connection) => connection.id === editingConnectionId
  );
  const editingEndpoint = endpoints.find((endpoint) => endpoint.id === editingEndpointId);
  const editingDirectoryEntry = directory.find(
    (entry) => entry.id === editingDirectoryId
  );

  const completedSimulationRuns = useMemo(
    () => runs.filter((run) => Boolean(runOutcome(run))),
    [runs]
  );
  const connectedSimulationRuns = useMemo(
    () =>
      completedSimulationRuns.filter((run) =>
        ["connected", "bridged"].includes(runOutcome(run))
      ),
    [completedSimulationRuns]
  );
  const connectRate = completedSimulationRuns.length
    ? Math.round((connectedSimulationRuns.length / completedSimulationRuns.length) * 100)
    : 0;

  async function submitConnection(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const connection = await simulatorApi.createConnection({
        provider: providerType,
        name: providerName,
        status: providerStatus,
        description: providerDescription,
        settings: parseSettingsJson(providerSettings),
      });
      updateData("connections", (current) => [...current, connection]);
      setEndpointConnection(connection.id);
      setProviderName("");
      setProviderDescription("");
      setProviderSettings("{}");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not create provider");
    } finally {
      setBusy(false);
    }
  }

  async function submitEndpoint(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const endpoint = await simulatorApi.createEndpoint({
        connection_id: endpointConnection,
        name: endpointName,
        kind: endpointKind,
        address: endpointAddress,
        routing_mode: endpointRoutingMode,
        default_scenario: endpointScenario,
      });
      updateData("endpoints", (current) => [...current, endpoint]);
      setRunEndpoint(endpoint.id);
      setEndpointName("");
      setEndpointAddress("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not create endpoint");
    } finally {
      setBusy(false);
    }
  }

  async function submitRun(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const run = await simulatorApi.createRun({
        endpoint_id: runEndpoint,
        scenario: runScenario,
      });
      updateData("runs", (current) => [run, ...current]);
      setView("runs");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not start run");
    } finally {
      setBusy(false);
    }
  }

  async function cancelRun(runId: string) {
    setBusy(true);
    setError(null);
    try {
      const updated = await simulatorApi.cancelRun(runId);
      updateData("runs", (current) => current.map((run) => run.id === updated.id ? updated : run));
      await loadConsoleData(true);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not cancel queued run");
      await loadConsoleData(true);
    } finally {
      setBusy(false);
    }
  }

  async function submitDirectoryEntry(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const entry = await simulatorApi.createDirectoryEntry({
        connection_id: directoryConnection,
        extension: directoryExtension,
        name: directoryName,
        destination: directoryDestination,
        department: directoryDepartment,
        ring_timeout: Number(directoryTimeout),
      });
      updateData("directory", (current) =>
        [...current, entry].sort((left, right) =>
          left.extension.localeCompare(right.extension)
        )
      );
      setIvrExtension(entry.extension);
      setDirectoryExtension("");
      setDirectoryName("");
      setDirectoryDestination("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not add directory route");
    } finally {
      setBusy(false);
    }
  }

  async function removeDirectoryEntry(entryId: string) {
    if (!window.confirm("Delete this extension route?")) return;
    setBusy(true);
    setError(null);
    try {
      await simulatorApi.deleteDirectoryEntry(entryId);
      updateData("directory", (current) => current.filter((entry) => entry.id !== entryId));
      if (editingDirectoryId === entryId) setEditingDirectoryId(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not delete directory route");
    } finally {
      setBusy(false);
    }
  }

  async function toggleDirectoryEntry(entry: DirectoryEntry) {
    setBusy(true);
    setError(null);
    try {
      const updated = await simulatorApi.updateDirectoryEntry(entry.id, {
        enabled: !entry.enabled,
      });
      updateData("directory", (current) =>
        current.map((item) => (item.id === updated.id ? updated : item))
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not update directory route");
    } finally {
      setBusy(false);
    }
  }

  async function toggleEndpoint(endpoint: Endpoint) {
    setBusy(true);
    setError(null);
    try {
      const updated = await simulatorApi.updateEndpoint(endpoint.id, {
        enabled: !endpoint.enabled,
      });
      updateData("endpoints", (current) =>
        current.map((item) => (item.id === updated.id ? updated : item))
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not update endpoint");
    } finally {
      setBusy(false);
    }
  }

  async function saveConnection(
    connectionId: string,
    values: ProviderConnectionUpdate
  ): Promise<boolean> {
    setBusy(true);
    setError(null);
    try {
      const updated = await simulatorApi.updateConnection(connectionId, values);
      updateData("connections", (current) =>
        current.map((item) => (item.id === updated.id ? updated : item))
      );
      updateData("endpoints", (current) =>
        current.map((item) =>
          item.connection_id === updated.id
            ? { ...item, connection_name: updated.name }
            : item
        )
      );
      updateData("directory", (current) =>
        current.map((item) =>
          item.connection_id === updated.id
            ? { ...item, connection_name: updated.name }
            : item
        )
      );
      return true;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not update provider");
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function saveEndpoint(
    endpointId: string,
    values: EndpointUpdate
  ): Promise<boolean> {
    setBusy(true);
    setError(null);
    try {
      const updated = await simulatorApi.updateEndpoint(endpointId, values);
      updateData("endpoints", (current) =>
        current.map((item) => (item.id === updated.id ? updated : item))
      );
      return true;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not update endpoint");
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function saveDirectoryEntry(
    entryId: string,
    values: DirectoryEntryUpdate
  ): Promise<boolean> {
    setBusy(true);
    setError(null);
    try {
      const previous = directory.find((entry) => entry.id === entryId);
      const updated = await simulatorApi.updateDirectoryEntry(entryId, values);
      updateData("directory", (current) =>
        current
          .map((item) => (item.id === updated.id ? updated : item))
          .sort((left, right) => left.extension.localeCompare(right.extension))
      );
      if (previous && ivrExtension === previous.extension) {
        setIvrExtension(updated.extension);
      }
      return true;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not update directory route");
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function removeConnection(connectionId: string) {
    if (
      !window.confirm(
        "Delete this provider connection and its endpoints, directory routes, and run history?"
      )
    ) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await simulatorApi.deleteConnection(connectionId);
      if (editingConnectionId === connectionId) setEditingConnectionId(null);
      await loadConsoleData(true);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not delete provider");
    } finally {
      setBusy(false);
    }
  }

  async function removeEndpoint(endpointId: string) {
    if (
      !window.confirm(
        "Delete this endpoint and its associated simulation runs? This does not detach or reconfigure the carrier number."
      )
    ) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await simulatorApi.deleteEndpoint(endpointId);
      if (editingEndpointId === endpointId) setEditingEndpointId(null);
      await loadConsoleData(true);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not delete endpoint");
    } finally {
      setBusy(false);
    }
  }

  async function removeCall(callId: string) {
    if (
      !window.confirm(
        "Delete this call's simulator metadata and local recording files? Twilio/provider copies follow provider retention and must be deleted separately."
      )
    ) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await simulatorApi.deleteCall(callId);
      updateData("calls", (current) => current.filter((call) => call.id !== callId));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not delete call");
    } finally {
      setBusy(false);
    }
  }

  async function submitIvrSimulation(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const run = await simulatorApi.createIvrSimulation({
        endpoint_id: runEndpoint,
        caller_number: ivrCaller,
        extension: ivrExtension,
        disposition: ivrDisposition,
      });
      updateData("runs", (current) => [run, ...current]);
      setView("runs");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not run IVR simulation");
    } finally {
      setBusy(false);
    }
  }

  function replaceAmdNumber(updated: ManagedNumber) {
    updateData("amdNumbers", (current) =>
      current.map((number) => (number.id === updated.id ? updated : number))
    );
  }

  async function syncAmdNumbers() {
    setBusy(true);
    setError(null);
    try {
      const imported = await simulatorApi.syncAmdNumbers();
      updateData("amdNumbers", imported);
      const [providerConnections, endpointList] = await Promise.all([
        simulatorApi.connections(),
        simulatorApi.endpoints(),
      ]);
      updateData("connections", providerConnections);
      updateData("endpoints", endpointList);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not sync Twilio AMD numbers");
    } finally {
      setBusy(false);
    }
  }

  async function updateAmdNumber(
    numberId: string,
    update: {
      friendly_name?: string;
      default_scenario?: string | null;
      routing_mode?: Endpoint["routing_mode"];
      enabled?: boolean;
      record_full_calls?: boolean;
    }
  ) {
    setBusy(true);
    setError(null);
    try {
      const updated = await simulatorApi.updateAmdNumber(numberId, update);
      replaceAmdNumber(updated);
      updateData("endpoints", (current) =>
        current.map((endpoint) =>
          endpoint.id === updated.endpoint.id ? updated.endpoint : endpoint
        )
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not update AMD number");
    } finally {
      setBusy(false);
    }
  }

  async function queueAmdNumber(numberId: string, scenario: string) {
    setBusy(true);
    setError(null);
    try {
      const result = await simulatorApi.queueAmdNumber(numberId, scenario);
      replaceAmdNumber(result.number);
      updateData("runs", (current) => [result.run, ...current]);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not queue AMD scenario");
    } finally {
      setBusy(false);
    }
  }

  async function attachAmdNumber(numberId: string) {
    setBusy(true);
    setError(null);
    try {
      replaceAmdNumber(await simulatorApi.attachAmdNumber(numberId));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not attach AMD number");
    } finally {
      setBusy(false);
    }
  }

  async function restoreAmdNumber(numberId: string) {
    setBusy(true);
    setError(null);
    try {
      replaceAmdNumber(await simulatorApi.restoreAmdNumber(numberId));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not restore AMD number");
    } finally {
      setBusy(false);
    }
  }

  return (
    <PageShell className="min-h-[calc(100svh-3rem)]">
      <div className="mb-5 flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="mb-1 flex items-center gap-2 text-sm text-muted-foreground">
            <RadioTower className="size-4" />
            <span>
              {online === null ? "Connecting" : online ? "Local API online" : "API offline"}
            </span>
            <span
              className={cn(
                "size-2 rounded-full",
                online ? "bg-success" : "bg-muted-foreground"
              )}
            />
          </div>
          <h1 className="text-3xl font-semibold tracking-tight">Telephony simulator console</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Run local simulations, manage provider endpoints, and inspect inbound calls.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-2 text-sm text-muted-foreground">
            <input type="checkbox" checked={autoRefresh} onChange={(event) => setAutoRefresh(event.target.checked)} />
            Auto-refresh · 10s
          </label>
          <Button variant="outline" disabled={refreshing || busy} onClick={() => { setError(null); void loadConsoleData(true); }}>
            <RefreshCw className={cn(refreshing && "animate-spin")} />
            Refresh
          </Button>
          {lastUpdatedAt && <span className="basis-full text-right text-xs text-muted-foreground">Updated {lastUpdatedAt.toLocaleTimeString()}</span>}
        </div>
      </div>

      <Tabs
        value={view}
        onValueChange={(value) => setView(value as View)}
        className="mb-5 overflow-x-auto"
      >
        <TabsList className="h-auto w-max min-w-full justify-start">
          {NAV.map((item) => (
            <TabsTrigger key={item.id} value={item.id} id={`console-tab-${item.id}`} aria-controls={`console-panel-${item.id}`} className="gap-2">
              <item.icon className="size-4" />
              {item.label}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>

      {(error || refreshError) && (
        <Alert variant="destructive" className="mb-5">
          <CircleAlert />
          <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
            <AlertTitle className="mb-0">Console could not complete the request:</AlertTitle>
            <AlertDescription>{error ?? refreshError}{!online && " Start the backend, then select Refresh to reconnect."}</AlertDescription>
          </div>
        </Alert>
      )}

      <div role="tabpanel" id={`console-panel-${view}`} aria-labelledby={`console-tab-${view}`}>
          {loading ? (
            <Card className="flex min-h-96 items-center justify-center">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" aria-label="Loading console" />
            </Card>
          ) : (
            <>
              {view === "numbers" && (
                <AmdNumberManager
                  numbers={amdNumbers}
                  scenarios={scenarios.filter((scenario) => scenario.kind === "amd")}
                  busy={busy}
                  onSync={syncAmdNumbers}
                  onUpdate={updateAmdNumber}
                  onQueue={queueAmdNumber}
                  onAttach={attachAmdNumber}
                  onRestore={restoreAmdNumber}
                />
              )}

              {view === "overview" && (
                <div className="space-y-5">
                  <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-6">
                    {OVERVIEW_STATS.map(({ label, value, icon: Icon }) => (
                      <Card key={label}>
                        <CardContent className="p-5 md:p-6">
                          <div className="flex items-center justify-between">
                            <p className="text-sm text-muted-foreground">{label}</p>
                            <Icon className="h-4 w-4 text-muted-foreground" />
                          </div>
                          <p className="mt-3 text-3xl font-semibold">
                            {value({ connections, endpoints, directory, scenarios, runs, calls })}
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

                  <div className="grid grid-cols-1 gap-5 xl:grid-cols-3">
                    <Card>
                      <CardHeader>
                        <CardTitle className="text-base">Run a scenario</CardTitle>
                        <CardDescription>
                          Use the local simulator for a credential-free timeline dry run.
                        </CardDescription>
                      </CardHeader>
                      <CardContent>
                        {endpoints.length ? (
                          <form className="grid gap-4" onSubmit={submitRun}>
                            <div className="grid gap-2">
                              <Label htmlFor="run-endpoint">Endpoint</Label>
                              <Select
                                value={runEndpoint}
                                onValueChange={(value) => {
                                  setRunEndpoint(value);
                                  const endpoint = endpoints.find((item) => item.id === value);
                                  const compatible = scenariosForProvider(scenarios, endpoint?.provider, catalog);
                                  setRunScenario(compatible[0]?.name ?? "");
                                }}
                              >
                                <SelectTrigger id="run-endpoint">
                                  <SelectValue placeholder="Select endpoint" />
                                </SelectTrigger>
                                <SelectContent>
                                  {endpoints.map((endpoint) => (
                                    <SelectItem value={endpoint.id} key={endpoint.id}>
                                      {endpoint.name} · {endpoint.address}
                                    </SelectItem>
                                  ))}
                                </SelectContent>
                              </Select>
                            </div>
                            <div className="grid gap-2">
                              <Label htmlFor="run-scenario">Scenario</Label>
                              <Select
                                value={runScenario}
                                onValueChange={setRunScenario}
                                disabled={Boolean(fixedRunScenario)}
                              >
                                <SelectTrigger id="run-scenario">
                                  <SelectValue placeholder="Select scenario" />
                                </SelectTrigger>
                                <SelectContent>
                                  {runScenarios.map((scenario) => (
                                    <SelectItem value={scenario.name} key={scenario.name}>
                                      {scenario.title}
                                    </SelectItem>
                                  ))}
                                </SelectContent>
                              </Select>
                            </div>
                            <Button
                              className="mt-1 w-fit"
                              disabled={busy || !runAvailable || !runScenario || !runScenarios.some((item) => item.name === runScenario)}
                            >
                              {busy ? <Loader2 className="animate-spin" /> : <Play />}
                              Start run
                            </Button>
                            {fixedRunScenario && (
                              <p className="text-xs text-muted-foreground">This fixed endpoint uses its default scenario. Change its default or routing mode in Endpoints to run a different scenario.</p>
                            )}
                            {!runAvailable && <p className="text-xs text-warning">Enable this endpoint and its provider connection before starting a run.</p>}
                            {selectedRunEndpoint?.provider === "twilio" && (
                              <p className="text-xs text-muted-foreground">
                                Catalog dispatch queues AMD callee scenarios. Twilio allows one
                                Voice URL per number: use a separate number for the shared
                                <code> /twilio/ivr</code> directory, or explicitly reattach this
                                number when switching modes. The hosted <code>/machine</code>
                                deployment uses a separate Twilio Sync queue and remains
                                CLI-controlled; this console controls only the unified backend.
                              </p>
                            )}
                            {selectedRunEndpoint?.provider === "telnyx" && (
                              <p className="text-xs text-warning">
                                Telnyx execution is a preview adapter and cannot start calls yet.
                              </p>
                            )}
                          </form>
                        ) : (
                          <EmptyState
                            icon={Route}
                            title="Create an endpoint first"
                            body="Add a local simulator connection and endpoint, then return here to run a scenario."
                          />
                        )}
                      </CardContent>
                    </Card>

                    <Card className="bg-info-soft">
                      <CardHeader>
                        <CardTitle className="text-base">Interactive IVR lab</CardTitle>
                        <CardDescription>
                          Pick any directory extension and force the destination response.
                        </CardDescription>
                      </CardHeader>
                      <CardContent>
                        {endpoints.length && directory.length ? (
                          <form className="grid gap-4" onSubmit={submitIvrSimulation}>
                            <div className="grid gap-2">
                              <Label htmlFor="ivr-caller">Caller number</Label>
                              <Input
                                required minLength={5} maxLength={40}
                                id="ivr-caller"
                                value={ivrCaller}
                                onChange={(event) => setIvrCaller(event.target.value)}
                              />
                            </div>
                            <div className="grid grid-cols-2 gap-3">
                              <div className="grid gap-2">
                                <Label htmlFor="ivr-extension">Extension</Label>
                                <Select value={ivrExtension} onValueChange={setIvrExtension}>
                                  <SelectTrigger id="ivr-extension">
                                    <SelectValue placeholder="Extension" />
                                  </SelectTrigger>
                                  <SelectContent>
                                    {directory.map((entry) => (
                                      <SelectItem value={entry.extension} key={entry.id}>
                                        {entry.extension} · {entry.name}
                                      </SelectItem>
                                    ))}
                                    {!directory.some((entry) => entry.extension === "9999") && <SelectItem value="9999">9999 · Unknown</SelectItem>}
                                  </SelectContent>
                                </Select>
                              </div>
                              <div className="grid gap-2">
                                <Label htmlFor="ivr-disposition">Destination</Label>
                                <Select
                                  value={ivrDisposition}
                                  onValueChange={(value) =>
                                    setIvrDisposition(
                                      value as
                                        | "connected"
                                        | "busy"
                                        | "no_answer"
                                        | "failed"
                                        | "abandoned"
                                    )
                                  }
                                >
                                  <SelectTrigger id="ivr-disposition">
                                    <SelectValue />
                                  </SelectTrigger>
                                  <SelectContent>
                                    <SelectItem value="connected">Answers</SelectItem>
                                    <SelectItem value="busy">Busy</SelectItem>
                                    <SelectItem value="no_answer">No answer</SelectItem>
                                    <SelectItem value="failed">Fails</SelectItem>
                                    <SelectItem value="abandoned">Caller abandons</SelectItem>
                                  </SelectContent>
                                </Select>
                              </div>
                            </div>
                            <Button disabled={busy || !runAvailable || !/^[0-9]{4}$/.test(ivrExtension) || ivrCaller.trim().length < 5}>
                              {busy ? <Loader2 className="animate-spin" /> : <PhoneCall />}
                              Simulate inbound call
                            </Button>
                          </form>
                        ) : (
                          <EmptyState
                            icon={ContactRound}
                            title="Add a line and directory route"
                            body="The IVR lab uses the public endpoint selected above and the shared extension directory."
                          />
                        )}
                      </CardContent>
                    </Card>

                    <Card>
                      <CardHeader>
                        <CardTitle className="text-base">Runtime status</CardTitle>
                        <CardDescription>Local API connectivity and available provider adapters.</CardDescription>
                      </CardHeader>
                      <CardContent className="space-y-4">
                        {[
                          {
                            icon: Server,
                            title: "Simulator API",
                            detail: online ? `Online at ${SIMULATOR_API_URL}` : "Not reachable",
                          },
                          { icon: Database, title: "Persistent state", detail: "Managed by the simulator backend" },
                          { icon: Cable, title: "Provider adapters", detail: `${catalog.length} registered` },
                        ].map(({ icon: Icon, title, detail }) => (
                          <div className="flex items-center gap-3" key={title}>
                            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-muted">
                              <Icon className="h-4 w-4" />
                            </div>
                            <div>
                              <p className="text-sm font-medium">{title}</p>
                              <p className="text-xs text-muted-foreground">{detail}</p>
                            </div>
                          </div>
                        ))}
                      </CardContent>
                    </Card>
                  </div>

                  <Card className="bg-surface-1">
                    <CardHeader>
                      <CardTitle className="text-base">Recent simulation calls</CardTitle>
                      <CardDescription>
                        First-class caller, route, outcome, and duration records from the unified run store.
                      </CardDescription>
                    </CardHeader>
                    <CardContent className="overflow-x-auto">
                      {completedSimulationRuns.length ? (
                        <Table className="min-w-[720px]">
                          <TableHeader>
                            <TableRow>
                              <TableHead>Caller</TableHead>
                              <TableHead>Extension</TableHead>
                              <TableHead>Destination</TableHead>
                              <TableHead>Provider</TableHead>
                              <TableHead>Outcome</TableHead>
                              <TableHead className="text-right">Duration</TableHead>
                            </TableRow>
                          </TableHeader>
                          <TableBody>
                            {completedSimulationRuns.slice(0, 6).map((run) => (
                              <TableRow key={run.id}>
                                <TableCell>
                                  {run.caller_number ?? run.result.caller_number ?? "—"}
                                </TableCell>
                                <TableCell className="font-mono text-xs">
                                  {run.extension ?? run.result.extension ?? "—"}
                                </TableCell>
                                <TableCell className="font-mono text-xs">
                                  {run.destination ?? run.result.destination ?? "—"}
                                </TableCell>
                                <TableCell>{run.provider}</TableCell>
                                <TableCell>{statusBadge(runOutcome(run) || run.status)}</TableCell>
                                <TableCell className="text-right">
                                  {runDuration(run)}s
                                </TableCell>
                              </TableRow>
                            ))}
                          </TableBody>
                        </Table>
                      ) : (
                        <EmptyState
                          icon={FlaskConical}
                          title="No simulation calls yet"
                          body="Use the scenario runner or interactive IVR lab to populate call analytics."
                        />
                      )}
                    </CardContent>
                  </Card>
                </div>
              )}

              {view === "providers" && (
                <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_340px]">
                  <div className="space-y-4">
                    {connections.length ? (
                      connections.map((connection) => {
                        const descriptor = catalog.find((item) => item.key === connection.provider);
                        return (
                          <Card key={connection.id}>
                            <CardContent className="flex items-start gap-4 p-5 md:p-6">
                              <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-muted">
                                <Cable className="h-4 w-4" />
                              </div>
                              <div className="min-w-0 flex-1">
                                <div className="flex flex-wrap items-center gap-2">
                                  <p className="font-medium">{connection.name}</p>
                                  <Badge variant="outline">{descriptor?.name ?? connection.provider}</Badge>
                                  <Badge variant={connection.runtime.ready ? "secondary" : "outline"}>
                                    {connection.status.replaceAll("_", " ")}
                                  </Badge>
                                </div>
                                <p className="mt-2 text-sm text-muted-foreground">
                                  {connection.description ||
                                    connection.runtime.message ||
                                    descriptor?.description}
                                </p>
                              </div>
                              <Button
                                type="button"
                                variant="ghost"
                                size="icon"
                                disabled={busy}
                                aria-label={`Edit provider ${connection.name}`}
                                onClick={() => setEditingConnectionId(connection.id)}
                              >
                                <Pencil />
                              </Button>
                              <Button
                                type="button"
                                variant="ghost"
                                size="icon"
                                disabled={busy}
                                aria-label={`Delete provider ${connection.name}`}
                                onClick={() => void removeConnection(connection.id)}
                              >
                                <Trash2 />
                              </Button>
                            </CardContent>
                          </Card>
                        );
                      })
                    ) : (
                      <EmptyState
                        icon={Cable}
                        title="No providers configured"
                        body="Start with the local simulator. It has no credentials and exercises the entire control plane."
                      />
                    )}
                  </div>
                  {editingConnection ? (
                    <Card className="h-fit bg-info-soft">
                      <CardHeader>
                        <CardTitle className="text-base">Edit provider</CardTitle>
                        <CardDescription>
                          Update the operator-facing profile and non-secret adapter settings.
                        </CardDescription>
                      </CardHeader>
                      <CardContent>
                        <ProviderConnectionEditor
                          key={editingConnection.id}
                          connection={editingConnection}
                          busy={busy}
                          onCancel={() => setEditingConnectionId(null)}
                          onSave={(values) => saveConnection(editingConnection.id, values)}
                        />
                      </CardContent>
                    </Card>
                  ) : (
                    <Card className="h-fit">
                      <CardHeader>
                        <CardTitle className="text-base">Add provider</CardTitle>
                        <CardDescription>
                          Credentials remain in the simulator environment.
                        </CardDescription>
                      </CardHeader>
                      <CardContent>
                        <form className="grid gap-4" onSubmit={submitConnection}>
                        <div className="grid gap-2">
                          <Label htmlFor="provider-type">Provider</Label>
                          <Select
                            value={providerType}
                            onValueChange={setProviderType}
                          >
                            <SelectTrigger id="provider-type">
                              <SelectValue placeholder="Select provider" />
                            </SelectTrigger>
                            <SelectContent>
                              {catalog.map((provider) => (
                                <SelectItem value={provider.key} key={provider.key}>
                                  {provider.name}{provider.status === "preview" ? " (preview)" : ""}
                                </SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                          <p className="text-xs text-muted-foreground">{selectedDescriptor?.description}</p>
                        </div>
                        <div className="grid gap-2">
                          <Label htmlFor="provider-name">Connection name</Label>
                          <Input
                            id="provider-name"
                            value={providerName}
                            onChange={(event) => setProviderName(event.target.value)}
                            placeholder="Local simulator"
                          />
                        </div>
                        <div className="grid grid-cols-2 gap-3">
                          <div className="grid gap-2">
                            <Label htmlFor="provider-status">Status</Label>
                            <Select
                              value={providerStatus}
                              onValueChange={(value) =>
                                setProviderStatus(value as ProviderConnection["status"])
                              }
                            >
                              <SelectTrigger id="provider-status">
                                <SelectValue />
                              </SelectTrigger>
                              <SelectContent>
                                <SelectItem value="ready">Ready</SelectItem>
                                <SelectItem value="needs_setup">Needs setup</SelectItem>
                                <SelectItem value="disabled">Disabled</SelectItem>
                              </SelectContent>
                            </Select>
                          </div>
                          <div className="grid gap-2">
                            <Label htmlFor="provider-description">Description</Label>
                            <Input
                              id="provider-description"
                              value={providerDescription}
                              onChange={(event) =>
                                setProviderDescription(event.target.value)
                              }
                              placeholder="What this profile is for"
                            />
                          </div>
                        </div>
                        <div className="grid gap-2">
                          <Label htmlFor="provider-settings">Settings JSON</Label>
                          <textarea
                            id="provider-settings"
                            className="min-h-24 w-full resize-y rounded-xl bg-background/80 px-3 py-2 font-mono text-xs outline-none transition-colors focus:bg-muted/70"
                            value={providerSettings}
                            onChange={(event) => setProviderSettings(event.target.value)}
                            spellCheck={false}
                          />
                          <p className="text-xs text-muted-foreground">
                            Optional non-secret adapter configuration as a JSON object.
                          </p>
                        </div>
                        <Button disabled={busy || !providerName.trim()}>
                          {busy ? <Loader2 className="animate-spin" /> : <Plus />}
                          Add connection
                        </Button>
                        </form>
                      </CardContent>
                    </Card>
                  )}
                </div>
              )}

              {view === "endpoints" && (
                <div className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1fr)_380px]">
                  <div className="min-w-0 space-y-4">
                    {endpoints.length ? (
                      endpoints.map((endpoint) => (
                        <Card key={endpoint.id}>
                          <CardContent className="p-5 md:p-6">
                            <div className="flex flex-wrap items-start justify-between gap-4">
                              <div>
                                <div className="flex flex-wrap items-center gap-2">
                                  <p className="font-medium">{endpoint.name}</p>
                                  <Badge variant="outline">{endpoint.kind.replace("_", " ")}</Badge>
                                </div>
                                <p className="mt-1 font-mono text-sm text-muted-foreground">
                                  {endpoint.address}
                                </p>
                              </div>
                              <div className="flex flex-wrap items-center gap-2">
                                <Badge variant="secondary">{endpoint.provider}</Badge>
                                <Button
                                  type="button"
                                  variant="ghost"
                                  size="icon"
                                  disabled={busy}
                                  aria-label={`Edit endpoint ${endpoint.name}`}
                                  onClick={() => setEditingEndpointId(endpoint.id)}
                                >
                                  <Pencil />
                                </Button>
                                <Button
                                  type="button"
                                  variant="ghost"
                                  size="sm"
                                  disabled={busy}
                                  onClick={() => void toggleEndpoint(endpoint)}
                                >
                                  {endpoint.enabled ? <Check /> : <CircleAlert />}
                                  {endpoint.enabled ? "Enabled" : "Disabled"}
                                </Button>
                                <Button
                                  type="button"
                                  variant="ghost"
                                  size="icon"
                                  disabled={busy}
                                  aria-label={`Delete endpoint ${endpoint.name}`}
                                  onClick={() => void removeEndpoint(endpoint.id)}
                                >
                                  <Trash2 />
                                </Button>
                              </div>
                            </div>
                            <div className="mt-4 flex flex-wrap gap-x-6 gap-y-2 text-xs text-muted-foreground">
                              <span>Connection: {endpoint.connection_name}</span>
                              <span>Route: {endpoint.routing_mode}</span>
                              <span>Default: {endpoint.default_scenario ?? "none"}</span>
                            </div>
                          </CardContent>
                        </Card>
                      ))
                    ) : (
                      <EmptyState
                        icon={Route}
                        title="No endpoints configured"
                        body="An endpoint is a provider-neutral phone number, SIP URI, or extension."
                      />
                    )}
                  </div>
                  {editingEndpoint ? (
                    <Card className="h-fit bg-info-soft">
                      <CardHeader>
                        <CardTitle className="text-base">Edit endpoint</CardTitle>
                        <CardDescription>
                          Change its provider binding, address, scenario, routing, or status.
                        </CardDescription>
                      </CardHeader>
                      <CardContent>
                        <EndpointEditor
                          key={editingEndpoint.id}
                          endpoint={editingEndpoint}
                          connections={connections}
                          catalog={catalog}
                          scenarios={scenarios}
                          busy={busy}
                          onCancel={() => setEditingEndpointId(null)}
                          onSave={(values) => saveEndpoint(editingEndpoint.id, values)}
                        />
                      </CardContent>
                    </Card>
                  ) : (
                    <Card className="h-fit">
                      <CardHeader>
                        <CardTitle className="text-base">Add endpoint</CardTitle>
                        <CardDescription>
                          Attach a callable address to a provider connection.
                        </CardDescription>
                      </CardHeader>
                      <CardContent>
                        {connections.length ? (
                        <form className="grid gap-4" onSubmit={submitEndpoint}>
                          <div className="grid gap-2">
                            <Label htmlFor="endpoint-provider">Connection</Label>
                            <Select
                              value={endpointConnection}
                              onValueChange={(value) => {
                                setEndpointConnection(value);
                                const connection = connections.find((item) => item.id === value);
                                const kinds = catalog.find((item) => item.key === connection?.provider)?.endpoint_kinds;
                                if (kinds?.[0]) setEndpointKind(kinds[0]);
                                const compatible = scenariosForProvider(scenarios, connection?.provider, catalog);
                                setEndpointScenario(compatible[0]?.name ?? "");
                              }}
                            >
                              <SelectTrigger id="endpoint-provider">
                                <SelectValue placeholder="Select connection" />
                              </SelectTrigger>
                              <SelectContent>
                                {connections.map((connection) => (
                                  <SelectItem value={connection.id} key={connection.id}>
                                    {connection.name}
                                  </SelectItem>
                                ))}
                              </SelectContent>
                            </Select>
                          </div>
                          <div className="grid gap-2">
                            <Label htmlFor="endpoint-name">Name</Label>
                            <Input id="endpoint-name" value={endpointName} onChange={(event) => setEndpointName(event.target.value)} />
                          </div>
                          <div className="grid grid-cols-2 gap-3">
                            <div className="grid gap-2">
                              <Label htmlFor="endpoint-kind">Kind</Label>
                              <Select value={endpointKind} onValueChange={(value) => setEndpointKind(value as Endpoint["kind"])}>
                                <SelectTrigger id="endpoint-kind">
                                  <SelectValue placeholder="Select kind" />
                                </SelectTrigger>
                                <SelectContent>
                                  {endpointKinds.map((kind) => (
                                    <SelectItem key={kind} value={kind}>
                                      {kind.replace("_", " ")}
                                    </SelectItem>
                                  ))}
                                </SelectContent>
                              </Select>
                            </div>
                            <div className="grid gap-2">
                              <Label htmlFor="endpoint-address">Address</Label>
                              <Input id="endpoint-address" value={endpointAddress} onChange={(event) => setEndpointAddress(event.target.value)} />
                            </div>
                          </div>
                          <div className="grid gap-2">
                            <Label htmlFor="endpoint-scenario">Default scenario</Label>
                            <Select value={endpointScenario} onValueChange={setEndpointScenario}>
                              <SelectTrigger id="endpoint-scenario">
                                <SelectValue placeholder="Select scenario" />
                              </SelectTrigger>
                              <SelectContent>
                                {endpointScenarios.map((scenario) => (
                                  <SelectItem value={scenario.name} key={scenario.name}>
                                    {scenario.title}
                                  </SelectItem>
                                ))}
                              </SelectContent>
                            </Select>
                          </div>
                          <div className="grid gap-2">
                            <Label htmlFor="endpoint-routing">Routing mode</Label>
                            <Select
                              value={endpointRoutingMode}
                              onValueChange={(value) =>
                                setEndpointRoutingMode(value as Endpoint["routing_mode"])
                              }
                            >
                              <SelectTrigger id="endpoint-routing">
                                <SelectValue />
                              </SelectTrigger>
                              <SelectContent>
                                <SelectItem value="fixed">Fixed default</SelectItem>
                                <SelectItem value="queued">Queued override</SelectItem>
                              </SelectContent>
                            </Select>
                            <p className="text-xs text-muted-foreground">
                              Fixed uses the endpoint default. Queued accepts a per-run override
                              with default fallback.
                            </p>
                          </div>
                          <Button
                            disabled={
                              busy ||
                              !endpointAddress.trim() ||
                              (selectedConnection?.provider !== "telnyx" && !endpointScenario)
                            }
                          >
                            {busy ? <Loader2 className="animate-spin" /> : <Plus />}
                            Add endpoint
                          </Button>
                        </form>
                        ) : (
                          <EmptyState
                            icon={Cable}
                            title="Add a provider first"
                            body="Endpoints always belong to a provider connection."
                          />
                        )}
                      </CardContent>
                    </Card>
                  )}
                </div>
              )}

              {view === "directory" && (
                <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_340px]">
                  <Card className="min-w-0 bg-accent-soft">
                    <CardHeader>
                      <CardTitle className="text-base">Extension directory</CardTitle>
                      <CardDescription>
                        These routes drive both the interactive simulator and the live Twilio IVR.
                      </CardDescription>
                    </CardHeader>
                    <CardContent className="overflow-x-auto">
                      {directory.length ? (
                        <Table className="min-w-[680px]">
                          <TableHeader>
                            <TableRow>
                              <TableHead>Extension</TableHead>
                              <TableHead>Name</TableHead>
                              <TableHead>Destination</TableHead>
                              <TableHead>Provider</TableHead>
                              <TableHead>Timeout</TableHead>
                              <TableHead>Status</TableHead>
                              <TableHead className="w-20" />
                            </TableRow>
                          </TableHeader>
                          <TableBody>
                            {directory.map((entry) => (
                              <TableRow key={entry.id}>
                                <TableCell>
                                  <span className="rounded-md bg-muted/70 px-2 py-1 font-mono text-xs font-medium text-primary">
                                    {entry.extension}
                                  </span>
                                </TableCell>
                                <TableCell>
                                  <p className="font-medium">{entry.name}</p>
                                  <p className="text-xs text-muted-foreground">
                                    {entry.department}
                                  </p>
                                </TableCell>
                                <TableCell className="font-mono text-xs">
                                  {entry.destination}
                                </TableCell>
                                <TableCell>
                                  <Badge variant="secondary">{entry.provider}</Badge>
                                </TableCell>
                                <TableCell>{entry.ring_timeout}s</TableCell>
                                <TableCell>
                                  <Button
                                    type="button"
                                    variant="ghost"
                                    size="sm"
                                    disabled={busy}
                                    onClick={() => void toggleDirectoryEntry(entry)}
                                  >
                                    <span
                                      className={cn(
                                        "size-2 rounded-full",
                                        entry.enabled ? "bg-success" : "bg-subtle-foreground"
                                      )}
                                    />
                                    {entry.enabled ? "Enabled" : "Disabled"}
                                  </Button>
                                </TableCell>
                                <TableCell>
                                  <div className="flex items-center justify-end">
                                    <Button
                                      type="button"
                                      variant="ghost"
                                      size="icon"
                                      disabled={busy}
                                      aria-label={`Edit extension ${entry.extension}`}
                                      onClick={() => setEditingDirectoryId(entry.id)}
                                    >
                                      <Pencil />
                                    </Button>
                                  <Button
                                    type="button"
                                    variant="ghost"
                                    size="icon"
                                    disabled={busy}
                                    aria-label={`Delete extension ${entry.extension}`}
                                    onClick={() => void removeDirectoryEntry(entry.id)}
                                  >
                                    <Trash2 />
                                  </Button>
                                  </div>
                                </TableCell>
                              </TableRow>
                            ))}
                          </TableBody>
                        </Table>
                      ) : (
                        <EmptyState
                          icon={ContactRound}
                          title="No directory routes"
                          body="Add an extension to simulate inbound IVR routing or serve it from /twilio/ivr."
                        />
                      )}
                    </CardContent>
                  </Card>

                  {editingDirectoryEntry ? (
                    <Card className="h-fit bg-accent-soft">
                      <CardHeader>
                        <CardTitle className="text-base">Edit extension</CardTitle>
                        <CardDescription>
                          Update its provider-backed destination and live routing behavior.
                        </CardDescription>
                      </CardHeader>
                      <CardContent>
                        <DirectoryEntryEditor
                          key={editingDirectoryEntry.id}
                          entry={editingDirectoryEntry}
                          connections={connections}
                          busy={busy}
                          onCancel={() => setEditingDirectoryId(null)}
                          onSave={(values) =>
                            saveDirectoryEntry(editingDirectoryEntry.id, values)
                          }
                        />
                      </CardContent>
                    </Card>
                  ) : (
                    <Card className="h-fit bg-accent-soft">
                      <CardHeader>
                        <CardTitle className="text-base">Add extension</CardTitle>
                        <CardDescription>
                          Map digits to a provider-backed destination.
                        </CardDescription>
                      </CardHeader>
                      <CardContent>
                        {connections.length ? (
                        <form className="grid gap-4" onSubmit={submitDirectoryEntry}>
                          <div className="grid gap-2">
                            <Label htmlFor="directory-provider">Provider connection</Label>
                            <Select
                              value={directoryConnection}
                              onValueChange={setDirectoryConnection}
                            >
                              <SelectTrigger id="directory-provider">
                                <SelectValue placeholder="Select connection" />
                              </SelectTrigger>
                              <SelectContent>
                                {connections.map((connection) => (
                                  <SelectItem value={connection.id} key={connection.id}>
                                    {connection.name} · {connection.provider}
                                  </SelectItem>
                                ))}
                              </SelectContent>
                            </Select>
                          </div>
                          <div className="grid grid-cols-2 gap-3">
                            <div className="grid gap-2">
                              <Label htmlFor="directory-extension">Extension</Label>
                              <Input
                                id="directory-extension"
                                inputMode="numeric"
                                minLength={4}
                                maxLength={4}
                                pattern="[0-9]{4}"
                                value={directoryExtension}
                                onChange={(event) => setDirectoryExtension(event.target.value)}
                              />
                            </div>
                            <div className="grid gap-2">
                              <Label htmlFor="directory-timeout">Timeout</Label>
                              <Input
                                id="directory-timeout"
                                type="number"
                                min="5"
                                max="120"
                                value={directoryTimeout}
                                onChange={(event) => setDirectoryTimeout(event.target.value)}
                              />
                            </div>
                          </div>
                          <div className="grid gap-2">
                            <Label htmlFor="directory-name">Display name</Label>
                            <Input
                              id="directory-name"
                              value={directoryName}
                              onChange={(event) => setDirectoryName(event.target.value)}
                            />
                          </div>
                          <div className="grid gap-2">
                            <Label htmlFor="directory-destination">Destination</Label>
                            <Input
                              id="directory-destination"
                              value={directoryDestination}
                              onChange={(event) =>
                                setDirectoryDestination(event.target.value)
                              }
                            />
                          </div>
                          <div className="grid gap-2">
                            <Label htmlFor="directory-department">Department</Label>
                            <Input
                              id="directory-department"
                              value={directoryDepartment}
                              onChange={(event) =>
                                setDirectoryDepartment(event.target.value)
                              }
                            />
                          </div>
                          <Button
                            disabled={
                              busy ||
                              !directoryConnection ||
                              !/^[0-9]{4}$/.test(directoryExtension) ||
                              !directoryDestination
                            }
                          >
                            {busy ? <Loader2 className="animate-spin" /> : <Plus />}
                            Add extension
                          </Button>
                        </form>
                        ) : (
                          <EmptyState
                            icon={Cable}
                            title="Add a provider first"
                            body="Every directory destination is attached to a provider connection."
                          />
                        )}
                      </CardContent>
                    </Card>
                  )}
                </div>
              )}

              {view === "scenarios" && (
                <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                  {scenarios.map((scenario) => {
                    const main = scenario.sequences.find((sequence) => sequence.name === "main");
                    return (
                      <Card key={scenario.name}>
                        <CardHeader>
                          <div className="mb-2 flex items-center justify-between">
                            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-muted">
                              <Workflow className="h-4 w-4" />
                            </div>
                            <div className="flex gap-1.5">
                              <Badge variant="secondary">{scenario.kind.toUpperCase()}</Badge>
                              {scenario.has_dtmf && <Badge variant="outline">DTMF</Badge>}
                              {scenario.pstn_only && <Badge variant="secondary">PSTN</Badge>}
                              {scenario.simulation_only && <Badge variant="outline">Model</Badge>}
                            </div>
                          </div>
                          <CardTitle className="text-base">{scenario.title}</CardTitle>
                          <CardDescription className="font-mono text-xs">{scenario.name}</CardDescription>
                          <p className="mt-2 text-sm leading-6 text-muted-foreground">
                            {scenario.description}
                          </p>
                        </CardHeader>
                        <CardContent>
                          <div className="flex gap-5 text-xs text-muted-foreground">
                            <span>{main?.steps.length ?? 0} steps</span>
                            <span>{scenario.expectation_count} checks</span>
                            <span>{scenario.sequences.length} sequences</span>
                          </div>
                        </CardContent>
                      </Card>
                    );
                  })}
                </div>
              )}

              {view === "runs" && <RunHistory runs={runs} endpoints={endpoints} busy={busy} onCancel={cancelRun} />}
              {view === "calls" && <CallHistory calls={calls} busy={busy} onDelete={removeCall} />}
            </>
          )}
      </div>
    </PageShell>
  );
}
