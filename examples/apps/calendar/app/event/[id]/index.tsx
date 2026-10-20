import { useState } from 'react';
import { Text, View } from 'react-native';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { runAction, useDatabase, useMembers, useSpace } from '@homeai/sdk';
import { Button, Card, EmptyState, Screen, Section, useTheme } from '@homeai/ui';
import { addDays, dateLabel, dayNumber, repeatLabel, timeLabel, type CalendarEvent } from '../../../lib/calendar';
import { useEvent } from '../../../lib/useEvent';

/** "Mon, Oct 19, 2026, 9:00 AM – 10:00 AM", "Sat, Oct 24 – Sun, Oct 25, 2026", "All day". */
function whenLabel(e: CalendarEvent, start: string): string {
  const end = addDays(start, dayNumber(e.end_date) - dayNumber(e.start_date));
  if (e.all_day) return start === end ? `${dateLabel(start)}, all day` : `${dateLabel(start)} – ${dateLabel(end)}`;
  if (start === end) return `${dateLabel(start)}, ${timeLabel(e.start_time!)} – ${timeLabel(e.end_time!)}`;
  return `${dateLabel(start)}, ${timeLabel(e.start_time!)} – ${dateLabel(end)}, ${timeLabel(e.end_time!)}`;
}

export default function EventScreen() {
  const t = useTheme();
  const db = useDatabase();
  const router = useRouter();
  const members = useMembers();
  const canEdit = useSpace()?.role !== 'viewer';
  const { id, date } = useLocalSearchParams<{ id: string; date?: string }>();
  const event = useEvent(Number(id));
  const [confirming, setConfirming] = useState(false);

  if (event === undefined) return null;
  if (event === null) return <EmptyState icon="calendar-outline" title="This event was deleted" testID="event-gone" />;

  const occurrence = date || event.start_date;
  const repeats = event.repeat !== 'none';
  const who = (event.people ?? '')
    .split(',')
    .filter(Boolean)
    .map((u) => members.find((m) => m.id === u)?.name ?? 'Someone');

  const edit = (scope: 'one' | 'all') => router.push({ pathname: '/event/[id]/edit', params: { id, date: occurrence, scope } });

  async function deleteOne() {
    await db.runAsync('INSERT OR IGNORE INTO event_skips (event_id, date) VALUES (?, ?)', [event!.id, occurrence]);
    router.back();
  }
  async function deleteAll() {
    await runAction('deleteEvent', { id: event!.id });
    router.back();
  }

  const muted = { color: t.colors.textMuted };
  return (
    <Screen>
      <Card>
        <Text style={{ fontSize: t.font.large, fontWeight: '700', color: t.colors.text }} selectable testID="detail-title">
          {event.title}
        </Text>
        <Text style={{ color: t.colors.text, marginTop: t.spacing.xs }} testID="detail-when">
          {whenLabel(event, occurrence)}
        </Text>
        {repeats ? (
          <Text style={muted} testID="detail-repeat">
            {repeatLabel(event)}
          </Text>
        ) : null}
      </Card>
      {event.location ? (
        <Section title="Location">
          <Text style={{ color: t.colors.text }} selectable testID="detail-location">
            {event.location}
          </Text>
        </Section>
      ) : null}
      {who.length ? (
        <Section title="Who">
          <Text style={{ color: t.colors.text }} testID="detail-who">
            {who.join(', ')}
          </Text>
        </Section>
      ) : null}
      {event.notes ? (
        <Section title="Notes">
          <Text style={{ color: t.colors.text }} selectable testID="detail-notes">
            {event.notes}
          </Text>
        </Section>
      ) : null}
      {canEdit ? (
        <View style={{ gap: t.spacing.sm, marginTop: t.spacing.md }}>
          {repeats ? (
            <>
              <Button title="Edit this event" variant="secondary" icon="create-outline" onPress={() => edit('one')} testID="edit-one" />
              <Button title="Edit all events" variant="secondary" icon="repeat-outline" onPress={() => edit('all')} testID="edit-all" />
            </>
          ) : (
            <Button title="Edit" variant="secondary" icon="create-outline" onPress={() => edit('all')} testID="edit-all" />
          )}
          {!confirming ? (
            <Button title="Delete" variant="destructive" icon="trash-outline" onPress={() => setConfirming(true)} testID="delete" />
          ) : repeats ? (
            <>
              <Button title="Delete this event" variant="destructive" onPress={() => void deleteOne()} testID="delete-one" />
              <Button title="Delete all events" variant="destructive" onPress={() => void deleteAll()} testID="delete-all" />
              <Button title="Cancel" variant="plain" onPress={() => setConfirming(false)} testID="delete-cancel" />
            </>
          ) : (
            <>
              <Button title="Delete event" variant="destructive" onPress={() => void deleteAll()} testID="delete-all" />
              <Button title="Cancel" variant="plain" onPress={() => setConfirming(false)} testID="delete-cancel" />
            </>
          )}
        </View>
      ) : null}
    </Screen>
  );
}
