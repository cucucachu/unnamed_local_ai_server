import { useLocalSearchParams } from 'expo-router';
import { useCallback } from 'react';
import { StyleSheet, Switch, Text, View } from 'react-native';

import {
  ExposedEntitiesNote,
  PlainHttpWarning,
  TokenEntry,
  connectionNote,
  integrationStyles,
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
import { Toast, useToast } from '@/components/Toast';
import {
  annotationLabels,
  changeMyIntegrationTools,
  connectIntegration,
  disconnectIntegration,
  listMyIntegrations,
  statusLabel,
  toolTitle,
  type Integration,
  type IntegrationTool,
} from '@/lib/integrations';
import { errorCodeMessage, type ToolPermission } from '@/lib/platform';
import { theme } from '@/lib/theme';
import { useAction, useLoad } from '@/lib/useAsync';

/** Settings → Integrations → one: connect it (per-user token), disconnect,
 * and the user's own setting for each tool. What an admin decided shows
 * locked and labelled. */
export default function IntegrationScreen() {
  const { integrationId } = useLocalSearchParams<{ integrationId: string }>();
  const { message: toast, showToast } = useToast();
  const { busyKey, run } = useAction(showToast);
  const { data: integrations, error, reload, setData } = useLoad(listMyIntegrations);
  const integration = integrations?.find((i) => i.id === integrationId) ?? null;

  const changeTools = useCallback(
    async (key: string, changes: { name: string; enabled?: boolean; permission?: ToolPermission }[]) => {
      await run(key, async () => setData(await changeMyIntegrationTools(integrationId, changes)));
    },
    [integrationId, run, setData],
  );

  async function handleConnect(secret: string): Promise<boolean> {
    let note = '';
    const ok = await run('connect', async () => {
      const result = await connectIntegration(integrationId, secret);
      note = connectionNote(result.checked, result.problem && errorCodeMessage(result.problem));
    });
    if (ok) {
      showToast(note);
      reload();
    }
    return ok;
  }

  async function handleDisconnect() {
    if (await run('disconnect', () => disconnectIntegration(integrationId))) reload();
  }

  const missing = integrations !== null && integration === null;

  return (
    <View style={styles.container}>
      <SettingsFrame title={integration?.name ?? 'Integration'} backTo="/settings/integrations" testID="settings-integration-screen">
        {integration === null ? (
          missing ? (
            <Text style={settingsStyles.muted}>This integration was removed.</Text>
          ) : (
            <LoadState error={error} onRetry={reload} />
          )
        ) : (
          <>
            {integration.description ? <Text style={settingsStyles.muted}>{integration.description}</Text> : null}
            <ConnectionCard
              integration={integration}
              busyKey={busyKey}
              onConnect={handleConnect}
              onDisconnect={handleDisconnect}
            />
            <ToolsSection integration={integration} busy={busyKey !== null} onChange={changeTools} />
            <ExposedEntitiesNote />
          </>
        )}
      </SettingsFrame>
      <Toast message={toast} testID="settings-toast" />
    </View>
  );
}

function ConnectionCard({
  integration,
  busyKey,
  onConnect,
  onDisconnect,
}: {
  integration: Integration;
  busyKey: string | null;
  onConnect: (secret: string) => Promise<boolean>;
  onDisconnect: () => void;
}) {
  const perUser = integration.credential_model === 'per_user';
  const connected = integration.status === 'connected';
  let explanation: string;
  if (integration.credential_model === 'none') explanation = 'No sign-in needed: anyone here can use it.';
  else if (integration.credential_model === 'shared')
    explanation = integration.status === 'connected'
      ? 'Set up by an admin for everyone in the household.'
      : 'An admin needs to finish setting it up.';
  else if (integration.auth_method === 'oauth')
    explanation = "This integration signs in with OAuth, which HomeAI can't do yet. Ask an admin to set it up with a token.";
  else if (connected) explanation = 'Connected with your token. The agent acts as you there.';
  else if (integration.status === 'needs_reconnecting')
    explanation = 'The server turned your token down. Connect again with a new one.';
  else explanation = 'Paste a token or API key from your account on that service. The agent will act as you there.';

  return (
    <Card testID="integration-connection">
      <View style={settingsStyles.cardBody}>
        <View style={styles.statusRow}>
          <Text style={settingsStyles.rowTitle}>Connection</Text>
          <Badge
            label={statusLabel(integration)}
            tone={integration.status === 'needs_reconnecting' ? 'danger' : connected || integration.status === 'ready' ? 'accent' : 'muted'}
            testID="integration-status"
          />
        </View>
        <Text style={settingsStyles.muted}>{explanation}</Text>
        {perUser && integration.auth_method === 'token' ? (
          <>
            {integration.plain_http ? <PlainHttpWarning /> : null}
            <TokenEntry
              label={connected ? 'Replace your token' : 'Your token'}
              submitLabel={connected ? 'Replace' : 'Connect'}
              busy={busyKey === 'connect'}
              onSubmit={onConnect}
              testID="integration-token"
            />
            {connected || integration.status === 'needs_reconnecting' ? (
              <ActionButton
                label="Disconnect"
                variant="danger"
                busy={busyKey === 'disconnect'}
                disabled={busyKey !== null}
                onPress={onDisconnect}
                testID="integration-disconnect"
              />
            ) : null}
          </>
        ) : null}
      </View>
    </Card>
  );
}

function ToolsSection({
  integration,
  busy,
  onChange,
}: {
  integration: Integration;
  busy: boolean;
  onChange: (key: string, changes: { name: string; enabled?: boolean; permission?: ToolPermission }[]) => void;
}) {
  const offered = integration.tools.filter((t) => t.enabled || !t.enabled_locked);
  const adminOff = integration.tools.length - offered.length;
  const askers = offered.filter((t) => t.enabled && t.permission === 'ask' && !t.permission_locked);

  return (
    <>
      <SectionTitle>Tools</SectionTitle>
      {offered.length === 0 ? (
        <Text style={settingsStyles.muted} testID="integration-no-tools">
          No tools are turned on for this integration yet.
        </Text>
      ) : (
        <>
          <Text style={settingsStyles.muted}>
            Ask first shows you each use to approve; Allow lets the agent go ahead. Routines follow the same settings.
          </Text>
          <Card>
            {offered.map((tool, index) => (
              <ToolRow
                key={tool.name}
                tool={tool}
                first={index === 0}
                busy={busy}
                onChange={(changes) => onChange(tool.name, [{ name: tool.name, ...changes }])}
              />
            ))}
          </Card>
          {askers.length > 1 ? (
            <ActionButton
              label={`Allow all ${askers.length} for ${integration.name}`}
              disabled={busy}
              onPress={() => onChange('allow-all', askers.map((t) => ({ name: t.name, permission: 'allow' as const })))}
              testID="integration-allow-all"
            />
          ) : null}
        </>
      )}
      {adminOff > 0 && offered.length > 0 ? (
        <Text style={settingsStyles.muted} testID="integration-admin-off">
          {adminOff === 1 ? '1 more is' : `${adminOff} more are`} turned off by an admin.
        </Text>
      ) : null}
    </>
  );
}

function ToolRow({
  tool,
  first,
  busy,
  onChange,
}: {
  tool: IntegrationTool;
  first: boolean;
  busy: boolean;
  onChange: (changes: { enabled?: boolean; permission?: ToolPermission }) => void;
}) {
  const hints = annotationLabels(tool.annotations);
  return (
    <View
      style={[settingsStyles.row, first && settingsStyles.firstRow, integrationStyles.toolRow]}
      testID={`integration-tool-${tool.name}`}
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
          disabled={busy || tool.enabled_locked}
          accessibilityLabel={`${toolTitle(tool)} on`}
          testID={`integration-tool-enabled-${tool.name}`}
        />
      </View>
      {hints.length ? (
        <View style={integrationStyles.hints}>
          {hints.map((hint) => (
            <Badge key={hint} label={hint} />
          ))}
        </View>
      ) : null}
      {tool.enabled ? (
        <>
          <Segmented<ToolPermission>
            value={tool.permission}
            onChange={(permission) => onChange({ permission })}
            disabled={busy || tool.permission_locked}
            options={[
              { value: 'ask', label: 'Ask first', testID: `integration-tool-permission-${tool.name}-ask` },
              { value: 'allow', label: 'Allow', testID: `integration-tool-permission-${tool.name}-allow` },
            ]}
          />
          {tool.permission_locked ? (
            <Text style={settingsStyles.muted} testID={`integration-tool-locked-${tool.name}`}>
              An admin requires asking first.
            </Text>
          ) : null}
        </>
      ) : null}
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
    justifyContent: 'space-between',
    gap: 12,
  },
});
