import { useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useRef } from 'react';
import { Pressable, StyleSheet, Switch, Text, View } from 'react-native';

import { ActionButton, Badge, Card, LoadState, SettingsFrame, settingsStyles } from '@/components/SettingsUI';
import { Toast, useToast } from '@/components/Toast';
import { runStatusLabel } from '@/lib/inbox';
import { listRoutines, nextRunLabel, scheduleSummary, updateRoutine, type Routine } from '@/lib/routines';
import { useAction, useLoad } from '@/lib/useAsync';

/** The Routines app (M17-06, its own app since M17-09): each routine's
 * schedule in words, next run, last result and an on/off switch. Tapping one
 * opens its detail. */
export default function RoutinesScreen() {
  const router = useRouter();
  const { message: toast, showToast } = useToast();
  const { busyKey, run } = useAction(showToast);
  const { data, error, reload, setData } = useLoad(listRoutines);

  const focused = useRef(false);
  useFocusEffect(
    useCallback(() => {
      if (focused.current) reload();
      focused.current = true;
    }, [reload]),
  );

  async function toggle(routine: Routine, enabled: boolean) {
    await run(`toggle-${routine.id}`, async () => {
      const updated = await updateRoutine(routine.id, { enabled });
      setData((prev) => prev?.map((r) => (r.id === routine.id ? { ...updated, last_run: r.last_run } : r)) ?? prev);
    });
  }

  return (
    <View style={styles.container}>
      <SettingsFrame title="Routines" backTo="/apps" testID="routines-screen">
        {data === null ? (
          <LoadState error={error} onRetry={reload} />
        ) : (
          <>
            <ActionButton
              label="New routine"
              variant="primary"
              onPress={() => router.push('/routines/edit')}
              testID="routines-new"
            />
            {data.length === 0 ? (
              <Text style={settingsStyles.muted} testID="routines-empty">
                No routines yet. A routine is a prompt the agent runs for you on a schedule, like a morning
                summary of your notes.
              </Text>
            ) : (
              <Card testID="routines-list">
                {data.map((routine, index) => (
                  <Pressable
                    key={routine.id}
                    onPress={() =>
                      router.push({ pathname: '/routines/[routineId]', params: { routineId: routine.id } })
                    }
                    style={[settingsStyles.row, index === 0 && settingsStyles.firstRow]}
                    accessibilityRole="button"
                    accessibilityLabel={routine.name}
                    testID={`routine-row-${routine.id}`}
                  >
                    <View style={settingsStyles.rowMain}>
                      <Text style={settingsStyles.rowTitle} numberOfLines={1}>
                        {routine.name}
                      </Text>
                      <Text style={settingsStyles.muted} testID={`routine-summary-${routine.id}`}>
                        {scheduleSummary(routine.schedule)}
                      </Text>
                      <Text style={settingsStyles.muted} testID={`routine-next-${routine.id}`}>
                        Next: {nextRunLabel(routine)}
                      </Text>
                      {routine.last_run ? (
                        <View style={styles.lastRun}>
                          <Text style={settingsStyles.muted}>Last:</Text>
                          <Badge
                            label={runStatusLabel(routine.last_run.status)}
                            tone={routine.last_run.status === 'succeeded' ? 'muted' : 'danger'}
                            testID={`routine-last-${routine.id}`}
                          />
                        </View>
                      ) : null}
                    </View>
                    <Switch
                      value={routine.enabled}
                      onValueChange={(enabled) => toggle(routine, enabled)}
                      disabled={busyKey !== null}
                      accessibilityLabel={`${routine.name} on`}
                      testID={`routine-toggle-${routine.id}`}
                    />
                  </Pressable>
                ))}
              </Card>
            )}
          </>
        )}
      </SettingsFrame>
      <Toast message={toast} testID="routines-toast" />
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
  },
  lastRun: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    marginTop: 2,
  },
});
