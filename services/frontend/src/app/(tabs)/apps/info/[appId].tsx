import { Stack, useLocalSearchParams } from 'expo-router';
import { useCallback, useState } from 'react';
import { Alert, Platform, ScrollView, StyleSheet, Text, View } from 'react-native';

import {
  ActionButton,
  Badge,
  Card,
  ErrorText,
  LoadState,
  SectionTitle,
  settingsStyles,
} from '@/components/SettingsUI';
import { isReadOnly } from '@/lib/appHost';
import { getApp, getAppHistory, revertApp, type App, type AppCommit, type RevertResult } from '@/lib/apps';
import { listSpaces } from '@/lib/platform';
import { monospaceFontFamily, theme } from '@/lib/theme';
import { useAction, useLoad } from '@/lib/useAsync';

interface AppInfo {
  app: App;
  /** Owner or editor of the app's source space. */
  canRevert: boolean;
  /** null when the user sees the app only through an install. */
  commits: AppCommit[] | null;
  nextOffset: number | null;
}

async function loadAppInfo(appId: string): Promise<AppInfo> {
  const [app, spaces] = await Promise.all([getApp(appId), listSpaces()]);
  const space = spaces.find((s) => s.id === app.source_space_id);
  if (app.source_path === null) return { app, canRevert: false, commits: null, nextOffset: null };
  const page = await getAppHistory(appId);
  return {
    app,
    canRevert: space !== undefined && !isReadOnly(space),
    commits: page.commits,
    nextOffset: page.next_offset,
  };
}

function shortId(commit: string): string {
  return commit.slice(0, 8);
}

/** Same `Alert` (native) / `window.confirm` (web) split as `files.tsx`'s
 * `confirmDeleteEntry`: RN's `Alert.alert` does nothing on web. */
function confirmRevert(commit: AppCommit): Promise<boolean> {
  const label = commit.version ? `version ${commit.version} (${shortId(commit.id)})` : shortId(commit.id);
  const message = `Restore the app's files to ${label} and rebuild it? Newer changes stay in the history.`;

  if (Platform.OS === 'web') {
    return Promise.resolve(window.confirm(message));
  }

  return new Promise((resolve) => {
    Alert.alert('Revert app', message, [
      { text: 'Cancel', style: 'cancel', onPress: () => resolve(false) },
      { text: 'Revert', style: 'destructive', onPress: () => resolve(true) },
    ]);
  });
}

function describeRevert(result: RevertResult): { text: string; ok: boolean } {
  if (!result.ok) {
    const reason = result.diagnostics[0]?.message;
    return { text: `Restored the files, but the rebuild failed${reason ? `: ${reason}` : '.'}`, ok: false };
  }
  const pending = result.migrations.some((m) => m.migration?.status === 'pending');
  return {
    text: pending ? 'Restored and rebuilt. A data change is waiting for approval.' : 'Restored and rebuilt.',
    ok: true,
  };
}

/**
 * An app's info and source history (`docs/PLATFORM.md` §7 "Source
 * history"), from the runner's info button. Every successful build is a
 * commit; the space's owners and editors can revert to an earlier one,
 * which restores its files as a new commit and rebuilds the app.
 */
export default function AppInfoScreen() {
  const params = useLocalSearchParams<{ appId: string }>();
  const appId = Array.isArray(params.appId) ? params.appId[0] : params.appId;
  const load = useCallback(() => loadAppInfo(appId), [appId]);
  const { data, error, reload, setData } = useLoad(load);
  const [notice, setNotice] = useState<{ text: string; ok: boolean } | null>(null);
  const onError = useCallback((text: string) => setNotice({ text, ok: false }), []);
  const { busyKey, run } = useAction(onError);

  if (data === null) {
    return (
      <View style={styles.container}>
        <Stack.Screen options={{ title: 'App info' }} />
        <LoadState error={error} onRetry={reload} />
      </View>
    );
  }

  const { app, canRevert, commits, nextOffset } = data;

  const revert = async (commit: AppCommit) => {
    if (!(await confirmRevert(commit))) return;
    setNotice(null);
    await run(`revert-${commit.id}`, async () => {
      setNotice(describeRevert(await revertApp(app.id, commit.id)));
      reload();
    });
  };

  const loadMore = () =>
    run('more', async () => {
      if (nextOffset === null || commits === null) return;
      const page = await getAppHistory(app.id, nextOffset);
      setData({ ...data, commits: [...commits, ...page.commits], nextOffset: page.next_offset });
    });

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.body} testID="app-info">
      <Stack.Screen options={{ title: app.name }} />
      <Card>
        <View style={settingsStyles.cardBody}>
          <Text style={settingsStyles.rowTitle}>{app.name}</Text>
          {app.working_version ? (
            <Text style={settingsStyles.muted}>Version {app.working_version.version}</Text>
          ) : null}
          {app.source_path ? (
            <Text style={[settingsStyles.muted, styles.mono]} selectable>
              {app.source_path}
            </Text>
          ) : null}
        </View>
      </Card>

      {notice ? (
        notice.ok ? (
          <Text style={styles.notice} accessibilityRole="alert" testID="app-info-notice">
            {notice.text}
          </Text>
        ) : (
          <ErrorText testID="app-info-error">{notice.text}</ErrorText>
        )
      ) : null}

      <SectionTitle>History</SectionTitle>
      {commits === null ? (
        <Text style={settingsStyles.muted} testID="app-info-no-source">
          This app&apos;s source is in a space you&apos;re not a member of.
        </Text>
      ) : commits.length === 0 ? (
        <Text style={settingsStyles.muted} testID="app-info-empty">
          No builds yet. Each successful build is saved here.
        </Text>
      ) : (
        <Card testID="app-history">
          {commits.map((commit, index) => (
            <CommitRow
              key={commit.id}
              commit={commit}
              first={index === 0}
              canRevert={canRevert && !commit.current}
              busy={busyKey === `revert-${commit.id}`}
              disabled={busyKey !== null}
              onRevert={() => revert(commit)}
            />
          ))}
        </Card>
      )}
      {nextOffset !== null ? (
        <ActionButton label="Show older" onPress={loadMore} busy={busyKey === 'more'} testID="app-history-more" />
      ) : null}
    </ScrollView>
  );
}

function CommitRow({
  commit,
  first,
  canRevert,
  busy,
  disabled,
  onRevert,
}: {
  commit: AppCommit;
  first: boolean;
  canRevert: boolean;
  busy: boolean;
  disabled: boolean;
  onRevert: () => void;
}) {
  const details = [
    commit.version ? `v${commit.version}` : null,
    commit.user,
    new Date(commit.created_at).toLocaleString(),
  ].filter(Boolean);
  return (
    <View style={[settingsStyles.row, first && settingsStyles.firstRow]} testID={`app-commit-${shortId(commit.id)}`}>
      <View style={settingsStyles.rowMain}>
        <Text style={settingsStyles.rowTitle}>{commit.subject}</Text>
        <Text style={settingsStyles.muted}>{details.join(' · ')}</Text>
        <Text style={[settingsStyles.muted, styles.mono]}>{shortId(commit.id)}</Text>
      </View>
      <View style={settingsStyles.rowActions}>
        {commit.current ? <Badge label="Current" tone="accent" /> : null}
        {commit.thread_id ? <Badge label="Agent" /> : null}
        {canRevert ? (
          <ActionButton
            label="Revert"
            variant="danger"
            compact
            busy={busy}
            disabled={disabled}
            onPress={onRevert}
            testID={`app-revert-${shortId(commit.id)}`}
          />
        ) : null}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: theme.bg,
  },
  body: {
    width: '100%',
    maxWidth: 640,
    alignSelf: 'center',
    padding: 16,
    gap: 12,
  },
  mono: {
    fontFamily: monospaceFontFamily,
  },
  notice: {
    color: theme.text,
    fontSize: 13,
  },
});
