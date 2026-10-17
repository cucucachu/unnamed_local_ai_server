import { useLocalSearchParams, useRouter } from 'expo-router';
import { useCallback, useState } from 'react';
import { StyleSheet, Switch, Text, TextInput, View } from 'react-native';

import {
  ExposedEntitiesNote,
  Notice,
  PlainHttpWarning,
  SignInNote,
  TokenEntry,
  connectionNote,
  integrationStyles,
  noticeTextStyle,
  useReloadOnReturn,
} from '@/components/IntegrationParts';
import {
  ActionButton,
  Badge,
  Card,
  LoadState,
  SectionTitle,
  Segmented,
  SettingsFrame,
  settingsStyles,
} from '@/components/SettingsUI';
import { useStepUp } from '@/components/StepUpProvider';
import { Toast, useToast } from '@/components/Toast';
import {
  adminChangeIntegrationTools,
  adminClearSharedCredential,
  adminDisconnectUser,
  adminEditIntegration,
  adminListIntegrations,
  adminRefreshIntegration,
  adminSetSharedCredential,
  adminStartSharedSignIn,
  adminUninstallIntegration,
  annotationLabels,
  formatFingerprint,
  openSignIn,
  toolTitle,
  type AdminIntegration,
  type AdminIntegrationTool,
} from '@/lib/integrations';
import { errorCodeMessage, type ToolPermission } from '@/lib/platform';
import { theme } from '@/lib/theme';
import { useAction, useLoad } from '@/lib/useAsync';

/** Settings → Integrations → Manage → one (admins): what it is, who's
 * connected (status only, never a credential), each tool on or off for
 * everyone and whether it always asks, refresh and uninstall. */
export default function ManageIntegrationScreen() {
  const { integrationId } = useLocalSearchParams<{ integrationId: string }>();
  const router = useRouter();
  const { withStepUp } = useStepUp();
  const { message: toast, showToast } = useToast();
  const { busyKey, run } = useAction(showToast);
  const load = useCallback(async () => {
    const all = await withStepUp(adminListIntegrations);
    return { found: all.find((i) => i.id === integrationId) ?? null };
  }, [integrationId, withStepUp]);
  const { data, error, reload, setData: setLoaded } = useLoad(load);
  const setData = useCallback((found: AdminIntegration) => setLoaded({ found }), [setLoaded]);
  const integration = data?.found ?? null;
  useReloadOnReturn(reload);
  const [confirmUninstall, setConfirmUninstall] = useState(false);

  const changeTools = useCallback(
    (key: string, changes: { name: string; enabled?: boolean; ceiling?: ToolPermission }[]) =>
      run(key, async () => setData(await withStepUp(() => adminChangeIntegrationTools(integrationId, changes)))),
    [integrationId, run, setData, withStepUp],
  );

  async function handleTurn(off: boolean) {
    await run('turn', async () =>
      setData(await withStepUp(() => adminEditIntegration(integrationId, { turned_off: off }))),
    );
  }

  async function handleRefresh() {
    await run('refresh', async () => setData(await withStepUp(() => adminRefreshIntegration(integrationId))));
  }

  async function handleSaveDescription(description: string) {
    await run('description', async () =>
      setData(await withStepUp(() => adminEditIntegration(integrationId, { description }))),
    );
  }

  async function handleSharedToken(secret: string): Promise<boolean> {
    let note = '';
    const ok = await run('shared', async () => {
      const result = await withStepUp(() => adminSetSharedCredential(integrationId, secret));
      note = connectionNote(result.checked, result.problem && errorCodeMessage(result.problem));
    });
    if (ok) {
      showToast(note);
      reload();
    }
    return ok;
  }

  async function handleSharedSignIn() {
    await run('shared', async () =>
      openSignIn((await withStepUp(() => adminStartSharedSignIn(integrationId))).authorize_url),
    );
  }

  async function handleClearShared() {
    if (await run('clear-shared', () => withStepUp(() => adminClearSharedCredential(integrationId)))) reload();
  }

  async function handleDisconnect(userId: string) {
    if (await run(`disconnect-${userId}`, () => withStepUp(() => adminDisconnectUser(integrationId, userId)))) reload();
  }

  async function handleUninstall() {
    if (await run('uninstall', () => withStepUp(() => adminUninstallIntegration(integrationId)))) {
      router.back();
    }
  }

  return (
    <View style={styles.container}>
      <SettingsFrame
        title={integration?.name ?? 'Integration'}
        backTo="/settings/integrations/manage"
        testID="settings-manage-integration-screen"
      >
        {integration === null ? (
          data ? (
            <Text style={settingsStyles.muted}>This integration was removed.</Text>
          ) : (
            <LoadState error={error} onRetry={reload} />
          )
        ) : (
          <>
            {integration.turned_off ? (
              <Notice testID="manage-integration-turned-off">
                <Text style={noticeTextStyle}>
                  Turned off: nobody can use it, and the agent doesn&apos;t see it. Connections and settings are kept.
                </Text>
              </Notice>
            ) : null}
            <ServerCard integration={integration} />
            <DescriptionCard
              key={integration.description}
              description={integration.description}
              busy={busyKey === 'description'}
              onSave={handleSaveDescription}
            />
            <SignInSection
              integration={integration}
              busyKey={busyKey}
              onSharedToken={handleSharedToken}
              onSharedSignIn={handleSharedSignIn}
              onClearShared={handleClearShared}
              onDisconnect={handleDisconnect}
            />
            <SectionTitle>Tools</SectionTitle>
            <Text style={settingsStyles.muted}>
              A tool that&apos;s off here is off for everyone. &ldquo;Always ask&rdquo; means nobody can let it run
              unasked; otherwise each person chooses, starting at Ask first. The server&apos;s own labels are its
              claims, shown for information only.
            </Text>
            {integration.tools.length === 0 ? (
              <Text style={settingsStyles.muted} testID="manage-integration-no-tools">
                {integration.discovered_at ? 'The server offers no tools.' : 'Not discovered yet. Refresh to look.'}
              </Text>
            ) : (
              <>
                {integration.tools.length > 1 ? (
                  <View style={settingsStyles.rowActions}>
                    {adminBulk(integration.tools).map((b) => (
                      <ActionButton
                        key={b.key}
                        label={b.label}
                        compact
                        disabled={busyKey !== null}
                        onPress={() => changeTools(b.key, b.changes)}
                        testID={`manage-integration-${b.key}`}
                      />
                    ))}
                  </View>
                ) : null}
                <Card>
                  {integration.tools.map((tool, index) => (
                    <AdminToolRow
                      key={tool.name}
                      tool={tool}
                      first={index === 0}
                      busy={busyKey !== null}
                      onChange={(changes) => changeTools(tool.name, [{ name: tool.name, ...changes }])}
                    />
                  ))}
                </Card>
              </>
            )}
            <ActionButton
              label="Refresh from the server"
              busy={busyKey === 'refresh'}
              disabled={busyKey !== null}
              onPress={handleRefresh}
              testID="manage-integration-refresh"
            />
            <Text style={settingsStyles.muted}>New tools a refresh finds start off.</Text>
            <ExposedEntitiesNote />

            <SectionTitle>{integration.turned_off ? 'Turned off' : 'Turn off'}</SectionTitle>
            <Text style={settingsStyles.muted}>
              {integration.turned_off
                ? 'Turning it back on restores it exactly as it was.'
                : 'Stops everyone using it for now, without deleting anything: connections, sign-ins and tool settings stay.'}
            </Text>
            <ActionButton
              label={integration.turned_off ? 'Turn on' : 'Turn off'}
              variant={integration.turned_off ? 'primary' : 'secondary'}
              busy={busyKey === 'turn'}
              disabled={busyKey !== null}
              onPress={() => handleTurn(!integration.turned_off)}
              testID="manage-integration-turn"
            />

            <SectionTitle>Uninstall</SectionTitle>
            {confirmUninstall ? (
              <Notice testID="manage-integration-uninstall-confirm">
                <Text style={noticeTextStyle}>
                  Uninstall {integration.name}? This deletes its tool settings and every stored token, for everyone.
                  Nothing changes on the server itself.
                </Text>
                <View style={settingsStyles.rowActions}>
                  <ActionButton
                    label="Keep it"
                    onPress={() => setConfirmUninstall(false)}
                    testID="manage-integration-uninstall-cancel"
                  />
                  <ActionButton
                    label="Uninstall"
                    variant="danger"
                    busy={busyKey === 'uninstall'}
                    disabled={busyKey !== null}
                    onPress={handleUninstall}
                    testID="manage-integration-uninstall-confirm-button"
                  />
                </View>
              </Notice>
            ) : (
              <ActionButton
                label="Uninstall"
                variant="danger"
                disabled={busyKey !== null}
                onPress={() => setConfirmUninstall(true)}
                testID="manage-integration-uninstall"
              />
            )}
          </>
        )}
      </SettingsFrame>
      <Toast message={toast} testID="settings-toast" />
    </View>
  );
}

function ServerCard({ integration }: { integration: AdminIntegration }) {
  const server = integration.server_info ?? {};
  const serverName = typeof server.name === 'string' ? server.name : null;
  return (
    <Card testID="manage-integration-server">
      <View style={settingsStyles.cardBody}>
        <Text style={integrationStyles.mono} selectable>
          {integration.url}
        </Text>
        <Text style={settingsStyles.muted}>
          The agent calls it &ldquo;{integration.slug}&rdquo;.
          {serverName ? ` Server: ${serverName}${typeof server.version === 'string' ? ` ${server.version}` : ''}.` : ''}
          {integration.protocol_version ? ` MCP ${integration.protocol_version}.` : ''}
        </Text>
        {integration.private_address ? (
          <Text style={settingsStyles.muted} testID="manage-integration-private">
            Allowed as a home-network address (this host and port only).
          </Text>
        ) : null}
        {integration.cert_fingerprint ? (
          <Text style={settingsStyles.muted} testID="manage-integration-pinned">
            Trusted certificate: {formatFingerprint(integration.cert_fingerprint).slice(0, 23)}…
          </Text>
        ) : null}
        {integration.plain_http ? <PlainHttpWarning /> : null}
      </View>
    </Card>
  );
}

function DescriptionCard({
  description,
  busy,
  onSave,
}: {
  description: string;
  busy: boolean;
  onSave: (description: string) => void;
}) {
  const [draft, setDraft] = useState(description);
  return (
    <>
      <SectionTitle>What the agent is told</SectionTitle>
      <Card>
        <View style={settingsStyles.cardBody}>
          <TextInput
            value={draft}
            onChangeText={setDraft}
            multiline
            maxLength={500}
            placeholder="One line on what this is for, e.g. Lights and switches at home."
            placeholderTextColor={theme.textMuted}
            style={styles.description}
            accessibilityLabel="Description"
            testID="manage-integration-description"
          />
          <ActionButton
            label="Save"
            busy={busy}
            disabled={busy || draft.trim() === description}
            onPress={() => onSave(draft.trim())}
            testID="manage-integration-description-save"
          />
        </View>
      </Card>
    </>
  );
}

function SignInSection({
  integration,
  busyKey,
  onSharedToken,
  onSharedSignIn,
  onClearShared,
  onDisconnect,
}: {
  integration: AdminIntegration;
  busyKey: string | null;
  onSharedToken: (secret: string) => Promise<boolean>;
  onSharedSignIn: () => void;
  onClearShared: () => void;
  onDisconnect: (userId: string) => void;
}) {
  const header = integration.token_header ? ` in the ${integration.token_header} header` : '';
  const oauth = integration.auth_method === 'oauth';
  const shared = integration.shared_credential;
  return (
    <>
      <SectionTitle>Sign-in</SectionTitle>
      <Card testID="manage-integration-signin">
        <View style={settingsStyles.cardBody}>
          {integration.credential_model === 'none' ? (
            <Text style={settingsStyles.muted}>No sign-in: anyone here can use it.</Text>
          ) : integration.credential_model === 'shared' ? (
            <>
              <View style={styles.statusRow}>
                <Text style={settingsStyles.muted}>
                  {oauth ? 'One household sign-in, used for everyone.' : `One household token${header}, used for everyone.`}
                </Text>
                <Badge
                  label={
                    integration.shared_credential === 'connected'
                      ? 'Set'
                      : integration.shared_credential === 'needs_reconnecting'
                        ? 'Rejected'
                        : 'Not set'
                  }
                  tone={integration.shared_credential === 'connected' ? 'accent' : 'danger'}
                  testID="manage-integration-shared-status"
                />
              </View>
              {oauth ? (
                <>
                  <SignInNote oauth={integration.oauth} testID="manage-integration-signin-note" />
                  <ActionButton
                    label={shared === 'not_connected' ? 'Sign in for the household' : 'Sign in again'}
                    variant={shared === 'connected' ? 'secondary' : 'primary'}
                    busy={busyKey === 'shared'}
                    disabled={busyKey !== null}
                    onPress={onSharedSignIn}
                    testID="manage-integration-shared-sign-in"
                  />
                </>
              ) : (
                <TokenEntry
                  label={shared === 'not_connected' ? 'Household token' : 'Replace household token'}
                  submitLabel="Save token"
                  busy={busyKey === 'shared'}
                  onSubmit={onSharedToken}
                  testID="manage-integration-shared"
                />
              )}
              {shared !== 'not_connected' ? (
                <ActionButton
                  label={oauth ? 'Sign out' : 'Remove token'}
                  variant="danger"
                  busy={busyKey === 'clear-shared'}
                  disabled={busyKey !== null}
                  onPress={onClearShared}
                  testID="manage-integration-shared-clear"
                />
              ) : null}
            </>
          ) : (
            <>
              <Text style={settingsStyles.muted}>
                {oauth
                  ? 'Each person signs in with their own account in Settings → Integrations, yours included.'
                  : `Each person connects their own token${header} in Settings → Integrations, yours included.`}
              </Text>
              {oauth ? <SignInNote oauth={integration.oauth} testID="manage-integration-signin-note" /> : null}
              {integration.connections.length === 0 ? (
                <Text style={settingsStyles.muted} testID="manage-integration-no-connections">
                  Nobody has connected yet.
                </Text>
              ) : (
                integration.connections.map((c) => (
                  <View key={c.user_id} style={styles.statusRow} testID={`manage-integration-connection-${c.username}`}>
                    <View style={settingsStyles.rowMain}>
                      <Text style={settingsStyles.rowTitle}>{c.display_name ?? c.username ?? 'Removed user'}</Text>
                      <Text style={settingsStyles.muted}>
                        {c.status === 'connected' ? 'Connected' : 'Needs reconnecting'}
                      </Text>
                    </View>
                    <ActionButton
                      label="Disconnect"
                      variant="danger"
                      compact
                      busy={busyKey === `disconnect-${c.user_id}`}
                      disabled={busyKey !== null}
                      onPress={() => onDisconnect(c.user_id)}
                      testID={`manage-integration-disconnect-${c.username}`}
                    />
                  </View>
                ))
              )}
            </>
          )}
        </View>
      </Card>
    </>
  );
}

function adminBulk(tools: AdminIntegrationTool[]) {
  return [
    {
      key: 'all-on',
      label: 'Turn all on',
      changes: tools.filter((t) => !t.enabled).map((t) => ({ name: t.name, enabled: true })),
    },
    {
      key: 'all-off',
      label: 'Turn all off',
      changes: tools.filter((t) => t.enabled).map((t) => ({ name: t.name, enabled: false })),
    },
    {
      key: 'all-ask',
      label: 'Always ask for all',
      changes: tools
        .filter((t) => t.enabled && t.ceiling === 'allow')
        .map((t) => ({ name: t.name, ceiling: 'ask' as const })),
    },
    {
      key: 'all-choose',
      label: 'Each person chooses for all',
      changes: tools
        .filter((t) => t.enabled && t.ceiling === 'ask')
        .map((t) => ({ name: t.name, ceiling: 'allow' as const })),
    },
  ].filter((b) => b.changes.length > 0);
}

function AdminToolRow({
  tool,
  first,
  busy,
  onChange,
}: {
  tool: AdminIntegrationTool;
  first: boolean;
  busy: boolean;
  onChange: (changes: { enabled?: boolean; ceiling?: ToolPermission }) => void;
}) {
  const hints = annotationLabels(tool.annotations);
  return (
    <View
      style={[settingsStyles.row, first && settingsStyles.firstRow, integrationStyles.toolRow]}
      testID={`manage-integration-tool-${tool.name}`}
    >
      <View style={integrationStyles.toolHeader}>
        <View style={settingsStyles.rowMain}>
          <Text style={[settingsStyles.rowTitle, !tool.enabled && integrationStyles.offText]}>{toolTitle(tool)}</Text>
          {tool.description ? (
            <Text style={settingsStyles.muted} numberOfLines={3}>
              {tool.description}
            </Text>
          ) : null}
        </View>
        <Switch
          value={tool.enabled}
          onValueChange={(enabled) => onChange({ enabled })}
          disabled={busy}
          accessibilityLabel={`${toolTitle(tool)} on for everyone`}
          testID={`manage-integration-tool-enabled-${tool.name}`}
        />
      </View>
      {hints.length ? (
        <View style={integrationStyles.hints} testID={`manage-integration-tool-hints-${tool.name}`}>
          {hints.map((hint) => (
            <Badge key={hint} label={hint} />
          ))}
        </View>
      ) : null}
      {tool.enabled ? (
        <Segmented<ToolPermission>
          value={tool.ceiling}
          onChange={(ceiling) => onChange({ ceiling })}
          disabled={busy}
          options={[
            { value: 'allow', label: 'Each person chooses', testID: `manage-integration-tool-ceiling-${tool.name}-allow` },
            { value: 'ask', label: 'Always ask', testID: `manage-integration-tool-ceiling-${tool.name}-ask` },
          ]}
        />
      ) : (
        <Text style={settingsStyles.muted}>Off for everyone.</Text>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: theme.bg,
  },
  statusRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
  },
  description: {
    minHeight: 60,
    color: theme.text,
    fontSize: 15,
    borderWidth: 1,
    borderColor: theme.border,
    borderRadius: 8,
    padding: 10,
    textAlignVertical: 'top',
  },
});
