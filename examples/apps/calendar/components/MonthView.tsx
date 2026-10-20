import { Pressable, Text, View } from 'react-native';
import { useTheme } from '@homeai/ui';
import { WEEKDAYS, daysBetween, onDay, timeLabel, viewRange, type Occurrence } from '../lib/calendar';

/** How many items a day cell lists before "+N more". */
const SHOWN = 3;

/** The weeks covering `day`'s month; tap a day to open it. */
export function MonthView({ day, today, occurrences, onDay: open }: { day: string; today: string; occurrences: Occurrence[]; onDay: (day: string) => void }) {
  const t = useTheme();
  const { from, to } = viewRange('month', day);
  const days = daysBetween(from, to);
  const month = day.slice(0, 7);
  const weeks: string[][] = [];
  for (let i = 0; i < days.length; i += 7) weeks.push(days.slice(i, i + 7));

  return (
    <View style={{ flex: 1 }} testID="month-grid">
      <View style={{ flexDirection: 'row', borderBottomWidth: 1, borderColor: t.colors.border }}>
        {WEEKDAYS.map((w) => (
          <Text key={w} style={{ flex: 1, textAlign: 'center', color: t.colors.textMuted, fontSize: t.font.small, paddingVertical: 4 }}>
            {w.slice(0, 3)}
          </Text>
        ))}
      </View>
      {weeks.map((week) => (
        <View key={week[0]} style={{ flex: 1, flexDirection: 'row', minHeight: 72 }}>
          {week.map((d) => {
            const items = occurrences.filter((o) => onDay(o, d));
            const isToday = d === today;
            const inMonth = d.startsWith(month);
            return (
              <Pressable
                key={d}
                onPress={() => open(d)}
                accessibilityLabel={d}
                testID={`month-day-${d}`}
                style={{ flex: 1, borderRightWidth: 1, borderBottomWidth: 1, borderColor: t.colors.border, padding: 2, overflow: 'hidden' }}
              >
                <View
                  style={{
                    alignSelf: 'center',
                    minWidth: 22,
                    borderRadius: 11,
                    paddingHorizontal: 4,
                    backgroundColor: isToday ? t.colors.tint : 'transparent',
                  }}
                >
                  <Text style={{ textAlign: 'center', fontSize: t.font.small, color: isToday ? t.colors.onTint : inMonth ? t.colors.text : t.colors.inactive }}>
                    {Number(d.slice(8))}
                  </Text>
                </View>
                {items.slice(0, SHOWN).map((o) => (
                  <Text
                    key={o.key}
                    numberOfLines={1}
                    testID={`month-item-${o.key}`}
                    style={{
                      fontSize: 11,
                      marginTop: 1,
                      paddingHorizontal: 2,
                      borderRadius: 3,
                      overflow: 'hidden',
                      color: o.allDay ? t.colors.onTint : t.colors.text,
                      backgroundColor: o.allDay ? (o.birthday ? t.colors.warning : t.colors.tint) : 'transparent',
                    }}
                  >
                    {!o.allDay && o.startDate === d ? `${timeLabel(o.startTime!).replace(':00', '').replace(' ', '').toLowerCase()} ` : ''}
                    {o.title}
                  </Text>
                ))}
                {items.length > SHOWN ? (
                  <Text style={{ fontSize: 11, color: t.colors.textMuted }} testID={`month-more-${d}`}>
                    +{items.length - SHOWN} more
                  </Text>
                ) : null}
              </Pressable>
            );
          })}
        </View>
      ))}
    </View>
  );
}
