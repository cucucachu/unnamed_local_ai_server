import { useEffect, useMemo, useRef } from 'react';
import { StyleSheet } from 'react-native';
import { WebView, type WebViewMessageEvent } from 'react-native-webview';
import type { ShouldStartLoadRequest } from 'react-native-webview/lib/WebViewTypes';

import { injectScriptFor, type BridgeHost } from '@homeai/sdk/host';

import { instanceBridge, type AppSandboxProps } from '@/lib/appHost';

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
export function AppSandbox({ instanceId, html, readOnly, onEvent, onHost }: AppSandboxProps) {
  const webview = useRef<WebView>(null);
  const host = useRef<BridgeHost | null>(null);

  useEffect(() => {
    const bridge = instanceBridge(instanceId, {
      send: (wire) => webview.current?.injectJavaScript(injectScriptFor(wire)),
      readOnly,
      onEvent,
    });
    host.current = bridge;
    onHost(bridge);
    return () => {
      bridge.close();
      host.current = null;
      onHost(null);
    };
  }, [instanceId, html, readOnly, onEvent, onHost]);

  const source = useMemo(() => ({ html }), [html]);

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
      style={styles.webview}
    />
  );
}

const styles = StyleSheet.create({
  webview: { flex: 1, backgroundColor: '#ffffff' },
});
