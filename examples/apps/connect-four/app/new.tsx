import { useState } from 'react';
import { Text, View } from 'react-native';
import { useRouter } from 'expo-router';
import { useDatabase, useMembers, useUser } from '@homeai/sdk';
import { Button, Chip, EmptyState, Screen, Section, useTheme } from '@homeai/ui';

type First = 'me' | 'them' | 'random';

export default function NewGameScreen() {
  const t = useTheme();
  const db = useDatabase();
  const router = useRouter();
  const me = useUser();
  const opponents = useMembers().filter((m) => m.id !== me?.id && m.role !== 'viewer');
  const [opponent, setOpponent] = useState<string | null>(opponents.length === 1 ? opponents[0].id : null);
  const [first, setFirst] = useState<First>('me');
  const [saving, setSaving] = useState(false);

  if (!opponents.length) return <EmptyState icon="people-outline" title="No one to play" message="Connect Four needs another member of this space who can edit it." testID="no-opponents" />;

  async function start() {
    if (!opponent || !me) return;
    setSaving(true);
    const meFirst = first === 'me' || (first === 'random' && Math.random() < 0.5);
    const [red, yellow] = meFirst ? [me.id, opponent] : [opponent, me.id];
    const { lastInsertRowId } = await db.runAsync('INSERT INTO games (red, yellow) VALUES (?, ?)', [red, yellow]);
    router.replace({ pathname: '/game/[id]', params: { id: String(lastInsertRowId) } });
  }

  return (
    <Screen>
      <Section title="Play against">
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: t.spacing.xs }}>
          {opponents.map((m) => (
            <Chip key={m.id} label={m.name} selected={opponent === m.id} onPress={() => setOpponent(m.id)} testID={`opponent-${m.username}`} />
          ))}
        </View>
      </Section>
      <Section title="Who goes first" footer="First player has red.">
        <View style={{ flexDirection: 'row', gap: t.spacing.xs }}>
          <Chip label="Me" selected={first === 'me'} onPress={() => setFirst('me')} testID="first-me" />
          <Chip label="Them" selected={first === 'them'} onPress={() => setFirst('them')} testID="first-them" />
          <Chip label="Random" selected={first === 'random'} onPress={() => setFirst('random')} testID="first-random" />
        </View>
      </Section>
      {!opponent ? <Text style={{ color: t.colors.textMuted }}>Pick who to play.</Text> : null}
      <Button title="Start game" onPress={() => void start()} disabled={!opponent} loading={saving} testID="start" />
    </Screen>
  );
}
