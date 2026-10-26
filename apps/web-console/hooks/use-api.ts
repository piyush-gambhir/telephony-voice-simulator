"use client";

import { useCallback, useEffect, useState } from "react";

/**
 * Load data for one page from the simulator API. `reload` refetches; the loader is
 * aborted when the page unmounts or `deps` change.
 */
export function useApi<T>(load: (signal: AbortSignal) => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [version, setVersion] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal).then(
      (next) => {
        if (controller.signal.aborted) return;
        setData(next);
        setError(null);
        setLoading(false);
      },
      (cause) => {
        if (controller.signal.aborted) return;
        setError(cause instanceof Error ? cause.message : "Request failed");
        setLoading(false);
      },
    );
    return () => controller.abort();
    // The loader reads only what callers list in `deps`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, version]);

  const reload = useCallback(() => {
    setLoading(true);
    setVersion((current) => current + 1);
  }, []);

  return { data, error, loading, reload, setData };
}

/** Run one mutation at a time and keep its error for inline display. */
export function useAction() {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = useCallback(async <T,>(task: () => Promise<T>): Promise<T | null> => {
    setPending(true);
    setError(null);
    try {
      return await task();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Request failed");
      return null;
    } finally {
      setPending(false);
    }
  }, []);

  return { pending, error, setError, run };
}
