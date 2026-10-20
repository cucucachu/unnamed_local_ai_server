import { useMemo, useRef, useState } from 'react';
import { PanResponder, Text, View } from 'react-native';
import { useRouter } from 'expo-router';
import { useMembers, useSpace } from '@homeai/sdk';
import { Button, Chip, IconButton, Select, useTheme } from '@homeai/ui';
import { MonthView } from '../components/MonthView';
import { TimeGrid } from '../components/TimeGrid';
import { daysBetween, fromMinutes, step, today as todayOf, viewRange, viewTitle, type Occurrence, type View as CalendarView } from '../lib/calendar';
import { useOccurrences } from '../lib/useOccurrences';

const VIEWS: { value: CalendarView; label: string }[] = [
  { value: 'day', label: 'Day' },
  { value: 'week', label: 'Week' },
  { value: 'month', label: 'Month' },
];

export default function CalendarScreen() {
  const t = useTheme();
  const router = useRouter();
  const members = useMembers();
  const canEdit = useSpace()?.role !== 'viewer';
  const today = todayOf();
  const [view, setView] = useState<CalendarView>('month');
  const [day, setDay] = useState(today);
  const [person, setPerson] = useState<string>('');
  const { from, to } = viewRange(view, day);
  const { occurrences } = useOccurrences(from, to, person || null);

  const go = (dir: 1 | -1) => setDay((d) => step(view, d, dir));
  const live = useRef({ go });
  live.current = { go };
  const swipe = useMemo(
    () =>
      PanResponder.create({
        onMoveShouldSetPanResponderCapture: (_e, g) => Math.abs(g.dx) > 24 && Math.abs(g.dx) > 2 * Math.abs(g.dy),
        onPanResponderRelease: (_e, g) => {
          if (Math.abs(g.dx) > 60) live.current.go(g.dx < 0 ? 1 : -1);
        },
      }),
    [],
  );

  function open(o: Occurrence) {
    if (o.event) router.push({ pathname: '/event/[id]', params: { id: String(o.event.id), date: o.startDate } });
  }

  function add(date: string, hour?: number) {
    const params: Record<string, string> = { date };
    if (hour !== undefined) params.time = fromMinutes(hour * 60);
    router.push({ pathname: '/new', params });
  }

  function openDay(d: string) {
    setDay(d);
    setView('day');
  }

  const people = [{ value: '', label: 'Everyone' }, ...members.map((m) => ({ value: m.id, label: m.name }))];

  return (
    <View style={{ flex: 1, backgroundColor: t.colors.background }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: t.spacing.xs, paddingHorizontal: t.spacing.sm, paddingTop: t.spacing.sm }}>
        {VIEWS.map((v) => (
          <Chip key={v.value} label={v.label} selected={view === v.value} onPress={() => setView(v.value)} testID={`view-${v.value}`} />
        ))}
        <View style={{ flex: 1 }} />
        <Button title="Today" variant="plain" onPress={() => setDay(today)} testID="today" />
        {canEdit ? <IconButton icon="add-outline" accessibilityLabel="New event" onPress={() => add(view === 'month' && day.startsWith(today.slice(0, 7)) ? today : day)} testID="new-event" /> : null}
      </View>
      <View style={{ flexDirection: 'row', alignItems: 'center', paddingHorizontal: t.spacing.xs }}>
        <IconButton icon="chevron-back-outline" accessibilityLabel="Previous" onPress={() => go(-1)} testID="prev" />
        <Text style={{ flex: 1, textAlign: 'center', fontSize: t.font.title, fontWeight: '600', color: t.colors.text }} testID="view-title">
          {viewTitle(view, day)}
        </Text>
        <IconButton icon="chevron-forward-outline" accessibilityLabel="Next" onPress={() => go(1)} testID="next" />
      </View>
      {members.length > 1 ? (
        <View style={{ paddingHorizontal: t.spacing.sm, paddingBottom: t.spacing.xs }}>
          <Select value={person} options={people} onChange={setPerson} testID="person-filter" />
        </View>
      ) : null}
      <View style={{ flex: 1 }} {...swipe.panHandlers}>
        {view === 'month' ? (
          <MonthView day={day} today={today} occurrences={occurrences} onDay={openDay} />
        ) : (
          <TimeGrid
            key={`${view}-${from}`}
            days={daysBetween(from, to)}
            today={today}
            occurrences={occurrences}
            onOpen={open}
            onSlot={canEdit ? (d, h) => add(d, h) : undefined}
            onDayHeader={openDay}
          />
        )}
      </View>
    </View>
  );
}
