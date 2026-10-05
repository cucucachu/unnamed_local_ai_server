import { Stack } from 'expo-router';

import { useAuth } from '@/components/AuthProvider';
import { StepUpProvider } from '@/components/StepUpProvider';
import { theme } from '@/lib/theme';

/** The Settings tab's own stack: the hub (`index`) plus its sub-screens.
 * Admin screens exist only for admins; the rest of the guard (signed in)
 * is the root layout's. */
export default function SettingsLayout() {
  const { state } = useAuth();
  const isAdmin = state.phase === 'ready' && state.user?.role === 'admin';

  return (
    <StepUpProvider>
      <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: theme.bg } }}>
        <Stack.Screen name="index" />
        <Stack.Screen name="account" />
        <Stack.Screen name="sessions" />
        <Stack.Screen name="remote" />
        <Stack.Screen name="spaces/index" />
        <Stack.Screen name="spaces/[spaceId]" />
        <Stack.Screen name="routines/index" />
        <Stack.Screen name="routines/edit" />
        <Stack.Screen name="routines/[routineId]" />
        <Stack.Protected guard={isAdmin}>
          <Stack.Screen name="users" />
          <Stack.Screen name="invites" />
        </Stack.Protected>
      </Stack>
    </StepUpProvider>
  );
}
