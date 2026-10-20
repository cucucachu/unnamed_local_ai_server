import Ionicons from '@expo/vector-icons/Ionicons';
import { useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useRef } from 'react';
import { Pressable, Text, View } from 'react-native';

import { ActionButton, Badge, Card, LoadState, SettingsFrame, useSettingsStyles } from '@/components/SettingsUI';
import { useStepUp } from '@/components/StepUpProvider';
import { adminListIntegrations, type AdminIntegration } from '@/lib/integrations';
import { useTheme } from '@/lib/theme';
import { useLoad } from '@/lib/useAsync';

/** Settings → Integrations → Manage (admins): every installed server, and Add. */
export default function ManageIntegrationsScreen() {
  const settingsStyles = useSettingsStyles();
  const theme = useTheme();
  const router = useRouter();
  const { withStepUp } = useStepUp();
  const load = useCallback(() => withStepUp(adminListIntegrations), [withStepUp]);
  const { data: integrations, error, reload } = useLoad(load);

  const focusedOnce = useRef(false);
  useFocusEffect(
    useCallback(() => {
      if (focusedOnce.current) reload();
      focusedOnce.current = true;
    }, [reload]),
  );

  return (
    <SettingsFrame title="Manage integrations" backTo="/settings/integrations" testID="settings-manage-integrations-screen">
      <Text style={settingsStyles.muted}>
        An integration is a server the agent can use through MCP, such as Home Assistant. You choose which of its tools
        anyone may use; each person then connects their own account and picks what asks first.
      </Text>
      {integrations === null ? (
        <LoadState error={error} onRetry={reload} />
      ) : integrations.length === 0 ? (
        <Text style={settingsStyles.muted} testID="manage-integrations-empty">
          Nothing installed yet.
        </Text>
      ) : (
        <Card>
          {integrations.map((integration, index) => (
            <Pressable
              key={integration.id}
              onPress={() =>
                router.push({
                  pathname: '/settings/integrations/manage/[integrationId]',
                  params: { integrationId: integration.id },
                })
              }
              style={[settingsStyles.row, index === 0 && settingsStyles.firstRow]}
              accessibilityRole="button"
              testID={`manage-integration-row-${integration.slug}`}
            >
              <View style={settingsStyles.rowMain}>
                <Text style={settingsStyles.rowTitle}>{integration.name}</Text>
                <Text style={settingsStyles.muted} numberOfLines={1}>
                  {integration.url}
                </Text>
              </View>
              <Badge label={summary(integration)} />
              <Ionicons name="chevron-forward" size={18} color={theme.textMuted} />
            </Pressable>
          ))}
        </Card>
      )}
      <ActionButton
        label="Add integration"
        variant="primary"
        onPress={() => router.push('/settings/integrations/manage/add')}
        testID="manage-integrations-add"
      />
    </SettingsFrame>
  );
}

function summary(integration: AdminIntegration): string {
  if (integration.turned_off) return 'Turned off';
  if (integration.discovered_at === null) return 'Not set up';
  const on = integration.tools.filter((t) => t.enabled).length;
  return `${on} of ${integration.tools.length} tools on`;
}
