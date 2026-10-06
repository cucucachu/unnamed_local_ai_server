import type { ReactNode } from 'react';
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';

import {
  acceptInvite as acceptInviteRequest,
  clearSession,
  getAuthStatus,
  login as loginRequest,
  loginWithPairedDevice,
  pairThisDevice,
  beginPasskeyLogin,
  finishPasskeyLogin,
  logout as logoutRequest,
  restoreSession,
  setup as setupRequest,
  type InviteAcceptInput,
  type LoginInput,
  type SetupInput,
  type User,
} from '@/lib/auth';
import { AppState } from 'react-native';

import { resolveApiHost } from '@/lib/api';
import { resetCurrentChat } from '@/lib/currentChat';
import { setCurrentSpace } from '@/lib/currentSpace';
import { getCredential } from '@/lib/webauthn';
import { onUnauthorized, sessionToken } from '@/lib/session';

/**
 * App-root auth state. On mount it restores any native token and asks
 * `GET /api/auth/status` which of Setup / Login / the app to show
 * (`AuthGate` renders the choice). Any request that comes back `401`
 * (`lib/api.ts`'s `notifyUnauthorized`) drops the user back to Login.
 */

export type AuthState =
  | { phase: 'loading' }
  | { phase: 'unreachable' }
  | { phase: 'ready'; setupRequired: boolean; user: User | null };

export interface AuthContextValue {
  state: AuthState;
  /** Re-reads `/api/auth/status` (e.g. Retry after the server was down). */
  refresh: () => Promise<void>;
  login: (input: LoginInput) => Promise<void>;
  loginWithPasskey: (input: { username: string; totpCode?: string }) => Promise<void>;
  loginWithDevice: (totpCode?: string) => Promise<void>;
  pairDevice: (payloadJson: string) => Promise<void>;
  setup: (input: SetupInput) => Promise<void>;
  acceptInvite: (input: InviteAcceptInput) => Promise<void>;
  logout: () => Promise<void>;
  /** Adopts a fresh copy of the signed-in user (e.g. from `PATCH
   * /api/platform/me`); ignored if it's someone else. */
  updateUser: (user: User) => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children?: ReactNode }) {
  const [state, setState] = useState<AuthState>({ phase: 'loading' });

  const refresh = useCallback(async () => {
    try {
      await resolveApiHost();
      const status = await getAuthStatus();
      if (!status.authenticated && sessionToken() !== null) await clearSession();
      setState({
        phase: 'ready',
        setupRequired: status.setup_required,
        user: status.authenticated ? (status.user ?? null) : null,
      });
    } catch {
      // Once the app is up (e.g. re-checking after logout) an unreachable
      // server just means signed out; only a cold start shows Retry.
      setState((previous) => (previous.phase === 'ready' ? { ...previous, user: null } : { phase: 'unreachable' }));
    }
  }, []);

  useEffect(() => {
    restoreSession().then(refresh);
  }, [refresh]);

  // Back in the foreground the phone may have moved between home Wi-Fi
  // and the VPN.
  useEffect(() => {
    const subscription = AppState.addEventListener('change', (next) => {
      if (next === 'active') void resolveApiHost();
    });
    return () => subscription.remove();
  }, []);

  useEffect(
    () =>
      onUnauthorized(() => {
        void clearSession();
        setState((previous) => ({
          phase: 'ready',
          setupRequired: previous.phase === 'ready' && previous.setupRequired,
          user: null,
        }));
      }),
    [],
  );

  const signedIn = useCallback((user: User, setupDone = false) => {
    setState((previous) => ({
      phase: 'ready',
      setupRequired: !setupDone && previous.phase === 'ready' && previous.setupRequired,
      user,
    }));
  }, []);

  const login = useCallback(
    async (input: LoginInput) => signedIn(await loginRequest(input)),
    [signedIn],
  );

  const loginWithPasskey = useCallback(
    async ({ username, totpCode }: { username: string; totpCode?: string }) => {
      const options = await beginPasskeyLogin(username);
      const credential = await getCredential(options);
      signedIn(await finishPasskeyLogin({ username, credential, totpCode }));
    },
    [signedIn],
  );

  const loginWithDevice = useCallback(
    async (totpCode?: string) => signedIn(await loginWithPairedDevice(totpCode)),
    [signedIn],
  );

  const pairDevice = useCallback(
    async (payloadJson: string) => {
      signedIn(await pairThisDevice(payloadJson));
    },
    [signedIn],
  );

  const setup = useCallback(
    async (input: SetupInput) => signedIn(await setupRequest(input), true),
    [signedIn],
  );

  const acceptInvite = useCallback(
    async (input: InviteAcceptInput) => signedIn(await acceptInviteRequest(input)),
    [signedIn],
  );

  const logout = useCallback(async () => {
    await logoutRequest();
    resetCurrentChat();
    setCurrentSpace(null);
    await refresh();
  }, [refresh]);

  const updateUser = useCallback((user: User) => {
    setState((previous) =>
      previous.phase === 'ready' && previous.user?.id === user.id ? { ...previous, user } : previous,
    );
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      state,
      refresh,
      login,
      loginWithPasskey,
      loginWithDevice,
      pairDevice,
      setup,
      acceptInvite,
      logout,
      updateUser,
    }),
    [
      state,
      refresh,
      login,
      loginWithPasskey,
      loginWithDevice,
      pairDevice,
      setup,
      acceptInvite,
      logout,
      updateUser,
    ],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (context === null) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}
