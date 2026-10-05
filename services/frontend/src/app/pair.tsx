import { useLocalSearchParams, useRouter } from 'expo-router';
import { useEffect, useRef } from 'react';

import { useAuth } from '@/components/AuthProvider';
import { LoginScreen } from '@/components/LoginScreen';
import { PAIR_LINK_PREFIX } from '@/lib/auth';

/**
 * `homeai://pair?token=hd_…&challenge=…` — what the Settings → Remote access
 * pairing QR encodes, so scanning it with the phone camera opens the host
 * app here. Shows the login screen's pairing form pre-filled; pairing still
 * needs a tap plus the biometric unlock. Reachable signed in or out (like
 * `invite`); once a pairing signs in, goes home.
 */
export default function PairRoute() {
  const router = useRouter();
  const { token, challenge } = useLocalSearchParams<{ token?: string; challenge?: string }>();
  const { state } = useAuth();
  const user = state.phase === 'ready' ? state.user : null;
  const userAtOpen = useRef(user);

  useEffect(() => {
    if (user && user !== userAtOpen.current) router.replace('/');
  }, [user, router]);

  const payload =
    token && challenge
      ? `${PAIR_LINK_PREFIX}token=${encodeURIComponent(token)}&challenge=${encodeURIComponent(challenge)}`
      : '';
  return <LoginScreen pairPayload={payload} />;
}
