"use client";

import { useState, type FormEvent, type ReactNode } from "react";
import { Loader2, RefreshCw } from "lucide-react";

import { OperationPanel, useOperation } from "@/components/console/operation-panel";
import {
  EmptyStrip,
  Field,
  formatAddress,
  formatWhen,
  PageFrame,
  PageHeader,
  Panel,
  PanelTitle,
  Segmented,
  TonePill,
  useConfirm,
} from "@/components/console/kit";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { useAction, useApi } from "@/hooks/use-api";
import { corpus, CORPUS_SOURCE_TYPES, PROMPT_ENGINES, type Operation } from "@/lib/workflows-api";

type Section = "build" | "import" | "prompts" | "public";

const SECTIONS: Array<{ id: Section; label: string }> = [
  { id: "build", label: "Build" },
  { id: "import", label: "Import" },
  { id: "prompts", label: "IVR prompts" },
  { id: "public", label: "Public audio" },
];

const SOURCE_TYPE_LABEL: Record<(typeof CORPUS_SOURCE_TYPES)[number], string> = {
  official_public: "Official public source",
  permissive_internet: "Permissively licensed internet audio",
  internal_device_capture: "Internal device capture",
  internal_carrier_capture: "Internal carrier capture",
};

const ENGINE_LABEL: Record<(typeof PROMPT_ENGINES)[number], string> = {
  auto: "Auto (ElevenLabs, then OpenAI, then macOS)",
  elevenlabs: "ElevenLabs",
  openai: "OpenAI",
  macos: "macOS say (local, free)",
};

type BuildResult = { built: string[]; cached: string[]; assets_dir: string; manifest: string };
type ImportResult = { asset: string; path: string; source?: string; channel?: number; manifest?: string };
type RenderResult = { generated: string[]; cached: string[]; manifest?: string };

export function CorpusPage() {
  const [section, setSection] = useState<Section>("build");
  const { ask, dialog } = useConfirm();

  return (
    <PageFrame>
      <PageHeader title="Corpus" freshness={false}>
        <Segmented label="Corpus section" value={section} options={SECTIONS} onChange={setSection} />
      </PageHeader>
      {section === "build" && <BuildSection ask={ask} />}
      {section === "import" && <ImportSection />}
      {section === "prompts" && <PromptsSection ask={ask} />}
      {section === "public" && <PublicSection />}
      {dialog}
    </PageFrame>
  );
}

type Ask = ReturnType<typeof useConfirm>["ask"];

/* ---------- shared bits ---------- */

function InlineError({ message }: { message: string | null }) {
  if (!message) return null;
  return <p role="alert" className="rounded-lg bg-danger-soft px-3 py-2 text-xs leading-5 text-danger">{message}</p>;
}

function NameList({ title, names, tone }: { title: string; names: string[]; tone: "success" | "neutral" }) {
  return (
    <div className="grid gap-2 rounded-lg bg-surface-1 px-3 py-2.5">
      <div className="flex items-center gap-2">
        <p className="text-[13px] font-medium">{title}</p>
        <TonePill tone={tone}>{names.length}</TonePill>
      </div>
      {names.length > 0 ? (
        <ul className="flex flex-wrap gap-1.5">
          {names.map((name) => (
            <li key={name} className="rounded-md bg-control px-2 py-0.5 font-mono text-[11.5px]">{name}</li>
          ))}
        </ul>
      ) : (
        <p className="text-xs text-muted-foreground">None</p>
      )}
    </div>
  );
}

function PathLine({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="grid gap-0.5 sm:grid-cols-[120px_1fr] sm:gap-3">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-all font-mono text-[11.5px]">{value}</dd>
    </div>
  );
}

function SwitchRow({ id, label, help, checked, onChange, disabled }: {
  id: string;
  label: string;
  help?: string;
  checked: boolean;
  onChange: (value: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <div className="flex items-start gap-3">
      <Switch id={id} label={label} checked={checked} onChange={onChange} disabled={disabled} />
      <div className="min-w-0">
        <label htmlFor={id} className="text-[13px] font-medium">{label}</label>
        {help && <p className="text-xs leading-5 text-muted-foreground">{help}</p>}
      </div>
    </div>
  );
}

/* ---------- build ---------- */

function BuildSection({ ask }: { ask: Ask }) {
  const [only, setOnly] = useState("");
  const [force, setForce] = useState(false);
  const [replaceImported, setReplaceImported] = useState(false);
  const [operation, setOperation] = useState<Operation | null>(null);
  const tracked = useOperation<BuildResult>(operation);
  const running = tracked != null && (tracked.status === "queued" || tracked.status === "running");

  function confirmBuild() {
    const scope = only.trim() ? `corpus assets whose name contains "${only.trim()}"` : "every corpus asset";
    const what = force
      ? `Regenerates ${scope}, including audio already on disk${replaceImported ? " and imported or captured recordings" : ""},`
      : `Synthesizes speech for ${scope} that has no audio yet`;
    ask({
      title: "Build corpus audio",
      body: `${what} with a paid TTS API (ElevenLabs, then OpenAI, falling back to macOS say), then writes the audio and the provenance manifest. Each generated clip is billed by the TTS provider.`,
      confirm: "Build",
      tone: "primary",
      onConfirm: async () => {
        try {
          setOperation(await corpus.build({ only: only.trim() || undefined, force, replace_imported: force && replaceImported }));
        } catch (cause) {
          return cause instanceof Error ? cause.message : "Request failed";
        }
      },
    });
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,420px)_1fr] lg:items-start">
      <Panel>
        <PanelTitle>Build</PanelTitle>
        <form
          className="grid gap-4"
          onSubmit={(event) => { event.preventDefault(); confirmBuild(); }}
        >
          <Field id="corpus-only" label="Name filter" help="Only build assets whose file name contains this text. Leave empty for all.">
            <Input id="corpus-only" value={only} onChange={(event) => setOnly(event.target.value)} />
          </Field>
          <SwitchRow
            id="corpus-force"
            label="Force rebuild"
            help="Regenerate audio that already exists."
            checked={force}
            onChange={(value) => { setForce(value); if (!value) setReplaceImported(false); }}
          />
          <SwitchRow
            id="corpus-replace"
            label="Replace imported assets"
            help="Overwrite imported and captured recordings too. Needs force rebuild."
            checked={replaceImported}
            onChange={setReplaceImported}
            disabled={!force}
          />
          <div>
            <Button type="submit" size="sm" disabled={running}>
              {running && <Loader2 className="animate-spin" />}
              Build corpus
            </Button>
          </div>
        </form>
      </Panel>
      {tracked ? (
        <OperationPanel
          operation={tracked}
          renderResult={(result) => (
            <div className="grid gap-3">
              <div className="grid gap-3 sm:grid-cols-2">
                <NameList title="Built" names={result.built ?? []} tone="success" />
                <NameList title="Cached" names={result.cached ?? []} tone="neutral" />
              </div>
              <dl className="grid gap-1.5">
                <PathLine label="Assets" value={result.assets_dir} />
                <PathLine label="Manifest" value={result.manifest} />
              </dl>
            </div>
          )}
        />
      ) : (
        <EmptyStrip title="No build yet" body="The built and cached assets of the next build appear here." />
      )}
    </div>
  );
}

/* ---------- import ---------- */

type ImportMode = "source" | "recording";

function ImportSection() {
  const [mode, setMode] = useState<ImportMode>("source");
  const [asset, setAsset] = useState("");
  const [source, setSource] = useState("");
  const [sourceType, setSourceType] = useState<string>(CORPUS_SOURCE_TYPES[0]);
  const [callId, setCallId] = useState("");
  const [channel, setChannel] = useState<"0" | "1">("1");
  const [start, setStart] = useState("0");
  const [duration, setDuration] = useState("0");
  const [license, setLicense] = useState("");
  const [transcript, setTranscript] = useState("");
  const [commitAllowed, setCommitAllowed] = useState(false);
  const [operation, setOperation] = useState<Operation | null>(null);
  const tracked = useOperation<ImportResult>(operation);
  const action = useAction();

  const startValue = Number(start);
  const durationValue = Number(duration);
  const timesValid = start.trim() !== "" && duration.trim() !== ""
    && Number.isFinite(startValue) && startValue >= 0 && Number.isFinite(durationValue) && durationValue >= 0;
  const valid = asset.trim() !== "" && license.trim() !== ""
    && (mode === "source" ? source.trim() !== "" : callId.trim() !== "" && timesValid);
  const running = tracked != null && (tracked.status === "queued" || tracked.status === "running");

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!valid) return;
    const common = {
      asset: asset.trim(),
      license_note: license.trim(),
      transcript: transcript.trim() || undefined,
      commit_allowed: commitAllowed,
    };
    const next = await action.run(() =>
      mode === "source"
        ? corpus.importAudio({ ...common, source: source.trim(), source_type: sourceType })
        : corpus.importRecording({
            ...common,
            call_id: callId.trim(),
            channel: channel === "0" ? 0 : 1,
            start: startValue,
            duration: durationValue,
          }),
    );
    if (next) setOperation(next);
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,480px)_1fr] lg:items-start">
      <Panel>
        <PanelTitle aside={<Segmented
          label="Import from"
          value={mode}
          options={[{ id: "source", label: "URL or path" }, { id: "recording", label: "Recorded call" }]}
          onChange={setMode}
        />}>Import</PanelTitle>
        <form className="grid gap-4" onSubmit={submit}>
          <Field id="import-asset" label="Asset name" help="Must already exist in the corpus catalog, for example greeting_stock_carrier.wav.">
            <Input id="import-asset" value={asset} onChange={(event) => setAsset(event.target.value)} />
          </Field>

          {mode === "source" ? (
            <>
              <Field id="import-source" label="Source" help="An http(s) URL or a file path on the API server.">
                <Input id="import-source" value={source} onChange={(event) => setSource(event.target.value)} />
              </Field>
              <Field id="import-type" label="Source type">
                <Select value={sourceType} onValueChange={setSourceType}>
                  <SelectTrigger id="import-type"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {CORPUS_SOURCE_TYPES.map((type) => (
                      <SelectItem key={type} value={type}>{SOURCE_TYPE_LABEL[type]}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
            </>
          ) : (
            <>
              <Field
                id="import-call"
                label="Agent call ID"
                help="The agent's own call ID: its recordings live under livekit/<call ID>/ in RECORDINGS_BUCKET. Simulator call SIDs do not match."
              >
                <Input id="import-call" value={callId} onChange={(event) => setCallId(event.target.value)} autoComplete="off" className="font-mono" />
              </Field>
              <div className="grid gap-4 sm:grid-cols-3">
                <Field id="import-channel" label="Channel">
                  <Select value={channel} onValueChange={(value) => setChannel(value as "0" | "1")}>
                    <SelectTrigger id="import-channel"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="0">0 (agent)</SelectItem>
                      <SelectItem value="1">1 (callee)</SelectItem>
                    </SelectContent>
                  </Select>
                </Field>
                <Field id="import-start" label="Start (s)">
                  <Input id="import-start" inputMode="decimal" className="t-num" value={start} onChange={(event) => setStart(event.target.value)} />
                </Field>
                <Field id="import-duration" label="Duration (s)" help="0 keeps the rest.">
                  <Input id="import-duration" inputMode="decimal" className="t-num" value={duration} onChange={(event) => setDuration(event.target.value)} />
                </Field>
              </div>
            </>
          )}

          <Field id="import-license" label="License note" help="Where the audio came from and the right to use it. Required.">
            <Textarea id="import-license" rows={2} value={license} onChange={(event) => setLicense(event.target.value)} />
          </Field>
          <Field id="import-transcript" label="Transcript">
            <Textarea id="import-transcript" rows={2} value={transcript} onChange={(event) => setTranscript(event.target.value)} />
          </Field>
          <SwitchRow
            id="import-commit"
            label="Commit allowed"
            help="Mark the asset as safe to commit to the repository."
            checked={commitAllowed}
            onChange={setCommitAllowed}
          />
          <div className="grid gap-2">
            <div>
              <Button type="submit" size="sm" disabled={!valid || action.pending || running}>
                {(action.pending || running) && <Loader2 className="animate-spin" />}
                Import
              </Button>
            </div>
            <InlineError message={action.error} />
          </div>
        </form>
      </Panel>
      {tracked ? (
        <OperationPanel
          operation={tracked}
          renderResult={(result) => (
            <dl className="grid gap-1.5 rounded-lg bg-surface-1 px-3 py-2.5">
              <PathLine label="Asset" value={result.asset} />
              <PathLine label="Path" value={result.path} />
              {result.source && <PathLine label="Source" value={result.source} />}
              {result.channel != null && <PathLine label="Channel" value={String(result.channel)} />}
              {result.manifest && <PathLine label="Manifest" value={result.manifest} />}
            </dl>
          )}
        />
      ) : (
        <EmptyStrip title="Nothing imported yet" body="The imported asset and where it was written appear here." />
      )}
    </div>
  );
}

/* ---------- IVR prompts ---------- */

function PromptsSection({ ask }: { ask: Ask }) {
  const check = useApi((signal) => corpus.prompts(signal), []);
  const [engine, setEngine] = useState<string>("auto");
  const [force, setForce] = useState(false);
  const [operation, setOperation] = useState<Operation | null>(null);
  const tracked = useOperation<RenderResult>(operation);
  const running = tracked != null && (tracked.status === "queued" || tracked.status === "running");
  const promptCount = check.data?.prompts ?? null;

  function confirmRender() {
    const which = force ? "every IVR prompt, replacing cached audio," : "IVR prompts that have no matching cached audio";
    const cost = engine === "macos"
      ? "with macOS say on the API host. This is local and free."
      : engine === "auto"
        ? "with the first available engine: ElevenLabs, then OpenAI (both paid TTS APIs, billed per clip), then macOS say."
        : `with ${ENGINE_LABEL[engine as (typeof PROMPT_ENGINES)[number]]}, a paid TTS API billed per clip.`;
    ask({
      title: "Render IVR prompts",
      body: `Renders ${which} ${cost} The audio and prompt manifest are written to disk.`,
      confirm: "Render",
      tone: "primary",
      onConfirm: async () => {
        try {
          setOperation(await corpus.renderPrompts({ engine, force }));
        } catch (cause) {
          return cause instanceof Error ? cause.message : "Request failed";
        }
      },
    });
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,420px)_1fr] lg:items-start">
      <div className="grid gap-4">
        <Panel>
          <PanelTitle aside={
            <Button variant="ghost" size="icon-xs" aria-label="Check again" disabled={check.loading} onClick={check.reload}>
              <RefreshCw className={check.loading ? "animate-spin" : undefined} />
            </Button>
          }>Prompt check</PanelTitle>
          <div aria-live="polite">
            {check.loading && !check.data ? (
              <p className="text-[13px] text-muted-foreground">Checking</p>
            ) : check.error ? (
              <InlineError message={check.error} />
            ) : check.data ? (
              <div className="grid gap-3">
                <div className="flex items-center gap-2">
                  <TonePill tone={check.data.valid ? "success" : "danger"}>{check.data.valid ? "Valid" : "Invalid"}</TonePill>
                  {promptCount != null && <span className="t-num text-[13px] text-muted-foreground">{promptCount} prompts</span>}
                </div>
                <dl className="grid gap-1.5">
                  <PathLine label="Config" value={check.data.config} />
                </dl>
              </div>
            ) : null}
          </div>
        </Panel>
        <Panel>
          <PanelTitle>Render</PanelTitle>
          <form className="grid gap-4" onSubmit={(event) => { event.preventDefault(); confirmRender(); }}>
            <Field id="prompt-engine" label="Engine">
              <Select value={engine} onValueChange={setEngine}>
                <SelectTrigger id="prompt-engine"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {PROMPT_ENGINES.map((item) => (
                    <SelectItem key={item} value={item}>{ENGINE_LABEL[item]}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            <SwitchRow id="prompt-force" label="Force" help="Render again even when cached audio matches." checked={force} onChange={setForce} />
            <div>
              <Button type="submit" size="sm" disabled={running || !check.data?.valid}>
                {running && <Loader2 className="animate-spin" />}
                Render prompts
              </Button>
            </div>
          </form>
        </Panel>
      </div>
      {tracked ? (
        <OperationPanel
          operation={tracked}
          renderResult={(result) => (
            <div className="grid gap-3">
              <div className="grid gap-3 sm:grid-cols-2">
                <NameList title="Generated" names={result.generated ?? []} tone="success" />
                <NameList title="Cached" names={result.cached ?? []} tone="neutral" />
              </div>
              {result.manifest && (
                <dl className="grid gap-1.5"><PathLine label="Manifest" value={result.manifest} /></dl>
              )}
            </div>
          )}
        />
      ) : (
        <EmptyStrip title="No render yet" body="Generated and cached prompt files of the next render appear here." />
      )}
    </div>
  );
}

/* ---------- public audio ---------- */

function PublicSection() {
  const [allowlist, setAllowlist] = useState("");
  const action = useAction();
  const [result, setResult] = useState<{ valid: boolean; referenced_assets: unknown } | null>(null);
  const valid = allowlist.trim() !== "";

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!valid) return;
    setResult(null);
    const next = await action.run(() => corpus.validatePublic(allowlist.trim()));
    if (next) setResult(next);
  }

  const referenced = result?.referenced_assets;
  return (
    <Panel className="max-w-2xl">
      <PanelTitle>Public audio</PanelTitle>
      <form className="grid gap-4" onSubmit={submit}>
        <Field id="public-allowlist" label="Allowlist file" help="Path on the API server to the reviewed deployment allowlist. Checks every audio file the public AMD runtime may serve.">
          <Input id="public-allowlist" value={allowlist} onChange={(event) => setAllowlist(event.target.value)} />
        </Field>
        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" size="sm" variant="secondary" disabled={!valid || action.pending}>
            {action.pending && <Loader2 className="animate-spin" />}
            Validate
          </Button>
          <div aria-live="polite" className="flex items-center gap-2">
            {result && (
              <>
                <TonePill tone={result.valid ? "success" : "danger"}>{result.valid ? "Valid" : "Invalid"}</TonePill>
                {typeof referenced === "number" && (
                  <span className="t-num text-[13px] text-muted-foreground">{referenced} referenced assets approved</span>
                )}
              </>
            )}
          </div>
        </div>
        {Array.isArray(referenced) && (
          <ul className="flex flex-wrap gap-1.5">
            {referenced.map((name) => (
              <li key={String(name)} className="rounded-md bg-control px-2 py-0.5 font-mono text-[11.5px]">{String(name)}</li>
            ))}
          </ul>
        )}
        <InlineError message={action.error} />
      </form>
    </Panel>
  );
}
