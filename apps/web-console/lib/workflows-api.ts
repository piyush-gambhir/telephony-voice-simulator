/**
 * Client for the simulator's workflow routes: models, the dealer PBX, hosted Twilio,
 * account numbers, agents, benchmarks, UAT, corpus, inbox and operations.
 *
 * Long jobs answer 202 with an Operation; poll it with `operations.get` until it settles.
 */
import { request } from "@/lib/simulator-api";

type Json = Record<string, unknown>;

const post = <T>(path: string, body: unknown = {}) =>
  request<T>(path, { method: "POST", body: JSON.stringify(body) });
const get = <T>(path: string, signal?: AbortSignal) => request<T>(path, { signal });
const q = (value: string) => encodeURIComponent(value);

/* ---------- operations ---------- */

export type OperationStatus = "queued" | "running" | "succeeded" | "failed";

export type Operation<R = unknown> = {
  id: string;
  kind: string;
  title: string;
  status: OperationStatus;
  params: Json;
  result: R | null;
  error: string | null;
  log: string[];
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
};

export const operations = {
  list: (kind?: string, signal?: AbortSignal) =>
    get<Operation[]>(`/api/operations${kind ? `?kind=${q(kind)}` : ""}`, signal),
  get: (id: string, signal?: AbortSignal) => get<Operation>(`/api/operations/${q(id)}`, signal),
};

/* ---------- built-in models ---------- */

export type TimelineStep = {
  index?: number;
  kind: string;
  label?: string;
  detail?: string;
  at?: string;
  actor?: string;
  [key: string]: unknown;
};

export type ModelResult = {
  scenario_id: string;
  title: string;
  outcome: string;
  timeline: TimelineStep[];
  caller?: string;
  caller_number?: string;
  dialed_number?: string;
  public_number?: string;
  intent?: string;
  expected_summary?: string;
  connected_to?: string | null;
  attempts?: number;
  overrides?: Record<string, string>;
};

export const models = {
  simulate: (body: { kind: "ivr" | "pbx"; scenario?: string; overrides?: Record<string, string> }) =>
    post<ModelResult | ModelResult[]>("/api/models/simulate", body),
  pbxExtensions: (signal?: AbortSignal) =>
    get<{ extensions: Array<{ id: string; name: string }>; states: string[] }>("/api/models/pbx-extensions", signal),
};

/* ---------- dealer-group PBX ---------- */

export type DealerSite = {
  id: string;
  label: string;
  platform: string;
  border_element: string;
  main_did: string;
  extension_range: string;
  attendant: string;
  trunks: string[];
  night_target: string;
  notes: string;
};

export type DealerMap = {
  group: string;
  intersite_prefix: string;
  sites: DealerSite[];
  trunks: Array<{ id: string; label: string; site: string; peer_site: string | null; kind: string; signaling: string; channels: number; dtmf_mode: string; glare_prone: boolean }>;
  dids: Array<{ number: string; label: string; site: string; target: string; tracking_source: string | null }>;
  agents: Array<{ id: string; label: string; site: string; ingress: string; address: string; reached_via: string; receives_diversion: boolean; notes: string }>;
  transfer_matrix: unknown;
};

export type DealerCallRequest = {
  to_number: string;
  from_number?: string;
  day?: string;
  time?: string;
  date?: string | null;
  digits?: string[];
  intent?: string;
  patience_seconds?: number;
  presence?: Record<string, string>;
  occupied_channels?: Record<string, number>;
  glare_trunks?: string[];
  transfer_method?: string | null;
  transfer_target?: string | null;
  transfer_agent?: string;
};

export const DEALER_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"] as const;
export const DEALER_PRESENCE = ["available", "busy", "with_customer", "offline", "dnd"] as const;
export const DEALER_TRANSFERS = ["blind_refer", "attended_refer", "bridge", "dtmf_redial", "sip_302"] as const;

export const pbx = {
  map: (signal?: AbortSignal) => get<DealerMap>("/api/pbx/map", signal),
  route: (body: DealerCallRequest) => post<{ call: Json; transfer?: Json }>("/api/pbx/calls", body),
  cases: (signal?: AbortSignal) => get<Array<{ id: string; title: string; teaches: string }>>("/api/pbx/cases", signal),
  runCase: (id: string) => post<Json>(`/api/pbx/cases/${q(id)}/run`),
  suite: () =>
    post<{ total: number; passed: number; cases: Array<{ id: string; title: string; passed: boolean; outcome: string; transfer: string | null }> }>("/api/pbx/suite"),
  capacity: (site?: string, channels?: number, signal?: AbortSignal) => {
    const params = new URLSearchParams();
    if (site) params.set("site", site);
    if (channels) params.set("channels", String(channels));
    const query = params.toString();
    return get<Json | Json[]>(`/api/pbx/capacity${query ? `?${query}` : ""}`, signal);
  },
};

/* ---------- hosted Twilio callee and account numbers ---------- */

export type AccountNumber = {
  sid: string;
  phone_number: string;
  friendly_name: string;
  voice_url: string;
  voice_method: string | null;
  status_callback: string;
};

export const hosted = {
  deploy: (body: { static_map?: Record<string, string>; default_scenario?: string; acknowledge_public_audio: boolean; audio_deployment_allowlist?: string }) =>
    post<Operation>("/api/hosted/deploy", body),
  queue: (body: { number: string; scenario: string }) =>
    post<{ number: string; scenario: string; queue: string[] }>("/api/hosted/queue", body),
  smoke: (body: { number: string; scenario: string; from_number?: string }) => post<Operation>("/api/hosted/smoke", body),
  grade: (body: { call_sid?: string; number?: string; last?: number }) => post<Operation>("/api/hosted/grade", body),
  agentCall: (body: { scenario?: string; queue?: boolean; wait?: boolean }) => post<Operation>("/api/hosted/agent-call", body),
};

export const accountNumbers = {
  list: (signal?: AbortSignal) => get<AccountNumber[]>("/api/account-numbers", signal),
  backups: (signal?: AbortSignal) => get<Array<{ name: string; modified: number }>>("/api/account-numbers/backups", signal),
  point: (body: { number: string; route?: "ivr" | "amd"; base_url?: string; voice_url?: string; status_callback_url?: string; dry_run?: boolean }) =>
    post<{ number: string; sid: string; change: Json; backup: Json; backup_path: string; applied: boolean }>("/api/account-numbers/point", body),
  revert: (body: { number: string; backup: string; dry_run?: boolean }) =>
    post<{ number: string; sid: string; restore: Json; applied: boolean }>("/api/account-numbers/revert", body),
};

/* ---------- agents ---------- */

export const agents = {
  trigger: (body: { scenario?: string; number?: string }) =>
    post<{ call_id: string; scenario: string; number: string; response: unknown }>("/api/agents/trigger", body),
  call: (body: { scenario?: string; number?: string; timeout?: number }) => post<Operation>("/api/agents/calls", body),
  record: (callId: string) => get<{ record: Json; outcome: Json }>(`/api/agents/calls/${q(callId)}`),
  room: (body: { scenario?: string; all?: boolean; filter?: string }) => post<Operation>("/api/agents/rooms", body),
};

/* ---------- benchmarks ---------- */

export type BenchmarkOptions = {
  suite?: string;
  scenarios?: string[];
  repeat?: number;
  numbers?: string[];
  concurrency?: number;
  pace?: number;
};

export type BenchmarkReportSummary = { report_id: string; [key: string]: unknown };

export const benchmarks = {
  suites: (signal?: AbortSignal) =>
    get<Array<{ name: string; suite: string; scenarios: number; repeat: number | null }>>("/api/benchmarks/suites", signal),
  plan: (body: BenchmarkOptions) => post<Json>("/api/benchmarks/plan", body),
  run: (body: BenchmarkOptions & { audio?: boolean }) => post<Operation>("/api/benchmarks/runs", body),
  reports: (signal?: AbortSignal) => get<BenchmarkReportSummary[]>("/api/benchmarks/reports", signal),
  report: (id: string, signal?: AbortSignal) => get<Json>(`/api/benchmarks/reports/${q(id)}`, signal),
  reportMarkdown: (id: string, signal?: AbortSignal) =>
    get<{ report_id: string; markdown: string }>(`/api/benchmarks/reports/${q(id)}?format=markdown`, signal),
  compare: (reports: string[]) => post<{ labels: string[]; path: string | null; markdown: string }>("/api/benchmarks/compare", { reports }),
  rescore: (id: string) => post<{ summary: Json; markdown: string }>(`/api/benchmarks/reports/${q(id)}/rescore`),
  preflight: (id: string) => get<{ healthy: boolean; detail: string }>(`/api/benchmarks/reports/${q(id)}/preflight`),
};

/* ---------- UAT ---------- */

export const uat = {
  configs: (signal?: AbortSignal) => get<Array<{ name: string }>>("/api/uat/configs", signal),
  check: (config: string) => post<{ valid: boolean; scenarios: unknown; steps: unknown }>("/api/uat/check", { config }),
  plan: (config: string, scenarios?: string[]) =>
    post<{ cases: Array<{ scenario: string; steps: Array<{ name: string; action: string }> }> }>("/api/uat/plan", { config, scenarios }),
  run: (config: string, scenarios?: string[]) => post<Operation>("/api/uat/runs", { config, scenarios }),
};

/* ---------- corpus ---------- */

export const CORPUS_SOURCE_TYPES = [
  "official_public",
  "permissive_internet",
  "internal_device_capture",
  "internal_carrier_capture",
] as const;
export const PROMPT_ENGINES = ["auto", "elevenlabs", "openai", "macos"] as const;

export const corpus = {
  build: (body: { only?: string; force?: boolean; replace_imported?: boolean }) => post<Operation>("/api/corpus/build", body),
  importAudio: (body: { asset: string; source: string; source_type: string; license_note: string; transcript?: string; commit_allowed?: boolean }) =>
    post<Operation>("/api/corpus/import", body),
  importRecording: (body: { call_id: string; asset: string; license_note: string; channel?: 0 | 1; start?: number; duration?: number; transcript?: string; commit_allowed?: boolean }) =>
    post<Operation>("/api/corpus/import-recording", body),
  validatePublic: (allowlist: string) => post<{ valid: boolean; referenced_assets: number }>("/api/corpus/validate-public", { allowlist }),
  prompts: (signal?: AbortSignal) => get<{ valid: boolean; config: string; prompts: number }>("/api/corpus/prompts", signal),
  renderPrompts: (body: { engine?: string; force?: boolean }) => post<Operation>("/api/corpus/prompts/render", body),
};

/* ---------- inbox and analysis ---------- */

export type SmsMessage = { message_sid: string; sender: string; recipient: string; body: string; received_at: string };

export type CustomerSession = {
  call_id: string;
  direction: string;
  from: string;
  to: string;
  status: string;
  started_at: string;
  ended_at: string | null;
  duration_s: number | null;
  stopped: unknown;
  error_type: string | null;
  /** ElevenLabs conversation state: "registered" until it is logged, then "processing" or "done". */
  transcript_status: string | null;
  /** "user" is the agent being tested; "assistant" is the synthetic customer. */
  dialogue: Array<{ role: "user" | "assistant"; content: string }>;
  persona_id: string | null;
  engine: PersonaEngine | null;
  /** Where the persona came from: a queued entry, or the line's default. */
  persona_source: { queue_id: string; label: string | null } | { line: string } | null;
  /** The persona agent that held the call, its pinned branch, and the exact version. */
  agent: PersonaAgentPin;
  grade: CustomerGrade | null;
};

export type PersonaAgentPin = { agent_id: string | null; branch_id: string | null; version_id: string | null };

export type PersonaAgentSync = { agent_id: string; branch_id: string; version_id: string };

export type PersonaEngine = "chained" | "realtime";

export type Persona = {
  id: string;
  profile: string;
  engine: PersonaEngine;
  description?: string;
  first_message?: string;
  language?: string;
  voice_id?: string;
  expectations: string[];
  checks: { must_say: string[]; must_not_say: string[]; max_turns: number | null };
  fidelity: { scenario: string | null; conditions: string[] };
  /** Background sound the dealership hears; set per agent, so it runs on a variant agent. */
  environment: { background: string; volume: number } | null;
  turn: { eagerness: "patient" | "normal" | "eager" | null; timeout_s: number | null } | null;
};

export type QueuedPersona = { id: string; number: string; persona: Persona; label: string | null; created_at: string };

export type CustomerGrade = {
  persona_id: string;
  agent?: PersonaAgentPin;
  passed: boolean;
  checks: Array<{ check: string; passed: boolean }>;
  expectations: Array<{ expectation: string; passed: boolean; reasons: string[] }>;
  invocation_id?: string;
  evaluation_model?: string;
  credits?: number;
  graded_at: string;
};

export type PersonaCheck = {
  persona_id: string;
  engine: PersonaEngine;
  agent_id: string;
  invocation_id: string;
  evaluation_model: string;
  passed: number;
  runs: Array<{ status: string; passed: boolean; reasons: string[]; credits: number | null }>;
  credits: number;
};

export const inbox = {
  sms: (limit = 50, signal?: AbortSignal) => get<SmsMessage[]>(`/api/sms/messages?limit=${limit}`, signal),
  smsStatus: (signal?: AbortSignal) =>
    get<{ inbox_configured: boolean; inbox_exists: boolean; numbers: string[]; allowed_senders: string[]; account_configured: boolean; signing_configured: boolean }>("/api/sms/status", signal),
  customerProfiles: (signal?: AbortSignal) =>
    get<{ profiles: Record<string, string>; allowed_callers: string[]; configured: Record<string, boolean> }>("/api/customer/profiles", signal),
  customerSessions: (limit = 20, signal?: AbortSignal) => get<CustomerSession[]>(`/api/customer/sessions?limit=${limit}`, signal),
  customerRefresh: (callId: string) => post<CustomerSession>(`/api/customer/sessions/${q(callId)}/refresh`),
  customerSyncAgent: () =>
    post<{
      agents: Partial<Record<PersonaEngine, PersonaAgentSync>>;
      variants: Array<PersonaAgentSync & { name: string; personas: string[] }>;
    }>("/api/customer/agent/sync"),
  personas: (signal?: AbortSignal) => get<Persona[]>("/api/customer/personas", signal),
  queue: (signal?: AbortSignal) => get<QueuedPersona[]>("/api/customer/queue", signal),
  enqueue: (body: { number: string; persona_id: string; label?: string }) => post<QueuedPersona>("/api/customer/queue", body),
  unqueue: (queueId: string) => request<{ cancelled: string }>(`/api/customer/queue/${q(queueId)}`, { method: "DELETE" }),
  grade: (callId: string) => post<Operation>(`/api/customer/sessions/${q(callId)}/grade`),
  checkPersona: (personaId: string) => post<Operation>("/api/customer/personas/check", { persona_id: personaId }),
};

export type SilenceShare = { count: number; share_percent: number };

export type SilenceSummary = {
  total: number;
  buckets: Array<{ bucket: string; count: number; share_percent: number }>;
  voicemail_like: SilenceShare;
  any_machine_or_system: SilenceShare;
  true_silence: SilenceShare;
  examples: Record<string, string[]>;
};

export const analysis = {
  silence: (rows: Array<{ call_uid: string; user_text: string }>) => post<SilenceSummary>("/api/analysis/silence", { rows }),
};
