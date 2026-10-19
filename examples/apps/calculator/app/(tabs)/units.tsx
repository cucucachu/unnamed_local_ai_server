import { useState } from 'react';
import { ScrollView, Text, View } from 'react-native';
import { Card, Chip, IconButton, List, ListRow, NumberField, Screen, Section, Select, useTheme } from '@homeai/ui';
import { formatNumber } from '../../lib/calc';
import { GROUPS, convert } from '../../lib/units';

export default function UnitsScreen() {
  const t = useTheme();
  const [groupKey, setGroupKey] = useState(GROUPS[0].key);
  const group = GROUPS.find((g) => g.key === groupKey)!;
  const [value, setValue] = useState<number | null>(1);
  const [fromKey, setFromKey] = useState(group.units[0].key);
  const [toKey, setToKey] = useState(group.units[1].key);
  const from = group.units.find((u) => u.key === fromKey) ?? group.units[0];
  const to = group.units.find((u) => u.key === toKey) ?? group.units[1];

  function pick(key: string) {
    const g = GROUPS.find((x) => x.key === key)!;
    setGroupKey(key);
    setFromKey(g.units[0].key);
    setToKey(g.units[1].key);
  }

  const options = group.units.map((u) => ({ label: u.label, value: u.key }));
  return (
    <Screen>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: t.spacing.sm }}>
        {GROUPS.map((g) => (
          <Chip key={g.key} label={g.label} selected={g.key === groupKey} onPress={() => pick(g.key)} testID={`units-group-${g.key}`} />
        ))}
      </ScrollView>
      <Card>
        <View style={{ gap: t.spacing.sm }}>
          <NumberField label="Amount" value={value} onChange={setValue} decimals={6} testID="units-value" />
          <Select label="From" value={from.key} options={options} onChange={(k) => k && setFromKey(k)} testID="units-from" />
          <View style={{ alignItems: 'center' }}>
            <IconButton
              icon="swap-vertical-outline"
              accessibilityLabel="Swap"
              onPress={() => {
                setFromKey(to.key);
                setToKey(from.key);
              }}
              testID="units-swap"
            />
          </View>
          <Select label="To" value={to.key} options={options} onChange={(k) => k && setToKey(k)} testID="units-to" />
          <Text style={{ fontSize: t.font.large, color: t.colors.text }} testID="units-result">
            {value === null ? '–' : formatNumber(convert(value, from, to))}
          </Text>
        </View>
      </Card>
      {value !== null ? (
        <Section title={`${formatNumber(value)} ${from.label} is`}>
          <List>
            {group.units
              .filter((u) => u.key !== from.key)
              .map((u) => (
                <ListRow key={u.key} title={formatNumber(convert(value, from, u))} subtitle={u.label} testID={`units-all-${u.key}`} />
              ))}
          </List>
        </Section>
      ) : null}
    </Screen>
  );
}
