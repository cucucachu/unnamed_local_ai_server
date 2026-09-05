import type { ReactNode } from 'react';
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';

import { ApiError } from '@/lib/api';
import { getSettings, updateSettings, type SettingsDocument, type SettingsPartial } from '@/lib/settings';

/**
 * App-wide settings context (M8-02) — loads `GET /api/settings` once at app
 * start (mounted from `src/app/_layout.tsx`, the app root, per the ticket),
 * and exposes the current document plus an `updateSettings` action that:
 *
 *   1. Applies the partial update to local state immediately (optimistic).
 *   2. Calls `PUT /api/settings`.
 *   3. On success, adopts the server's response as the new truth (it's the
 *      full merged document — "refetches/refreshes... to stay in sync with
 *      server-merged truth", per spec — using the response directly rather
 *      than issuing a SEPARATE `GET` is the same data, one request instead
 *      of two).
 *   4. On failure, REVERTS to the pre-update snapshot and surfaces an error
 *      via `onError` (the caller — `settings.tsx` — wires this to the same
 *      `Toast`/`useToast` convention `chat/index.tsx`/`files.tsx` already
 *      use for optimistic-revert-on-failure, rather than this provider
 *      inventing its own toast UI).
 *
 * No provider pattern exists elsewhere in this codebase yet (screens fetch
 * their own data directly, e.g. `chat/index.tsx`'s `listThreads`) — kept
 * deliberately simple (plain `createContext` + `useState`, no reducer/
 * external state library) rather than introducing new infrastructure for
 * one small settings document.
 */

export interface SettingsContextValue {
  /** `null` while the initial `GET /api/settings` is still in flight, or if
   * it failed and no document has ever loaded. */
  settings: SettingsDocument | null;
  /** True only for the initial load (not for `updateSettings` calls). */
  loading: boolean;
  /** Optimistic update + revert-on-failure, per the module docstring.
   * Rejects (after reverting) so a caller can `await` and know it failed,
   * IN ADDITION to the `onError` callback below — `settings.tsx` uses
   * `onError` for the toast and doesn't need to also catch this. */
  updateSettings: (partial: SettingsPartial) => Promise<void>;
}

const SettingsContext = createContext<SettingsContextValue | null>(null);

export interface SettingsProviderProps {
  // Optional in the TYPE (real usage — `_layout.tsx` — always supplies real
  // children via JSX) so tests can call `React.createElement(SettingsProvider,
  // props, ...children)` directly: TS's `createElement` overload for that
  // rest-args call shape doesn't reconcile with a `props` type that marks
  // `children` required, even though passing children via rest args is a
  // perfectly normal, real `React.createElement` call.
  children?: ReactNode;
  /** Called with a human-readable message whenever an `updateSettings` call
   * fails (after the optimistic change has already been reverted). Kept as
   * a prop rather than a second context value so `_layout.tsx` can wire it
   * straight to a screen-agnostic toast if one exists at that level —
   * today no root-level toast exists, so this is normally a no-op there;
   * `settings.tsx` reads/writes settings via `useSettings()` directly and
   * shows its own toast for its own calls instead of relying on this prop.
   */
  onError?: (message: string) => void;
}

export function SettingsProvider({ children, onError }: SettingsProviderProps) {
  const [settings, setSettings] = useState<SettingsDocument | null>(null);
  const [loading, setLoading] = useState(true);
  // Avoids a stale-closure `onError` inside `updateSettings` without having
  // to put `onError` in that callback's own dependency array (which would
  // change identity on every render for an inline arrow-function prop).
  // Assigned in an effect (not during render) per the `react-hooks/refs`
  // lint rule — mutating a ref's `.current` during render is disallowed.
  const onErrorRef = useRef(onError);
  useEffect(() => {
    onErrorRef.current = onError;
  }, [onError]);

  // Mirrors `settings` on every commit — `handleUpdate` below is a stable
  // (`useCallback([])`) closure, so it can't read the `settings` state
  // variable itself (that would always see its FIRST-render value, stale
  // forever). React also does not invoke a `setState` functional updater
  // synchronously (confirmed directly: logging inside one such updater
  // here showed it running strictly LATER than the very next line after
  // the `setSettings(...)` call that scheduled it) — so this ref, kept in
  // sync via an effect rather than mutated during render, is what
  // `handleUpdate` reads as "the value right before this optimistic
  // update" to revert to.
  const settingsRef = useRef(settings);
  useEffect(() => {
    settingsRef.current = settings;
  }, [settings]);

  useEffect(() => {
    let cancelled = false;
    getSettings()
      .then((doc) => {
        if (!cancelled) setSettings(doc);
      })
      .catch((error) => {
        if (!cancelled) {
          onErrorRef.current?.(error instanceof ApiError ? error.detail : 'Failed to load settings');
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleUpdate = useCallback(async (partial: SettingsPartial) => {
    const previous = settingsRef.current;
    if (previous) {
      const optimistic = { ...previous, ...partial };
      settingsRef.current = optimistic;
      setSettings(optimistic);
    }

    try {
      const merged = await updateSettings(partial);
      settingsRef.current = merged;
      setSettings(merged);
    } catch (error) {
      settingsRef.current = previous;
      setSettings(previous);
      onErrorRef.current?.(error instanceof ApiError ? error.detail : 'Failed to update settings');
      throw error;
    }
  }, []);

  const value = useMemo<SettingsContextValue>(
    () => ({ settings, loading, updateSettings: handleUpdate }),
    [settings, loading, handleUpdate],
  );

  return <SettingsContext.Provider value={value}>{children}</SettingsContext.Provider>;
}

/** Throws if called outside a `SettingsProvider` (a real bug, not a
 * recoverable condition — every screen that needs settings renders under
 * the app-root provider, see `src/app/_layout.tsx`). */
export function useSettings(): SettingsContextValue {
  const context = useContext(SettingsContext);
  if (context === null) {
    throw new Error('useSettings must be used within a SettingsProvider');
  }
  return context;
}
