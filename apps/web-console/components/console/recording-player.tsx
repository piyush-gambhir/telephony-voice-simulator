"use client";

import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";

import { AudioPlayer } from "@/components/audio-player";
import { simulatorApi } from "@/lib/simulator-api";

export function RecordingPlayer({
  recordingId,
  label,
}: {
  recordingId: string;
  label: string;
}) {
  const [source, setSource] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let active = true;
    let objectUrl = "";

    void simulatorApi
      .recordingMedia(recordingId)
      .then((blob) => {
        if (!active) return;
        objectUrl = URL.createObjectURL(blob);
        setSource(objectUrl);
      })
      .catch(() => {
        if (active) setFailed(true);
      });

    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [recordingId]);

  if (failed) {
    return <span className="text-xs text-muted-foreground">Audio unavailable</span>;
  }
  if (!source) {
    return (
      <span className="inline-flex items-center gap-2 text-xs text-muted-foreground">
        <Loader2 className="size-3 animate-spin" />
        Loading audio
      </span>
    );
  }
  return <AudioPlayer src={source} label={label} sublabel="Stored locally by the simulator" />;
}
