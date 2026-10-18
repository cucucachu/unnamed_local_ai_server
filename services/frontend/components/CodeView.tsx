import { ScrollView, StyleSheet, Text, View } from 'react-native';

import { monospaceFontFamily, theme } from '@/lib/theme';

/** JSON and code in monospace with line numbers (M22-11). Lines don't wrap,
 * so the gutter stays aligned; long lines scroll sideways. */
export function CodeView({ text }: { text: string }) {
  const lines = text.endsWith('\n') ? text.slice(0, -1).split('\n') : text.split('\n');
  return (
    <ScrollView style={styles.container} testID="code-view">
      <View style={styles.row}>
        <Text style={[styles.mono, styles.gutter]} testID="code-gutter">
          {lines.map((_, i) => i + 1).join('\n')}
        </Text>
        <ScrollView horizontal contentContainerStyle={styles.code}>
          <Text selectable style={styles.mono} testID="code-text">
            {lines.join('\n')}
          </Text>
        </ScrollView>
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
  },
  row: {
    flexDirection: 'row',
    paddingVertical: 12,
  },
  mono: {
    color: theme.text,
    fontFamily: monospaceFontFamily,
    fontSize: 13,
    lineHeight: 19,
  },
  gutter: {
    color: theme.textMuted,
    textAlign: 'right',
    paddingLeft: 8,
    paddingRight: 10,
    borderRightWidth: StyleSheet.hairlineWidth,
    borderRightColor: theme.border,
  },
  code: {
    paddingHorizontal: 10,
  },
});
