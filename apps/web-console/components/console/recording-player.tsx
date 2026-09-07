"use client";

import { useEffect, useRef, useState } from "react";
import { Download, Loader2, Play } from "lucide-react";

import { AudioPlayer } from "@/components/audio-player";
import { Button } from "@/components/ui/button";
import { simulatorApi } from "@/lib/simulator-api";

export function RecordingPlayer({ recordingId, label }: { recordingId: string; label: string }) {
  const [source, setSource] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const objectUrl = useRef<string | null>(null);
  const request = useRef<AbortController | null>(null);

  useEffect(() => () => {
    const controller = request.current;
    request.current = null;
    controller?.abort();
    if (objectUrl.current) URL.revokeObjectURL(objectUrl.current);
  }, []);

  async function load() {
    if (request.current) return;
    const controller = new AbortController();
    request.current = controller;
    setLoading(true);
    setError(null);
    const timer = window.setTimeout(() => controller.abort(), 30_000);
    try {
      const blob = await simulatorApi.recordingMedia(recordingId, controller.signal);
      if (controller.signal.aborted) return;
      const url = URL.createObjectURL(blob);
      objectUrl.current = url;
      setSource(url);
    } catch (cause) {
      if (request.current === controller) {
        setError(controller.signal.aborted ? "Recording request timed out. Try again."
          : cause instanceof Error ? cause.message : "Audio unavailable");
      }
    } finally {
      window.clearTimeout(timer);
      if (request.current === controller) {
        request.current = null;
        setLoading(false);
      }
    }
  }

  if (source) {
    return (
      <div className="space-y-2">
        <AudioPlayer src={source} label={label} sublabel="Stored locally by the simulator" />
        <Button asChild variant="ghost" size="sm">
          <a href={source} download={`${recordingId}.wav`}><Download /> Download recording</a>
        </Button>
      </div>
    );
  }
  return (
    <div className="rounded-xl bg-card p-4">
      <p className="mb-2 text-sm font-medium">{label}</p>
      <Button variant="outline" size="sm" onClick={() => void load()} disabled={loading}>
        {loading ? <Loader2 className="animate-spin" /> : <Play />}
        {loading ? "Loading audio" : error ? "Retry recording" : "Load recording"}
      </Button>
      {error && <p className="mt-2 text-xs text-destructive" role="alert">{error}</p>}
    </div>
  );
}
