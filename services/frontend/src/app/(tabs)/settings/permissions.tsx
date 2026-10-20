import { useCallback } from 'react';
import { Switch, Text, View } from 'react-native';

import { Card, LoadState, SectionTitle, Segmented, SettingsFrame, useSettingsStyles } from '@/components/SettingsUI';
import { Toast, useToast } from '@/components/Toast';
import { changeMyTools, listMyTools, toolGroups, type MyTool, type ToolPermission } from '@/lib/platform';
import { themedStyles } from '@/lib/theme';
import { useAction, useLoad } from '@/lib/useAsync';

/** Settings → Agent permissions (#342): each of the agent's tools on or
 * off, and whether it asks first. Chats and routines both follow these.
 * Where an admin has decided for everyone, the row shows what applies and
 * can't be changed. */
export default function PermissionsScreen() {
  const settingsStyles = useSettingsStyles();
  const styles = useStyles();
  const { message: toast, showToast } = useToast();
  const { data: tools, error, reload, setData } = useLoad(listMyTools);
  const { busyKey, run } = useAction(showToast);

  const change = useCallback(
    async (tool: MyTool, changes: { enabled?: boolean; permission?: ToolPermission }) => {
      await run(tool.name, async () => setData(await changeMyTools([{ name: tool.name, ...changes }])));
    },
    [run, setData],
  );

  return (
    <View style={styles.container}>
      <SettingsFrame title="Agent permissions" testID="settings-permissions-screen">
        <Text style={settingsStyles.muted}>
          Ask first shows you each use to approve; Allow lets the agent go ahead. A tool that&apos;s off isn&apos;t
          available to the agent at all. Routines follow the same settings, and wait for you when they need to ask.
        </Text>
        {tools === null ? (
          <LoadState error={error} onRetry={reload} />
        ) : (
          toolGroups(tools).map(([group, members]) => (
            <View key={group} style={styles.group}>
              <SectionTitle>{group}</SectionTitle>
              <Card>
                {members.map((tool, index) => (
                  <ToolRow
                    key={tool.name}
                    tool={tool}
                    first={index === 0}
                    busy={busyKey !== null}
                    onChange={(changes) => change(tool, changes)}
                  />
                ))}
              </Card>
            </View>
          ))
        )}
        <Text style={settingsStyles.muted}>
          Turning a tool on takes effect in new chats. Turning one off, or changing whether it asks, applies right away.
        </Text>
      </SettingsFrame>
      <Toast message={toast} testID="settings-toast" />
    </View>
  );
}

function ToolRow({
  tool,
  first,
  busy,
  onChange,
}: {
  tool: MyTool;
  first: boolean;
  busy: boolean;
  onChange: (changes: { enabled?: boolean; permission?: ToolPermission }) => void;
}) {
  const settingsStyles = useSettingsStyles();
  const styles = useStyles();
  return (
    <View style={[settingsStyles.row, first && settingsStyles.firstRow, styles.toolRow]} testID={`tool-row-${tool.name}`}>
      <View style={styles.toolHeader}>
        <View style={settingsStyles.rowMain}>
          <Text style={[settingsStyles.rowTitle, !tool.enabled && styles.offText]}>{tool.label}</Text>
          <Text style={settingsStyles.muted}>{tool.description}</Text>
        </View>
        <Switch
          value={tool.enabled}
          onValueChange={(enabled) => onChange({ enabled })}
          disabled={busy || tool.enabled_locked}
          accessibilityLabel={`${tool.label} on`}
          testID={`tool-enabled-${tool.name}`}
        />
      </View>
      {tool.enabled_locked ? (
        <Text style={settingsStyles.muted} testID={`tool-enabled-locked-${tool.name}`}>
          Turned off by an admin.
        </Text>
      ) : null}
      {tool.enabled ? (
        <View style={styles.permission}>
          <Segmented<ToolPermission>
            value={tool.permission}
            onChange={(permission) => onChange({ permission })}
            disabled={busy || tool.permission_locked}
            options={[
              { value: 'ask', label: 'Ask first', testID: `tool-permission-${tool.name}-ask` },
              { value: 'allow', label: 'Allow', testID: `tool-permission-${tool.name}-allow` },
            ]}
          />
          {tool.permission_locked ? (
            <Text style={settingsStyles.muted} testID={`tool-permission-locked-${tool.name}`}>
              An admin requires asking first.
            </Text>
          ) : null}
        </View>
      ) : null}
    </View>
  );
}

const useStyles = themedStyles((theme) => ({
  container: {
    flex: 1,
    backgroundColor: theme.bg,
  },
  group: {
    gap: 8,
  },
  toolRow: {
    flexDirection: 'column',
    alignItems: 'stretch',
    gap: 8,
  },
  toolHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
  },
  permission: {
    gap: 6,
  },
  offText: {
    color: theme.textMuted,
  },
}));
