import { useLocalSearchParams, useRouter } from 'expo-router';
import { useCallback, useState } from 'react';
import { StyleSheet, View } from 'react-native';

import { RoutineForm } from '@/components/RoutineForm';
import { LoadState, SettingsFrame } from '@/components/SettingsUI';
import { listSpaces, platformErrorMessage, type Space } from '@/lib/platform';
import { createRoutine, getRoutine, updateRoutine, type Routine, type RoutineInput } from '@/lib/routines';
import { useLoad } from '@/lib/useAsync';

/** Only what changed, so an unchanged space isn't checked (or moved) again. */
function changes(routine: Routine, input: RoutineInput): Partial<RoutineInput> {
  const changed: Partial<RoutineInput> = { schedule: input.schedule };
  for (const key of ['name', 'prompt', 'space', 'timezone', 'enabled', 'approval_mode'] as const) {
    if (input[key] !== routine[key]) (changed as Record<string, unknown>)[key] = input[key];
  }
  return changed;
}

/** Settings → Routines → new or edit (`?routineId=`) (M17-06). */
export default function RoutineEditScreen() {
  const { routineId } = useLocalSearchParams<{ routineId?: string }>();
  const router = useRouter();
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (): Promise<{ routine: Routine | null; spaces: Space[] }> => {
    const [routine, spaces] = await Promise.all([routineId ? getRoutine(routineId) : null, listSpaces()]);
    return { routine, spaces };
  }, [routineId]);
  const { data, error: loadError, reload } = useLoad(load);

  async function submit(input: RoutineInput) {
    setSaving(true);
    setError(null);
    try {
      const routine = data?.routine;
      if (routine) {
        await updateRoutine(routine.id, changes(routine, input));
        router.back();
      } else {
        const created = await createRoutine(input);
        router.replace({ pathname: '/settings/routines/[routineId]', params: { routineId: created.id } });
      }
    } catch (caught) {
      setError(platformErrorMessage(caught));
    } finally {
      setSaving(false);
    }
  }

  return (
    <View style={styles.container}>
      <SettingsFrame title={routineId ? 'Edit routine' : 'New routine'} testID="settings-routine-edit-screen">
        {data === null ? (
          <LoadState error={loadError} onRetry={reload} />
        ) : (
          <RoutineForm routine={data.routine} spaces={data.spaces} saving={saving} error={error} onSubmit={submit} />
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
