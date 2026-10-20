import { mountSandboxFrame } from '@homeai/sdk/host/web';
import { useEffect, useRef } from 'react';
import { View } from 'react-native';

import { instanceForward, type AppSandboxProps } from '@/lib/appHost';
import { themedStyles } from '@/lib/theme';

/**
 * Web app sandbox: `@homeai/sdk/host/web`'s `<iframe sandbox="allow-scripts"
 * srcdoc>` (opaque origin, CSP'd document), mounted into this View's DOM
 * node. The library checks `event.source` and removes a frame that
 * navigates itself (`onKilled`).
 */
export function AppSandbox({ instanceId, html, readOnly, onEvent, onHost, onKilled, onAskAgent }: AppSandboxProps) {
  const styles = useStyles();
  const container = useRef<View>(null);

  useEffect(() => {
    const element = container.current as unknown as HTMLElement | null;
    if (!element) return;
    const sandbox = mountSandboxFrame(element, {
      html,
      forward: instanceForward(instanceId),
      readOnly,
      onEvent,
      onAskAgent,
      onKilled: () => {
        onHost(null);
        onKilled();
      },
    });
    onHost(sandbox.host);
    return () => {
      sandbox.destroy();
      onHost(null);
    };
  }, [instanceId, html, readOnly, onEvent, onAskAgent, onHost, onKilled]);

  return <View ref={container} testID="app-sandbox" style={styles.container} />;
}

// The app paints its own background; this only shows until it does.
const useStyles = themedStyles((theme) => ({
  container: { flex: 1, backgroundColor: theme.bg },
}));
