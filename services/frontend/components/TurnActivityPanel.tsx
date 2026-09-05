import Ionicons from '@expo/vector-icons/Ionicons';
import { useState, type ReactElement, type ReactNode } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';

import { finishedHeaderLabel, runningStatusLine, type ChatTurn } from '@/lib/chatTurns';
import { theme } from '@/lib/theme';

export interface TurnActivityPanelProps {
  turn: ChatTurn;
  children?: ReactNode;
}

/**
 * M9-02 activity panel. Collapsed by default; expansion is local state and
 * persists for this mount (one panel per turn id).
 *
 * Running: spinner + status line. Finished with activity: "Worked for Xs ▸"
 * (or Stopped/Failed). The parent skips this component when a completed
 * turn has empty activity (small duration caption instead).
 */
export function TurnActivityPanel({ turn, children }: TurnActivityPanelProps): ReactElement {
  const [expanded, setExpanded] = useState(false);
  const running = turn.status === 'running';
  const statusLine = runningStatusLine(turn.activity);
  const header = running ? statusLine : finishedHeaderLabel(turn.status, turn.durationMs);

  return (
    <View
      style={styles.panel}
      testID="turn-activity-panel"
      accessibilityState={{ expanded, busy: running }}
    >
      <Pressable
        style={styles.header}
        onPress={() => setExpanded((prev) => !prev)}
        accessibilityRole="button"
        accessibilityLabel={header}
        testID="turn-activity-header"
      >
        {running ? (
          <ActivityIndicator size="small" color={theme.accent} testID="turn-activity-spinner" />
        ) : null}
        <Text
          style={[styles.headerText, turn.status === 'cancelled' && styles.stoppedText]}
          testID={
            turn.status === 'cancelled'
              ? 'chat-item-stopped-caption'
              : running
                ? 'turn-activity-status'
                : 'turn-activity-duration'
          }
        >
          {header}
        </Text>
        <Ionicons
          name={expanded ? 'chevron-down' : 'chevron-forward'}
          size={14}
          color={theme.textMuted}
          style={styles.chevron}
        />
      </Pressable>
      {expanded ? <View style={styles.body}>{children}</View> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  panel: {
    alignSelf: 'flex-start',
    maxWidth: '85%',
    backgroundColor: theme.surface,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: theme.border,
    overflow: 'hidden',
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    paddingHorizontal: 10,
    paddingVertical: 8,
  },
  headerText: {
    color: theme.textMuted,
    fontSize: 13,
    flexShrink: 1,
  },
  stoppedText: {
    fontStyle: 'italic',
  },
  chevron: {
    marginLeft: 'auto',
  },
  body: {
    borderTopWidth: 1,
    borderTopColor: theme.border,
    paddingHorizontal: 8,
    paddingVertical: 8,
    gap: 8,
  },
});
