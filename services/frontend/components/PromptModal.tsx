import { useState } from 'react';
import { Modal, Pressable, StyleSheet, Text, TextInput, View } from 'react-native';

import { AppKeyboardAvoidingView } from '@/components/AppKeyboardAvoidingView';
import { theme } from '@/lib/theme';

export interface PromptModalProps {
  title: string;
  initialValue?: string;
  submitLabel?: string;
  onSubmit: (value: string) => void;
  onCancel: () => void;
}

/**
 * Cross-platform stand-in for `Alert.prompt` — used for both "New folder"
 * and "Rename" (per the ticket). `Alert.prompt` is iOS-only in React Native:
 * confirmed by reading `react-native`'s own implementation
 * (`node_modules/react-native/Libraries/Alert/Alert.js`), where `static
 * prompt(...)` is annotated `@platform ios` and `Alert.alert`'s own Android
 * branch never calls it at all — so there's no Android implementation, and
 * (same as `Alert.alert`, see `chat/index.tsx`'s own note on this) no real
 * web implementation either. A plain custom `Modal` + `TextInput` works
 * identically on every platform this app targets.
 *
 * No `visible` prop, deliberately: the caller (`files.tsx`) conditionally
 * RENDERS this component only while a prompt is actually pending (mirrors
 * `FileActionSheet`'s own `entry: FileEntry | null` convention), so a fresh
 * mount always starts from the current `initialValue` with no separate
 * "reset the text field when re-opened for a different entry" effect
 * needed — avoiding a synchronous `setState` inside `useEffect`, which
 * this project's `react-hooks/set-state-in-effect` lint rule (from
 * `app.json`'s `experiments.reactCompiler`) flags.
 */
export function PromptModal({ title, initialValue = '', submitLabel = 'OK', onSubmit, onCancel }: PromptModalProps) {
  const [value, setValue] = useState(initialValue);

  const trimmed = value.trim();
  const canSubmit = trimmed.length > 0;

  return (
    <Modal visible transparent animationType="fade" onRequestClose={onCancel}>
      {/* M9-04: same keyboard-controller KAV as chat, so the centered
          card (autoFocus TextInput) sits above the Android keyboard
          instead of being covered. Web is RN's KAV / viewport resize. */}
      <AppKeyboardAvoidingView style={styles.overlay}>
        <View style={styles.card}>
          <Text style={styles.title}>{title}</Text>
          <TextInput
            style={styles.input}
            value={value}
            onChangeText={setValue}
            placeholderTextColor={theme.textMuted}
            autoFocus
            testID="prompt-modal-input"
          />
          <View style={styles.actions}>
            <Pressable style={styles.button} onPress={onCancel} accessibilityRole="button" testID="prompt-modal-cancel">
              <Text style={styles.buttonText}>Cancel</Text>
            </Pressable>
            <Pressable
              style={[styles.button, styles.primaryButton, !canSubmit && styles.buttonDisabled]}
              onPress={() => canSubmit && onSubmit(trimmed)}
              disabled={!canSubmit}
              accessibilityRole="button"
              testID="prompt-modal-submit"
            >
              <Text style={styles.buttonText}>{submitLabel}</Text>
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
  actions: {
    flexDirection: 'row',
    justifyContent: 'flex-end',
    gap: 8,
  },
  button: {
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
