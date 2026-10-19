import { Pressable, Text, View } from 'react-native';
import { useTheme } from '@homeai/ui';

/** Five stars to tap; tapping the current rating clears it. */
export function Stars({ value, onChange, disabled, testID }: { value: number | null; onChange: (rating: number | null) => void; disabled?: boolean; testID?: string }) {
  const t = useTheme();
  return (
    <View style={{ gap: t.spacing.xs }} testID={testID}>
      <Text style={{ color: t.colors.textMuted, fontSize: t.font.small }}>Rating</Text>
      <View style={{ flexDirection: 'row', gap: t.spacing.xs }}>
        {[1, 2, 3, 4, 5].map((n) => (
          <Pressable
            key={n}
            role="button"
            aria-label={`${n} star${n === 1 ? '' : 's'}`}
            aria-pressed={value === n}
            disabled={disabled}
            onPress={() => onChange(value === n ? null : n)}
            hitSlop={4}
            testID={testID ? `${testID}-${n}` : undefined}
          >
            <Text style={{ fontSize: 30, color: value !== null && n <= value ? t.colors.warning : t.colors.border }}>★</Text>
          </Pressable>
        ))}
      </View>
    </View>
  );
}
