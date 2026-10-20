import { useState } from 'react';
import { Text, View } from 'react-native';
import { useMembers } from '@homeai/sdk';
import { Button, Chip, DateField, Section, Select, Stepper, TextField, TimeField, Toggle, useTheme } from '@homeai/ui';
import { WEEKDAYS, addDays, dayNumber, fromMinutes, minutes, validate, weekday, type EventValues, type Repeat } from '../lib/calendar';

const REPEATS: { value: Repeat; label: string }[] = [
  { value: 'none', label: 'Never' },
  { value: 'daily', label: 'Daily' },
  { value: 'weekly', label: 'Weekly' },
  { value: 'monthly', label: 'Monthly' },
  { value: 'yearly', label: 'Yearly' },
];
const UNIT: Record<Repeat, string> = { none: '', daily: 'day', weekly: 'week', monthly: 'month', yearly: 'year' };

/** A new event on `date`, at `time` for an hour, or all day without a time. */
export function newEvent(date: string, time?: string): EventValues {
  const start = time ?? '09:00';
  return {
    title: '',
    location: '',
    notes: '',
    allDay: false,
    startDate: date,
    startTime: start,
    endDate: date,
    endTime: fromMinutes(minutes(start) + 60),
    repeat: 'none',
    repeatEvery: 1,
    repeatDays: [],
    repeatUntil: null,
    people: [],
  };
}

export function EventForm({ initial, saveTitle, onSave, oneOccurrence }: { initial: EventValues; saveTitle: string; onSave: (v: EventValues) => Promise<void>; oneOccurrence?: boolean }) {
  const t = useTheme();
  const members = useMembers();
  const [v, setV] = useState<EventValues>(initial);
  const [problem, setProblem] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const set = (patch: Partial<EventValues>) => setV((old) => ({ ...old, ...patch }));

  // Moving the start moves the end with it, keeping the length.
  function setStartDate(date: string | null) {
    if (!date) return;
    set({ startDate: date, endDate: addDays(date, dayNumber(v.endDate) - dayNumber(v.startDate)) });
  }
  function setStartTime(time: string | null) {
    if (!time) return;
    if (v.startDate !== v.endDate) return set({ startTime: time });
    set({ startTime: time, endTime: fromMinutes(minutes(time) + Math.max(minutes(v.endTime) - minutes(v.startTime), 15)) });
  }
  function toggleDay(d: number) {
    const days = v.repeatDays.length ? v.repeatDays : [weekday(v.startDate)];
    set({ repeatDays: days.includes(d) ? days.filter((x) => x !== d) : [...days, d] });
  }
  function togglePerson(id: string) {
    set({ people: v.people.includes(id) ? v.people.filter((p) => p !== id) : [...v.people, id] });
  }

  async function save() {
    const wrong = validate(v);
    setProblem(wrong);
    if (wrong) return;
    setSaving(true);
    try {
      await onSave(v);
    } catch (e) {
      setProblem((e as Error).message);
      setSaving(false);
    }
  }

  const days = v.repeatDays.length ? v.repeatDays : [weekday(v.startDate)];
  return (
    <View style={{ gap: t.spacing.md }}>
      <TextField label="Title" value={v.title} onChangeText={(title) => set({ title })} placeholder="Dentist, soccer practice…" autoFocus={!initial.title} testID="event-title" />
      <Toggle label="All day" value={v.allDay} onValueChange={(allDay) => set({ allDay })} testID="event-all-day" />
      <View style={{ flexDirection: 'row', gap: t.spacing.sm }}>
        <View style={{ flex: 3 }}>
          <DateField label="Starts" value={v.startDate} onChange={setStartDate} testID="event-start-date" />
        </View>
        {v.allDay ? null : (
          <View style={{ flex: 2 }}>
            <TimeField label=" " value={v.startTime} onChange={setStartTime} testID="event-start-time" />
          </View>
        )}
      </View>
      <View style={{ flexDirection: 'row', gap: t.spacing.sm }}>
        <View style={{ flex: 3 }}>
          <DateField label="Ends" value={v.endDate} onChange={(endDate) => endDate && set({ endDate })} min={v.startDate} testID="event-end-date" />
        </View>
        {v.allDay ? null : (
          <View style={{ flex: 2 }}>
            <TimeField label=" " value={v.endTime} onChange={(endTime) => endTime && set({ endTime })} testID="event-end-time" />
          </View>
        )}
      </View>
      {oneOccurrence ? (
        <Text style={{ color: t.colors.textMuted }} testID="one-occurrence">
          Only this one changes; the rest of the series stays as it is.
        </Text>
      ) : (
        <>
          <Select label="Repeat" value={v.repeat} options={REPEATS} onChange={(repeat) => set({ repeat })} testID="event-repeat" />
          {v.repeat === 'none' ? null : (
            <>
              <Stepper label={`Every ${v.repeatEvery === 1 ? UNIT[v.repeat] : `${v.repeatEvery} ${UNIT[v.repeat]}s`}`} value={v.repeatEvery} onChange={(repeatEvery) => set({ repeatEvery })} min={1} max={99} testID="event-repeat-every" />
              {v.repeat === 'weekly' ? (
                <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: t.spacing.xs }}>
                  {WEEKDAYS.map((w, d) => (
                    <Chip key={w} label={w.slice(0, 3)} selected={days.includes(d)} onPress={() => toggleDay(d)} testID={`repeat-day-${d}`} />
                  ))}
                </View>
              ) : null}
              <DateField label="Ends on (optional)" value={v.repeatUntil} onChange={(repeatUntil) => set({ repeatUntil })} min={v.startDate} hint="Leave empty to repeat forever." testID="event-repeat-until" />
            </>
          )}
        </>
      )}
      {members.length ? (
        <Section title="Who">
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: t.spacing.xs }}>
            {members.map((m) => (
              <Chip key={m.id} label={m.name} selected={v.people.includes(m.id)} onPress={() => togglePerson(m.id)} testID={`who-${m.username}`} />
            ))}
          </View>
        </Section>
      ) : null}
      <TextField label="Location" value={v.location} onChangeText={(location) => set({ location })} testID="event-location" />
      <TextField label="Notes" value={v.notes} onChangeText={(notes) => set({ notes })} multiline testID="event-notes" />
      {problem ? (
        <Text style={{ color: t.colors.danger }} testID="event-error">
          {problem}
        </Text>
      ) : null}
      <Button title={saveTitle} onPress={() => void save()} loading={saving} testID="event-save" />
    </View>
  );
}
