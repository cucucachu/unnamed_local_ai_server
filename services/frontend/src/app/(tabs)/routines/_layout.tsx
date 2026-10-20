import { Stack } from 'expo-router';

import { useTheme } from '@/lib/theme';

/** The Routines app (M17-09): the list, a routine, its runs, the editor.
 * Each screen draws its own header (`SettingsFrame`). */
export default function RoutinesLayout() {
  const theme = useTheme();
  return <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: theme.bg } }} />;
}
