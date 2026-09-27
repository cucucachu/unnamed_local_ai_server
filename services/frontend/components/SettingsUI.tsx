import Ionicons from '@expo/vector-icons/Ionicons';
import { useRouter } from 'expo-router';
import type { ComponentProps, ReactNode } from 'react';
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';

import { AppKeyboardAvoidingView } from '@/components/AppKeyboardAvoidingView';
import { theme } from '@/lib/theme';

/** Shared chrome for the Settings stack (`src/app/settings/`): a header
 * with a back (sub-screens) or close (the hub) button, and a scrolling,
 * keyboard-aware body capped to a phone-ish width on wide screens. */
export function SettingsFrame({
  title,
  leading = 'back',
  testID,
  children,
  footer,
}: {
  title: string;
  leading?: 'back' | 'close';
  testID?: string;
  children: ReactNode;
  footer?: ReactNode;
}) {
  const router = useRouter();
  const handleLeading = () => {
    if (leading === 'close' || router.canGoBack()) router.back();
    else router.replace('/settings');
  };

  return (
    <AppKeyboardAvoidingView style={styles.container}>
      <View style={styles.container} testID={testID}>
        <View style={styles.header}>
          <Pressable
            onPress={handleLeading}
            style={styles.headerButton}
            accessibilityRole="button"
            accessibilityLabel={leading === 'close' ? 'Close' : 'Back'}
            testID={leading === 'close' ? 'settings-close-button' : 'settings-back-button'}
          >
            <Ionicons name={leading === 'close' ? 'close' : 'chevron-back'} size={26} color={theme.text} />
          </Pressable>
          <Text style={styles.title} accessibilityRole="header">
            {title}
          </Text>
        </View>
        <ScrollView contentContainerStyle={styles.scroll} keyboardShouldPersistTaps="handled">
          <View style={styles.body}>{children}</View>
          {footer}
        </ScrollView>
      </View>
    </AppKeyboardAvoidingView>
  );
}

export function SectionTitle({ children }: { children: ReactNode }) {
  return <Text style={styles.sectionTitle}>{children}</Text>;
}

/** A bordered group of rows. */
export function Card({ children, testID }: { children: ReactNode; testID?: string }) {
  return (
    <View style={styles.card} testID={testID}>
      {children}
    </View>
  );
}

export function NavRow({
  icon,
  title,
  description,
  onPress,
  testID,
}: {
  icon: ComponentProps<typeof Ionicons>['name'];
  title: string;
  description?: string;
  onPress: () => void;
  testID: string;
}) {
  return (
    <Pressable onPress={onPress} style={styles.navRow} accessibilityRole="button" testID={testID}>
      <Ionicons name={icon} size={20} color={theme.textMuted} />
      <View style={styles.navRowText}>
        <Text style={styles.navRowTitle}>{title}</Text>
        {description ? <Text style={styles.muted}>{description}</Text> : null}
      </View>
      <Ionicons name="chevron-forward" size={18} color={theme.textMuted} />
    </Pressable>
  );
}

export function ActionButton({
  label,
  onPress,
  variant = 'secondary',
  busy = false,
  disabled = false,
  compact = false,
  testID,
}: {
  label: string;
  onPress: () => void;
  variant?: 'primary' | 'secondary' | 'danger';
  busy?: boolean;
  disabled?: boolean;
  compact?: boolean;
  testID?: string;
}) {
  const inactive = busy || disabled;
  return (
    <Pressable
      onPress={onPress}
      disabled={inactive}
      style={[
        styles.button,
        compact && styles.buttonCompact,
        variant === 'primary' && styles.buttonPrimary,
        variant === 'danger' && styles.buttonDanger,
        inactive && styles.buttonInactive,
      ]}
      accessibilityRole="button"
      accessibilityState={{ disabled: inactive, busy }}
      testID={testID}
    >
      {busy ? (
        <ActivityIndicator color={theme.text} size="small" />
      ) : (
        <Text style={[styles.buttonText, variant === 'danger' && styles.buttonTextDanger]}>{label}</Text>
      )}
    </Pressable>
  );
}

export interface SegmentOption<T extends string> {
  value: T;
  label: string;
  testID?: string;
}

export function Segmented<T extends string>({
  options,
  value,
  onChange,
  disabled = false,
  testID,
}: {
  options: SegmentOption<T>[];
  value: T;
  onChange: (value: T) => void;
  disabled?: boolean;
  testID?: string;
}) {
  return (
    <View style={[styles.segmented, disabled && styles.buttonInactive]} testID={testID}>
      {options.map((option) => {
        const selected = option.value === value;
        return (
          <Pressable
            key={option.value}
            onPress={() => !selected && onChange(option.value)}
            disabled={disabled}
            style={[styles.segmentButton, selected && styles.segmentButtonSelected]}
            accessibilityRole="button"
            accessibilityState={{ selected, disabled }}
            testID={option.testID}
          >
            <Text style={[styles.segmentButtonText, selected && styles.segmentButtonTextSelected]}>
              {option.label}
            </Text>
          </Pressable>
        );
      })}
    </View>
  );
}

export function ErrorText({ children, testID }: { children: ReactNode; testID?: string }) {
  if (!children) return null;
  return (
    <Text style={styles.error} accessibilityRole="alert" testID={testID}>
      {children}
    </Text>
  );
}

export function Badge({ label, tone = 'muted', testID }: { label: string; tone?: 'muted' | 'accent' | 'danger'; testID?: string }) {
  return (
    <View
      style={[styles.badge, tone === 'accent' && styles.badgeAccent, tone === 'danger' && styles.badgeDanger]}
      testID={testID}
    >
      <Text style={[styles.badgeText, tone === 'danger' && styles.buttonTextDanger]}>{label}</Text>
    </View>
  );
}

/** Spinner / error-with-retry placeholder for a screen's initial load. */
export function LoadState({ error, onRetry }: { error: string | null; onRetry: () => void }) {
  return (
    <View style={styles.loadState}>
      {error ? (
        <>
          <ErrorText testID="settings-load-error">{error}</ErrorText>
          <ActionButton label="Retry" onPress={onRetry} testID="settings-load-retry" />
        </>
      ) : (
        <ActivityIndicator size="large" color={theme.accent} />
      )}
    </View>
  );
}

export const settingsStyles = StyleSheet.create({
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    paddingHorizontal: 14,
    paddingVertical: 12,
    borderTopWidth: 1,
    borderTopColor: theme.border,
  },
  firstRow: {
    borderTopWidth: 0,
  },
  rowMain: {
    flex: 1,
    gap: 2,
  },
  rowTitle: {
    color: theme.text,
    fontSize: 15,
    fontWeight: '600',
  },
  rowActions: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    alignItems: 'center',
    gap: 8,
  },
  muted: {
    color: theme.textMuted,
    fontSize: 13,
  },
  cardBody: {
    padding: 14,
    gap: 12,
  },
});

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
  headerButton: {
    padding: 4,
  },
  title: {
    flex: 1,
    color: theme.text,
    fontSize: 16,
    fontWeight: '600',
  },
  scroll: {
    flexGrow: 1,
  },
  body: {
    width: '100%',
    maxWidth: 640,
    alignSelf: 'center',
    padding: 16,
    gap: 12,
  },
  sectionTitle: {
    color: theme.textMuted,
    fontSize: 12,
    fontWeight: '700',
    letterSpacing: 0.6,
    textTransform: 'uppercase',
    marginTop: 8,
  },
  card: {
    borderRadius: 10,
    borderWidth: 1,
    borderColor: theme.border,
    backgroundColor: theme.surface,
    overflow: 'hidden',
  },
  navRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    paddingHorizontal: 14,
    paddingVertical: 12,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: theme.border,
    backgroundColor: theme.surface,
  },
  navRowText: {
    flex: 1,
    gap: 2,
  },
  navRowTitle: {
    color: theme.text,
    fontSize: 15,
    fontWeight: '600',
  },
  muted: {
    color: theme.textMuted,
    fontSize: 13,
  },
  button: {
    alignItems: 'center',
    justifyContent: 'center',
    minHeight: 40,
    paddingHorizontal: 14,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: theme.border,
    backgroundColor: theme.bg,
  },
  buttonCompact: {
    minHeight: 32,
    paddingHorizontal: 10,
  },
  buttonPrimary: {
    backgroundColor: theme.accent,
    borderColor: theme.accent,
  },
  buttonDanger: {
    borderColor: theme.danger,
  },
  buttonInactive: {
    opacity: 0.5,
  },
  buttonText: {
    color: theme.text,
    fontSize: 14,
    fontWeight: '600',
  },
  buttonTextDanger: {
    color: theme.danger,
  },
  segmented: {
    flexDirection: 'row',
    alignSelf: 'flex-start',
    borderRadius: 8,
    borderWidth: 1,
    borderColor: theme.border,
    overflow: 'hidden',
  },
  segmentButton: {
    paddingHorizontal: 12,
    paddingVertical: 7,
    backgroundColor: theme.surface,
  },
  segmentButtonSelected: {
    backgroundColor: theme.accent,
  },
  segmentButtonText: {
    color: theme.textMuted,
    fontSize: 13,
    fontWeight: '600',
  },
  segmentButtonTextSelected: {
    color: theme.text,
  },
  error: {
    color: theme.danger,
    fontSize: 13,
    lineHeight: 18,
  },
  badge: {
    paddingHorizontal: 8,
    paddingVertical: 2,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: theme.border,
  },
  badgeAccent: {
    borderColor: theme.accent,
  },
  badgeDanger: {
    borderColor: theme.danger,
  },
  badgeText: {
    color: theme.textMuted,
    fontSize: 12,
    fontWeight: '600',
  },
  loadState: {
    alignItems: 'center',
    justifyContent: 'center',
    gap: 12,
    paddingVertical: 40,
  },
});
