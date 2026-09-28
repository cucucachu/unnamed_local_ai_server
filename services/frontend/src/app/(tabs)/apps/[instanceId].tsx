import { Stack, useLocalSearchParams } from 'expo-router';
import { useCallback } from 'react';
import { StyleSheet, View } from 'react-native';

import { AppRunner } from '@/components/AppRunner';
import { LoadState } from '@/components/SettingsUI';
import { findInstance } from '@/lib/apps';
import { theme } from '@/lib/theme';
import { useLoad } from '@/lib/useAsync';

/**
 * The runner for one installed instance. The instance id comes from the
 * route and is fixed for the runner's lifetime; the user's role in the
 * instance's space (from the platform, not the link) decides read-only.
 */
export default function AppRunnerScreen() {
  const params = useLocalSearchParams<{ instanceId: string }>();
  const instanceId = Array.isArray(params.instanceId) ? params.instanceId[0] : params.instanceId;
  const load = useCallback(() => findInstance(instanceId), [instanceId]);
  const { data, error, reload } = useLoad(load);

  return (
    <View style={styles.container}>
      <Stack.Screen options={{ title: data?.instance.app.name ?? 'App' }} />
      {data ? (
        <AppRunner key={data.instance.id} instanceId={data.instance.id} space={data.space} />
      ) : (
        <LoadState error={error} onRetry={reload} />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: theme.bg,
  },
});
