import Ionicons from '@expo/vector-icons/Ionicons';
import { platformEventRelay, type BridgeHost, type SandboxEvents } from '@homeai/sdk/host';
import { useCallback, useEffect, useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';

import { AppAgentPanel } from '@/components/AppAgentPanel';
import { AppSandbox } from '@/components/AppSandbox';
import { ActionButton, ErrorText } from '@/components/SettingsUI';
import {
  isReadOnly,
  loadInstanceCode,
  loadInstanceDocument,
  type InstanceDocument,
} from '@/lib/appHost';
import type { Space } from '@/lib/platform';
import { openPlatformEvents } from '@/lib/platformEvents';
import { monospaceFontFamily, theme } from '@/lib/theme';

const LOAD_ERRORS: Record<string, string> = {
  no_bundle: "This app hasn't been built yet.",
  not_found: 'This app is no longer installed here.',
  unavailable: "Couldn't reach the server. Check your connection and try again.",
};

function loadErrorMessage(error: unknown): string {
  const code = (error as { code?: unknown } | null)?.code;
  if (typeof code === 'string') return LOAD_ERRORS[code] ?? `Couldn't open this app (${code}).`;
  return "Couldn't open this app.";
}

type Load = { doc: InstanceDocument | null; error: string | null };
type Crash = { title: string; message: string };

/**
 * Runs one installed instance (`docs/PLATFORM.md` §7 "Runtime and bridge"):
 * loads its bundle and SDK runtime into a sandbox (`AppSandbox`), relays
 * `/ws/platform/events` into it (`db_changed` -> live queries re-run,
 * `app_built` -> hot reload), and covers it with an error overlay when the
 * app reports a runtime error. Reload starts a fresh sandbox from the newest
 * bundle. `instanceId` is the only instance the sandbox can reach. "Ask the
 * agent" opens a chat thread pre-seeded with this app's files (M13-04);
 * `askAgent` from the sandbox opens it with a prompt. Agent writes still
 * arrive as this instance's `db_changed`.
 */
export function AppRunner({
  instanceId,
  space,
  appId,
  appName = 'App',
}: {
  instanceId: string;
  space: Space;
  appId?: string;
  appName?: string;
}) {
  const readOnly = isReadOnly(space);
  const [generation, setGeneration] = useState(0);
  const [load, setLoad] = useState<Load>({ doc: null, error: null });
  const [host, setHost] = useState<BridgeHost | null>(null);
  const [crash, setCrash] = useState<Crash | null>(null);
  const [agent, setAgent] = useState<{ open: boolean; prompt: string | null }>({ open: false, prompt: null });

  useEffect(() => {
    let cancelled = false;
    loadInstanceDocument(instanceId, space).then(
      (doc) => !cancelled && setLoad({ doc, error: null }),
      (error) => !cancelled && setLoad({ doc: null, error: loadErrorMessage(error) }),
    );
    return () => {
      cancelled = true;
    };
  }, [instanceId, space, generation]);

  const { doc } = load;
  useEffect(() => {
    if (!host || !doc) return;
    const relay = platformEventRelay({
      instanceId,
      appId: doc.appId,
      host,
      reload: () => loadInstanceCode(instanceId),
      // A failed hot reload leaves the running version; the next build retries.
      onError: () => {},
    });
    const events = openPlatformEvents(relay);
    return () => events.close();
  }, [host, doc, instanceId]);

  const onEvent = useCallback(<E extends keyof SandboxEvents>(event: E, data: SandboxEvents[E]) => {
    if (event === 'runtime.ready') setCrash(null);
    else if (event === 'runtime.error') {
      const { message } = data as SandboxEvents['runtime.error'];
      setCrash({ title: 'This app hit an error', message: message || 'Unknown error' });
    }
  }, []);

  const onKilled = useCallback(() => {
    setCrash({ title: 'This app was stopped', message: 'It tried to navigate away from its sandbox.' });
  }, []);

  const onAskAgent = useCallback((prompt: string) => {
    setAgent({ open: true, prompt });
  }, []);

  const openAgent = useCallback(() => {
    setAgent((current) => (current.open ? { open: false, prompt: null } : { open: true, prompt: null }));
  }, []);

  const restart = useCallback(() => {
    setCrash(null);
    setHost(null);
    setLoad({ doc: null, error: null });
    setGeneration((n) => n + 1);
  }, []);

  return (
    <View style={styles.container} testID="app-runner">
      {readOnly ? (
        <Text style={styles.readOnly} testID="app-runner-read-only">
          View only — you can&apos;t change this app&apos;s data.
        </Text>
      ) : null}
      <View style={styles.bar}>
        <Pressable
          onPress={openAgent}
          accessibilityRole="button"
          accessibilityLabel="Ask the agent"
          testID="app-ask-agent"
          style={styles.askButton}
        >
          <Ionicons name="chatbubble-ellipses-outline" size={18} color={theme.text} />
          <Text style={styles.askLabel}>{agent.open ? 'Hide agent' : 'Ask the agent'}</Text>
        </Pressable>
      </View>
      <View style={styles.body}>
      <View style={styles.stage}>
        {doc ? (
          <AppSandbox
            key={generation}
            instanceId={instanceId}
            html={doc.html}
            readOnly={readOnly}
            onEvent={onEvent}
            onHost={setHost}
            onKilled={onKilled}
            onAskAgent={onAskAgent}
          />
        ) : (
          <View style={styles.centered}>
            {load.error ? (
              <>
                <ErrorText testID="app-runner-load-error">{load.error}</ErrorText>
                <ActionButton label="Retry" onPress={restart} testID="app-runner-retry" />
              </>
            ) : (
              <ActivityIndicator size="large" color={theme.accent} />
            )}
          </View>
        )}
        {crash ? (
          <View style={styles.overlay} testID="app-error-overlay" accessibilityRole="alert">
            <View style={styles.overlayCard}>
              <Text style={styles.overlayTitle}>{crash.title}</Text>
              <Text style={styles.overlayMessage} selectable testID="app-error-message">
                {crash.message}
              </Text>
              <View style={styles.overlayActions}>
                <ActionButton label="Reload" variant="primary" onPress={restart} testID="app-error-reload" />
                <ActionButton label="Dismiss" onPress={() => setCrash(null)} testID="app-error-dismiss" />
              </View>
            </View>
          </View>
        ) : null}
      </View>
      {agent.open && (appId ?? doc?.appId) ? (
        <AppAgentPanel
          instanceId={instanceId}
          space={space}
          appId={(appId ?? doc?.appId)!}
          appName={appName}
          initialPrompt={agent.prompt}
          onClose={() => setAgent({ open: false, prompt: null })}
        />
      ) : null}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: theme.bg,
  },
  stage: {
    flex: 1,
  },
  readOnly: {
    color: theme.textMuted,
    fontSize: 13,
    paddingHorizontal: 16,
    paddingVertical: 8,
    borderBottomWidth: 1,
    borderBottomColor: theme.border,
  },
  bar: {
    flexDirection: 'row',
    justifyContent: 'flex-end',
    paddingHorizontal: 8,
    paddingVertical: 6,
    borderBottomWidth: 1,
    borderBottomColor: theme.border,
  },
  askButton: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    paddingHorizontal: 8,
    paddingVertical: 4,
  },
  askLabel: { color: theme.text, fontSize: 14 },
  body: { flex: 1, flexDirection: 'row' },
  centered: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    gap: 12,
    padding: 24,
  },
  overlay: {
    ...StyleSheet.absoluteFill,
    backgroundColor: 'rgba(14, 17, 22, 0.85)',
    alignItems: 'center',
    justifyContent: 'center',
    padding: 24,
  },
  overlayCard: {
    width: '100%',
    maxWidth: 520,
    gap: 12,
    padding: 16,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: theme.danger,
    backgroundColor: theme.surface,
  },
  overlayTitle: {
    color: theme.text,
    fontSize: 16,
    fontWeight: '600',
  },
  overlayMessage: {
    color: theme.danger,
    fontSize: 13,
    fontFamily: monospaceFontFamily,
  },
  overlayActions: {
    flexDirection: 'row',
    gap: 8,
  },
});
