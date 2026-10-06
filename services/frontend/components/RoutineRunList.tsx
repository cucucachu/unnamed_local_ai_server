import { Pressable, Text, View } from 'react-native';

import { Badge, Card, settingsStyles } from '@/components/SettingsUI';
import { runStatusLabel, type RunStatus } from '@/lib/inbox';
import { relativeTime } from '@/lib/relativeTime';
import type { RoutineRun } from '@/lib/routines';

function tone(status: RunStatus): 'accent' | 'danger' | 'muted' {
  if (status === 'waiting_approval' || status === 'running' || status === 'queued') return 'accent';
  return status === 'succeeded' ? 'muted' : 'danger';
}

function runTime(run: RoutineRun): string {
  const at = run.finished_at ?? run.started_at ?? run.created_at;
  return `${run.trigger === 'manual' ? 'Run now' : 'Scheduled'} · ${relativeTime(at)}`;
}

/** A routine's runs, newest first (M17-06); one with a thread opens its chat. */
export function RoutineRunList({ runs, onOpen }: { runs: RoutineRun[]; onOpen: (threadId: string) => void }) {
  if (runs.length === 0) {
    return (
      <Text style={settingsStyles.muted} testID="routine-runs-empty">
        No runs yet.
      </Text>
    );
  }
  return (
    <Card testID="routine-runs">
      {runs.map((item, index) => (
        <Pressable
          key={item.id}
          onPress={item.thread_id ? () => onOpen(item.thread_id as string) : undefined}
          disabled={!item.thread_id}
          style={[settingsStyles.row, index === 0 && settingsStyles.firstRow]}
          accessibilityRole="button"
          testID={`routine-run-${item.id}`}
        >
          <View style={settingsStyles.rowMain}>
            <Text style={settingsStyles.muted}>{runTime(item)}</Text>
            {item.detail ? (
              <Text style={settingsStyles.muted} numberOfLines={2}>
                {item.detail}
              </Text>
            ) : null}
          </View>
          <Badge label={runStatusLabel(item.status)} tone={tone(item.status)} testID={`routine-run-status-${item.id}`} />
        </Pressable>
      ))}
    </Card>
  );
}
