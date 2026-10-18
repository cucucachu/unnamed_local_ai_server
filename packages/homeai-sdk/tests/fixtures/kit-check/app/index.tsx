import { useState } from 'react';
import { Link } from 'expo-router';
import {
  Badge,
  Button,
  Chip,
  DateField,
  Duration,
  EmptyState,
  List,
  ListRow,
  Money,
  NumberField,
  ProgressBar,
  Screen,
  SearchField,
  Section,
  Select,
  Stepper,
  TextField,
  TimeField,
  Toggle,
  useTheme,
} from '@homeai/ui';
import { Text } from 'react-native';

const SIZES = [
  { label: 'Small', value: 's' },
  { label: 'Large', value: 'l' },
] as const;

export default function Kit() {
  const t = useTheme();
  const [name, setName] = useState('');
  const [qty, setQty] = useState<number | null>(null);
  const [date, setDate] = useState<string | null>(null);
  const [time, setTime] = useState<string | null>(null);
  const [size, setSize] = useState<'s' | 'l' | null>(null);
  const [on, setOn] = useState(false);
  const [count, setCount] = useState(1);
  const [query, setQuery] = useState('');
  const [rows, setRows] = useState(['Milk', 'Eggs']);
  const [pressed, setPressed] = useState('');
  const [chip, setChip] = useState(false);
  const shown = rows.filter((r) => r.toLowerCase().includes(query.toLowerCase()));
  const state = { scheme: t.scheme, name, qty, date, time, size, on, count, query, rows, pressed, chip };
  return (
    <Screen testID="kit-screen">
      <Text testID="state">{JSON.stringify(state)}</Text>
      <Section title="Fields">
        <TextField label="Name" value={name} onChangeText={setName} placeholder="Your name" testID="name" error={name === 'bad' ? 'Not that one' : null} />
        <NumberField label="Quantity" value={qty} onChange={setQty} min={0} max={10} decimals={1} testID="qty" />
        <DateField label="Date" value={date} onChange={setDate} testID="date" />
        <TimeField label="Time" value={time} onChange={setTime} testID="time" />
        <Select label="Size" value={size} options={SIZES} onChange={setSize} testID="size" />
        <Toggle label="Reminders" value={on} onValueChange={setOn} testID="toggle" />
        <Stepper label="Count" value={count} onChange={setCount} min={0} max={3} testID="count" />
      </Section>
      <Section title="List">
        <SearchField value={query} onChangeText={setQuery} testID="search" />
        <List
          testID="rows"
          data={shown}
          keyExtractor={(r) => r}
          renderItem={(r) => (
            <ListRow
              title={r}
              testID={`row-${r}`}
              onPress={() => setPressed(r)}
              actions={[{ label: 'Delete', icon: 'trash-outline', onPress: () => setRows((all) => all.filter((x) => x !== r)) }]}
            />
          )}
          empty={<EmptyState testID="empty" icon="cart-outline" title="Nothing here" message="Add something" />}
        />
      </Section>
      <Section title="Display">
        <ProgressBar value={0.25} testID="progress" />
        <Badge value={3} testID="badge" />
        <Money amount={-12.5} colored testID="money" />
        <Duration seconds={750} testID="duration" />
        <Chip label="Fresh" selected={chip} onPress={() => setChip((c) => !c)} testID="chip" />
        <Button title="Save" icon="checkmark-outline" onPress={() => setPressed('save')} testID="save" />
        <Button title="Busy" onPress={() => setPressed('busy')} loading testID="busy" />
        <Link href="/plain" testID="to-plain">
          Plain
        </Link>
      </Section>
    </Screen>
  );
}
