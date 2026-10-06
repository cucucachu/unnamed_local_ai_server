import Ionicons from '@expo/vector-icons/Ionicons';
import type { ComponentProps } from 'react';
import { ActivityIndicator, Modal, Pressable, StyleSheet, Text, View } from 'react-native';

import { AppIcon } from '@/components/AppGrid';
import type { Instance } from '@/lib/apps';
import { theme } from '@/lib/theme';

type IconName = ComponentProps<typeof Ionicons>['name'];

export interface AppActionSheetProps {
  instance: Instance;
  spaceName: string;
  /** Rebuild needs the app's source (it was made or forked in a space the user can see). */
  canRebuild: boolean;
  /** Update, rebuild and uninstall need write access to the space. */
  canChange: boolean;
  busy: 'rebuild' | 'uninstall' | null;
  onOpen: () => void;
  onUpdate: () => void;
  onRebuild: () => void;
  onInfo: () => void;
  onUninstall: () => void;
  onClose: () => void;
}

/** What a long press on an app tile offers (M19-04). History and publish live on App info. */
export function AppActionSheet({
  instance,
  spaceName,
  canRebuild,
  canChange,
  busy,
  onOpen,
  onUpdate,
  onRebuild,
  onInfo,
  onUninstall,
  onClose,
}: AppActionSheetProps) {
  const { app, update } = instance;
  return (
    <Modal visible transparent animationType="fade" onRequestClose={onClose}>
      <Pressable style={styles.backdrop} onPress={onClose} testID="app-sheet-backdrop" accessibilityLabel="Close" />
      <View style={styles.sheet} testID="app-sheet">
        <View style={styles.header}>
          <AppIcon icon={app.icon} slug={app.slug} name={app.name} />
          <View style={styles.headerText}>
            <Text style={styles.title} numberOfLines={1}>
              {app.name}
            </Text>
            <Text style={styles.subtitle} numberOfLines={1}>
              {[app.version ? `Version ${app.version}` : null, spaceName].filter(Boolean).join(' · ')}
            </Text>
          </View>
        </View>
        <SheetItem icon="open-outline" label="Open" onPress={onOpen} testID="app-sheet-open" />
        {update && canChange ? (
          <SheetItem
            icon="arrow-up-circle-outline"
            label={`Update to ${update.version}`}
            onPress={onUpdate}
            testID={`apps-update-${app.slug}`}
          />
        ) : null}
        {canRebuild && canChange ? (
          <SheetItem
            icon="hammer-outline"
            label="Rebuild"
            onPress={onRebuild}
            busy={busy === 'rebuild'}
            testID="app-sheet-rebuild"
          />
        ) : null}
        <SheetItem
          icon="information-circle-outline"
          label="History and publishing"
          onPress={onInfo}
          testID="app-sheet-info"
        />
        {canChange ? (
          <SheetItem
            icon="trash-outline"
            label="Uninstall"
            danger
            onPress={onUninstall}
            busy={busy === 'uninstall'}
            testID="app-sheet-uninstall"
          />
        ) : null}
      </View>
    </Modal>
  );
}

function SheetItem({
  icon,
  label,
  onPress,
  busy,
  danger,
  testID,
}: {
  icon: IconName;
  label: string;
  onPress: () => void;
  busy?: boolean;
  danger?: boolean;
  testID: string;
}) {
  const color = danger ? theme.danger : theme.text;
  return (
    <Pressable style={styles.item} onPress={onPress} disabled={busy} accessibilityRole="button" testID={testID}>
      {busy ? <ActivityIndicator size="small" color={color} /> : <Ionicons name={icon} size={20} color={color} />}
      <Text style={[styles.itemText, { color }]}>{label}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  backdrop: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.5)',
  },
  sheet: {
    position: 'absolute',
    left: 0,
    right: 0,
    bottom: 0,
    marginHorizontal: 'auto',
    maxWidth: 520,
    paddingTop: 16,
    paddingBottom: 24,
    borderTopLeftRadius: 16,
    borderTopRightRadius: 16,
    backgroundColor: theme.surface,
    borderWidth: 1,
    borderColor: theme.border,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    paddingHorizontal: 20,
    paddingBottom: 12,
  },
  headerText: {
    flex: 1,
    gap: 2,
  },
  title: {
    color: theme.text,
    fontSize: 17,
    fontWeight: '600',
  },
  subtitle: {
    color: theme.textMuted,
    fontSize: 13,
  },
  item: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 14,
    paddingHorizontal: 20,
    paddingVertical: 14,
  },
  itemText: {
    fontSize: 15,
  },
});
