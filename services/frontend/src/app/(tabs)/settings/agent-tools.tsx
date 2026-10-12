import { useCallback } from 'react';
import { StyleSheet, Switch, Text, View } from 'react-native';

import { Card, LoadState, SectionTitle, Segmented, SettingsFrame, settingsStyles } from '@/components/SettingsUI';
import { useStepUp } from '@/components/StepUpProvider';
import { Toast, useToast } from '@/components/Toast';
import { adminChangeTools, adminListTools, toolGroups, type AdminTool, type ToolPermission } from '@/lib/platform';
import { theme } from '@/lib/theme';
import { useAction, useLoad } from '@/lib/useAsync';

/** Settings → Agent tools (admins, #342): a tool turned off here is off
 * for everyone; "Always ask" means nobody can let it run unasked. Within
 * that, each person picks their own (Settings → Agent permissions). */
export default function AgentToolsScreen() {
  const { withStepUp } = useStepUp();
  const { message: toast, showToast } = useToast();
  const load = useCallback(() => withStepUp(adminListTools), [withStepUp]);
  const { data: tools, error, reload, setData } = useLoad(load);
  const { busyKey, run } = useAction(showToast);

  const change = useCallback(
    async (tool: AdminTool, changes: { enabled?: boolean; ceiling?: ToolPermission }) => {
      await run(tool.name, async () =>
        setData(await withStepUp(() => adminChangeTools([{ name: tool.name, ...changes }]))),
      );
    },
    [run, setData, withStepUp],
  );

  return (
    <View style={styles.container}>
      <SettingsFrame title="Agent tools" testID="settings-agent-tools-screen">
        <Text style={settingsStyles.muted}>
          These apply to everyone on this server. Within them, each person chooses what asks first in Settings → Agent
          permissions.
        </Text>
        {tools === null ? (
          <LoadState error={error} onRetry={reload} />
        ) : (
          toolGroups(tools).map(([group, members]) => (
            <View key={group} style={styles.group}>
              <SectionTitle>{group}</SectionTitle>
              <Card>
                {members.map((tool, index) => (
                  <View
                    key={tool.name}
                    style={[settingsStyles.row, index === 0 && settingsStyles.firstRow, styles.toolRow]}
                    testID={`admin-tool-row-${tool.name}`}
                  >
                    <View style={styles.toolHeader}>
                      <View style={settingsStyles.rowMain}>
                        <Text style={[settingsStyles.rowTitle, !tool.enabled && styles.offText]}>{tool.label}</Text>
                        <Text style={settingsStyles.muted}>{tool.description}</Text>
                      </View>
                      <Switch
                        value={tool.enabled}
                        onValueChange={(enabled) => change(tool, { enabled })}
                        disabled={busyKey !== null}
                        accessibilityLabel={`${tool.label} on for everyone`}
                        testID={`admin-tool-enabled-${tool.name}`}
                      />
                    </View>
                    {tool.enabled ? (
                      <Segmented<ToolPermission>
                        value={tool.ceiling}
                        onChange={(ceiling) => change(tool, { ceiling })}
                        disabled={busyKey !== null}
                        options={[
                          { value: 'allow', label: 'Each person chooses', testID: `admin-tool-ceiling-${tool.name}-allow` },
                          { value: 'ask', label: 'Always ask', testID: `admin-tool-ceiling-${tool.name}-ask` },
                        ]}
                      />
                    ) : (
                      <Text style={settingsStyles.muted}>Off for everyone.</Text>
                    )}
                  </View>
                ))}
              </Card>
            </View>
          ))
        )}
      </SettingsFrame>
      <Toast message={toast} testID="settings-toast" />
    </View>
  );
}

const styles = StyleSheet.create({
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
  offText: {
    color: theme.textMuted,
  },
});
