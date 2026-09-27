import type { ReactNode } from 'react';
import { createContext, useCallback, useContext, useMemo, useRef, useState } from 'react';
import { ActivityIndicator, Modal, Pressable, StyleSheet, Text, TextInput, View } from 'react-native';

import { AppKeyboardAvoidingView } from '@/components/AppKeyboardAvoidingView';
import { stepUp } from '@/lib/auth';
import { platformErrorMessage } from '@/lib/platform';
import { withStepUp as runWithStepUp } from '@/lib/stepUp';
import { theme } from '@/lib/theme';

interface StepUpContextValue {
  /** `fn`, retried once after a password prompt if it answers `403
   * step_up_required`. Rejects with `StepUpCancelledError` if the user
   * dismisses the prompt. */
  withStepUp: <T>(fn: () => Promise<T>) => Promise<T>;
}

const StepUpContext = createContext<StepUpContextValue | null>(null);

/** Owns the one password prompt admin actions share. Concurrent callers
 * that all hit `step_up_required` wait on the same prompt. */
export function StepUpProvider({ children }: { children?: ReactNode }) {
  const [prompting, setPrompting] = useState(false);
  const pendingRef = useRef<{ promise: Promise<boolean>; resolve: (ok: boolean) => void } | null>(null);

  const confirmPassword = useCallback(() => {
    if (pendingRef.current === null) {
      let resolve!: (ok: boolean) => void;
      const promise = new Promise<boolean>((r) => {
        resolve = r;
      });
      pendingRef.current = { promise, resolve };
      setPrompting(true);
    }
    return pendingRef.current.promise;
  }, []);

  const finish = useCallback((ok: boolean) => {
    const pending = pendingRef.current;
    pendingRef.current = null;
    setPrompting(false);
    pending?.resolve(ok);
  }, []);

  const withStepUp = useCallback(
    <T,>(fn: () => Promise<T>) => runWithStepUp(fn, confirmPassword),
    [confirmPassword],
  );
  const value = useMemo(() => ({ withStepUp }), [withStepUp]);

  return (
    <StepUpContext.Provider value={value}>
      {children}
      {prompting ? <StepUpModal onDone={finish} /> : null}
    </StepUpContext.Provider>
  );
}

export function useStepUp(): StepUpContextValue {
  const context = useContext(StepUpContext);
  if (context === null) throw new Error('useStepUp must be used within a StepUpProvider');
  return context;
}

function StepUpModal({ onDone }: { onDone: (ok: boolean) => void }) {
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit() {
    if (busy || !password) return;
    setBusy(true);
    setError(null);
    try {
      await stepUp(password);
      onDone(true);
    } catch (caught) {
      setError(platformErrorMessage(caught));
      setBusy(false);
    }
  }

  return (
    <Modal visible transparent animationType="fade" onRequestClose={() => onDone(false)}>
      <AppKeyboardAvoidingView style={styles.overlay}>
        <View style={styles.card} testID="step-up-modal">
          <Text style={styles.title}>Confirm your password</Text>
          <Text style={styles.subtitle}>Admin changes need your password again (good for 5 minutes).</Text>
          <TextInput
            style={styles.input}
            value={password}
            onChangeText={setPassword}
            secureTextEntry
            autoFocus
            autoCapitalize="none"
            autoComplete="current-password"
            textContentType="password"
            placeholder="Password"
            placeholderTextColor={theme.textMuted}
            accessibilityLabel="Password"
            returnKeyType="go"
            onSubmitEditing={handleSubmit}
            testID="step-up-password"
          />
          {error ? (
            <Text style={styles.error} accessibilityRole="alert" testID="step-up-error">
              {error}
            </Text>
          ) : null}
          <View style={styles.actions}>
            <Pressable
              style={styles.button}
              onPress={() => onDone(false)}
              accessibilityRole="button"
              testID="step-up-cancel"
            >
              <Text style={styles.buttonText}>Cancel</Text>
            </Pressable>
            <Pressable
              style={[styles.button, styles.primaryButton, (!password || busy) && styles.buttonDisabled]}
              onPress={handleSubmit}
              disabled={!password || busy}
              accessibilityRole="button"
              testID="step-up-submit"
            >
              {busy ? <ActivityIndicator color={theme.text} /> : <Text style={styles.buttonText}>Confirm</Text>}
            </Pressable>
          </View>
        </View>
      </AppKeyboardAvoidingView>
    </Modal>
  );
}

const styles = StyleSheet.create({
  overlay: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.5)',
    alignItems: 'center',
    justifyContent: 'center',
    padding: 24,
  },
  card: {
    width: '100%',
    maxWidth: 360,
    backgroundColor: theme.surface,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: theme.border,
    padding: 16,
    gap: 12,
  },
  title: {
    color: theme.text,
    fontSize: 16,
    fontWeight: '600',
  },
  subtitle: {
    color: theme.textMuted,
    fontSize: 13,
  },
  input: {
    borderRadius: 8,
    borderWidth: 1,
    borderColor: theme.border,
    backgroundColor: theme.bg,
    color: theme.text,
    paddingHorizontal: 12,
    paddingVertical: 8,
    fontSize: 15,
  },
  error: {
    color: theme.danger,
    fontSize: 13,
  },
  actions: {
    flexDirection: 'row',
    justifyContent: 'flex-end',
    gap: 8,
  },
  button: {
    minWidth: 72,
    alignItems: 'center',
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderRadius: 8,
    backgroundColor: theme.bg,
    borderWidth: 1,
    borderColor: theme.border,
  },
  primaryButton: {
    backgroundColor: theme.accent,
    borderColor: theme.accent,
  },
  buttonDisabled: {
    opacity: 0.5,
  },
  buttonText: {
    color: theme.text,
    fontSize: 14,
    fontWeight: '600',
  },
});
