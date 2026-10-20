import { useEffect, useMemo, useRef, useState } from 'react';
import { WebView, type WebViewMessageEvent } from 'react-native-webview';
import type { ShouldStartLoadRequest } from 'react-native-webview/lib/WebViewTypes';

import { injectScriptFor, type BridgeHost } from '@homeai/sdk/host';

import { instanceBridge, type AppSandboxProps } from '@/lib/appHost';
import { themedStyles } from '@/lib/theme';

/** Only the initial `source={{ html }}` document (`about:blank`) may load. */
export function allowSandboxLoad(request: Pick<ShouldStartLoadRequest, 'url'>): boolean {
  return request.url.startsWith('about:');
}

/**
 * Native app sandbox: `react-native-webview` with the M12-01 spike's settings
 * (`docs/PLATFORM.md` §7 "Runtime and bridge"). Every origin passes the
 * whitelist so no URL is handed to `Linking.openURL`, and
 * `onShouldStartLoadWithRequest` then refuses every navigation but the
 * document itself, before any request, so unlike the web frame the sandbox
 * never needs killing. Host -> sandbox is `injectJavaScript` (WebView's own
 * `postMessage` dispatches on `document` on Android but `window` on iOS).
 */
export function AppSandbox({ instanceId, html, readOnly, onEvent, onHost, onAskAgent }: AppSandboxProps) {
  const styles = useStyles();
  const webview = useRef<WebView>(null);
  const host = useRef<BridgeHost | null>(null);

  useEffect(() => {
    const bridge = instanceBridge(instanceId, {
      send: (wire) => webview.current?.injectJavaScript(injectScriptFor(wire)),
      readOnly,
      onEvent,
      onAskAgent,
    });
    host.current = bridge;
    onHost(bridge);
    return () => {
      bridge.close();
      host.current = null;
      onHost(null);
    };
  }, [instanceId, html, readOnly, onEvent, onAskAgent, onHost]);

  const source = useMemo(() => ({ html }), [html]);
  const [loadedHtml, setLoadedHtml] = useState<string | null>(null);

  return (
    <WebView
      ref={webview}
      testID="app-sandbox"
      source={source}
      onMessage={(event: WebViewMessageEvent) => host.current?.receive(event.nativeEvent.data)}
      originWhitelist={['*']}
      onShouldStartLoadWithRequest={allowSandboxLoad}
      setSupportMultipleWindows={false}
      javaScriptCanOpenWindowsAutomatically={false}
      domStorageEnabled={false}
      incognito
      cacheEnabled={false}
      thirdPartyCookiesEnabled={false}
      sharedCookiesEnabled={false}
      allowFileAccess={false}
      allowFileAccessFromFileURLs={false}
      allowUniversalAccessFromFileURLs={false}
      mixedContentMode="never"
      webviewDebuggingEnabled={__DEV__}
      onLoadEnd={() => setLoadedHtml(html)}
      style={[styles.webview, loadedHtml !== html && styles.loading]}
      containerStyle={styles.container}
    />
  );
}

// Android's WebView paints white before the document's own background, so it
// stays transparent over the themed container until the document has loaded.
const useStyles = themedStyles((theme) => ({
  container: { flex: 1, backgroundColor: theme.bg },
  webview: { flex: 1, backgroundColor: theme.bg },
  loading: { opacity: 0 },
}));
