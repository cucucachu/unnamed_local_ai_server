import { useFocusEffect, useLocalSearchParams, useRouter } from 'expo-router';
import { useCallback, useRef } from 'react';
import { StyleSheet, View } from 'react-native';

import { RoutineRunList } from '@/components/RoutineRunList';
import { LoadState, SettingsFrame } from '@/components/SettingsUI';
import { getRoutine, listRoutineRuns, type Routine, type RoutineRun } from '@/lib/routines';
import { useLoad } from '@/lib/useAsync';

/** Routines → a routine → More… (M17-09): every run, newest first. */
export default function RoutineRunsScreen() {
  const { routineId } = useLocalSearchParams<{ routineId: string }>();
  const router = useRouter();
  const load = useCallback(async (): Promise<{ routine: Routine; runs: RoutineRun[] }> => {
    const [routine, runs] = await Promise.all([getRoutine(routineId), listRoutineRuns(routineId)]);
    return { routine, runs };
  }, [routineId]);
  const { data, error, reload } = useLoad(load);

  const focused = useRef(false);
  useFocusEffect(
    useCallback(() => {
      if (focused.current) reload();
      focused.current = true;
    }, [reload]),
  );

  return (
    <View style={styles.container}>
      <SettingsFrame
        title={data ? `${data.routine.name}: runs` : 'Runs'}
        backTo={{ pathname: '/routines/[routineId]', params: { routineId } }}
        testID="routine-runs-screen"
      >
        {data === null ? (
          <LoadState error={error} onRetry={reload} />
        ) : (
          <RoutineRunList
            runs={data.runs}
            onOpen={(threadId) => router.push({ pathname: '/chat/[threadId]', params: { threadId } })}
          />
        )}
      </SettingsFrame>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
  },
});
