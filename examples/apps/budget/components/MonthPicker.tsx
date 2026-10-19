import { Text, View } from 'react-native';
import { IconButton, useTheme } from '@homeai/ui';
import { addMonths, monthLabel } from '../lib/budget';

export function MonthPicker({ month, onChange }: { month: string; onChange: (month: string) => void }) {
  const t = useTheme();
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
      <IconButton icon="chevron-back-outline" accessibilityLabel="Previous month" onPress={() => onChange(addMonths(month, -1))} testID="month-prev" />
      <Text style={{ color: t.colors.text, fontSize: t.font.title, fontWeight: '600' }} testID="month-label">
        {monthLabel(month)}
      </Text>
      <IconButton icon="chevron-forward-outline" accessibilityLabel="Next month" onPress={() => onChange(addMonths(month, 1))} testID="month-next" />
    </View>
  );
}
