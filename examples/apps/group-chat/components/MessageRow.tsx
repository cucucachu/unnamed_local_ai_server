import { Pressable, Text, View } from 'react-native';
import { useTheme } from '@homeai/ui';
import { dayLabel, timeLabel, type Message } from '../lib/chat';

/**
 * One message: a day separator above it when it starts a day, the sender's name when it starts
 * a run, then the bubble (yours on the right). `changedBy` is set when someone other than the
 * sender edited or deleted it.
 */
export function MessageRow({ message, mine, newDay, showSender, senderName, changedBy, selected, onPress }: {
  message: Message;
  mine: boolean;
  newDay: boolean;
  showSender: boolean;
  senderName: string;
  changedBy: string | null;
  selected: boolean;
  onPress?: () => void;
}) {
  const t = useTheme();
  const id = `message-${message.id}`;
  const fg = mine ? t.colors.onTint : t.colors.text;
  const by = changedBy ? ` by ${changedBy}` : '';
  return (
    <View testID={id}>
      {newDay ? (
        <Text style={{ alignSelf: 'center', color: t.colors.textMuted, fontSize: t.font.small, fontWeight: '600', marginVertical: t.spacing.md }} testID={`${id}-day`}>
          {dayLabel(message._created_at)}
        </Text>
      ) : null}
      {showSender && !mine ? (
        <Text style={{ color: t.colors.textMuted, fontSize: t.font.small, marginLeft: t.spacing.sm, marginTop: t.spacing.sm }} testID={`${id}-sender`}>
          {senderName}
        </Text>
      ) : null}
      <View style={{ alignItems: mine ? 'flex-end' : 'flex-start', marginTop: showSender ? 4 : 2 }}>
        <Pressable
          onPress={onPress}
          onLongPress={onPress}
          disabled={!onPress}
          accessibilityLabel={onPress ? 'Your message: edit or delete' : undefined}
          testID={`${id}-bubble`}
          style={{
            maxWidth: '80%',
            backgroundColor: message.deleted ? 'transparent' : mine ? t.colors.tint : t.colors.surface,
            borderRadius: t.radius.lg,
            borderWidth: message.deleted || selected ? 1 : 0,
            borderColor: selected ? t.colors.text : t.colors.border,
            paddingHorizontal: t.spacing.md,
            paddingVertical: t.spacing.sm,
            gap: 2,
          }}
        >
          {message.deleted ? (
            <Text style={{ color: t.colors.textMuted, fontStyle: 'italic' }} testID={`${id}-body`}>
              Message deleted{by}
            </Text>
          ) : (
            <Text selectable style={{ color: fg, fontSize: t.font.body }} testID={`${id}-body`}>
              {message.body}
            </Text>
          )}
          <Text style={{ alignSelf: 'flex-end', color: message.deleted ? t.colors.textMuted : fg, opacity: 0.75, fontSize: t.font.small }} testID={`${id}-meta`}>
            {timeLabel(message._created_at)}
            {message.edited && !message.deleted ? ` · edited${by}` : ''}
          </Text>
        </Pressable>
      </View>
    </View>
  );
}
