import Ionicons from '@expo/vector-icons/Ionicons';
import { useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useRef, useState } from 'react';
import { Pressable, Text, View } from 'react-native';

import { AuthField } from '@/components/AuthForm';
import {
  ActionButton,
  Badge,
  Card,
  ErrorText,
  LoadState,
  SectionTitle,
  SettingsFrame,
  settingsStyles,
} from '@/components/SettingsUI';
import { createSpace, listSpaces, platformErrorMessage, slugify } from '@/lib/platform';
import { theme } from '@/lib/theme';
import { useLoad } from '@/lib/useAsync';

/** Settings → Spaces: the caller's spaces with their role, and a form to
 * create a shared space (the creator becomes its owner). */
export default function SpacesScreen() {
  const router = useRouter();
  const load = useCallback(() => listSpaces(), []);
  const { data: spaces, error, reload } = useLoad(load);

  // Membership changes on the detail screen (e.g. leaving a space) show up
  // when coming back here.
  const focusedOnce = useRef(false);
  useFocusEffect(
    useCallback(() => {
      if (focusedOnce.current) reload();
      focusedOnce.current = true;
    }, [reload]),
  );

  return (
    <SettingsFrame title="Spaces" testID="settings-spaces-screen">
      <Text style={settingsStyles.muted}>
        A space is a shared set of files. Your personal space is yours alone; shared spaces have members who can view
        or edit.
      </Text>
      <SectionTitle>Your spaces</SectionTitle>
      {spaces === null ? (
        <LoadState error={error} onRetry={reload} />
      ) : (
        <Card>
          {spaces.map((space, index) => (
            <Pressable
              key={space.id}
              onPress={() => router.push({ pathname: '/settings/spaces/[spaceId]', params: { spaceId: space.id } })}
              style={[settingsStyles.row, index === 0 && settingsStyles.firstRow]}
              accessibilityRole="button"
              testID={`space-row-${space.slug}`}
            >
              <View style={settingsStyles.rowMain}>
                <Text style={settingsStyles.rowTitle}>{space.name}</Text>
                <Text style={settingsStyles.muted}>
                  {space.kind === 'personal' ? 'Personal' : 'Shared'} · {space.slug}
                </Text>
              </View>
              {space.role ? <Badge label={space.role} tone={space.role === 'owner' ? 'accent' : 'muted'} /> : null}
              <Ionicons name="chevron-forward" size={18} color={theme.textMuted} />
            </Pressable>
          ))}
        </Card>
      )}
      <SectionTitle>New shared space</SectionTitle>
      <CreateSpaceCard
        onCreated={(id) => {
          reload();
          router.push({ pathname: '/settings/spaces/[spaceId]', params: { spaceId: id } });
        }}
      />
    </SettingsFrame>
  );
}

function CreateSpaceCard({ onCreated }: { onCreated: (id: string) => void }) {
  const [name, setName] = useState('');
  const [slug, setSlug] = useState('');
  const [slugEdited, setSlugEdited] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const effectiveSlug = slugEdited ? slug : slugify(name);

  async function handleCreate() {
    setBusy(true);
    setError(null);
    try {
      const space = await createSpace({ name: name.trim(), slug: effectiveSlug.trim() });
      setName('');
      setSlug('');
      setSlugEdited(false);
      onCreated(space.id);
    } catch (caught) {
      setError(platformErrorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <View style={settingsStyles.cardBody}>
        <AuthField
          label="Name"
          value={name}
          onChangeText={setName}
          autoCapitalize="words"
          maxLength={64}
          placeholder="Family photos"
          testID="space-create-name"
        />
        <AuthField
          label="Short name (can't be changed later)"
          value={effectiveSlug}
          onChangeText={(text) => {
            setSlugEdited(true);
            setSlug(text.toLowerCase());
          }}
          maxLength={40}
          placeholder="family-photos"
          testID="space-create-slug"
        />
        <ErrorText testID="space-create-error">{error}</ErrorText>
        <ActionButton
          label="Create space"
          variant="primary"
          onPress={handleCreate}
          busy={busy}
          disabled={!name.trim() || !effectiveSlug.trim()}
          testID="space-create-submit"
        />
      </View>
    </Card>
  );
}
