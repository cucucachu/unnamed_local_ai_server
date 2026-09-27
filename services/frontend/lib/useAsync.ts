import { useCallback, useEffect, useState } from 'react';

import { platformErrorMessage } from './platform';
import { StepUpCancelledError } from './stepUp';

/** Loads `load()` on mount and on `reload()`. `load` must be stable
 * (`useCallback`). While a reload is in flight the previous `data` stays
 * visible; `data === null && error === null` means the first load. */
export function useLoad<T>(load: () => Promise<T>) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    load().then(
      (value) => {
        if (cancelled) return;
        setData(value);
        setError(null);
      },
      (caught) => {
        if (!cancelled) setError(platformErrorMessage(caught));
      },
    );
    return () => {
      cancelled = true;
    };
  }, [load, attempt]);

  const reload = useCallback(() => {
    setError(null);
    setAttempt((n) => n + 1);
  }, []);

  return { data, error, reload, setData };
}

/** Runs one mutation at a time per screen, tracking which (`busyKey`) is
 * in flight and reporting failures (other than a dismissed step-up
 * prompt) to `onError`. Resolves `true` on success. */
export function useAction(onError: (message: string) => void) {
  const [busyKey, setBusyKey] = useState<string | null>(null);

  const run = useCallback(
    async (key: string, fn: () => Promise<unknown>): Promise<boolean> => {
      setBusyKey(key);
      try {
        await fn();
        return true;
      } catch (caught) {
        if (!(caught instanceof StepUpCancelledError)) onError(platformErrorMessage(caught));
        return false;
      } finally {
        setBusyKey(null);
      }
    },
    [onError],
  );

  return { busyKey, run };
}
