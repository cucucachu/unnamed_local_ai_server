import Ionicons from '@expo/vector-icons/Ionicons';
import { Stack, useLocalSearchParams, useRouter } from 'expo-router';
import { useCallback } from 'react';
import { Pressable, StyleSheet, View } from 'react-native';

import { AppRunner } from '@/components/AppRunner';
import { LoadState } from '@/components/SettingsUI';
import { findInstance } from '@/lib/apps';
import { theme } from '@/lib/theme';
import { useLoad } from '@/lib/useAsync';

/**
 * The runner for one installed instance. The instance id comes from the
 * route and is fixed for the runner's lifetime; the user's role in the
 * instance's space (from the platform, not the link) decides read-only.
 * The header's info button opens the app's info and history.
 */
export default function AppRunnerScreen() {
  const router = useRouter();
  const params = useLocalSearchParams<{ instanceId: string }>();
  const instanceId = Array.isArray(params.instanceId) ? params.instanceId[0] : params.instanceId;
  const load = useCallback(() => findInstance(instanceId), [instanceId]);
  const { data, error, reload } = useLoad(load);
  const appId = data?.instance.app_id;

  return (
    <View style={styles.container}>
      <Stack.Screen
        options={{
          title: data?.instance.app.name ?? 'App',
          headerRight: appId
            ? () => (
                <Pressable
                  onPress={() => router.push({ pathname: '/apps/info/[appId]', params: { appId } })}
                  accessibilityRole="button"
                  accessibilityLabel="App info"
                  testID="app-info-button"
                  style={styles.headerButton}
                >
                  <Ionicons name="information-circle-outline" size={24} color={theme.text} />
                </Pressable>
              )
            : undefined,
        }}
      />
      {data ? (
        <AppRunner
          key={data.instance.id}
          instanceId={data.instance.id}
          space={data.space}
          appId={data.instance.app_id}
          appName={data.instance.app.name}
        />
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
  headerButton: {
    padding: 4,
  },
});
