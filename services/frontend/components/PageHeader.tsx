import type { ReactNode } from 'react';
import { Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { themedStyles } from '@/lib/theme';

/**
 * The title bar of a home pager page (M19-08). Each page draws its own, so
 * it slides with the page: the history pushes Chat's aside as it opens.
 */
export function PageHeader({ title, left, right }: { title: string; left?: ReactNode; right?: ReactNode }) {
  const styles = useStyles();
  const insets = useSafeAreaInsets();
  return (
    <View style={[styles.header, { paddingTop: insets.top }]}>
      {left}
      <Text style={[styles.title, !left && styles.titleAlone]} numberOfLines={1} accessibilityRole="header">
        {title}
      </Text>
      {right}
    </View>
  );
}

const useStyles = themedStyles((theme) => ({
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    minHeight: 56,
    paddingHorizontal: 4,
    backgroundColor: theme.bg,
  },
  title: {
    flex: 1,
    color: theme.text,
    fontSize: 20,
    fontWeight: '500',
    marginLeft: 8,
  },
  titleAlone: {
    marginLeft: 16,
  },
}));
