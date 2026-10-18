import { useLocalSearchParams, useRouter } from 'expo-router';
import { useCallback, useState } from 'react';
import { ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';

import { ActionButton, Card, ErrorText, LoadState, SectionTitle, Segmented, settingsStyles } from '@/components/SettingsUI';
import { ApiError } from '@/lib/api';
import { installTemplate, listAppTemplates, type AppTemplate } from '@/lib/apps';
import { isReadOnly, listSpaces, slugify, type Space } from '@/lib/platform';
import { theme } from '@/lib/theme';
import { useAction, useLoad } from '@/lib/useAsync';

interface TemplateTarget {
  template: AppTemplate;
  spaces: Space[];
}

async function loadTarget(slug: string): Promise<TemplateTarget> {
  const [templates, spaces] = await Promise.all([listAppTemplates(), listSpaces()]);
  const template = templates.find((t) => t.slug === slug);
  if (!template) throw new ApiError(404, 'unknown_template');
  const writable = spaces
    .filter((space) => space.archived_at === null && !isReadOnly(space))
    .sort((a, b) => (a.kind === b.kind ? a.name.localeCompare(b.name) : a.kind === 'personal' ? -1 : 1));
  return { template, spaces: writable };
}

/**
 * Start a new app from a template (M22-05): name it, pick the space, and the
 * platform copies the template into `Apps/<slug>/`, installs and builds it.
 * The app's agent can then change it like any other app.
 */
export default function TemplateScreen() {
  const router = useRouter();
  const params = useLocalSearchParams<{ template: string; spaceId?: string }>();
  const slug = Array.isArray(params.template) ? params.template[0] : params.template;
  const preferred = Array.isArray(params.spaceId) ? params.spaceId[0] : params.spaceId;
  const load = useCallback(() => loadTarget(slug), [slug]);
  const { data, error, reload } = useLoad(load);
  const [name, setName] = useState<string | null>(null);
  const [spaceId, setSpaceId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const { busyKey, run } = useAction(setActionError);

  if (data === null) {
    return (
      <View style={styles.container}>
        <LoadState error={error} onRetry={reload} />
      </View>
    );
  }

  const { template, spaces } = data;
  const appName = name ?? template.name;
  const appSlug = slugify(appName);
  const space =
    spaces.find((s) => s.id === spaceId) ?? spaces.find((s) => s.id === preferred) ?? spaces[0] ?? null;
  const canCreate = space !== null && appSlug !== '' && appName.trim() !== '';

  const create = () => {
    if (!space) return;
    setActionError(null);
    void run('create', async () => {
      const { instance } = await installTemplate(space.id, template.slug, appSlug, appName.trim());
      router.replace({ pathname: '/apps/[instanceId]', params: { instanceId: instance.id } });
    });
  };

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.body} testID="apps-template">
      <Card>
        <View style={settingsStyles.cardBody}>
          <Text style={settingsStyles.rowTitle}>{template.name}</Text>
          <Text style={settingsStyles.muted}>{template.description}</Text>
        </View>
      </Card>

      <SectionTitle>Name</SectionTitle>
      <Card>
        <View style={settingsStyles.cardBody}>
          <TextInput
            value={appName}
            onChangeText={setName}
            maxLength={64}
            autoCorrect={false}
            placeholder={template.name}
            placeholderTextColor={theme.textMuted}
            style={styles.input}
            accessibilityLabel="App name"
            testID="template-name"
          />
          <Text style={settingsStyles.muted} testID="template-folder">
            {appSlug ? `Saved in Apps/${appSlug}` : 'The name needs a letter or digit.'}
          </Text>
        </View>
      </Card>

      {spaces.length > 1 ? (
        <>
          <SectionTitle>Space</SectionTitle>
          <Segmented
            options={spaces.map((s) => ({
              value: s.id,
              label: s.kind === 'personal' ? 'Personal' : s.name,
              testID: `template-space-${s.slug}`,
            }))}
            value={space?.id ?? ''}
            onChange={setSpaceId}
          />
        </>
      ) : null}
      {space === null ? <ErrorText>You can&apos;t add apps to any of your spaces.</ErrorText> : null}

      {actionError ? <ErrorText testID="template-error">{actionError}</ErrorText> : null}

      <ActionButton
        label="Create app"
        variant="primary"
        onPress={create}
        busy={busyKey === 'create'}
        disabled={!canCreate}
        testID="template-create"
      />
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: theme.bg },
  body: { width: '100%', maxWidth: 640, alignSelf: 'center', padding: 16, gap: 12 },
  input: {
    color: theme.text,
    fontSize: 16,
    paddingVertical: 8,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: theme.border,
  },
});
