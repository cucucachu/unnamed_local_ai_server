import { useEffect, useMemo, useState } from 'react';
import { ScrollView, View } from 'react-native';
import { useRouter } from 'expo-router';
import { runAction, search, useQuery, useSpace } from '@homeai/sdk';
import { Chip, EmptyState, List, ListRow, Screen, SearchField, Section, useTheme, type RowAction } from '@homeai/ui';
import { ITEM_COLUMNS, KINDS, STATUSES, kindOf, stars, statusLabel, today, type Item, type Kind, type Status } from '../../lib/books';

const ORDER: Status[] = ['started', 'want', 'finished', 'dropped'];
const SECTION: Record<Status, string> = { started: 'On it now', want: 'Want to', finished: 'Finished', dropped: 'Gave up' };

export default function ShelfScreen() {
  const t = useTheme();
  const router = useRouter();
  const canEdit = useSpace()?.role !== 'viewer';
  const { data: items } = useQuery<Item>(`SELECT ${ITEM_COLUMNS} FROM items ORDER BY coalesce(finished_on, started_on) DESC, title COLLATE NOCASE`);
  const [status, setStatus] = useState<Status | null>(null);
  const [kind, setKind] = useState<Kind | null>(null);
  const [query, setQuery] = useState('');
  const [found, setFound] = useState<number[] | null>(null);

  // search() finds items by meaning and by words (homeai.index in app.json); keep the hits' order.
  useEffect(() => {
    if (!query.trim()) return setFound(null);
    let live = true;
    const timer = setTimeout(() => {
      search(query, 50).then(
        (hits) => live && setFound(hits.filter((h) => h.table === 'items').map((h) => h.id)),
        () => live && setFound([]),
      );
    }, 300);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [query]);

  const all = items ?? [];
  const kinds = useMemo(() => KINDS.filter((k) => all.some((i) => i.kind === k.value)), [all]);
  const shown = all.filter((i) => (!status || i.status === status) && (!kind || i.kind === kind));

  // Swipe (or long-press) to move it along: start what you want to, finish what you're on.
  const actionsFor = (i: Item) => {
    const set = (s: Status) => () => void runAction('setStatus', { id: i.id, status: s, today: today() });
    const actions: RowAction[] = [];
    if (i.status === 'want') actions.push({ label: 'Start', icon: 'play-outline', color: t.colors.tint, onPress: set('started') });
    if (i.status === 'started') {
      actions.push({ label: 'Finished', icon: 'checkmark-outline', color: t.colors.success, onPress: set('finished') });
      actions.push({ label: 'Gave up', icon: 'close-outline', onPress: set('dropped') });
    }
    return actions.length ? actions : undefined;
  };

  const row = (i: Item) => (
    <ListRow
      key={i.id}
      title={i.title}
      subtitle={[i.creator, statusLabel(i)].filter(Boolean).join(' · ')}
      icon={kindOf(i.kind).icon}
      value={stars(i.rating) || undefined}
      onPress={() => router.push({ pathname: '/item/[id]', params: { id: String(i.id) } })}
      actions={canEdit ? actionsFor(i) : undefined}
      testID={`item-${i.id}`}
    />
  );

  if (!all.length) {
    return (
      <Screen>
        <EmptyState
          icon="library-outline"
          title="Nothing on the shelf yet"
          message="Add the books, films, shows and games you're on or want to get to."
          action={canEdit ? { title: 'Add one', onPress: () => router.push('/new') } : undefined}
          testID="shelf-empty"
        />
      </Screen>
    );
  }

  return (
    <Screen>
      <SearchField value={query} onChangeText={setQuery} placeholder="Search titles, people, notes" testID="items-search" />
      {found ? (
        <List
          data={found.map((id) => all.find((i) => i.id === id)).filter((i): i is Item => !!i)}
          keyExtractor={(i) => i.id}
          renderItem={row}
          empty={<EmptyState icon="search-outline" title="Nothing matches" testID="search-empty" />}
          testID="search-results"
        />
      ) : (
        <>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: t.spacing.sm }}>
            <Chip label="All" selected={!status} onPress={() => setStatus(null)} testID="status-all" />
            {STATUSES.map((s) => (
              <Chip key={s.value} label={s.label} selected={status === s.value} onPress={() => setStatus(status === s.value ? null : s.value)} testID={`status-${s.value}`} />
            ))}
          </ScrollView>
          {kinds.length > 1 ? (
            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: t.spacing.sm }}>
              {kinds.map((k) => (
                <Chip key={k.value} label={k.label} icon={k.icon} selected={kind === k.value} onPress={() => setKind(kind === k.value ? null : k.value)} testID={`kind-${k.value}`} />
              ))}
            </ScrollView>
          ) : null}
          {shown.length ? (
            ORDER.filter((s) => shown.some((i) => i.status === s)).map((s) => (
              <Section key={s} title={SECTION[s]} footer={s === 'started' && canEdit ? 'Swipe one when you finish it.' : undefined}>
                <View testID={`section-${s}`}>
                  <List>{shown.filter((i) => i.status === s).map(row)}</List>
                </View>
              </Section>
            ))
          ) : (
            <EmptyState icon="filter-outline" title="Nothing here" testID="filter-empty" />
          )}
        </>
      )}
    </Screen>
  );
}
