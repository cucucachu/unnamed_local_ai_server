import { useState } from 'react';
import { ScrollView, View } from 'react-native';
import { Card, Chip, List, ListRow, NumberField, Screen, Stepper, Toggle, formatMoney, useTheme } from '@homeai/ui';

const PERCENTS = [10, 15, 18, 20, 25];

/** The bill, a tip and how many are splitting it. Each person's share can round up to a whole amount. */
export default function TipScreen() {
  const t = useTheme();
  const [bill, setBill] = useState<number | null>(null);
  const [percent, setPercent] = useState(18);
  const [people, setPeople] = useState(1);
  const [roundUp, setRoundUp] = useState(false);

  const amount = bill ?? 0;
  const tip = (amount * percent) / 100;
  const exact = (amount + tip) / people;
  const each = roundUp ? Math.ceil(exact) : Math.round(exact * 100) / 100;
  const total = each * people;

  return (
    <Screen>
      <Card>
        <View style={{ gap: t.spacing.sm }}>
          <NumberField label="Bill" value={bill} onChange={setBill} min={0} decimals={2} placeholder="0.00" testID="tip-bill" />
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: t.spacing.sm }}>
            {PERCENTS.map((p) => (
              <Chip key={p} label={`${p}%`} selected={p === percent} onPress={() => setPercent(p)} testID={`tip-${p}`} />
            ))}
          </ScrollView>
          <Stepper label="Tip %" value={percent} onChange={setPercent} min={0} max={100} testID="tip-percent" />
          <Stepper label="People" value={people} onChange={setPeople} min={1} max={50} testID="tip-people" />
          <Toggle label="Round each share up" description="To a whole amount; the extra goes to the tip" value={roundUp} onValueChange={setRoundUp} testID="tip-round" />
        </View>
      </Card>
      <List>
        <ListRow title="Tip" value={formatMoney(total - amount)} testID="tip-amount" />
        <ListRow title="Total" value={formatMoney(total)} testID="tip-total" />
        {people > 1 ? <ListRow title="Each" value={formatMoney(each)} testID="tip-each" /> : null}
      </List>
    </Screen>
  );
}
