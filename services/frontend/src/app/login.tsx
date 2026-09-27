import { useState } from 'react';

import { useAuth } from '@/components/AuthProvider';
import { LoginScreen } from '@/components/LoginScreen';
import { SetupScreen } from '@/components/SetupScreen';

/**
 * The signed-out entry route (guarded in `_layout.tsx` to exist only while
 * signed out, so Expo Router lands every signed-out visit here). Shows
 * Setup while bootstrap is open — with a way to sign in instead, since
 * recovery-CLI accounts can log in before setup is done — else Login.
 */
export default function LoginRoute() {
  const { state } = useAuth();
  const setupRequired = state.phase === 'ready' && state.setupRequired;
  const [preferLogin, setPreferLogin] = useState(false);

  if (setupRequired && !preferLogin) {
    return <SetupScreen onShowLogin={() => setPreferLogin(true)} />;
  }
  return <LoginScreen onShowSetup={setupRequired ? () => setPreferLogin(false) : undefined} />;
}
