import { forwardRef, useImperativeHandle, useMemo, useRef } from 'react';
import { WebView } from 'react-native-webview';
import { injectScriptFor, makeHost, type Envelope, type RpcHandler } from './bridgeHost';

export type SandboxHandle = { event(event: string, data?: any): void; inject(js: string): void };
type Props = { html: string; rpc: RpcHandler; onEvent: (event: string, data: any) => void; onBlockedNavigation: (url: string) => void };

export const Sandbox = forwardRef<SandboxHandle, Props>(function Sandbox({ html, rpc, onEvent, onBlockedNavigation }, ref) {
  const web = useRef<WebView>(null);
  const host = useMemo(
    () =>
      makeHost(
        // injectJavaScript behaves the same on Android and iOS; WebView.postMessage
        // dispatches on `document` on Android but on `window` on iOS.
        (env: Envelope) => web.current?.injectJavaScript(injectScriptFor(env)),
        rpc,
        onEvent,
      ),
    [rpc, onEvent],
  );
  useImperativeHandle(ref, () => ({ event: host.event, inject: (js) => web.current?.injectJavaScript(js) }), [host]);
  const source = useMemo(() => ({ html }), [html]);
  return (
    <WebView
      ref={web}
      source={source}
      onMessage={(e) => host.receive(e.nativeEvent.data)}
      // Everything passes the whitelist so nothing is handed to Linking.openURL;
      // onShouldStartLoadWithRequest then refuses every navigation but the initial document.
      originWhitelist={['*']}
      onShouldStartLoadWithRequest={(req) => {
        const ok = req.url.startsWith('about:');
        if (!ok) onBlockedNavigation(req.url);
        return ok;
      }}
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
      style={{ flex: 1 }}
    />
  );
});
