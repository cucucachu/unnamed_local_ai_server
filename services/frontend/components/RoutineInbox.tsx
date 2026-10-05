import Ionicons from '@expo/vector-icons/Ionicons';
import { useRouter } from 'expo-router';
import { forwardRef, useCallback, useEffect, useImperativeHandle, useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { ActionButton, Badge, Card, SectionTitle, settingsStyles } from '@/components/SettingsUI';
import { getInbox, markInboxRead, runStatusLabel, type Inbox, type InboxItem } from '@/lib/inbox';
import { relativeTime } from '@/lib/relativeTime';
import { theme } from '@/lib/theme';

export const INBOX_SHOWN = 5;

export interface RoutineInboxHandle {
  reload: () => void;
}

function tone(item: InboxItem): 'accent' | 'danger' | 'muted' {
  if (item.status === 'waiting_approval') return 'accent';
  if (item.status === 'succeeded') return 'muted';
  return 'danger';
}

/** What Home shows: runs waiting on the user, then unread ones, newest first. */
export function inboxShown(inbox: Inbox): InboxItem[] {
  const waiting = inbox.items.filter((item) => item.status === 'waiting_approval');
  const unread = inbox.items.filter((item) => item.unread && item.status !== 'waiting_approval');
  return [...waiting, ...unread].slice(0, INBOX_SHOWN);
}

/**
 * Home's routine inbox (M17-05): runs that need approval, finished or
 * failed. Tapping one marks it read and opens its chat, where a pending
 * approval shows as the usual approval card. Renders nothing when there's
 * nothing to show (or the inbox can't be loaded).
 */
export const RoutineInbox = forwardRef<RoutineInboxHandle>(function RoutineInbox(_props, ref) {
  const router = useRouter();
  const [inbox, setInbox] = useState<Inbox | null>(null);

  const reload = useCallback(() => {
    getInbox().then(setInbox, () => undefined);
  }, []);
  useEffect(reload, [reload]);
  useImperativeHandle(ref, () => ({ reload }), [reload]);

  const open = useCallback(
    (item: InboxItem) => {
      if (item.unread) markInboxRead([item.id]).then(setInbox, () => undefined);
      if (item.thread_id) router.push({ pathname: '/chat/[threadId]', params: { threadId: item.thread_id } });
    },
    [router],
  );

  const shown = inbox ? inboxShown(inbox) : [];
  if (inbox === null || shown.length === 0) return null;

  return (
    <View style={styles.group} testID="home-inbox">
      <View style={styles.header}>
        <SectionTitle>Routines{inbox.unread ? ` · ${inbox.unread} new` : ''}</SectionTitle>
        {inbox.unread ? (
          <ActionButton
            label="Mark all read"
            compact
            onPress={() => markInboxRead().then(setInbox, () => undefined)}
            testID="home-inbox-read-all"
          />
        ) : null}
      </View>
      <Card>
        {shown.map((item, index) => (
          <Pressable
            key={item.id}
            onPress={() => open(item)}
            style={[settingsStyles.row, index === 0 && settingsStyles.firstRow]}
            accessibilityRole="button"
            accessibilityLabel={`${item.routine_name}: ${runStatusLabel(item.status)}`}
            testID={`home-inbox-${item.id}`}
          >
            <Ionicons
              name={item.unread ? 'ellipse' : 'ellipse-outline'}
              size={10}
              color={item.unread ? theme.accent : theme.textMuted}
            />
            <View style={settingsStyles.rowMain}>
              <Text style={settingsStyles.rowTitle} numberOfLines={1}>
                {item.routine_name}
              </Text>
              {item.detail ? (
                <Text style={settingsStyles.muted} numberOfLines={1}>
                  {item.detail}
                </Text>
              ) : null}
            </View>
            <View style={styles.trailing}>
              <Badge label={runStatusLabel(item.status)} tone={tone(item)} testID={`home-inbox-status-${item.id}`} />
              {item.finished_at ? <Text style={styles.time}>{relativeTime(item.finished_at)}</Text> : null}
            </View>
          </Pressable>
        ))}
      </Card>
    </View>
  );
});

const styles = StyleSheet.create({
  group: {
    gap: 8,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  trailing: {
    alignItems: 'flex-end',
    gap: 4,
  },
  time: {
    color: theme.textMuted,
    fontSize: 12,
  },
});
