import Ionicons from '@expo/vector-icons/Ionicons';
import { useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useRef } from 'react';
import { Pressable, Text, View } from 'react-native';

import { useAuth } from '@/components/AuthProvider';
import { Badge, Card, LoadState, NavRow, SectionTitle, SettingsFrame, settingsStyles } from '@/components/SettingsUI';
import { listMyIntegrations, statusLabel, type Integration } from '@/lib/integrations';
import { theme } from '@/lib/theme';
import { useLoad } from '@/lib/useAsync';

/** Settings → Integrations (M18-08): the services the agent can use for
 * you, e.g. Home Assistant. Each one opens to connect it and choose what
 * the agent may do there. Admins also get the way in to install them. */
export default function IntegrationsScreen() {
  const router = useRouter();
  const { state } = useAuth();
  const isAdmin = state.phase === 'ready' && state.user?.role === 'admin';
  const { data: integrations, error, reload } = useLoad(listMyIntegrations);

  const focusedOnce = useRef(false);
  useFocusEffect(
    useCallback(() => {
      if (focusedOnce.current) reload();
      focusedOnce.current = true;
    }, [reload]),
  );

  return (
    <SettingsFrame title="Integrations" testID="settings-integrations-screen">
      <Text style={settingsStyles.muted}>
        Services the agent can use for you, like Home Assistant. Connect one with your own account, then choose which
        of its tools the agent may use and which ask you first.
      </Text>
      {integrations === null ? (
        <LoadState error={error} onRetry={reload} />
      ) : integrations.length === 0 ? (
        <Text style={settingsStyles.muted} testID="integrations-empty">
          {isAdmin ? 'Nothing installed yet. Add one under Manage integrations.' : "An admin hasn't installed any yet."}
        </Text>
      ) : (
        <Card>
          {integrations.map((integration, index) => (
            <Pressable
              key={integration.id}
              onPress={() =>
                router.push({ pathname: '/settings/integrations/[integrationId]', params: { integrationId: integration.id } })
              }
              style={[settingsStyles.row, index === 0 && settingsStyles.firstRow]}
              accessibilityRole="button"
              testID={`integration-row-${integration.slug}`}
            >
              <View style={settingsStyles.rowMain}>
                <Text style={settingsStyles.rowTitle}>{integration.name}</Text>
                {integration.description ? (
                  <Text style={settingsStyles.muted} numberOfLines={2}>
                    {integration.description}
                  </Text>
                ) : null}
              </View>
              <Badge
                label={statusLabel(integration)}
                tone={badgeTone(integration)}
                testID={`integration-status-${integration.slug}`}
              />
              <Ionicons name="chevron-forward" size={18} color={theme.textMuted} />
            </Pressable>
          ))}
        </Card>
      )}
      {isAdmin ? (
        <>
          <SectionTitle>Admin</SectionTitle>
          <NavRow
            icon="extension-puzzle-outline"
            title="Manage integrations"
            description="Install servers, choose their tools for everyone"
            onPress={() => router.push('/settings/integrations/manage')}
            testID="integrations-nav-manage"
          />
        </>
      ) : null}
    </SettingsFrame>
  );
}

function badgeTone(integration: Integration): 'muted' | 'accent' | 'danger' {
  if (integration.status === 'needs_reconnecting') return 'danger';
  if (integration.status === 'connected' || integration.status === 'ready') return 'accent';
  return 'muted';
}
