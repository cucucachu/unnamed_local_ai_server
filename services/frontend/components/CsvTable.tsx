import { useMemo } from 'react';
import { FlatList, ScrollView, StyleSheet, Text, View } from 'react-native';

import { csvDelimiterFor, parseCsv } from '@/lib/textFiles';
import { theme } from '@/lib/theme';

/** Rows shown at most; the rest are counted, not rendered. */
export const CSV_MAX_ROWS = 5000;

const CHAR_WIDTH = 8;
const MIN_WIDTH = 64;
const MAX_WIDTH = 280;

/**
 * A CSV/TSV file as a table (M22-11): the header row, then the first
 * `CSV_MAX_ROWS` rows, scrolling both ways, with "Showing N of M rows".
 * Column widths come from the header and the first 200 rows.
 */
export function CsvTable({ name, text }: { name: string; text: string }) {
  const { header, rows, total, widths } = useMemo(() => {
    const all = parseCsv(text, csvDelimiterFor(name, text));
    const [first = [], ...data] = all;
    const columns = Math.max(first.length, ...data.slice(0, 200).map((r) => r.length));
    const sample = [first, ...data.slice(0, 200)];
    const widths = Array.from({ length: columns }, (_, c) => {
      const longest = Math.max(...sample.map((r) => (r[c] ?? '').length));
      return Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, longest * CHAR_WIDTH + 16));
    });
    return { header: first, rows: data.slice(0, CSV_MAX_ROWS), total: data.length, widths };
  }, [name, text]);

  const renderRow = (cells: string[], index: number, head = false) => (
    <View style={[styles.row, head && styles.headRow]} testID={head ? 'csv-header' : `csv-row-${index}`}>
      {widths.map((width, c) => (
        <Text key={c} style={[styles.cell, head && styles.headCell, { width }]} numberOfLines={head ? 2 : 3} selectable>
          {cells[c] ?? ''}
        </Text>
      ))}
    </View>
  );

  return (
    <View style={styles.container} testID="csv-table">
      <Text style={styles.count} testID="csv-count">
        {total > rows.length
          ? `Showing ${rows.length.toLocaleString('en-US')} of ${total.toLocaleString('en-US')} rows`
          : `${total.toLocaleString('en-US')} ${total === 1 ? 'row' : 'rows'}`}
      </Text>
      <ScrollView horizontal style={styles.container}>
        <View>
          {renderRow(header, -1, true)}
          <FlatList
            data={rows}
            keyExtractor={(_, index) => String(index)}
            renderItem={({ item, index }) => renderRow(item, index)}
            initialNumToRender={40}
            windowSize={11}
          />
        </View>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
  },
  count: {
    color: theme.textMuted,
    fontSize: 13,
    paddingHorizontal: 12,
    paddingVertical: 8,
  },
  row: {
    flexDirection: 'row',
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: theme.border,
  },
  headRow: {
    backgroundColor: theme.surface,
    borderBottomWidth: 1,
  },
  cell: {
    color: theme.text,
    fontSize: 13,
    paddingHorizontal: 8,
    paddingVertical: 6,
    borderRightWidth: StyleSheet.hairlineWidth,
    borderRightColor: theme.border,
  },
  headCell: {
    fontWeight: '600',
  },
});
