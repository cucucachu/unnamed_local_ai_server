import type { ReactNode } from 'react';
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
  type TextInputProps,
} from 'react-native';

import { AppKeyboardAvoidingView } from '@/components/AppKeyboardAvoidingView';
import { theme } from '@/lib/theme';

/** Shared chrome for the Setup / Login / Invite screens: a centered card
 * with a title, fields, an error line, and a submit button. */
export function AuthFormFrame({
  title,
  subtitle,
  error,
  testID,
  children,
  footer,
}: {
  title: string;
  subtitle?: string;
  error: string | null;
  testID: string;
  children: ReactNode;
  footer?: ReactNode;
}) {
  return (
    <AppKeyboardAvoidingView style={styles.container}>
      <ScrollView contentContainerStyle={styles.scroll} keyboardShouldPersistTaps="handled">
        <View style={styles.card} testID={testID}>
          <Text style={styles.title} accessibilityRole="header">
            {title}
          </Text>
          {subtitle ? <Text style={styles.subtitle}>{subtitle}</Text> : null}
          {children}
          {error ? (
            <Text style={styles.error} testID="auth-error" accessibilityRole="alert">
              {error}
            </Text>
          ) : null}
          {footer}
        </View>
      </ScrollView>
    </AppKeyboardAvoidingView>
  );
}

export function AuthField({ label, ...inputProps }: { label: string } & TextInputProps) {
  return (
    <View style={styles.field}>
      <Text style={styles.label}>{label}</Text>
      <TextInput
        style={styles.input}
        placeholderTextColor={theme.textMuted}
        autoCapitalize="none"
        autoCorrect={false}
        accessibilityLabel={label}
        {...inputProps}
      />
    </View>
  );
}

export function AuthSubmitButton({
  label,
  busy,
  onPress,
}: {
  label: string;
  busy: boolean;
  onPress: () => void;
}) {
  return (
    <Pressable
      onPress={onPress}
      disabled={busy}
      style={[styles.submit, busy && styles.submitBusy]}
      accessibilityRole="button"
      accessibilityState={{ disabled: busy, busy }}
      testID="auth-submit"
    >
      {busy ? <ActivityIndicator color={theme.text} /> : <Text style={styles.submitText}>{label}</Text>}
    </Pressable>
  );
}

export function AuthLink({ label, onPress, testID }: { label: string; onPress: () => void; testID: string }) {
  return (
    <Pressable onPress={onPress} accessibilityRole="link" testID={testID} style={styles.link}>
      <Text style={styles.linkText}>{label}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: theme.bg,
  },
  scroll: {
    flexGrow: 1,
    justifyContent: 'center',
    padding: 20,
  },
  card: {
    width: '100%',
    maxWidth: 400,
    alignSelf: 'center',
    gap: 14,
    padding: 20,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: theme.border,
    backgroundColor: theme.surface,
  },
  title: {
    color: theme.text,
    fontSize: 22,
    fontWeight: '700',
  },
  subtitle: {
    color: theme.textMuted,
    fontSize: 14,
    lineHeight: 20,
  },
  field: {
    gap: 6,
  },
  label: {
    color: theme.textMuted,
    fontSize: 13,
    fontWeight: '600',
  },
  input: {
    color: theme.text,
    fontSize: 16,
    paddingHorizontal: 12,
    paddingVertical: 10,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: theme.border,
    backgroundColor: theme.bg,
  },
  error: {
    color: theme.danger,
    fontSize: 14,
    lineHeight: 20,
  },
  submit: {
    alignItems: 'center',
    justifyContent: 'center',
    minHeight: 44,
    borderRadius: 8,
    backgroundColor: theme.accent,
  },
  submitBusy: {
    opacity: 0.7,
  },
  submitText: {
    color: theme.text,
    fontSize: 16,
    fontWeight: '600',
  },
  link: {
    alignSelf: 'center',
    padding: 4,
  },
  linkText: {
    color: theme.accent,
    fontSize: 14,
  },
});
