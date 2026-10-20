import { useEffect, useState } from 'react';
import { Text } from 'react-native';
import { search, useMembers, useQuery, useUser } from '@homeai/sdk';
import { EmptyState, List, ListRow, Screen, SearchField, useTheme } from '@homeai/ui';
import { MESSAGE_COLUMNS, dayLabel, timeLabel, type Message } from '../lib/chat';

export default function SearchScreen() {
  const t = useTheme();
  const me = useUser();
  const members = useMembers();
  const [query, setQuery] = useState('');
  const [found, setFound] = useState<number[] | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const { data } = useQuery<Message>(`SELECT ${MESSAGE_COLUMNS} FROM messages WHERE id IN (SELECT value FROM json_each(?)) AND deleted = 0`, [
    JSON.stringify(found ?? []),
  ]);

  // By meaning and by words, over message text (homeai.index).
  useEffect(() => {
    if (!query.trim()) return setFound(null);
    let live = true;
    const timer = setTimeout(() => {
      search(query, 30).then(
        (hits) => {
          if (!live) return;
          setProblem(null);
          setFound(hits.filter((h) => h.table === 'messages').map((h) => h.id));
        },
        (e) => live && setProblem((e as Error).message),
      );
    }, 300);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [query]);

  const byId = new Map((data ?? []).map((m) => [m.id, m]));
  const results = (found ?? []).map((id) => byId.get(id)).filter((m): m is Message => !!m);
  const name = (id: string) => (id === me?.id ? 'You' : (members.find((m) => m.id === id)?.name ?? 'Someone'));

  return (
    <Screen>
      <SearchField value={query} onChangeText={setQuery} placeholder="Search messages" testID="search" />
      {problem ? (
        <Text role="alert" style={{ color: t.colors.danger }}>
          {problem}
        </Text>
      ) : null}
      {found ? (
        <List
          data={results}
          keyExtractor={(m) => m.id}
          empty={<EmptyState icon="search-outline" title="Nothing found" testID="search-empty" />}
          renderItem={(m) => (
            <ListRow title={m.body} subtitle={`${name(m._created_by)} · ${dayLabel(m._created_at)} ${timeLabel(m._created_at)}`} testID={`result-${m.id}`} />
          )}
          testID="results"
        />
      ) : null}
    </Screen>
  );
}
