"use client";

import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import { Download, Loader2, Pause, Play } from "lucide-react";

import { makeWaveform, type WaveBin } from "@/components/audio-player";
import { Button } from "@/components/ui/button";
import { onRecordingAxis, type Timing } from "@/lib/results";
import { simulatorApi, type CallRecording } from "@/lib/simulator-api";
import { cn } from "@/lib/utils";

const BINS = 180;

function clock(seconds: number): string {
  const whole = Math.max(0, seconds);
  return `${Math.floor(whole / 60)}:${(whole % 60).toFixed(1).padStart(4, "0")}`;
}

function niceStep(span: number): number {
  return [0.5, 1, 2, 5, 10, 15, 30, 60].find((step) => span / step <= 8) ?? 120;
}

/**
 * The message recording on its own time axis. The shaded band is when the agent was
 * allowed to start speaking, the mark is when the grader heard it start. Audio loads
 * only on request, and the waveform is decoded from the real file.
 */
export function MessageTimeline({
  recording,
  timing,
  passed,
  recordingStatus,
}: {
  recording: CallRecording | null;
  timing: Timing | null;
  passed: boolean | null;
  /** The call's overall recording status, to explain a missing message. */
  recordingStatus?: string;
}) {
  const [source, setSource] = useState<string | null>(null);
  const [wave, setWave] = useState<WaveBin[] | null>(null);
  const [decoded, setDecoded] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [playing, setPlaying] = useState(false);
  const [time, setTime] = useState(0);
  const audio = useRef<HTMLAudioElement>(null);
  const objectUrl = useRef<string | null>(null);
  const request = useRef<AbortController | null>(null);

  useEffect(() => () => {
    request.current?.abort();
    if (objectUrl.current) URL.revokeObjectURL(objectUrl.current);
  }, []);

  const axis = onRecordingAxis(timing);
  const duration = decoded ?? recording?.duration_s ?? null;
  const span = Math.max(
    duration ?? 0,
    axis ? Math.max(axis.max, axis.start) + 1 : 0,
    4,
  );
  const at = (seconds: number) => `${Math.min(100, Math.max(0, (seconds / span) * 100))}%`;
  const step = niceStep(span);
  const ticks = Array.from({ length: Math.floor(span / step) + 1 }, (_, index) => index * step);

  async function load() {
    if (!recording || request.current) return;
    const controller = new AbortController();
    request.current = controller;
    setLoading(true);
    setError(null);
    const timer = window.setTimeout(() => controller.abort(), 30_000);
    try {
      const blob = await simulatorApi.recordingMedia(recording.id, controller.signal);
      const url = URL.createObjectURL(blob);
      objectUrl.current = url;
      setSource(url);
      try {
        const context = new AudioContext();
        try {
          const buffer = await context.decodeAudioData(await blob.arrayBuffer());
          setWave(makeWaveform(buffer, BINS));
          setDecoded(buffer.duration);
        } finally {
          await context.close();
        }
      } catch {
        // Playback still works through the audio element when decoding is unsupported.
      }
    } catch (cause) {
      setError(controller.signal.aborted
        ? "The recording took too long to load. Try again."
        : cause instanceof Error ? cause.message : "Recording unavailable.");
    } finally {
      window.clearTimeout(timer);
      request.current = null;
      setLoading(false);
    }
  }

  function toggle() {
    const element = audio.current;
    if (!element) return;
    if (element.paused) {
      document.querySelectorAll("audio").forEach((other) => { if (other !== element) other.pause(); });
      void element.play().catch(() => {});
    } else {
      element.pause();
    }
  }

  function seekTo(seconds: number) {
    const element = audio.current;
    if (!element || !Number.isFinite(element.duration)) return;
    element.currentTime = Math.min(element.duration, Math.max(0, seconds));
    setTime(element.currentTime);
  }

  function onPointer(event: PointerEvent<HTMLDivElement>) {
    if (!source || (event.type === "pointermove" && event.buttons !== 1)) return;
    const rect = event.currentTarget.getBoundingClientRect();
    seekTo(((event.clientX - rect.left) / rect.width) * span);
  }

  function onKey(event: KeyboardEvent<HTMLDivElement>) {
    if (!source) return;
    if (event.key === " " || event.key === "Enter") { event.preventDefault(); toggle(); }
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      event.preventDefault();
      seekTo(time + (event.key === "ArrowLeft" ? -1 : 1));
    }
  }

  const startTone = passed === false ? "bg-danger" : "bg-success";
  const startText = passed === false ? "text-danger" : "text-success";

  return (
    <div className="flex flex-col gap-3">
      <div
        role={source ? "slider" : undefined}
        tabIndex={source ? 0 : undefined}
        aria-label={source ? "Recording position. Space plays, arrows move one second." : undefined}
        aria-valuemin={source ? 0 : undefined}
        aria-valuemax={source ? Math.round(span) : undefined}
        aria-valuenow={source ? Math.round(time) : undefined}
        onPointerDown={onPointer}
        onPointerMove={onPointer}
        onKeyDown={onKey}
        className={cn(
          "relative h-40 select-none rounded-lg bg-surface-1 px-3 outline-none focus-visible:ring-2 focus-visible:ring-ring/40",
          source && "cursor-pointer",
        )}
      >
        <div className="absolute inset-x-3 bottom-7 top-7">
          {axis && (
            <div
              className="absolute inset-y-0 rounded-md bg-window"
              style={{ left: at(axis.min), width: `calc(${at(axis.max)} - ${at(axis.min)})` }}
            >
              <span className="t-micro absolute -top-5 left-0 whitespace-nowrap text-success">Allowed start</span>
            </div>
          )}

          <div
            className="absolute inset-y-0 left-0 flex items-center gap-px"
            style={{ width: wave && duration ? at(duration) : "100%" }}
          >
            {wave ? (
              wave.map((bin, index) => {
                const played = source && duration ? (index / wave.length) * duration <= time : false;
                return (
                  <span
                    key={index}
                    className={cn(
                      "min-w-0 flex-1 rounded-full",
                      played ? "bg-accent" : bin.silent ? "bg-wave/40" : "bg-wave",
                    )}
                    style={{ height: bin.silent ? "2px" : `${Math.max(10, Math.round(bin.level * 100))}%` }}
                  />
                );
              })
            ) : (
              <span className="h-px w-full bg-wave/60" />
            )}
          </div>

          {axis && timing && (
            <div className={cn("absolute -top-2 bottom-0 w-0.5 rounded-full", startTone)} style={{ left: at(axis.start) }}>
              <span
                className={cn(
                  "t-micro absolute -top-5 whitespace-nowrap",
                  startText,
                  axis.start / span > 0.5 ? "right-1.5" : "left-1.5",
                )}
              >
                Agent starts {timing.start.toFixed(2)}s{timing.basis === "mark" ? " after tone" : ""}
              </span>
            </div>
          )}

          {source && (
            <div className="pointer-events-none absolute -bottom-1 -top-1 w-px bg-foreground" style={{ left: at(time) }} />
          )}
        </div>

        <div className="t-num absolute inset-x-3 bottom-1.5 h-4 text-[11px] text-subtle-foreground">
          {ticks.map((tick, index) => (
            <span
              key={tick}
              className={cn("absolute", index === 0 ? "" : tick >= span - step / 2 ? "-translate-x-full" : "-translate-x-1/2")}
              style={{ left: at(tick) }}
            >
              {tick}s
            </span>
          ))}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {source ? (
          <>
            <Button size="sm" variant="secondary" onClick={toggle} aria-label={playing ? "Pause recording" : "Play recording"}>
              {playing ? <Pause /> : <Play />}
              {playing ? "Pause" : "Play"}
            </Button>
            <span className="t-num text-xs text-muted-foreground">
              {clock(time)} / {clock(duration ?? 0)}
            </span>
            <Button asChild size="sm" variant="ghost" className="ml-auto">
              <a href={source} download={`${recording?.id ?? "recording"}.wav`}><Download /> Download</a>
            </Button>
            <audio
              ref={audio}
              src={source}
              preload="auto"
              onPlay={() => setPlaying(true)}
              onPause={() => setPlaying(false)}
              onEnded={() => setPlaying(false)}
              onTimeUpdate={(event) => setTime(event.currentTarget.currentTime)}
            />
          </>
        ) : recording?.local_path ? (
          <>
            <Button size="sm" variant="secondary" onClick={() => void load()} disabled={loading}>
              {loading ? <Loader2 className="animate-spin" /> : <Play />}
              {loading ? "Loading recording" : error ? "Retry" : "Load recording"}
            </Button>
            <span className="text-xs text-muted-foreground">
              {duration ? `${duration.toFixed(1)}s message` : "Message recording"} stored by the simulator
            </span>
          </>
        ) : (
          <span className="text-xs text-muted-foreground">
            {recording
              ? recording.status === "absent"
                ? "The provider reported that no message was recorded."
                : recording.status === "download_failed"
                  ? "The simulator could not download this recording from the provider."
                  : "Waiting for the provider to deliver the recording."
              : recordingStatus === "disabled"
                ? "Recording was disabled for this call."
                : recordingStatus === "failed"
                  ? "The provider could not deliver the recording."
                  : "No message recording for this call."}
          </span>
        )}
        {error && <span role="alert" className="text-xs text-danger">{error}</span>}
      </div>
      {timing && !axis && (
        <p className="text-xs text-muted-foreground">
          This check measures from the scenario mark ({timing.start.toFixed(2)}s, allowed {timing.min} to {timing.max}s),
          not from the start of the recording, so it is not drawn on the waveform.
        </p>
      )}
    </div>
  );
}
