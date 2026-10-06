import { useFocusEffect, useLocalSearchParams, useRouter } from 'expo-router';
import { useCallback, useRef } from 'react';
import { Alert, Platform, Pressable, StyleSheet, Text, View } from 'react-native';

import { RoutineRunList } from '@/components/RoutineRunList';
import {
  ActionButton,
  Card,
  LoadState,
  SectionTitle,
  SettingsFrame,
  settingsStyles,
} from '@/components/SettingsUI';
import { Toast, useToast } from '@/components/Toast';
import { openChat } from '@/lib/currentChat';
import { listSpaces, type Space } from '@/lib/platform';
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

const RECENT_RUNS = 5;

/** Routines → one routine (M17-06, M17-09): what it does and when, Run now,
 * edit, delete, and its last few runs (More… for all); a run opens its chat. */
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

  const openRun = (threadId: string) => {
    openChat(threadId);
    router.navigate('/chat');
  };

  async function handleRunNow() {
    const started: { run?: RoutineRun } = {};
    const ok = await run('run-now', async () => {
      started.run = await runRoutineNow(routineId);
    });
    if (!ok) return;
    reload();
    if (started.run?.thread_id) openRun(started.run.thread_id);
  }

  async function handleDelete(routine: Routine) {
    if (!(await confirmDelete(routine))) return;
    const ok = await run('delete', () => deleteRoutine(routine.id));
    if (ok) router.back();
  }

  const routine = data?.routine ?? null;
  return (
    <View style={styles.container}>
      <SettingsFrame title={routine?.name ?? 'Routine'} backTo="/routines" testID="routine-screen">
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
                onPress={() => router.push({ pathname: '/routines/edit', params: { routineId } })}
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

            <SectionTitle>Recent runs</SectionTitle>
            <RoutineRunList runs={data.runs.slice(0, RECENT_RUNS)} onOpen={openRun} />
            {data.runs.length > RECENT_RUNS ? (
              <Pressable
                onPress={() => router.push({ pathname: '/routines/[routineId]/runs', params: { routineId } })}
                style={styles.more}
                accessibilityRole="link"
                testID="routine-runs-more"
              >
                <Text style={styles.moreText}>More…</Text>
              </Pressable>
            ) : null}
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
  more: {
    alignSelf: 'flex-start',
    paddingVertical: 8,
  },
  moreText: {
    color: theme.accent,
    fontSize: 15,
  },
});
