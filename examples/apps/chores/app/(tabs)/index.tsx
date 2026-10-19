import { useState } from 'react';
import { Text } from 'react-native';
import { useRouter } from 'expo-router';
import { runAction, useMembers, useQuery, useSpace, useUser } from '@homeai/sdk';
import { Button, EmptyState, List, ListRow, Screen, Section, useTheme } from '@homeai/ui';
import { CHORES_SQL, dueLabel, today, type Chore } from '../../lib/chores';

export default function DueScreen() {
  const t = useTheme();
  const router = useRouter();
  const me = useUser();
  const members = useMembers();
  const canEdit = useSpace()?.role !== 'viewer';
  const now = today();
  const { data, loading, error } = useQuery<Chore>(`${CHORES_SQL} WHERE c.next_due <= ? ORDER BY c.next_due, c.name`, [now]);
  const [message, setMessage] = useState('');

  const nameOf = (id: string | null) => (id ? (id === me?.id ? 'Your' : `${members.find((m) => m.id === id)?.name ?? 'Someone'}'s`) : null);
  const mine = (data ?? []).filter((c) => c.turn_user === me?.id);
  const others = (data ?? []).filter((c) => c.turn_user !== me?.id);

  async function done(chore: Chore) {
    try {
      await runAction('completeChore', { chore_id: chore.id, today: now });
      setMessage(`Done: ${chore.name}`);
    } catch (e) {
      setMessage((e as Error).message);
    }
  }

  const row = (c: Chore) => (
    <ListRow
      key={c.id}
      title={c.name}
      subtitle={[dueLabel(c.next_due, now), nameOf(c.turn_user) && `${nameOf(c.turn_user)} turn`].filter(Boolean).join(' · ')}
      onPress={() => router.push({ pathname: '/chore/[id]', params: { id: String(c.id) } })}
      chevron={false}
      right={canEdit ? <Button title="Done" variant="secondary" onPress={() => done(c)} testID={`done-${c.id}`} /> : undefined}
      testID={`due-${c.id}`}
    />
  );

  if (!loading && !error && !data?.length) {
    return (
      <Screen>
        <EmptyState icon="checkmark-done-outline" title="Nothing due" message="Chores show up here on the day they're due." testID="due-empty" />
        {message ? <Text style={{ color: t.colors.textMuted }} testID="due-message">{message}</Text> : null}
      </Screen>
    );
  }
  return (
    <Screen>
      {error ? <Text style={{ color: t.colors.danger }}>{error.message}</Text> : null}
      {message ? <Text style={{ color: t.colors.textMuted }} testID="due-message">{message}</Text> : null}
      {mine.length ? (
        <Section title="Your turn">
          <List>{mine.map(row)}</List>
        </Section>
      ) : null}
      {others.length ? (
        <Section title={mine.length ? 'Everyone else' : 'Due'}>
          <List>{others.map(row)}</List>
        </Section>
      ) : null}
    </Screen>
  );
}
