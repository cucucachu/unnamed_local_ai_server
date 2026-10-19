import { useEffect, useMemo, useState } from 'react';
import { Text, View } from 'react-native';
import { useRouter } from 'expo-router';
import { search, useQuery } from '@homeai/sdk';
import { Chip, EmptyState, List, ListRow, Screen, SearchField, Section, useTheme } from '@homeai/ui';
import { ITEM_COLUMNS, SOON_DAYS, amount, daysLeft, expiryLabel, inDays, type Item } from '../lib/pantry';

export default function PantryScreen() {
  const t = useTheme();
  const router = useRouter();
  const [place, setPlace] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [found, setFound] = useState<number[] | null>(null);
  const { data: items, error } = useQuery<Item>(`SELECT ${ITEM_COLUMNS} FROM items ORDER BY location, name COLLATE NOCASE`);
  const { data: soon } = useQuery<Item>(
    `SELECT ${ITEM_COLUMNS} FROM items WHERE expires_on IS NOT NULL AND expires_on <= ? ORDER BY expires_on, name`,
    [inDays(SOON_DAYS)],
  );

  // search() finds rows by meaning and by words (homeai.index in app.json); keep the hits' order.
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

  const places = useMemo(() => [...new Set((items ?? []).map((i) => i.location))], [items]);
  const shown = useMemo(() => {
    const all = items ?? [];
    if (found) return found.map((id) => all.find((i) => i.id === id)).filter((i): i is Item => !!i);
    return place ? all.filter((i) => i.location === place) : all;
  }, [items, found, place]);

  const row = (i: Item) => {
    const days = daysLeft(i);
    return (
      <ListRow
        key={i.id}
        title={i.name}
        subtitle={[found || !place ? i.location : null, expiryLabel(i)].filter(Boolean).join(' · ') || undefined}
        value={amount(i)}
        icon={days !== null && days <= SOON_DAYS ? 'alert-circle-outline' : undefined}
        iconColor={days !== null && days < 0 ? t.colors.danger : t.colors.warning}
        onPress={() => router.push({ pathname: '/item/[id]', params: { id: String(i.id) } })}
        testID={`item-${i.id}`}
      />
    );
  };

  return (
    <Screen>
      <SearchField value={query} onChangeText={setQuery} placeholder="Search the pantry" testID="search" />
      {error ? <Text style={{ color: t.colors.danger }}>{error.message}</Text> : null}
      {!query && soon?.length ? (
        <Section title="Use soon" footer={`Expired or expiring in the next ${SOON_DAYS} days.`}>
          <List testID="soon">{soon.map(row)}</List>
        </Section>
      ) : null}
      {!query && places.length > 1 ? (
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: t.spacing.sm }}>
          <Chip label="Everywhere" selected={place === null} onPress={() => setPlace(null)} testID="place-all" />
          {places.map((p) => (
            <Chip key={p} label={p} selected={place === p} onPress={() => setPlace(p)} testID={`place-${p}`} />
          ))}
        </View>
      ) : null}
      <List
        data={shown}
        keyExtractor={(i) => i.id}
        renderItem={(i) => row(i)}
        empty={
          query ? (
            <EmptyState icon="search-outline" title="Nothing found" message={`Nothing matches "${query}".`} testID="search-empty" />
          ) : (
            <EmptyState icon="basket-outline" title="The pantry is empty" message="Add what you have, with where it is and when it expires." testID="pantry-empty" />
          )
        }
        testID="items"
      />
    </Screen>
  );
}
