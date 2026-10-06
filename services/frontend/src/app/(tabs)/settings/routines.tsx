import { Redirect } from 'expo-router';

/** Routines moved to their own app (M17-09); old links still land there. */
export default function SettingsRoutinesRedirect() {
  return <Redirect href="/routines" />;
}
