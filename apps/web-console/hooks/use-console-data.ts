"use client";

import { useCallback, useEffect, useRef, useState, type SetStateAction } from "react";

import {
  simulatorApi,
  type DirectoryEntry,
  type Endpoint,
  type IncomingCall,
  type ManagedNumber,
  type ProviderConnection,
  type ProviderDescriptor,
  type Scenario,
  type SimulationRun,
} from "@/lib/simulator-api";

type ConsoleData = {
  catalog: ProviderDescriptor[];
  amdNumbers: ManagedNumber[];
  connections: ProviderConnection[];
  endpoints: Endpoint[];
  directory: DirectoryEntry[];
  scenarios: Scenario[];
  runs: SimulationRun[];
  calls: IncomingCall[];
};

const INITIAL_DATA: ConsoleData = {
  catalog: [], amdNumbers: [], connections: [], endpoints: [],
  directory: [], scenarios: [], runs: [], calls: [],
};

const RESOURCES = [
  ["catalog", "Provider catalog", simulatorApi.providerCatalog],
  ["amdNumbers", "AMD numbers", simulatorApi.amdNumbers],
  ["connections", "Connections", simulatorApi.connections],
  ["endpoints", "Endpoints", simulatorApi.endpoints],
  ["directory", "Directory", simulatorApi.directory],
  ["scenarios", "Scenarios", simulatorApi.scenarios],
  ["runs", "Runs", simulatorApi.runs],
  ["calls", "Calls", simulatorApi.calls],
] as const;

/** Keeps successful resources usable when a different API resource fails. */
export function useConsoleData({ paused }: { paused: boolean }) {
  const [data, setData] = useState<ConsoleData>(INITIAL_DATA);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [online, setOnline] = useState<boolean | null>(null);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const [lastUpdatedAt, setLastUpdatedAt] = useState<Date | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const inFlight = useRef<AbortController | null>(null);
  const revision = useRef(0);

  const updateData = useCallback(<K extends keyof ConsoleData>(
    key: K,
    value: SetStateAction<ConsoleData[K]>,
  ) => {
    // A read started before a mutation must never overwrite its result.
    revision.current += 1;
    setData((current) => ({
      ...current,
      [key]: typeof value === "function" ? value(current[key]) : value,
    }));
  }, []);

  const refresh = useCallback(async (force = false) => {
    if (inFlight.current && !force) return;
    inFlight.current?.abort();
    const controller = new AbortController();
    const startedAtRevision = revision.current;
    inFlight.current = controller;
    setRefreshing(true);

    try {
      const results = await Promise.allSettled(
        RESOURCES.map(([, , fetchResource]) => fetchResource(controller.signal)),
      );
      if (controller.signal.aborted || startedAtRevision !== revision.current) return;

      const successful: Partial<ConsoleData> = {};
      const failures: string[] = [];
      results.forEach((result, index) => {
        const [key, label] = RESOURCES[index];
        if (result.status === "fulfilled") {
          Object.assign(successful, { [key]: result.value });
        } else {
          const message = result.reason instanceof Error ? result.reason.message : "Unavailable";
          failures.push(`${label}: ${message}`);
        }
      });
      const hasData = results.some((result) => result.status === "fulfilled");
      setData((current) => ({ ...current, ...successful }));
      setOnline(hasData);
      setRefreshError(failures.length
        ? hasData ? failures.join(" · ") : "Simulator API is unavailable. Previously loaded records may be out of date."
        : null);
      if (hasData) setLastUpdatedAt(new Date());
    } finally {
      if (inFlight.current === controller) {
        inFlight.current = null;
        if (!controller.signal.aborted) {
          setLoading(false);
          setRefreshing(false);
        }
      }
    }
  }, []);

  useEffect(() => {
    void refresh();
    return () => {
      inFlight.current?.abort();
      inFlight.current = null;
    };
  }, [refresh]);

  useEffect(() => {
    if (!autoRefresh || paused) return;
    const refreshVisiblePage = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    const timer = window.setInterval(refreshVisiblePage, 10_000);
    document.addEventListener("visibilitychange", refreshVisiblePage);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", refreshVisiblePage);
    };
  }, [autoRefresh, paused, refresh]);

  return {
    data, updateData, loading, refreshing, online, refreshError, lastUpdatedAt,
    autoRefresh, setAutoRefresh, refresh,
  };
}
