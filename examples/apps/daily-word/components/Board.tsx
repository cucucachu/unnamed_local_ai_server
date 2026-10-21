import { Pressable, Text, View } from 'react-native';
import { useTheme, type Theme } from '@homeai/ui';
import { LENGTH, MAX_GUESSES, mark, type Mark } from '../lib/game';

export function markColor(t: Theme, m: Mark | undefined): string {
  if (m === 'correct') return t.colors.success;
  if (m === 'present') return t.colors.warning;
  if (m === 'absent') return t.colors.inactive;
  return 'transparent';
}

/** Six rows of five: the guesses so far, marked, then the one being typed, then empty rows. */
export function Board({ guesses, word, typing }: { guesses: string[]; word: string; typing: string }) {
  const t = useTheme();
  const rows = [...guesses, ...(guesses.length < MAX_GUESSES ? [typing] : [])];
  while (rows.length < MAX_GUESSES) rows.push('');
  return (
    <View style={{ gap: 6, alignSelf: 'center' }} testID="board">
      {rows.map((row, r) => {
        const marks = r < guesses.length ? mark(row, word) : null;
        return (
          <View key={r} style={{ flexDirection: 'row', gap: 6 }} testID={`row-${r}`}>
            {Array.from({ length: LENGTH }, (_, c) => {
              const letter = row[c] ?? '';
              const m = marks?.[c];
              return (
                <View
                  key={c}
                  testID={`tile-${r}-${c}`}
                  accessibilityLabel={`${letter || 'empty'}${m ? ` ${m}` : ''}`}
                  style={{
                    width: 54,
                    height: 54,
                    alignItems: 'center',
                    justifyContent: 'center',
                    borderWidth: m ? 0 : 2,
                    borderColor: letter ? t.colors.textMuted : t.colors.border,
                    backgroundColor: markColor(t, m),
                    borderRadius: 4,
                  }}
                >
                  <Text style={{ fontSize: 28, fontWeight: '700', color: m ? '#ffffff' : t.colors.text }}>{letter.toUpperCase()}</Text>
                </View>
              );
            })}
          </View>
        );
      })}
    </View>
  );
}

const ROWS = ['qwertyuiop', 'asdfghjkl', 'zxcvbnm'];

/** The on-screen keyboard: each key colored by the best mark its letter has had. */
export function Keyboard({ marks, onKey, disabled }: { marks: Record<string, Mark>; onKey: (key: string) => void; disabled?: boolean }) {
  const t = useTheme();
  const key = (label: string, value: string, flex = 1) => {
    const m = marks[value];
    return (
      <Pressable
        key={value}
        disabled={disabled}
        onPress={() => onKey(value)}
        accessibilityLabel={value === 'enter' ? 'Enter' : value === 'back' ? 'Delete letter' : value}
        testID={`key-${value}`}
        style={({ pressed }) => ({
          flex,
          height: 54,
          borderRadius: 6,
          alignItems: 'center',
          justifyContent: 'center',
          backgroundColor: m ? markColor(t, m) : t.colors.surface,
          borderWidth: m ? 0 : 1,
          borderColor: t.colors.border,
          opacity: pressed ? 0.7 : 1,
        })}
      >
        <Text style={{ fontSize: label.length > 1 ? 12 : 18, fontWeight: '700', color: m ? '#ffffff' : t.colors.text }}>{label}</Text>
      </Pressable>
    );
  };
  return (
    <View style={{ gap: 6, width: '100%', maxWidth: 500, alignSelf: 'center' }} testID="keyboard">
      {ROWS.map((row, i) => (
        <View key={row} style={{ flexDirection: 'row', gap: 4, paddingHorizontal: i === 1 ? 16 : 0 }}>
          {i === 2 ? key('ENTER', 'enter', 1.5) : null}
          {[...row].map((c) => key(c.toUpperCase(), c))}
          {i === 2 ? key('⌫', 'back', 1.5) : null}
        </View>
      ))}
    </View>
  );
}
