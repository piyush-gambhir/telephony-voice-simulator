export const SIMULATOR_API_URL =
  process.env.NEXT_PUBLIC_SIMULATOR_API_URL ??
  (process.env.NODE_ENV === "development"
    ? "/simulator-api"
    : "http://127.0.0.1:8978");

export type RuntimeStatus = {
  ready: boolean;
  message?: string;
  credentials?: boolean;
  public_url?: boolean;
};

export type ProviderDescriptor = {
  key: string;
  name: string;
  description: string;
  status: "ready" | "preview";
  endpoint_kinds: Array<"phone_number" | "sip_uri" | "extension">;
  capabilities: Record<string, boolean>;
  supported_scenario_kinds: Array<Scenario["kind"]>;
  runtime: RuntimeStatus;
};

export type ProviderConnection = {
  id: string;
  provider: string;
  name: string;
  status: "ready" | "needs_setup" | "disabled";
  description: string;
  enabled: boolean;
  settings: Record<string, unknown>;
  created_at: string;
  runtime: RuntimeStatus;
};

export type Endpoint = {
  id: string;
  connection_id: string;
  connection_name: string;
  provider: string;
  name: string;
  kind: "phone_number" | "sip_uri" | "extension";
  address: string;
  routing_mode: "fixed" | "queued";
  default_scenario: string | null;
  enabled: boolean;
  created_at: string;
};

export type ManagedNumber = {
  id: string;
  provider: "twilio";
  connection_id: string;
  endpoint_id: string;
  provider_resource_id: string;
  phone_number: string;
  friendly_name: string;
  voice_url: string;
  voice_method: string;
  status: string;
  managed_mode: "amd";
  capabilities: Record<string, boolean>;
  configuration: {
    previous_voice_url?: string;
    previous_voice_method?: string;
    record_full_calls?: boolean;
  };
  last_synced_at: string;
  updated_at: string;
  endpoint: Endpoint;
  target_voice_url: string | null;
  attached_to_runtime: boolean;
  pending_runs: number;
};

export type DirectoryEntry = {
  id: string;
  connection_id: string;
  connection_name: string;
  provider: string;
  extension: string;
  name: string;
  destination: string;
  department: string;
  ring_timeout: number;
  enabled: boolean;
  created_at: string;
};

export type ScenarioStep = {
  index: number;
  kind: string;
  label: string;
  event?: string;
  duration_s?: number;
  asset?: string;
  state?: string;
};

export type Scenario = {
  name: string;
  title: string;
  kind: "amd" | "ivr" | "pbx";
  description: string;
  pstn_only: boolean;
  simulation_only: boolean;
  expectation_count: number;
  expected_outcome: string | null;
  has_dtmf: boolean;
  sequences: Array<{ name: string; steps: ScenarioStep[] }>;
};

export type SimulationRun = {
  id: string;
  endpoint_id: string;
  provider: string;
  scenario: string;
  status: string;
  caller_number: string | null;
  extension: string | null;
  destination: string | null;
  outcome: string | null;
  duration_seconds: number | null;
  timeline: ScenarioStep[];
  result: {
    graded?: boolean;
    passed?: boolean | null;
    carrier_status?: string;
    mode?: string;
    summary?: string;
    error?: string;
    outcome?: string;
    call_outcome?: string;
    caller_number?: string;
    extension?: string;
    destination?: string | null;
    duration_seconds?: number;
    department?: string;
    analysis?: {
      passed?: boolean | null;
      checks?: Array<{
        check?: string;
        name?: string;
        passed?: boolean | null;
        detail?: string;
      }>;
    };
  };
  created_at: string;
  completed_at: string | null;
};

export type CallRecording = {
  id: string;
  call_id: string;
  provider: string;
  kind: "full_call" | "message";
  status: string;
  duration_s: number | null;
  channels: number | null;
  provider_url: string | null;
  local_path: string | null;
  created_at: string;
  updated_at: string;
};

export type IncomingCall = {
  id: string;
  provider: string;
  direction: "inbound";
  from_address: string;
  to_address: string;
  scenario: string | null;
  status: string;
  recording_status: string;
  started_at: string;
  ended_at: string | null;
  duration_s: number | null;
  analysis: { passed?: boolean | null };
  recordings: CallRecording[];
  created_at: string;
  updated_at: string;
};

export class SimulatorApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
    this.name = "SimulatorApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  const timeout = setTimeout(abort, 15_000);
  init?.signal?.addEventListener("abort", abort, { once: true });
  if (init?.signal?.aborted) controller.abort();
  try {
    const headers = new Headers(init?.headers);
    headers.set("Accept", "application/json");
    if (init?.body) headers.set("Content-Type", "application/json");
    const response = await fetch(`${SIMULATOR_API_URL.replace(/\/+$/, "")}${path}`, {
      ...init,
      headers,
      signal: controller.signal,
      cache: "no-store",
    });
    if (!response.ok) {
      const body = (await response.json().catch(() => ({}))) as { error?: unknown };
      throw new SimulatorApiError(
        typeof body.error === "string" ? body.error : `Simulator API returned ${response.status}`,
        response.status,
      );
    }
    if (response.status === 204) return undefined as T;
    return await response.json() as T;
  } catch (cause) {
    if (controller.signal.aborted && !init?.signal?.aborted) {
      throw new Error("Simulator API timed out. Check that the backend is running.");
    }
    throw cause;
  } finally {
    clearTimeout(timeout);
    init?.signal?.removeEventListener("abort", abort);
  }
}

export const simulatorApi = {
  providerCatalog: (signal?: AbortSignal) =>
    request<ProviderDescriptor[]>("/api/providers/catalog", { signal }),
  amdNumbers: (signal?: AbortSignal) => request<ManagedNumber[]>("/api/amd-numbers", { signal }),
  syncAmdNumbers: () =>
    request<ManagedNumber[]>("/api/amd-numbers/sync", { method: "POST" }),
  updateAmdNumber: (
    numberId: string,
    body: {
      friendly_name?: string;
      default_scenario?: string | null;
      routing_mode?: Endpoint["routing_mode"];
      enabled?: boolean;
      record_full_calls?: boolean;
    }
  ) =>
    request<ManagedNumber>(`/api/amd-numbers/${encodeURIComponent(numberId)}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  attachAmdNumber: (numberId: string) =>
    request<ManagedNumber>(
      `/api/amd-numbers/${encodeURIComponent(numberId)}/attach`,
      { method: "POST" }
    ),
  restoreAmdNumber: (numberId: string) =>
    request<ManagedNumber>(
      `/api/amd-numbers/${encodeURIComponent(numberId)}/restore`,
      { method: "POST" }
    ),
  queueAmdNumber: (numberId: string, scenario: string) =>
    request<{ number: ManagedNumber; run: SimulationRun }>(
      `/api/amd-numbers/${encodeURIComponent(numberId)}/queue`,
      {
        method: "POST",
        body: JSON.stringify({ scenario }),
      }
    ),
  connections: (signal?: AbortSignal) => request<ProviderConnection[]>("/api/providers", { signal }),
  createConnection: (body: {
    provider: string;
    name: string;
    status: ProviderConnection["status"];
    description: string;
    settings: Record<string, unknown>;
  }) =>
    request<ProviderConnection>("/api/providers", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updateConnection: (
    connectionId: string,
    body: Partial<
      Pick<ProviderConnection, "name" | "status" | "description" | "settings">
    >
  ) =>
    request<ProviderConnection>(
      `/api/providers/${encodeURIComponent(connectionId)}`,
      {
        method: "PATCH",
        body: JSON.stringify(body),
      }
    ),
  deleteConnection: (connectionId: string) =>
    request<void>(`/api/providers/${encodeURIComponent(connectionId)}`, {
      method: "DELETE",
    }),
  endpoints: (signal?: AbortSignal) => request<Endpoint[]>("/api/endpoints", { signal }),
  createEndpoint: (body: {
    connection_id: string;
    name: string;
    kind: Endpoint["kind"];
    address: string;
    routing_mode: Endpoint["routing_mode"];
    default_scenario?: string;
  }) =>
    request<Endpoint>("/api/endpoints", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updateEndpoint: (
    endpointId: string,
    body: Partial<
      Pick<
        Endpoint,
        | "connection_id"
        | "name"
        | "kind"
        | "address"
        | "routing_mode"
        | "default_scenario"
        | "enabled"
      >
    >
  ) =>
    request<Endpoint>(`/api/endpoints/${encodeURIComponent(endpointId)}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  deleteEndpoint: (endpointId: string) =>
    request<void>(`/api/endpoints/${encodeURIComponent(endpointId)}`, {
      method: "DELETE",
    }),
  directory: (signal?: AbortSignal) => request<DirectoryEntry[]>("/api/directory", { signal }),
  createDirectoryEntry: (body: {
    connection_id: string;
    extension: string;
    name: string;
    destination: string;
    department: string;
    ring_timeout: number;
  }) =>
    request<DirectoryEntry>("/api/directory", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updateDirectoryEntry: (
    entryId: string,
    body: Partial<
      Pick<
        DirectoryEntry,
        | "connection_id"
        | "extension"
        | "name"
        | "destination"
        | "department"
        | "ring_timeout"
        | "enabled"
      >
    >
  ) =>
    request<DirectoryEntry>(`/api/directory/${encodeURIComponent(entryId)}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  deleteDirectoryEntry: (entryId: string) =>
    request<void>(`/api/directory/${encodeURIComponent(entryId)}`, {
      method: "DELETE",
    }),
  scenarios: (signal?: AbortSignal) => request<Scenario[]>("/api/scenarios", { signal }),
  runs: (signal?: AbortSignal) => request<SimulationRun[]>("/api/runs", { signal }),
  calls: (signal?: AbortSignal) => request<IncomingCall[]>("/api/calls", { signal }),
  deleteCall: (callId: string) =>
    request<{
      deleted: string;
      already_deleted: boolean;
      recording_files: {
        deleted: number;
        missing: number;
        skipped_unowned: number;
      };
      artifacts_directory_deleted: boolean;
    }>(`/api/calls/${encodeURIComponent(callId)}`, {
      method: "DELETE",
    }),
  recordingMedia: async (recordingId: string, signal?: AbortSignal) => {
    const response = await fetch(
      `${SIMULATOR_API_URL.replace(/\/+$/, "")}/api/recordings/${encodeURIComponent(recordingId)}/media`,
      { signal },
    );
    if (!response.ok) {
      throw new Error(`Recording API returned ${response.status}`);
    }
    return response.blob();
  },
  cancelRun: (runId: string) =>
    request<SimulationRun>(`/api/runs/${encodeURIComponent(runId)}/cancel`, {
      method: "POST",
    }),
  createRun: (body: { endpoint_id: string; scenario: string }) =>
    request<SimulationRun>("/api/runs", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  createIvrSimulation: (body: {
    endpoint_id: string;
    caller_number: string;
    extension: string;
    disposition: "connected" | "busy" | "no_answer" | "failed" | "abandoned";
  }) =>
    request<SimulationRun>("/api/ivr/simulations", {
      method: "POST",
      body: JSON.stringify(body),
    }),
};
