import { useState } from 'react';
import { Text, View } from 'react-native';
import { useRouter } from 'expo-router';
import { useQuery, useSpace } from '@homeai/sdk';
import { Button, List, ListRow, Screen, Section, useTheme } from '@homeai/ui';
import { SLOTS, addDays, dayLabel, localDate, weekStart } from '../../lib/recipes';

type Meal = { id: number; day: string; slot: string; note: string; recipe_id: number | null; name: string | null };

export default function PlanScreen() {
  const t = useTheme();
  const router = useRouter();
  const canEdit = useSpace()?.role !== 'viewer';
  const today = localDate(new Date());
  const [monday, setMonday] = useState(() => weekStart(today));
  const days = Array.from({ length: 7 }, (_, i) => addDays(monday, i));
  const { data } = useQuery<Meal>(
    `SELECT m.id, m.day, m.slot, m.note, m.recipe_id, r.name
     FROM meals m LEFT JOIN recipes r ON r.id = m.recipe_id
     WHERE m.day BETWEEN ? AND ?`,
    [days[0], days[6]],
  );
  const meals = (day: string) =>
    (data ?? []).filter((m) => m.day === day).sort((a, b) => SLOTS.indexOf(a.slot as never) - SLOTS.indexOf(b.slot as never));
  const label = (m: Meal) => [m.recipe_id === null ? null : (m.name ?? 'A deleted recipe'), m.note].filter(Boolean).join(' · ');

  return (
    <Screen>
      <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}>
        <Button title="Previous" variant="plain" icon="chevron-back-outline" onPress={() => setMonday(addDays(monday, -7))} testID="plan-prev" />
        <Text style={{ color: t.colors.text, fontWeight: '600' }} testID="plan-week">
          Week of {dayLabel(monday)}
        </Text>
        <Button title="Next" variant="plain" onPress={() => setMonday(addDays(monday, 7))} testID="plan-next" />
      </View>
      {days.map((day) => (
        <Section key={day} title={day === today ? `${dayLabel(day)} (today)` : dayLabel(day)}>
          <List>
            {meals(day).map((m) => (
              <ListRow
                key={m.id}
                title={label(m) || m.slot}
                subtitle={m.slot}
                onPress={m.recipe_id === null ? undefined : () => router.push({ pathname: '/recipe/[id]', params: { id: String(m.recipe_id) } })}
                testID={`meal-${day}-${m.slot}`}
              />
            ))}
            {canEdit ? (
              <ListRow
                title="Plan a meal"
                icon="add-outline"
                onPress={() => router.push({ pathname: '/plan/[day]', params: { day } })}
                testID={`plan-${day}`}
              />
            ) : null}
          </List>
        </Section>
      ))}
    </Screen>
  );
}
