import { useState } from 'react';
import { Pressable, StyleSheet, Text, TextInput, View } from 'react-native';

import { ActionButton, Card, SectionTitle, Segmented, useSettingsStyles } from '@/components/SettingsUI';
import {
  exportAppData,
  importCsv,
  importErrorMessage,
  type CsvDelimiter,
  type DataMapInstance,
  type ImportResult,
} from '@/lib/apps';
import { themedStyles, useTheme } from '@/lib/theme';

export interface Notice {
  text: string;
  ok: boolean;
}

const DELIMITERS: { value: CsvDelimiter; label: string; testID: string }[] = [
  { value: ',', label: 'Comma', testID: 'app-import-delimiter-comma' },
  { value: ';', label: 'Semicolon', testID: 'app-import-delimiter-semicolon' },
  { value: '\t', label: 'Tab', testID: 'app-import-delimiter-tab' },
];

/**
 * An installed app's data as files (`docs/PLATFORM.md` §7 "CSV export and
 * import", M22-08): anyone who can see the instance exports every table as
 * CSV into a new folder; its space's owners and editors import one CSV file
 * from Files into one table, checking it first (a dry run) and mapping the
 * file's headers to the table's columns.
 */
export function AppDataSection({
  instance,
  busyKey,
  run,
  onNotice,
}: {
  instance: DataMapInstance;
  busyKey: string | null;
  run: (key: string, fn: () => Promise<unknown>) => Promise<boolean>;
  onNotice: (notice: Notice | null) => void;
}) {
  const settingsStyles = useSettingsStyles();
  const theme = useTheme();
  const styles = useStyles();
  const canImport = instance.space.role === 'owner' || instance.space.role === 'editor';
  const [table, setTable] = useState(instance.tables[0]?.name ?? '');
  const [path, setPath] = useState('');
  const [delimiter, setDelimiter] = useState<CsvDelimiter>(',');
  const [plan, setPlan] = useState<ImportResult | null>(null);
  const [mapping, setMapping] = useState<Record<string, string | null>>({});

  const exportData = () =>
    run('export', async () => {
      onNotice(null);
      const result = await exportAppData(instance.id);
      const rows = result.tables.reduce((sum, t) => sum + t.rows, 0);
      onNotice({ text: `Exported ${result.tables.length} tables (${rows} rows) to ${result.folder}.`, ok: true });
    });

  /** `fn`, showing an `import_failed` error's line and column instead of a generic message. */
  const attempt = (key: string, fn: () => Promise<void>) =>
    run(key, async () => {
      onNotice(null);
      try {
        await fn();
      } catch (caught) {
        const message = importErrorMessage(caught);
        if (message === null) throw caught;
        onNotice({ text: message, ok: false });
      }
    });

  const check = () =>
    attempt('check', async () => {
      setPlan(null);
      const result = await importCsv(instance.id, { table, path: path.trim(), delimiter, dryRun: true });
      setMapping(Object.fromEntries(result.headers.map((h) => [h, result.mapping[h] ?? null])));
      setPlan(result);
    });

  const chosen = Object.fromEntries(
    Object.entries(mapping).filter((entry): entry is [string, string] => entry[1] !== null),
  );

  const doImport = () =>
    attempt('import', async () => {
      const result = await importCsv(instance.id, { table, path: path.trim(), delimiter, mapping: chosen });
      setPlan(null);
      onNotice({ text: `Imported ${result.rows} rows into ${result.table}.`, ok: true });
    });

  const reset = <T,>(set: (value: T) => void) => (value: T) => {
    set(value);
    setPlan(null);
  };

  return (
    <>
      <SectionTitle>Data</SectionTitle>
      <ActionButton
        label="Export data"
        onPress={exportData}
        busy={busyKey === 'export'}
        disabled={busyKey !== null}
        testID="app-export"
      />
      <Text style={settingsStyles.muted}>
        Saves each table as a CSV file in a new folder under Exports.
      </Text>

      {canImport && instance.tables.length > 0 ? (
        <Card testID="app-import">
          <View style={settingsStyles.cardBody}>
            <Text style={settingsStyles.rowTitle}>Import a CSV file</Text>
            <Text style={settingsStyles.muted}>Into table</Text>
            <View style={styles.chips}>
              {instance.tables.map((t) => (
                <Chip
                  key={t.name}
                  label={t.name}
                  selected={table === t.name}
                  onPress={() => reset(setTable)(t.name)}
                  testID={`app-import-table-${t.name}`}
                />
              ))}
            </View>
            <TextInput
              value={path}
              onChangeText={reset(setPath)}
              autoCapitalize="none"
              autoCorrect={false}
              placeholder="/personal/data.csv"
              placeholderTextColor={theme.textMuted}
              style={styles.input}
              accessibilityLabel="CSV file path"
              testID="app-import-path"
            />
            <Segmented options={DELIMITERS} value={delimiter} onChange={reset(setDelimiter)} />
            <ActionButton
              label="Check file"
              onPress={check}
              busy={busyKey === 'check'}
              disabled={!path.trim() || busyKey !== null}
              testID="app-import-check"
            />

            {plan ? (
              <View style={styles.plan} testID="app-import-plan">
                <Text style={settingsStyles.muted} testID="app-import-summary">
                  {plan.unmatched.length > 0
                    ? `No column of ${plan.table} for ${plan.unmatched.join(', ')}. Pick a column for each header, or skip it.`
                    : `${plan.rows} rows ready to import into ${plan.table}.`}
                </Text>
                {plan.headers.map((header) => (
                  <View key={header} style={styles.header} testID={`app-import-header-${header}`}>
                    <Text style={settingsStyles.rowTitle}>{header}</Text>
                    <View style={styles.chips}>
                      {plan.columns.map((column) => (
                        <Chip
                          key={column}
                          label={column}
                          selected={mapping[header] === column}
                          onPress={() => setMapping({ ...mapping, [header]: column })}
                          testID={`app-import-map-${header}-${column}`}
                        />
                      ))}
                      <Chip
                        label="Skip"
                        selected={mapping[header] === null}
                        onPress={() => setMapping({ ...mapping, [header]: null })}
                        testID={`app-import-skip-${header}`}
                      />
                    </View>
                  </View>
                ))}
                <ActionButton
                  label="Import"
                  variant="primary"
                  onPress={doImport}
                  busy={busyKey === 'import'}
                  disabled={Object.keys(chosen).length === 0 || busyKey !== null}
                  testID="app-import-confirm"
                />
              </View>
            ) : null}
          </View>
        </Card>
      ) : null}
    </>
  );
}

function Chip({
  label,
  selected,
  onPress,
  testID,
}: {
  label: string;
  selected: boolean;
  onPress: () => void;
  testID?: string;
}) {
  const styles = useStyles();
  return (
    <Pressable
      onPress={onPress}
      style={[styles.chip, selected && styles.chipSelected]}
      accessibilityRole="radio"
      accessibilityState={{ selected }}
      testID={testID}
    >
      <Text style={[styles.chipText, selected && styles.chipTextSelected]}>{label}</Text>
    </Pressable>
  );
}

const useStyles = themedStyles((theme) => ({
  chips: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 6,
  },
  chip: {
    borderRadius: 14,
    borderWidth: 1,
    borderColor: theme.border,
    paddingHorizontal: 10,
    paddingVertical: 4,
  },
  chipSelected: {
    borderColor: theme.accent,
    backgroundColor: theme.accent,
  },
  chipText: {
    color: theme.textMuted,
    fontSize: 13,
  },
  chipTextSelected: {
    color: theme.onAccent,
  },
  input: {
    color: theme.text,
    fontSize: 16,
    paddingVertical: 8,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: theme.border,
  },
  plan: {
    gap: 10,
  },
  header: {
    gap: 4,
  },
}));
