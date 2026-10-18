import Ionicons from '@expo/vector-icons/Ionicons';
import { useLocalSearchParams, useNavigation, useRouter } from 'expo-router';
import { useCallback, useEffect, useState } from 'react';
import { ActivityIndicator, Alert, Platform, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';

import { AppKeyboardAvoidingView } from '@/components/AppKeyboardAvoidingView';
import { Markdown } from '@/components/Markdown';
import { ApiError } from '@/lib/api';
import { describeFilesError, parentPath } from '@/lib/files';
import {
  NotTextError,
  copyPathFor,
  isMarkdownName,
  loadTextFile,
  saveTextFile,
  type TextFile,
} from '@/lib/textFiles';
import { monospaceFontFamily, theme } from '@/lib/theme';

type Load = { state: 'loading' } | { state: 'error'; message: string } | { state: 'done'; file: TextFile };

function loadErrorMessage(error: unknown): string {
  if (error instanceof NotTextError) {
    return error.reason === 'too_large'
      ? 'This file is too large to open here (1 MB at most). Download it instead.'
      : "This file isn't text. Download it instead.";
  }
  if (error instanceof ApiError) return describeFilesError(error.detail);
  return "Couldn't load this file.";
}

/** `Alert` (native) / `window.confirm` (web), as in `files.tsx`. */
function confirmDiscard(): Promise<boolean> {
  const message = 'Discard your unsaved changes?';
  if (Platform.OS === 'web') return Promise.resolve(window.confirm(message));
  return new Promise((resolve) => {
    Alert.alert('Unsaved changes', message, [
      { text: 'Keep editing', style: 'cancel', onPress: () => resolve(false) },
      { text: 'Discard', style: 'destructive', onPress: () => resolve(true) },
    ]);
  });
}

/**
 * A text file from Files (M22-10, docs/APP_TEMPLATES.md §8): markdown
 * rendered with the chat's `Markdown` (relative and `file:` links open in
 * Files), other text in monospace. The space's owners and editors get Edit,
 * a plain editor whose Save sends the version it loaded (`if_version`). If
 * the file changed since, they choose: reload theirs, keep mine
 * (overwrite), or save mine as a copy. Leaving with unsaved changes asks
 * first.
 */
export default function FileScreen() {
  const { path: pathParam } = useLocalSearchParams<{ path?: string | string[] }>();
  const path = (Array.isArray(pathParam) ? pathParam[0] : pathParam) ?? '';
  const router = useRouter();
  const navigation = useNavigation();
  const [load, setLoad] = useState<{ path: string; load: Load }>({ path: '', load: { state: 'loading' } });
  const [draft, setDraft] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [notice, setNotice] = useState<{ text: string; ok: boolean } | null>(null);

  const current: Load = load.path === path ? load.load : { state: 'loading' };
  const file = current.state === 'done' ? current.file : null;
  const dirty = file !== null && draft !== null && draft !== file.text;

  const fetchFile = useCallback(
    (target: string) =>
      loadTextFile(target).then(
        (loaded) => setLoad({ path: target, load: { state: 'done', file: loaded } }),
        (error: unknown) => setLoad({ path: target, load: { state: 'error', message: loadErrorMessage(error) } }),
      ),
    [],
  );

  useEffect(() => {
    void fetchFile(path);
  }, [path, fetchFile]);

  const reload = () => {
    setLoad({ path: '', load: { state: 'loading' } });
    setDraft(null);
    setConflict(false);
    setNotice(null);
    void fetchFile(path);
  };

  useEffect(() => {
    if (!dirty) return;
    return navigation.addListener('beforeRemove', (event) => {
      event.preventDefault();
      void confirmDiscard().then((discard) => {
        if (discard) navigation.dispatch(event.data.action);
      });
    });
  }, [navigation, dirty]);

  useEffect(() => {
    if (Platform.OS !== 'web' || !dirty) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);

  const save = async (target: string, ifVersion: string | null) => {
    if (file === null || draft === null) return;
    setSaving(true);
    setNotice(null);
    try {
      const version = await saveTextFile(target, draft, ifVersion);
      setConflict(false);
      setDraft(null);
      if (target === file.path) {
        setLoad({ path, load: { state: 'done', file: { ...file, text: draft, version } } });
        setNotice({ text: 'Saved.', ok: true });
      } else {
        setNotice({ text: `Saved your version as ${target.split('/').pop()}.`, ok: true });
        router.setParams({ path: target });
      }
    } catch (error) {
      if (error instanceof ApiError && error.status === 409 && error.detail === 'changed') {
        setConflict(true);
      } else {
        setNotice({ text: error instanceof ApiError ? describeFilesError(error.detail) : 'Saving failed.', ok: false });
      }
    } finally {
      setSaving(false);
    }
  };

  const saveAsCopy = async () => {
    try {
      await save(await copyPathFor(path), null);
    } catch (error) {
      setNotice({ text: error instanceof ApiError ? describeFilesError(error.detail) : 'Saving failed.', ok: false });
    }
  };

  const cancelEdit = async () => {
    if (dirty && !(await confirmDiscard())) return;
    setDraft(null);
    setConflict(false);
  };

  const openInFiles = (target: string) => router.dismissTo({ pathname: '/files', params: { path: target } });

  const name = path.split('/').pop() || path;
  const editing = draft !== null;

  return (
    <AppKeyboardAvoidingView style={styles.container}>
      <View style={styles.header}>
        <Pressable
          onPress={() => router.back()}
          style={styles.headerButton}
          accessibilityRole="button"
          accessibilityLabel="Close"
          testID="file-close"
        >
          <Ionicons name="close" size={26} color={theme.text} />
        </Pressable>
        <Text style={styles.title} numberOfLines={1}>
          {dirty ? `${name} •` : name}
        </Text>
        {file && editing ? (
          <>
            <Pressable onPress={() => void cancelEdit()} accessibilityRole="button" testID="file-cancel" style={styles.textButton}>
              <Text style={styles.textButtonLabel}>Cancel</Text>
            </Pressable>
            <Pressable
              onPress={() => void save(file.path, file.version)}
              disabled={saving || !dirty}
              accessibilityRole="button"
              accessibilityState={{ disabled: saving || !dirty }}
              testID="file-save"
              style={[styles.textButton, styles.primaryButton, (saving || !dirty) && styles.disabled]}
            >
              {saving ? <ActivityIndicator size="small" color={theme.text} /> : <Text style={styles.textButtonLabel}>Save</Text>}
            </Pressable>
          </>
        ) : file?.writable ? (
          <Pressable
            onPress={() => {
              setNotice(null);
              setDraft(file.text);
            }}
            accessibilityRole="button"
            testID="file-edit"
            style={styles.textButton}
          >
            <Text style={styles.textButtonLabel}>Edit</Text>
          </Pressable>
        ) : null}
      </View>

      {conflict ? (
        <View style={styles.conflict} accessibilityRole="alert" testID="file-conflict">
          <Text style={styles.conflictText}>This file changed since you opened it.</Text>
          <View style={styles.conflictActions}>
            <ConflictButton label="Reload theirs" onPress={reload} testID="file-conflict-reload" disabled={saving} />
            <ConflictButton
              label="Keep mine"
              onPress={() => file && void save(file.path, null)}
              testID="file-conflict-overwrite"
              disabled={saving}
            />
            <ConflictButton label="Save as copy" onPress={() => void saveAsCopy()} testID="file-conflict-copy" disabled={saving} />
          </View>
        </View>
      ) : null}

      {notice ? (
        <Text style={[styles.notice, !notice.ok && styles.noticeError]} accessibilityRole="alert" testID="file-notice">
          {notice.text}
        </Text>
      ) : null}

      {current.state === 'loading' ? (
        <View style={styles.centered}>
          <ActivityIndicator size="large" color={theme.accent} />
        </View>
      ) : current.state === 'error' ? (
        <View style={styles.centered}>
          <Text style={styles.errorText} testID="file-error">
            {current.message}
          </Text>
        </View>
      ) : editing ? (
        <TextInput
          value={draft}
          onChangeText={setDraft}
          multiline
          autoCapitalize="none"
          autoCorrect={false}
          spellCheck={false}
          textAlignVertical="top"
          style={styles.editor}
          accessibilityLabel={`Edit ${name}`}
          testID="file-editor"
        />
      ) : (
        <ScrollView style={styles.body} contentContainerStyle={styles.bodyContent} testID="file-view">
          {isMarkdownName(name) ? (
            <Markdown onFileLink={openInFiles} baseDir={parentPath(current.file.path)}>
              {current.file.text}
            </Markdown>
          ) : (
            <Text selectable style={styles.mono} testID="file-text">
              {current.file.text}
            </Text>
          )}
        </ScrollView>
      )}
    </AppKeyboardAvoidingView>
  );
}

function ConflictButton({
  label,
  onPress,
  testID,
  disabled,
}: {
  label: string;
  onPress: () => void;
  testID: string;
  disabled: boolean;
}) {
  return (
    <Pressable
      onPress={onPress}
      disabled={disabled}
      accessibilityRole="button"
      testID={testID}
      style={[styles.conflictButton, disabled && styles.disabled]}
    >
      <Text style={styles.textButtonLabel}>{label}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: theme.bg,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    paddingHorizontal: 12,
    paddingVertical: 10,
    borderBottomWidth: 1,
    borderBottomColor: theme.border,
  },
  headerButton: {
    padding: 4,
  },
  title: {
    flex: 1,
    color: theme.text,
    fontSize: 16,
    fontWeight: '600',
  },
  textButton: {
    minHeight: 34,
    minWidth: 64,
    paddingHorizontal: 12,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: theme.border,
    alignItems: 'center',
    justifyContent: 'center',
  },
  primaryButton: {
    backgroundColor: theme.accent,
    borderColor: theme.accent,
  },
  textButtonLabel: {
    color: theme.text,
    fontSize: 14,
    fontWeight: '600',
  },
  disabled: {
    opacity: 0.5,
  },
  conflict: {
    margin: 12,
    padding: 12,
    gap: 10,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: theme.danger,
    backgroundColor: theme.surface,
  },
  conflictText: {
    color: theme.text,
    fontSize: 14,
  },
  conflictActions: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
  },
  conflictButton: {
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: theme.border,
  },
  notice: {
    color: theme.textMuted,
    fontSize: 13,
    paddingHorizontal: 12,
    paddingTop: 8,
  },
  noticeError: {
    color: theme.danger,
  },
  centered: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 24,
  },
  errorText: {
    color: theme.danger,
    fontSize: 15,
    textAlign: 'center',
  },
  body: {
    flex: 1,
  },
  bodyContent: {
    padding: 16,
    width: '100%',
    maxWidth: 820,
    alignSelf: 'center',
  },
  mono: {
    color: theme.text,
    fontFamily: monospaceFontFamily,
    fontSize: 13,
    lineHeight: 19,
  },
  editor: {
    flex: 1,
    color: theme.text,
    fontFamily: monospaceFontFamily,
    fontSize: 14,
    lineHeight: 20,
    padding: 16,
  },
});
