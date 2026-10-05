import { useState } from 'react';
import { Pressable, StyleSheet, Switch, Text, TextInput, View, type TextInputProps } from 'react-native';

import { ActionButton, Card, ErrorText, SectionTitle, Segmented, settingsStyles } from '@/components/SettingsUI';
import type { Space } from '@/lib/platform';
import {
  APPROVAL_MODE_HELP,
  APPROVAL_MODE_LABELS,
  WEEKDAYS,
  deviceTimezone,
  routineSpaces,
  scheduleSummary,
  spacePath,
  type ApprovalMode,
  type Routine,
  type RoutineInput,
  type Schedule,
  type ScheduleKind,
  type Weekday,
} from '@/lib/routines';
import { theme } from '@/lib/theme';

const KIND_LABELS: Record<ScheduleKind, string> = {
  once: 'Once',
  daily: 'Daily',
  weekdays: 'Weekdays',
  weekly: 'Weekly',
  monthly: 'Monthly',
};
const DAY_LETTERS: Record<Weekday, string> = {
  mon: 'Mon',
  tue: 'Tue',
  wed: 'Wed',
  thu: 'Thu',
  fri: 'Fri',
  sat: 'Sat',
  sun: 'Sun',
};

export interface RoutineFormState {
  name: string;
  prompt: string;
  space: string;
  kind: ScheduleKind;
  time: string;
  date: string;
  days: Weekday[];
  monthDay: string;
  timezone: string;
  enabled: boolean;
  approvalMode: ApprovalMode;
}

function today(): string {
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

export function initialForm(routine: Routine | null): RoutineFormState {
  const blank: RoutineFormState = {
    name: '',
    prompt: '',
    space: '/personal',
    kind: 'weekdays',
    time: '08:00',
    date: today(),
    days: ['mon'],
    monthDay: '1',
    timezone: deviceTimezone(),
    enabled: true,
    approvalMode: 'ask',
  };
  if (routine === null) return blank;
  const schedule = routine.schedule;
  const form: RoutineFormState = {
    ...blank,
    name: routine.name,
    prompt: routine.prompt,
    space: routine.space,
    kind: schedule.kind,
    timezone: routine.timezone,
    enabled: routine.enabled,
    approvalMode: routine.approval_mode,
  };
  if (schedule.kind === 'once') {
    const [date, time = '00:00'] = schedule.at.split('T');
    return { ...form, date, time: time.slice(0, 5) };
  }
  form.time = schedule.time.slice(0, 5);
  if (schedule.kind === 'weekly') form.days = schedule.days;
  if (schedule.kind === 'monthly') form.monthDay = String(schedule.day);
  return form;
}

const TIME = /^([01]?\d|2[0-3]):([0-5]\d)$/;
const DATE = /^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$/;

/** The form as a request body, or what's wrong with it. */
export function formToInput(form: RoutineFormState): { input: RoutineInput } | { error: string } {
  const name = form.name.trim();
  const prompt = form.prompt.trim();
  if (!name) return { error: 'Give the routine a name.' };
  if (!prompt) return { error: 'Say what the agent should do.' };
  const time = TIME.exec(form.time.trim());
  if (!time) return { error: 'Enter the time as HH:MM, e.g. 07:30.' };
  const hhmm = `${time[1].padStart(2, '0')}:${time[2]}`;
  let schedule: Schedule;
  switch (form.kind) {
    case 'once':
      if (!DATE.test(form.date.trim())) return { error: 'Enter the date as YYYY-MM-DD.' };
      schedule = { kind: 'once', at: `${form.date.trim()}T${hhmm}` };
      break;
    case 'weekly':
      if (form.days.length === 0) return { error: 'Pick at least one day.' };
      schedule = { kind: 'weekly', days: WEEKDAYS.filter((d) => form.days.includes(d)), time: hhmm };
      break;
    case 'monthly': {
      const day = Number(form.monthDay);
      if (!Number.isInteger(day) || day < 1 || day > 31) return { error: 'Pick a day of the month from 1 to 31.' };
      schedule = { kind: 'monthly', day, time: hhmm };
      break;
    }
    default:
      schedule = { kind: form.kind, time: hhmm };
  }
  const timezone = form.timezone.trim();
  if (!timezone) return { error: 'Enter a timezone, e.g. America/Los_Angeles.' };
  return {
    input: {
      name,
      prompt,
      space: form.space,
      schedule,
      timezone,
      enabled: form.enabled,
      approval_mode: form.approvalMode,
    },
  };
}

/** Name, prompt, space, schedule, timezone and approval mode (M17-06). */
export function RoutineForm({
  routine,
  spaces,
  saving,
  error,
  onSubmit,
}: {
  routine: Routine | null;
  spaces: Space[];
  saving: boolean;
  error: string | null;
  onSubmit: (input: RoutineInput) => void;
}) {
  const [form, setForm] = useState(() => initialForm(routine));
  const [invalid, setInvalid] = useState<string | null>(null);
  const set = <K extends keyof RoutineFormState>(key: K, value: RoutineFormState[K]) =>
    setForm((prev) => ({ ...prev, [key]: value }));

  const choices = routineSpaces(spaces).map((space) => ({
    value: spacePath(space),
    label: space.kind === 'personal' ? 'Personal' : space.name,
    testID: `routine-space-${space.slug}`,
  }));
  if (!choices.some((choice) => choice.value === form.space)) {
    // Archived, or edit rights lost: still shown, so saving doesn't move it.
    choices.push({ value: form.space, label: form.space, testID: 'routine-space-current' });
  }
  const checked = formToInput(form);
  const preview = 'input' in checked ? scheduleSummary(checked.input.schedule) : null;

  const submit = () => {
    if ('error' in checked) {
      setInvalid(checked.error);
      return;
    }
    setInvalid(null);
    onSubmit(checked.input);
  };

  return (
    <View style={styles.form} testID="routine-form">
      <Field label="Name" value={form.name} onChangeText={(v) => set('name', v)} testID="routine-name" maxLength={120} />
      <Field
        label="What should the agent do?"
        value={form.prompt}
        onChangeText={(v) => set('prompt', v)}
        multiline
        style={[styles.input, styles.prompt]}
        testID="routine-prompt"
        placeholder="Summarize my unread notes and list what's due this week."
      />

      <SectionTitle>Space</SectionTitle>
      <View style={styles.chips}>
        {choices.map((choice) => (
          <Chip
            key={choice.value}
            label={choice.label}
            selected={form.space === choice.value}
            onPress={() => set('space', choice.value)}
            testID={choice.testID}
          />
        ))}
      </View>

      <SectionTitle>When</SectionTitle>
      <View style={styles.chips}>
        {(Object.keys(KIND_LABELS) as ScheduleKind[]).map((kind) => (
          <Chip
            key={kind}
            label={KIND_LABELS[kind]}
            selected={form.kind === kind}
            onPress={() => set('kind', kind)}
            testID={`routine-kind-${kind}`}
          />
        ))}
      </View>
      {form.kind === 'weekly' ? (
        <View style={styles.chips}>
          {WEEKDAYS.map((day) => (
            <Chip
              key={day}
              label={DAY_LETTERS[day]}
              selected={form.days.includes(day)}
              onPress={() =>
                set('days', form.days.includes(day) ? form.days.filter((d) => d !== day) : [...form.days, day])
              }
              testID={`routine-day-${day}`}
            />
          ))}
        </View>
      ) : null}
      <View style={styles.row}>
        {form.kind === 'once' ? (
          <Field
            label="Date"
            value={form.date}
            onChangeText={(v) => set('date', v)}
            placeholder="2026-11-02"
            testID="routine-date"
            containerStyle={styles.grow}
          />
        ) : null}
        {form.kind === 'monthly' ? (
          <Field
            label="Day of month"
            value={form.monthDay}
            onChangeText={(v) => set('monthDay', v)}
            keyboardType="number-pad"
            testID="routine-month-day"
            containerStyle={styles.grow}
          />
        ) : null}
        <Field
          label="Time"
          value={form.time}
          onChangeText={(v) => set('time', v)}
          placeholder="07:30"
          testID="routine-time"
          containerStyle={styles.grow}
        />
      </View>
      <Field
        label="Timezone"
        value={form.timezone}
        onChangeText={(v) => set('timezone', v)}
        placeholder="America/Los_Angeles"
        testID="routine-timezone"
      />
      {preview ? (
        <Text style={settingsStyles.muted} testID="routine-schedule-preview">
          {preview}
        </Text>
      ) : null}

      <SectionTitle>When it wants to change something</SectionTitle>
      <Segmented
        value={form.approvalMode}
        onChange={(mode) => set('approvalMode', mode)}
        options={(Object.keys(APPROVAL_MODE_LABELS) as ApprovalMode[]).map((mode) => ({
          value: mode,
          label: APPROVAL_MODE_LABELS[mode],
          testID: `routine-approval-${mode}`,
        }))}
      />
      <Text style={settingsStyles.muted}>{APPROVAL_MODE_HELP[form.approvalMode]}</Text>

      <Card>
        <View style={[settingsStyles.row, settingsStyles.firstRow]}>
          <Text style={[settingsStyles.rowTitle, styles.grow]}>On</Text>
          <Switch
            value={form.enabled}
            onValueChange={(v) => set('enabled', v)}
            accessibilityLabel="Routine on"
            testID="routine-enabled"
          />
        </View>
      </Card>

      <ErrorText testID="routine-form-error">{invalid ?? error}</ErrorText>
      <ActionButton
        label={routine ? 'Save' : 'Create routine'}
        variant="primary"
        onPress={submit}
        busy={saving}
        testID="routine-save"
      />
    </View>
  );
}

function Field({
  label,
  containerStyle,
  style,
  ...props
}: { label: string; containerStyle?: object } & TextInputProps) {
  return (
    <View style={[styles.field, containerStyle]}>
      <Text style={styles.label}>{label}</Text>
      <TextInput
        style={style ?? styles.input}
        placeholderTextColor={theme.textMuted}
        autoCorrect={false}
        accessibilityLabel={label}
        {...props}
      />
    </View>
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
  testID: string;
}) {
  return (
    <Pressable
      onPress={onPress}
      style={[styles.chip, selected && styles.chipSelected]}
      accessibilityRole="button"
      accessibilityState={{ selected }}
      testID={testID}
    >
      <Text style={[styles.chipText, selected && styles.chipTextSelected]}>{label}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  form: {
    gap: 12,
  },
  field: {
    gap: 6,
  },
  label: {
    color: theme.textMuted,
    fontSize: 13,
  },
  input: {
    borderWidth: 1,
    borderColor: theme.border,
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 10,
    color: theme.text,
    backgroundColor: theme.surface,
    fontSize: 15,
  },
  prompt: {
    minHeight: 110,
    textAlignVertical: 'top',
  },
  row: {
    flexDirection: 'row',
    gap: 12,
  },
  grow: {
    flex: 1,
  },
  chips: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
  },
  chip: {
    paddingHorizontal: 12,
    paddingVertical: 7,
    borderRadius: 16,
    borderWidth: 1,
    borderColor: theme.border,
    backgroundColor: theme.surface,
  },
  chipSelected: {
    borderColor: theme.accent,
    backgroundColor: theme.accent,
  },
  chipText: {
    color: theme.text,
    fontSize: 14,
  },
  chipTextSelected: {
    fontWeight: '600',
  },
});
