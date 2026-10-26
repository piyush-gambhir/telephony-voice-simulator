"use client";

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

import { useConsoleData } from "@/hooks/use-console-data";
import { buildResults, type Result } from "@/lib/results";
import {
  simulatorApi,
  type DirectoryEntry,
  type Endpoint,
  type ManagedNumber,
  type ProviderConnection,
} from "@/lib/simulator-api";
import type {
  DirectoryEntryUpdate,
  EndpointUpdate,
  ProviderConnectionUpdate,
} from "@/components/console/resource-editors";

type ConsoleState = ReturnType<typeof useConsoleData>;

export type AmdNumberUpdate = {
  friendly_name?: string;
  default_scenario?: string | null;
  routing_mode?: Endpoint["routing_mode"];
  enabled?: boolean;
  record_full_calls?: boolean;
};

type Actions = {
  createRun: (endpointId: string, scenario: string) => Promise<string | null>;
  cancelRun: (runId: string) => Promise<boolean>;
  createIvrSimulation: (body: Parameters<typeof simulatorApi.createIvrSimulation>[0]) => Promise<string | null>;
  deleteCall: (callId: string) => Promise<boolean>;
  createConnection: (body: Parameters<typeof simulatorApi.createConnection>[0]) => Promise<boolean>;
  saveConnection: (id: string, values: ProviderConnectionUpdate) => Promise<boolean>;
  deleteConnection: (id: string) => Promise<boolean>;
  createEndpoint: (body: Parameters<typeof simulatorApi.createEndpoint>[0]) => Promise<boolean>;
  saveEndpoint: (id: string, values: Partial<EndpointUpdate>) => Promise<boolean>;
  deleteEndpoint: (id: string) => Promise<boolean>;
  createDirectoryEntry: (body: Parameters<typeof simulatorApi.createDirectoryEntry>[0]) => Promise<boolean>;
  saveDirectoryEntry: (id: string, values: Partial<DirectoryEntryUpdate>) => Promise<boolean>;
  deleteDirectoryEntry: (id: string) => Promise<boolean>;
  syncAmdNumbers: () => Promise<boolean>;
  updateAmdNumber: (id: string, update: AmdNumberUpdate) => Promise<boolean>;
  queueAmdNumber: (id: string, scenario: string) => Promise<string | null>;
  attachAmdNumber: (id: string) => Promise<boolean>;
  restoreAmdNumber: (id: string) => Promise<boolean>;
};

type ConsoleContextValue = ConsoleState & {
  results: Result[];
  busy: boolean;
  error: string | null;
  clearError: () => void;
  actions: Actions;
};

const ConsoleContext = createContext<ConsoleContextValue | null>(null);

export function useConsole(): ConsoleContextValue {
  const value = useContext(ConsoleContext);
  if (!value) throw new Error("useConsole must be used inside ConsoleProvider");
  return value;
}

function replaceById<T extends { id: string }>(items: T[], updated: T): T[] {
  return items.map((item) => (item.id === updated.id ? updated : item));
}

export function ConsoleProvider({ children }: { children: ReactNode }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const state = useConsoleData({ paused: busy });
  const { data, updateData, refresh } = state;

  const results = useMemo(
    () => buildResults(data.runs, data.calls, data.endpoints, data.scenarios),
    [data.runs, data.calls, data.endpoints, data.scenarios],
  );

  /** Every mutation pauses polling, reports failures inline and never throws. */
  const run = useCallback(async <T,>(fallback: string, task: () => Promise<T>): Promise<T | null> => {
    setBusy(true);
    setError(null);
    try {
      return await task();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : fallback);
      return null;
    } finally {
      setBusy(false);
    }
  }, []);

  const actions = useMemo<Actions>(() => {
    const ok = async (fallback: string, task: () => Promise<unknown>) =>
      (await run(fallback, async () => { await task(); return true; })) === true;
    const replaceNumber = (updated: ManagedNumber) => {
      updateData("amdNumbers", (current) => replaceById(current, updated));
      updateData("endpoints", (current) => replaceById(current, updated.endpoint));
    };

    return {
      createRun: async (endpointId, scenario) => {
        const created = await run("Could not start run", () =>
          simulatorApi.createRun({ endpoint_id: endpointId, scenario }));
        if (!created) return null;
        updateData("runs", (current) => [created, ...current]);
        // Managed numbers count their own pending runs; refresh them so Lines stays accurate.
        if (data.amdNumbers.some((number) => number.endpoint_id === endpointId)) {
          void simulatorApi.amdNumbers().then((numbers) => updateData("amdNumbers", numbers)).catch(() => {});
        }
        return created.id;
      },
      cancelRun: async (runId) => {
        const done = await ok("Could not cancel queued run", async () => {
          const updated = await simulatorApi.cancelRun(runId);
          updateData("runs", (current) => replaceById(current, updated));
        });
        await refresh(true);
        return done;
      },
      createIvrSimulation: async (body) => {
        const created = await run("Could not run IVR simulation", () => simulatorApi.createIvrSimulation(body));
        if (!created) return null;
        updateData("runs", (current) => [created, ...current]);
        return created.id;
      },
      deleteCall: (callId) => ok("Could not delete call", async () => {
        await simulatorApi.deleteCall(callId);
        updateData("calls", (current) => current.filter((call) => call.id !== callId));
      }),
      createConnection: (body) => ok("Could not create provider", async () => {
        const created = await simulatorApi.createConnection(body);
        updateData("connections", (current) => [...current, created]);
      }),
      saveConnection: (id, values) => ok("Could not update provider", async () => {
        const updated: ProviderConnection = await simulatorApi.updateConnection(id, values);
        updateData("connections", (current) => replaceById(current, updated));
        const rename = <T extends { connection_id: string; connection_name: string }>(items: T[]) =>
          items.map((item) => item.connection_id === id ? { ...item, connection_name: updated.name } : item);
        updateData("endpoints", rename);
        updateData("directory", rename);
      }),
      deleteConnection: (id) => ok("Could not delete provider", async () => {
        await simulatorApi.deleteConnection(id);
        await refresh(true);
      }),
      createEndpoint: (body) => ok("Could not create line", async () => {
        const created = await simulatorApi.createEndpoint(body);
        updateData("endpoints", (current) => [...current, created]);
      }),
      saveEndpoint: (id, values) => ok("Could not update line", async () => {
        const updated: Endpoint = await simulatorApi.updateEndpoint(id, values);
        updateData("endpoints", (current) => replaceById(current, updated));
      }),
      deleteEndpoint: (id) => ok("Could not delete line", async () => {
        await simulatorApi.deleteEndpoint(id);
        await refresh(true);
      }),
      createDirectoryEntry: (body) => ok("Could not add extension", async () => {
        const created = await simulatorApi.createDirectoryEntry(body);
        updateData("directory", (current) =>
          [...current, created].sort((left, right) => left.extension.localeCompare(right.extension)));
      }),
      saveDirectoryEntry: (id, values) => ok("Could not update extension", async () => {
        const updated: DirectoryEntry = await simulatorApi.updateDirectoryEntry(id, values);
        updateData("directory", (current) =>
          replaceById(current, updated).sort((left, right) => left.extension.localeCompare(right.extension)));
      }),
      deleteDirectoryEntry: (id) => ok("Could not delete extension", async () => {
        await simulatorApi.deleteDirectoryEntry(id);
        updateData("directory", (current) => current.filter((entry) => entry.id !== id));
      }),
      syncAmdNumbers: () => ok("Could not sync Twilio numbers", async () => {
        updateData("amdNumbers", await simulatorApi.syncAmdNumbers());
        const [connections, endpoints] = await Promise.all([
          simulatorApi.connections(),
          simulatorApi.endpoints(),
        ]);
        updateData("connections", connections);
        updateData("endpoints", endpoints);
      }),
      updateAmdNumber: (id, update) => ok("Could not update number", async () => {
        replaceNumber(await simulatorApi.updateAmdNumber(id, update));
      }),
      queueAmdNumber: async (id, scenario) => {
        const queued = await run("Could not queue scenario", () => simulatorApi.queueAmdNumber(id, scenario));
        if (!queued) return null;
        replaceNumber(queued.number);
        updateData("runs", (current) => [queued.run, ...current]);
        return queued.run.id;
      },
      attachAmdNumber: (id) => ok("Could not connect number", async () => {
        replaceNumber(await simulatorApi.attachAmdNumber(id));
      }),
      restoreAmdNumber: (id) => ok("Could not restore number", async () => {
        replaceNumber(await simulatorApi.restoreAmdNumber(id));
      }),
    };
  }, [data.amdNumbers, refresh, run, updateData]);

  const value = useMemo<ConsoleContextValue>(() => ({
    ...state,
    results,
    busy,
    error,
    clearError: () => setError(null),
    actions,
  }), [state, results, busy, error, actions]);

  return <ConsoleContext.Provider value={value}>{children}</ConsoleContext.Provider>;
}
