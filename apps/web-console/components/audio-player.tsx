"use client";

import * as React from "react";
import { Pause, Play } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

type WaveBin = {
  level: number;
  silent: boolean;
};

const WAVE_BINS = 120;

function placeholderWave(): WaveBin[] {
  return Array.from({ length: WAVE_BINS }, (_, index) => ({
    level: 0.1 + Math.sin((index / WAVE_BINS) * Math.PI) * 0.12,
    silent: true,
  }));
}

function makeWaveform(audio: AudioBuffer): WaveBin[] {
  const channel = audio.getChannelData(0);
  const binSize = Math.max(1, Math.floor(channel.length / WAVE_BINS));
  const energy: number[] = [];

  for (let bin = 0; bin < WAVE_BINS; bin++) {
    const start = bin * binSize;
    const end =
      bin === WAVE_BINS - 1
        ? channel.length
        : Math.min(channel.length, start + binSize);
    let squares = 0;
    let peak = 0;
    let count = 0;

    for (let sample = start; sample < end; sample++) {
      const value = Math.abs(channel[sample] ?? 0);
      squares += value * value;
      peak = Math.max(peak, value);
      count++;
    }

    const rms = count ? Math.sqrt(squares / count) : 0;
    energy.push(rms * 0.8 + peak * 0.2);
  }

  const maxEnergy = Math.max(...energy, 0.001);
  const silenceThreshold = Math.max(0.0035, maxEnergy * 0.025);

  return energy.map((value) => ({
    level: Math.max(0.08, Math.min(1, Math.sqrt(value / maxEnergy))),
    silent: value < silenceThreshold,
  }));
}

function timelineTicks(duration: number): number[] {
  if (!duration) return [0, 0.5, 1];
  if (duration <= 10) return [0, 0.5, 1];
  return [0, 0.25, 0.5, 0.75, 1];
}

export function AudioPlayer({
  src,
  label,
  sublabel,
}: {
  src: string;
  label: string;
  sublabel?: string;
}) {
  const ref = React.useRef<HTMLAudioElement>(null);
  const [playing, setPlaying] = React.useState(false);
  const [progress, setProgress] = React.useState(0); // 0..1
  const [duration, setDuration] = React.useState(0);
  const [wave, setWave] = React.useState<WaveBin[]>(placeholderWave);
  const [waveReady, setWaveReady] = React.useState(false);

  React.useEffect(() => {
    const audio = ref.current;
    if (!audio) return;
    const syncDuration = () =>
      setDuration(Number.isFinite(audio.duration) ? audio.duration : 0);
    syncDuration();
    audio.addEventListener("loadedmetadata", syncDuration);
    audio.addEventListener("durationchange", syncDuration);
    return () => {
      audio.removeEventListener("loadedmetadata", syncDuration);
      audio.removeEventListener("durationchange", syncDuration);
    };
  }, [src]);

  React.useEffect(() => {
    let cancelled = false;
    const decode = async () => {
      setWaveReady(false);
      try {
        const response = await fetch(src);
        if (!response.ok) throw new Error(`Audio request failed: ${response.status}`);
        const encoded = await response.arrayBuffer();
        const context = new AudioContext();
        try {
          const decoded = await context.decodeAudioData(encoded);
          if (!cancelled) {
            setWave(makeWaveform(decoded));
            setDuration(decoded.duration);
            setWaveReady(true);
          }
        } finally {
          await context.close();
        }
      } catch {
        if (!cancelled) {
          setWave(placeholderWave());
          setWaveReady(false);
        }
      }
    };
    void decode();
    return () => {
      cancelled = true;
    };
  }, [src]);

  const toggle = () => {
    const a = ref.current;
    if (!a) return;
    if (a.paused) {
      // Pausing every OTHER audio on the page first means only one recording
      // plays at a time (onPlay also enforces this, belt-and-suspenders).
      document.querySelectorAll("audio").forEach((el) => {
        if (el !== a) el.pause();
      });
      void a.play().catch(() => {});
    } else {
      a.pause();
    }
  };

  const seek = (e: React.MouseEvent<HTMLButtonElement>) => {
    const a = ref.current;
    if (!a || !a.duration) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const ratio = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
    a.currentTime = ratio * a.duration;
    setProgress(ratio);
  };

  const seekBy = (seconds: number) => {
    const audio = ref.current;
    if (!audio || !audio.duration) return;
    audio.currentTime = Math.min(
      audio.duration,
      Math.max(0, audio.currentTime + seconds)
    );
    setProgress(audio.currentTime / audio.duration);
  };

  const fmt = (s: number) =>
    Number.isFinite(s) ? `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}` : "0:00";

  const ticks = timelineTicks(duration);

  return (
    <div className="rounded-2xl bg-card p-4 md:p-5">
      <audio
        ref={ref}
        src={src}
        preload="metadata"
        onPlay={(e) => {
          const self = e.currentTarget;
          document.querySelectorAll("audio").forEach((el) => {
            if (el !== self) el.pause();
          });
          setPlaying(true);
        }}
        onPause={() => setPlaying(false)}
        onEnded={(e) => {
          e.currentTarget.currentTime = 0;
          setPlaying(false);
          setProgress(0);
        }}
        onLoadedMetadata={(e) => setDuration(e.currentTarget.duration)}
        onTimeUpdate={(e) =>
          setProgress(
            e.currentTarget.duration
              ? e.currentTarget.currentTime / e.currentTarget.duration
              : 0
          )
        }
      />
      <div className="grid grid-cols-[3rem_minmax(0,1fr)] gap-4">
        <Button
          type="button"
          size="icon"
          onClick={toggle}
          aria-label={`${playing ? "Pause" : "Play"} ${label}`}
          className="mt-0.5 size-12 shrink-0 rounded-full transition-transform active:scale-95"
        >
          {playing ? (
            <Pause className="size-5" />
          ) : (
            <Play className="size-5 translate-x-[1px]" />
          )}
        </Button>

        <div className="min-w-0">
          <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-1">
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold">{label}</p>
              {sublabel ? (
                <p className="mt-0.5 text-xs text-muted-foreground">{sublabel}</p>
              ) : null}
            </div>
            <span className="shrink-0 font-mono text-xs tabular-nums text-muted-foreground">
              {fmt(progress * duration)} / {fmt(duration)}
            </span>
          </div>

          <Button
            type="button"
            variant="ghost"
            aria-label={`Seek ${label}. Use left and right arrows to move five seconds.`}
            onClick={seek}
            onKeyDown={(event) => {
              if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
                event.preventDefault();
                seekBy(event.key === "ArrowLeft" ? -5 : 5);
              }
            }}
            className="relative mt-3 block h-[4.75rem] w-full cursor-pointer overflow-visible p-0 hover:bg-transparent"
          >
            <span className="absolute inset-x-0 top-0 flex h-10 items-center gap-px">
              {wave.map((bin, index) => {
                const position = index / wave.length;
                const filled = position <= progress;
                return (
                  <span
                    key={index}
                    className={cn(
                      "min-w-0 flex-1 rounded-full transition-[height,background-color]",
                      filled
                        ? bin.silent
                          ? "bg-primary/35"
                          : "bg-primary"
                        : bin.silent
                          ? "bg-muted-foreground/10"
                          : "bg-muted-foreground/35",
                      !waveReady && "animate-pulse"
                    )}
                    style={{
                      height: bin.silent
                        ? "3px"
                        : `${Math.max(18, Math.round(bin.level * 100))}%`,
                    }}
                  />
                );
              })}
            </span>
            <span
              aria-hidden="true"
              className="absolute top-0 h-10 w-px bg-foreground/65"
              style={{ left: `${progress * 100}%` }}
            />
            <span className="absolute inset-x-0 top-[2.85rem] h-px bg-muted-foreground/15" />
            {ticks.map((tick, index) => (
              <span
                key={tick}
                className={cn(
                  "absolute top-[3.05rem] font-mono text-[10px] tabular-nums text-muted-foreground",
                  index === 0 && "translate-x-0",
                  index > 0 && index < ticks.length - 1 && "-translate-x-1/2",
                  index === ticks.length - 1 && "-translate-x-full"
                )}
                style={{ left: `${tick * 100}%` }}
              >
                {fmt(tick * duration)}
              </span>
            ))}
          </Button>

          <div className="mt-1 flex items-center gap-4 text-[11px] text-muted-foreground">
            <span className="flex items-center gap-1.5">
              <span className="h-3 w-1 rounded-full bg-muted-foreground/40" />
              Audio
            </span>
            <span className="flex items-center gap-1.5">
              <span className="h-[3px] w-3 rounded-full bg-muted-foreground/20" />
              Silence
            </span>
            <span className="ml-auto hidden sm:inline">
              Click to seek · ←/→ 5s
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}
