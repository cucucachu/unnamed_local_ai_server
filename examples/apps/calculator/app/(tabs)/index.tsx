import { useState } from 'react';
import { Pressable, Text, View } from 'react-native';
import { List, ListRow, Screen, Section, useTheme } from '@homeai/ui';
import { evaluate, formatNumber } from '../../lib/calc';

type Key = { label: string; id: string; insert?: string; kind?: 'op' | 'action' | 'equals' };

const KEYS: Key[][] = [
  [
    { label: 'C', id: 'clear', kind: 'action' },
    { label: '(', id: 'open', insert: '(', kind: 'op' },
    { label: ')', id: 'close', insert: ')', kind: 'op' },
    { label: '⌫', id: 'back', kind: 'action' },
  ],
  [{ label: '7', id: '7' }, { label: '8', id: '8' }, { label: '9', id: '9' }, { label: '÷', id: 'divide', insert: '÷', kind: 'op' }],
  [{ label: '4', id: '4' }, { label: '5', id: '5' }, { label: '6', id: '6' }, { label: '×', id: 'times', insert: '×', kind: 'op' }],
  [{ label: '1', id: '1' }, { label: '2', id: '2' }, { label: '3', id: '3' }, { label: '−', id: 'minus', insert: '−', kind: 'op' }],
  [{ label: '0', id: '0' }, { label: '.', id: 'dot', insert: '.' }, { label: '%', id: 'percent', insert: '%', kind: 'op' }, { label: '+', id: 'plus', insert: '+', kind: 'op' }],
];

/** The calculator. History lasts until the app is closed; nothing is saved. */
export default function CalculatorScreen() {
  const t = useTheme();
  const [text, setText] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<{ expression: string; result: string }[]>([]);
  const live = text ? evaluate(text) : null;
  const preview = live && 'value' in live ? formatNumber(live.value) : '';

  function press(k: Key) {
    setError(null);
    if (k.id === 'clear') return setText('');
    if (k.id === 'back') return setText((s) => s.slice(0, -1));
    setText((s) => s + (k.insert ?? k.label));
  }

  function equals() {
    if (!text) return;
    const r = evaluate(text);
    if ('error' in r) return setError(r.error);
    const result = formatNumber(r.value);
    setHistory((h) => [{ expression: text, result }, ...h].slice(0, 20));
    setText(result.replace(/,/g, ''));
  }

  const key = (k: Key) => (
    <Pressable
      key={k.id}
      role="button"
      aria-label={k.id === 'back' ? 'Delete' : k.id === 'clear' ? 'Clear' : k.label}
      onPress={() => press(k)}
      style={({ pressed }) => ({
        flex: 1,
        height: 60,
        borderRadius: t.radius.md,
        alignItems: 'center',
        justifyContent: 'center',
        backgroundColor: k.kind === 'op' ? t.colors.tint : k.kind === 'action' ? t.colors.inactive : t.colors.surface,
        opacity: pressed ? 0.6 : 1,
      })}
      testID={`key-${k.id}`}
    >
      <Text style={{ fontSize: 24, color: k.kind ? '#fff' : t.colors.text }}>{k.label}</Text>
    </Pressable>
  );

  return (
    <Screen>
      <View style={{ alignItems: 'flex-end', gap: t.spacing.xs, paddingVertical: t.spacing.md, minHeight: 100 }}>
        <Text style={{ fontSize: t.font.large, color: t.colors.text }} numberOfLines={2} testID="calc-expression">
          {text || '0'}
        </Text>
        <Text style={{ fontSize: t.font.title, color: error ? t.colors.danger : t.colors.textMuted }} role={error ? 'alert' : undefined} testID="calc-result">
          {error ?? (preview && preview !== text ? `= ${preview}` : ' ')}
        </Text>
      </View>
      <View style={{ gap: t.spacing.sm }}>
        {KEYS.map((row, i) => (
          <View key={i} style={{ flexDirection: 'row', gap: t.spacing.sm }}>
            {row.map(key)}
          </View>
        ))}
        <Pressable
          role="button"
          aria-label="Equals"
          onPress={equals}
          style={({ pressed }) => ({ height: 60, borderRadius: t.radius.md, alignItems: 'center', justifyContent: 'center', backgroundColor: t.colors.success, opacity: pressed ? 0.6 : 1 })}
          testID="key-equals"
        >
          <Text style={{ fontSize: 24, color: '#fff' }}>=</Text>
        </Pressable>
      </View>
      {history.length ? (
        <Section title="Earlier" footer="Tap one to use its answer.">
          <List>
            {history.map((h, i) => (
              <ListRow key={i} title={h.result} subtitle={h.expression} onPress={() => setText((s) => s + h.result.replace(/,/g, ''))} chevron={false} testID={`history-${i}`} />
            ))}
          </List>
        </Section>
      ) : null}
    </Screen>
  );
}
