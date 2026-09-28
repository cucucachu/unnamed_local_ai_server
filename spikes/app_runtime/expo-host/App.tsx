// M12-01 test screen: the sandboxed app runtime inside an iframe (web) or a
// react-native-webview WebView (Expo Go). Buttons run the bridge benchmark,
// push a hot-reload bundle, and run escape probes; the JSON panel at the
// bottom is what the maintainer copies back into the PR.
import { useCallback, useRef, useState } from 'react';
import { Button, Platform, ScrollView, StyleSheet, Text, View } from 'react-native';
import { SafeAreaProvider, SafeAreaView } from 'react-native-safe-area-context';
import { StatusBar } from 'expo-status-bar';
import { Sandbox, type SandboxHandle } from './Sandbox';
import { rpc } from './rpc';
import { stats } from './bridgeHost';
import { APP_V2, PROBES_JS, SANDBOX_HTML } from './sandboxAssets.generated';

type Report = Record<string, any>;

export default function App() {
  const sandbox = useRef<SandboxHandle>(null);
  const [report, setReport] = useState<Report>({ platform: Platform.OS, platformVersion: Platform.Version, htmlBytes: SANDBOX_HTML.length });
  const pending = useRef<Record<string, (data: any) => void>>({});
  const t0 = useRef(Date.now());
  const merge = (r: Report) => setReport((prev) => ({ ...prev, ...r }));

  const onEvent = useCallback((event: string, data: any) => {
    if (event === 'runtime.ready') merge({ [`ready_v${data.version}`]: { msSinceMount: Date.now() - t0.current, renderMs: +data.renderMs.toFixed(1) } });
    if (event === 'nav.changed') merge({ lastPath: data.path });
    if (event === 'runtime.error') merge({ lastError: data.message });
    const w = pending.current[event];
    if (w) {
      delete pending.current[event];
      w(data);
    }
  }, []);
  const onBlockedNavigation = useCallback((url: string) => setReport((prev) => ({ ...prev, blockedNavigations: [...(prev.blockedNavigations ?? []), url] })), []);
  const waitFor = (event: string) => new Promise<any>((resolve) => (pending.current[event] = resolve));

  async function bench() {
    for (const method of ['echo', 'db.getAll']) {
      sandbox.current?.event('bench.run', { n: 20, method });
      await waitFor('bench.result');
      sandbox.current?.event('bench.run', { n: 200, method });
      const { samples } = await waitFor('bench.result');
      merge({ [`latencyMs_${method}`]: stats(samples) });
    }
  }

  async function hotReload() {
    const start = Date.now();
    const ready = waitFor('runtime.ready');
    sandbox.current?.event('bundle.load', { code: APP_V2 });
    await ready;
    merge({ hotReloadMs: Date.now() - start });
  }

  async function probes() {
    const result = waitFor('probe.result');
    sandbox.current?.inject(PROBES_JS);
    const r = await result;
    merge({ probes: Object.fromEntries(Object.entries(r).map(([k, v]: [string, any]) => [k, v.succeeded ? `SUCCEEDED ${v.value}` : `blocked (${v.error})`])) });
  }

  return (
    <SafeAreaProvider>
      <SafeAreaView style={styles.page}>
        <StatusBar style="auto" />
        <View style={styles.toolbar}>
          <Button title="Bench" onPress={bench} testID="btn-bench" />
          <Button title="Hot reload" onPress={hotReload} testID="btn-reload" />
          {Platform.OS !== 'web' && <Button title="Probes" onPress={probes} testID="btn-probes" />}
        </View>
        <View style={styles.sandbox}>
          <Sandbox ref={sandbox} html={SANDBOX_HTML} rpc={rpc} onEvent={onEvent} onBlockedNavigation={onBlockedNavigation} />
        </View>
        <ScrollView style={styles.report}>
          <Text selectable testID="report" style={styles.mono}>
            {JSON.stringify(report, null, 1)}
          </Text>
        </ScrollView>
      </SafeAreaView>
    </SafeAreaProvider>
  );
}

const styles = StyleSheet.create({
  page: { flex: 1, backgroundColor: '#fff' },
  toolbar: { flexDirection: 'row', gap: 8, padding: 8, borderBottomWidth: 1, borderColor: '#ccc' },
  sandbox: { flex: 3, borderWidth: 2, borderColor: '#208AEF' },
  report: { flex: 2, padding: 8 },
  mono: { fontFamily: Platform.select({ ios: 'Menlo', default: 'monospace' }), fontSize: 11 },
});
