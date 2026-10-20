import { useEffect, useRef, useState } from 'react';
import { Pressable, ScrollView, Text, View } from 'react-native';
import { useTheme } from '@homeai/ui';
import { WEEKDAYS, dayBlocks, hourLabel, onDay, timeLabel, weekday, type Occurrence } from '../lib/calendar';

const HOUR = 48;
const GUTTER = 48;
/** Where the grid starts scrolled to. */
const FIRST_HOUR = 7;

function nowMinutes() {
  const d = new Date();
  return d.getHours() * 60 + d.getMinutes();
}

/**
 * Days side by side (one for Day, seven for Week): an all-day strip, then
 * 24 hours. Tap an empty hour to add an event there; tap an event to open it.
 */
export function TimeGrid({
  days,
  today,
  occurrences,
  onOpen,
  onSlot,
  onDayHeader,
}: {
  days: string[];
  today: string;
  occurrences: Occurrence[];
  onOpen: (o: Occurrence) => void;
  onSlot?: (day: string, hour: number) => void;
  onDayHeader?: (day: string) => void;
}) {
  const t = useTheme();
  const scroll = useRef<ScrollView>(null);
  const [now, setNow] = useState(nowMinutes);
  useEffect(() => {
    const timer = setTimeout(() => scroll.current?.scrollTo({ y: FIRST_HOUR * HOUR - 8, animated: false }), 0);
    return () => clearTimeout(timer);
  }, []);
  useEffect(() => {
    const timer = setInterval(() => setNow(nowMinutes()), 60_000);
    return () => clearInterval(timer);
  }, []);

  const allDay = days.map((d) => occurrences.filter((o) => o.allDay && onDay(o, d)));
  const hasAllDay = allDay.some((list) => list.length > 0);
  const line = { borderColor: t.colors.border };

  return (
    <View style={{ flex: 1 }}>
      {days.length > 1 ? (
        <View style={{ flexDirection: 'row', paddingLeft: GUTTER, borderBottomWidth: 1, ...line }}>
          {days.map((d) => (
            <Pressable key={d} onPress={() => onDayHeader?.(d)} style={{ flex: 1, alignItems: 'center', paddingVertical: 4 }} testID={`grid-day-${d}`}>
              <Text style={{ fontSize: 11, color: t.colors.textMuted }}>{WEEKDAYS[weekday(d)].slice(0, 3)}</Text>
              <View style={{ minWidth: 26, borderRadius: 13, backgroundColor: d === today ? t.colors.tint : 'transparent' }}>
                <Text style={{ textAlign: 'center', fontWeight: '600', color: d === today ? t.colors.onTint : t.colors.text }}>{Number(d.slice(8))}</Text>
              </View>
            </Pressable>
          ))}
        </View>
      ) : null}
      {hasAllDay ? (
        <View style={{ flexDirection: 'row', borderBottomWidth: 1, ...line }} testID="all-day">
          <Text style={{ width: GUTTER, fontSize: 10, color: t.colors.textMuted, paddingTop: 4, textAlign: 'center' }}>all-day</Text>
          {allDay.map((list, i) => (
            <View key={days[i]} style={{ flex: 1, paddingVertical: 2, paddingHorizontal: 1, gap: 2 }}>
              {list.map((o) => (
                <Pressable key={o.key} onPress={() => onOpen(o)} disabled={!o.event} testID={`allday-${o.key}-${days[i]}`}>
                  <Text
                    numberOfLines={1}
                    style={{ fontSize: 12, color: t.colors.onTint, backgroundColor: o.birthday ? t.colors.warning : t.colors.tint, borderRadius: 4, paddingHorizontal: 4, overflow: 'hidden' }}
                  >
                    {o.title}
                  </Text>
                </Pressable>
              ))}
            </View>
          ))}
        </View>
      ) : null}
      <ScrollView ref={scroll} style={{ flex: 1 }} testID="time-grid">
        <View style={{ flexDirection: 'row', height: 24 * HOUR }}>
          <View style={{ width: GUTTER }}>
            {Array.from({ length: 24 }, (_, h) => (
              <Text key={h} style={{ position: 'absolute', top: h * HOUR - 7, right: 6, fontSize: 10, color: t.colors.textMuted }}>
                {h === 0 ? '' : hourLabel(h)}
              </Text>
            ))}
          </View>
          {days.map((d) => (
            <View key={d} style={{ flex: 1, borderLeftWidth: 1, ...line }} testID={`column-${d}`}>
              {Array.from({ length: 24 }, (_, h) => (
                <Pressable
                  key={h}
                  disabled={!onSlot}
                  onPress={() => onSlot?.(d, h)}
                  accessibilityLabel={`${d} ${hourLabel(h)}`}
                  testID={`slot-${d}-${h}`}
                  style={{ position: 'absolute', top: h * HOUR, left: 0, right: 0, height: HOUR, borderTopWidth: 1, ...line }}
                />
              ))}
              {dayBlocks(occurrences, d).map((b) => (
                <Pressable
                  key={b.o.key}
                  onPress={() => onOpen(b.o)}
                  testID={`block-${b.o.key}-${d}`}
                  style={{
                    position: 'absolute',
                    top: (b.top / 60) * HOUR + 1,
                    height: ((b.bottom - b.top) / 60) * HOUR - 2,
                    left: `${(b.column / b.columns) * 100}%`,
                    width: `${100 / b.columns}%`,
                    paddingHorizontal: 1,
                  }}
                >
                  <View style={{ flex: 1, borderRadius: 4, backgroundColor: t.colors.tint, paddingHorizontal: 3, paddingVertical: 1, overflow: 'hidden' }}>
                    <Text numberOfLines={2} style={{ fontSize: 11, fontWeight: '600', color: t.colors.onTint }}>
                      {b.o.title}
                    </Text>
                    {b.bottom - b.top >= 45 && b.o.startDate === d ? (
                      <Text numberOfLines={1} style={{ fontSize: 10, color: t.colors.onTint }}>
                        {timeLabel(b.o.startTime!)}
                      </Text>
                    ) : null}
                  </View>
                </Pressable>
              ))}
              {d === today ? (
                <View pointerEvents="none" style={{ position: 'absolute', top: (now / 60) * HOUR, left: 0, right: 0, height: 2, backgroundColor: t.colors.danger }} testID="now-line" />
              ) : null}
            </View>
          ))}
        </View>
      </ScrollView>
    </View>
  );
}
