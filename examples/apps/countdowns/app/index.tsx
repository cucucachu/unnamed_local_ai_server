import { Text } from 'react-native';
import { useRouter } from 'expo-router';
import { runAction, useDatabase, useQuery, useSpace } from '@homeai/sdk';
import { Button, Card, EmptyState, List, ListRow, Screen, Section, useTheme } from '@homeai/ui';
import { COUNTDOWN_COLUMNS, daysLabel, daysUntil, nextYearly, ordinal, today, type Countdown } from '../lib/countdowns';

type Person = { _instance: string; id: number; name: string; birthday: string };
type Item = { key: string; title: string; days: number; detail: string | null; countdown?: number; person?: Person };

// Contacts' people (homeai.reads in app.json). contacts_people only exists
// while a Contacts copy is attached, so ask homeai_exports first.
const NO_PEOPLE = 'SELECT NULL AS _instance, NULL AS id, NULL AS name, NULL AS birthday WHERE 0';
const PEOPLE = `SELECT p._instance, p.id, p.name, p.birthday FROM contacts_people p
  WHERE p.birthday IS NOT NULL
    AND NOT EXISTS (SELECT 1 FROM hidden_birthdays h WHERE h.instance = p._instance AND h.person_id = p.id)`;

export default function CountdownsScreen() {
  const t = useTheme();
  const db = useDatabase();
  const router = useRouter();
  const canEdit = useSpace()?.role !== 'viewer';
  const now = today();
  const { data: own } = useQuery<Countdown>(`SELECT ${COUNTDOWN_COLUMNS} FROM countdowns`);
  const { data: copies } = useQuery<{ n: number }>("SELECT count(*) AS n FROM homeai_exports WHERE app = 'contacts' AND export = 'people'");
  const hasContacts = (copies?.[0]?.n ?? 0) > 0;
  const { data: people } = useQuery<Person>(hasContacts ? PEOPLE : NO_PEOPLE);
  const { data: hidden } = useQuery<{ n: number }>('SELECT count(*) AS n FROM hidden_birthdays');

  const items: Item[] = [
    ...(own ?? []).map((c) => {
      if (!c.yearly) return { key: `c${c.id}`, title: c.title, days: daysUntil(c.day, now), detail: null, countdown: c.id };
      const next = nextYearly(c.day, now);
      return { key: `c${c.id}`, title: c.title, days: next.days, detail: next.years > 0 ? `${ordinal(next.years)} anniversary` : null, countdown: c.id };
    }),
    ...(people ?? []).map((p) => {
      const next = nextYearly(p.birthday, now);
      const year = Number(p.birthday.slice(0, 4));
      return { key: `p${p._instance}${p.id}`, title: `${p.name}'s birthday`, days: next.days, detail: year > 1900 ? `turns ${next.years}` : null, person: p };
    }),
  ];
  const upcoming = items.filter((i) => i.days >= 0).sort((a, b) => a.days - b.days || a.title.localeCompare(b.title));
  const past = items.filter((i) => i.days < 0).sort((a, b) => b.days - a.days);
  const next = upcoming[0];
  const hiddenCount = hidden?.[0]?.n ?? 0;

  const row = (i: Item) => (
    <ListRow
      key={i.key}
      title={i.title}
      subtitle={[daysLabel(i.days), i.detail].filter(Boolean).join(' · ')}
      icon={i.person ? 'gift-outline' : 'calendar-outline'}
      onPress={i.countdown !== undefined ? () => router.push({ pathname: '/countdown/[id]', params: { id: String(i.countdown) } }) : undefined}
      actions={
        i.person && canEdit
          ? [{ label: 'Hide', icon: 'eye-off-outline', onPress: () => void runAction('hideBirthday', { instance: i.person!._instance, person_id: i.person!.id }) }]
          : undefined
      }
      testID={i.person ? `birthday-${i.person._instance}-${i.person.id}` : `countdown-${i.countdown}`}
    />
  );

  return (
    <Screen>
      {next ? (
        <Card>
          <Text style={{ color: t.colors.textMuted }}>Next up</Text>
          <Text style={{ color: t.colors.text, fontSize: 48, fontWeight: '700' }} testID="next-days">
            {next.days === 0 ? 'Today' : next.days}
          </Text>
          <Text style={{ color: t.colors.text, fontSize: t.font.title }} testID="next-title">
            {next.days === 0 ? next.title : `day${next.days === 1 ? '' : 's'} until ${next.title}`}
          </Text>
        </Card>
      ) : null}
      {upcoming.length ? (
        <Section title="Coming up" footer={hasContacts && canEdit ? 'Long-press a birthday to hide it.' : undefined}>
          <List testID="upcoming">{upcoming.map(row)}</List>
        </Section>
      ) : (
        <EmptyState icon="hourglass-outline" title="Nothing to count down to" message="Add a trip, a deadline or an anniversary with +." testID="countdowns-empty" />
      )}
      {past.length ? (
        <Section title="Past">
          <List testID="past">{past.map(row)}</List>
        </Section>
      ) : null}
      {!hasContacts ? (
        <Text style={{ color: t.colors.textMuted }} testID="no-contacts">
          Add the Contacts app to see birthdays here.
        </Text>
      ) : hiddenCount && canEdit ? (
        <Button
          title={`Show ${hiddenCount} hidden birthday${hiddenCount === 1 ? '' : 's'}`}
          variant="plain"
          icon="eye-outline"
          onPress={() => void db.runAsync('DELETE FROM hidden_birthdays')}
          testID="show-hidden"
        />
      ) : null}
    </Screen>
  );
}
