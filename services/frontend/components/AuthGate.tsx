import type { ReactNode } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';

import { useAuth } from '@/components/AuthProvider';
import { theme } from '@/lib/theme';

/**
 * Holds the app back until the first `/api/auth/status` answer (spinner, or
 * Retry if the server is unreachable), then renders `children` — the root
 * Stack, whose `Stack.Protected` guards pick Login vs the app. Once
 * mounted the Stack stays mounted: unmounting the root navigator makes
 * Expo Router rewrite the URL to its first route.
 */
export function AuthGate({ children }: { children?: ReactNode }) {
  const { state, refresh } = useAuth();

  if (state.phase === 'loading') {
    return (
      <View style={styles.centered} testID="auth-loading">
        <ActivityIndicator size="large" color={theme.accent} />
      </View>
    );
  }

  if (state.phase === 'unreachable') {
    return (
      <View style={styles.centered} testID="auth-unreachable">
        <Text style={styles.message}>Can&apos;t reach the Home AI server.</Text>
        <Pressable onPress={refresh} style={styles.retry} accessibilityRole="button" testID="auth-retry">
          <Text style={styles.retryText}>Retry</Text>
        </Pressable>
      </View>
    );
  }

  return (
    <View style={styles.app} testID={state.user ? 'auth-authenticated' : 'auth-signed-out'}>
      {children}
    </View>
  );
}

const styles = StyleSheet.create({
  app: {
    flex: 1,
  },
  centered: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    gap: 16,
    padding: 24,
    backgroundColor: theme.bg,
  },
  message: {
    color: theme.text,
    fontSize: 16,
  },
  retry: {
    paddingHorizontal: 20,
    paddingVertical: 10,
    borderRadius: 8,
    backgroundColor: theme.accent,
  },
  retryText: {
    color: theme.text,
    fontSize: 15,
    fontWeight: '600',
  },
});
