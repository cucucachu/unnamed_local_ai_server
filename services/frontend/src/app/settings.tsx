import Ionicons from '@expo/vector-icons/Ionicons';
import { useRouter } from 'expo-router';
import type { ReactNode } from 'react';
import { useCallback } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Switch, Text, View } from 'react-native';

import { useSettings } from '@/components/SettingsProvider';
import { Toast, useToast } from '@/components/Toast';
import { ApiError } from '@/lib/api';
import type { EditModeDefault } from '@/lib/settings';
import { theme } from '@/lib/theme';

/**
 * M8-02 — modal settings screen, registered as a `presentation: 'modal'`
 * `Stack.Screen` in `_layout.tsx` (a sibling of the `(tabs)` group route,
 * same pattern as `media.tsx`). Reachable from the chat thread-list
 * header's gear icon (see `chat/index.tsx`).
 *
 * Every control here reads `useSettings()`'s current document and calls
 * its `updateSettings` action on change — that hook already does the
 * optimistic-apply / revert-on-failure dance (`components/
 * SettingsProvider.tsx`); this screen's only job on top of that is
 * surfacing a toast when a change fails, using the exact same `Toast`/
 * `useToast` convention `chat/index.tsx`/`files.tsx` already use for
 * optimistic-revert-on-failure.
 */
export default function SettingsScreen() {
  const router = useRouter();
  const { settings, loading, updateSettings } = useSettings();
  const { message: toast, showToast } = useToast();

  const handleToggleHitl = useCallback(
    (value: boolean) => {
      updateSettings({ hitl_enabled: value }).catch((error) => {
        showToast(error instanceof ApiError ? error.detail : 'Failed to update settings');
      });
    },
    [updateSettings, showToast],
  );

  const handleToggleThinking = useCallback(
    (value: boolean) => {
      updateSettings({ thinking_enabled: value }).catch((error) => {
        showToast(error instanceof ApiError ? error.detail : 'Failed to update settings');
      });
    },
    [updateSettings, showToast],
  );

  const handleSetEditMode = useCallback(
    (value: EditModeDefault) => {
      updateSettings({ edit_mode_default: value }).catch((error) => {
        showToast(error instanceof ApiError ? error.detail : 'Failed to update settings');
      });
    },
    [updateSettings, showToast],
  );

  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <Pressable
          onPress={() => router.back()}
          style={styles.closeButton}
          accessibilityRole="button"
          accessibilityLabel="Close"
          testID="settings-close-button"
        >
          <Ionicons name="close" size={26} color={theme.text} />
        </Pressable>
        <Text style={styles.title}>Settings</Text>
      </View>

      {loading || settings === null ? (
        <View style={styles.centered}>
          <ActivityIndicator size="large" color={theme.accent} />
        </View>
      ) : (
        <View style={styles.body}>
          <SettingRow
            title="Require approval"
            description="Require approval before the agent writes files or runs code."
            testID="settings-hitl-row"
          >
            <Switch
              value={settings.hitl_enabled}
              onValueChange={handleToggleHitl}
              testID="settings-hitl-switch"
            />
          </SettingRow>

          <SettingRow
            title="Show thinking"
            description="Show the agent's step-by-step reasoning while it works."
            testID="settings-thinking-row"
          >
            <Switch
              value={settings.thinking_enabled}
              onValueChange={handleToggleThinking}
              testID="settings-thinking-switch"
            />
          </SettingRow>

          <SettingRow
            title="Default edit mode"
            description="How file edits are applied by default: replace the file's content, or branch into a new version."
            testID="settings-edit-mode-row"
          >
            <SegmentedChoice value={settings.edit_mode_default} onChange={handleSetEditMode} />
          </SettingRow>

          <Text style={styles.footnote} testID="settings-voice-footnote">
            Voice input uses your browser&apos;s speech service.
          </Text>
        </View>
      )}

      <Toast message={toast} testID="settings-toast" />
    </View>
  );
}

function SettingRow({
  title,
  description,
  testID,
  children,
}: {
  title: string;
  description: string;
  testID: string;
  children: ReactNode;
}) {
  return (
    <View style={styles.row} testID={testID}>
      <View style={styles.rowText}>
        <Text style={styles.rowTitle}>{title}</Text>
        <Text style={styles.rowDescription}>{description}</Text>
      </View>
      {children}
    </View>
  );
}

function SegmentedChoice({
  value,
  onChange,
}: {
  value: EditModeDefault;
  onChange: (value: EditModeDefault) => void;
}) {
  return (
    <View style={styles.segmented}>
      <SegmentButton
        label="Replace"
        selected={value === 'truncate'}
        onPress={() => onChange('truncate')}
        testID="settings-edit-mode-truncate"
      />
      <SegmentButton
        label="Branch"
        selected={value === 'fork'}
        onPress={() => onChange('fork')}
        testID="settings-edit-mode-fork"
      />
    </View>
  );
}

function SegmentButton({
  label,
  selected,
  onPress,
  testID,
}: {
  label: string;
  selected: boolean;
  onPress: () => void;
  testID: string;
}) {
  return (
    <Pressable
      onPress={onPress}
      style={[styles.segmentButton, selected && styles.segmentButtonSelected]}
      accessibilityRole="button"
      accessibilityState={{ selected }}
      testID={testID}
    >
      <Text style={[styles.segmentButtonText, selected && styles.segmentButtonTextSelected]}>{label}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: theme.bg,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    paddingHorizontal: 12,
    paddingVertical: 10,
    borderBottomWidth: 1,
    borderBottomColor: theme.border,
  },
  closeButton: {
    padding: 4,
  },
  title: {
    flex: 1,
    color: theme.text,
    fontSize: 16,
    fontWeight: '600',
  },
  centered: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
  },
  body: {
    padding: 16,
    gap: 20,
  },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 16,
    paddingVertical: 8,
    borderBottomWidth: 1,
    borderBottomColor: theme.border,
  },
  rowText: {
    flex: 1,
    gap: 4,
  },
  rowTitle: {
    color: theme.text,
    fontSize: 16,
    fontWeight: '600',
  },
  rowDescription: {
    color: theme.textMuted,
    fontSize: 13,
  },
  segmented: {
    flexDirection: 'row',
    borderRadius: 8,
    borderWidth: 1,
    borderColor: theme.border,
    overflow: 'hidden',
  },
  segmentButton: {
    paddingHorizontal: 14,
    paddingVertical: 8,
    backgroundColor: theme.surface,
  },
  segmentButtonSelected: {
    backgroundColor: theme.accent,
  },
  segmentButtonText: {
    color: theme.textMuted,
    fontSize: 14,
    fontWeight: '600',
  },
  segmentButtonTextSelected: {
    color: theme.text,
  },
  footnote: {
    color: theme.textMuted,
    fontSize: 12,
    lineHeight: 16,
    marginTop: 8,
  },
});
