import { useFocusEffect, useLocalSearchParams, useRouter } from 'expo-router';
import { useCallback, useRef } from 'react';
import { Alert, Platform, Pressable, StyleSheet, Text, View } from 'react-native';

import {
  ActionButton,
  Badge,
  Card,
  LoadState,
  SectionTitle,
  SettingsFrame,
  settingsStyles,
} from '@/components/SettingsUI';
import { Toast, useToast } from '@/components/Toast';
import { runStatusLabel, type RunStatus } from '@/lib/inbox';
import { listSpaces, type Space } from '@/lib/platform';
import { relativeTime } from '@/lib/relativeTime';
import { theme } from '@/lib/theme';
import {
  APPROVAL_MODE_LABELS,
  deleteRoutine,
  getRoutine,
  listRoutineRuns,
  nextRunLabel,
  runRoutineNow,
  scheduleSummary,
  spaceLabel,
  type Routine,
  type RoutineRun,
} from '@/lib/routines';
import { useAction, useLoad } from '@/lib/useAsync';

/** Same Alert (native) / window.confirm (web) split as files.tsx: RN's
 * Alert.alert has no web implementation. */
function confirmDelete(routine: Routine): Promise<boolean> {
  const message = `Delete "${routine.name}"? It won't run again. Its past runs stay in your chats.`;
  if (Platform.OS === 'web') {
    return Promise.resolve(window.confirm(message));
  }
  return new Promise((resolve) => {
    Alert.alert('Delete routine', message, [
      { text: 'Cancel', style: 'cancel', onPress: () => resolve(false) },
      { text: 'Delete', style: 'destructive', onPress: () => resolve(true) },
    ]);
  });
}

function tone(status: RunStatus): 'accent' | 'danger' | 'muted' {
  if (status === 'waiting_approval' || status === 'running' || status === 'queued') return 'accent';
  return status === 'succeeded' ? 'muted' : 'danger';
}

function runTime(run: RoutineRun): string {
  const at = run.finished_at ?? run.started_at ?? run.created_at;
  return `${run.trigger === 'manual' ? 'Run now' : 'Scheduled'} · ${relativeTime(at)}`;
}

/** Settings → Routines → one routine (M17-06): what it does and when, Run
 * now, edit, delete, and every run - tapping one opens its chat. */
export default function RoutineDetailScreen() {
  const { routineId } = useLocalSearchParams<{ routineId: string }>();
  const router = useRouter();
  const { message: toast, showToast } = useToast();
  const { busyKey, run } = useAction(showToast);

  const load = useCallback(async (): Promise<{ routine: Routine; runs: RoutineRun[]; spaces: Space[] }> => {
    const [routine, runs, spaces] = await Promise.all([
      getRoutine(routineId),
      listRoutineRuns(routineId),
      listSpaces().catch(() => [] as Space[]),
    ]);
    return { routine, runs, spaces };
  }, [routineId]);
  const { data, error, reload } = useLoad(load);

  const focused = useRef(false);
  useFocusEffect(
    useCallback(() => {
      if (focused.current) reload();
      focused.current = true;
    }, [reload]),
  );

  const openChat = (threadId: string) => router.push({ pathname: '/chat/[threadId]', params: { threadId } });

  async function handleRunNow() {
    const started: { run?: RoutineRun } = {};
    const ok = await run('run-now', async () => {
      started.run = await runRoutineNow(routineId);
    });
    if (!ok) return;
    reload();
    if (started.run?.thread_id) openChat(started.run.thread_id);
  }

  async function handleDelete(routine: Routine) {
    if (!(await confirmDelete(routine))) return;
    const ok = await run('delete', () => deleteRoutine(routine.id));
    if (ok) router.back();
  }

  const routine = data?.routine ?? null;
  return (
    <View style={styles.container}>
      <SettingsFrame title={routine?.name ?? 'Routine'} testID="settings-routine-screen">
        {data === null || routine === null ? (
          <LoadState error={error} onRetry={reload} />
        ) : (
          <>
            <Card>
              <View style={settingsStyles.cardBody}>
                <Text style={settingsStyles.rowTitle} testID="routine-detail-summary">
                  {scheduleSummary(routine.schedule)}
                </Text>
                <Text style={settingsStyles.muted}>
                  {routine.timezone} · {spaceLabel(routine.space, data.spaces)} ·{' '}
                  {APPROVAL_MODE_LABELS[routine.approval_mode]}
                </Text>
                <Text style={settingsStyles.muted} testID="routine-detail-next">
                  Next: {nextRunLabel(routine)}
                </Text>
                <Text style={styles.prompt} testID="routine-detail-prompt">
                  {routine.prompt}
                </Text>
              </View>
            </Card>

            <View style={settingsStyles.rowActions}>
              <ActionButton
                label="Run now"
                variant="primary"
                onPress={handleRunNow}
                busy={busyKey === 'run-now'}
                disabled={busyKey !== null}
                testID="routine-run-now"
              />
              <ActionButton
                label="Edit"
                onPress={() => router.push({ pathname: '/settings/routines/edit', params: { routineId } })}
                disabled={busyKey !== null}
                testID="routine-edit"
              />
              <ActionButton
                label="Delete"
                variant="danger"
                onPress={() => handleDelete(routine)}
                busy={busyKey === 'delete'}
                disabled={busyKey !== null}
                testID="routine-delete"
              />
            </View>

            <SectionTitle>Runs</SectionTitle>
            {data.runs.length === 0 ? (
              <Text style={settingsStyles.muted} testID="routine-runs-empty">
                No runs yet.
              </Text>
            ) : (
              <Card testID="routine-runs">
                {data.runs.map((item, index) => (
                  <Pressable
                    key={item.id}
                    onPress={item.thread_id ? () => openChat(item.thread_id as string) : undefined}
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
            )}
          </>
        )}
      </SettingsFrame>
      <Toast message={toast} testID="routine-toast" />
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
  },
  prompt: {
    color: theme.text,
    fontSize: 14,
  },
});
