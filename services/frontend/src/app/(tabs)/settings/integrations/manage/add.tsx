import { useRouter } from 'expo-router';
import { useState } from 'react';
import { StyleSheet, Switch, Text, View } from 'react-native';

import { AuthField } from '@/components/AuthForm';
import { Notice, PlainHttpWarning, useIntegrationStyles, useNoticeTextStyle } from '@/components/IntegrationParts';
import {
  ActionButton,
  Card,
  ErrorText,
  SectionTitle,
  Segmented,
  SettingsFrame,
  useSettingsStyles,
} from '@/components/SettingsUI';
import { useStepUp } from '@/components/StepUpProvider';
import {
  addressFacts,
  adminCheckIntegration,
  adminEditIntegration,
  adminInstallIntegration,
  adminRefreshIntegration,
  adminSetSharedCredential,
  adminStartSharedSignIn,
  adminUninstallIntegration,
  connectIntegration,
  formatFingerprint,
  needsException,
  oauthHostsLine,
  openSignIn,
  sameMachineFirewallCommand,
  startMySignIn,
  type AddressFacts,
  type AdminIntegration,
  type ServerCheck,
} from '@/lib/integrations';
import { errorCodeMessage, platformErrorMessage, slugify } from '@/lib/platform';
import { StepUpCancelledError } from '@/lib/stepUp';

type SignIn = 'per_user' | 'shared';
type Method = 'oauth' | 'token';

/** The check found an OAuth sign-in HomeAI can use. */
function oauthUsable(check: ServerCheck | null): boolean {
  return check?.auth === 'oauth' && !!check.oauth && !check.oauth.problem;
}

/** Settings → Integrations → Manage → Add (docs/INTEGRATIONS.md §4).
 *
 * The egress proxy only lets the gateway reach registered servers, so
 * "Check server" installs first (no sign-in, nothing visible to the agent:
 * new tools start off), then asks the gateway what it finds. Cancel
 * uninstalls it again. */
export default function AddIntegrationScreen() {
  const noticeTextStyle = useNoticeTextStyle();
  const settingsStyles = useSettingsStyles();
  const router = useRouter();
  const { withStepUp } = useStepUp();
  const [name, setName] = useState('');
  const [slug, setSlug] = useState('');
  const [slugEdited, setSlugEdited] = useState(false);
  const [url, setUrl] = useState('');
  const [allowPrivate, setAllowPrivate] = useState(false);
  const [installed, setInstalled] = useState<AdminIntegration | null>(null);
  const [check, setCheck] = useState<ServerCheck | null>(null);
  const [signIn, setSignIn] = useState<SignIn>('per_user');
  const [method, setMethod] = useState<Method>('oauth');
  const [header, setHeader] = useState('');
  const [secret, setSecret] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const effectiveSlug = slugEdited ? slug : slugify(name).replace(/^[^a-z]+/, '');
  const facts = addressFacts(url);
  const exceptionNeeded = needsException(facts) || check?.problem === 'address_refused';
  const canCheck =
    name.trim() !== '' && effectiveSlug !== '' && facts.refused === null && (!exceptionNeeded || allowPrivate);

  async function step(key: string, fn: () => Promise<void>): Promise<boolean> {
    setBusy(key);
    setError(null);
    try {
      await fn();
      return true;
    } catch (caught) {
      if (!(caught instanceof StepUpCancelledError)) setError(platformErrorMessage(caught));
      return false;
    } finally {
      setBusy(null);
    }
  }

  async function runCheck(target: AdminIntegration) {
    setCheck(null);
    const found = await withStepUp(() => adminCheckIntegration(target.id));
    setMethod(oauthUsable(found) ? 'oauth' : 'token');
    setCheck(found);
  }

  function handleCheck() {
    return step('check', async () => {
      const fields = { name: name.trim(), slug: effectiveSlug, url: url.trim(), private_address: allowPrivate };
      let target = installed;
      if (target === null) {
        target = await withStepUp(() =>
          adminInstallIntegration({ ...fields, credential_model: 'none', auth_method: 'none' }),
        );
      } else if (
        target.name !== fields.name ||
        target.slug !== fields.slug ||
        target.url !== fields.url ||
        target.private_address !== fields.private_address
      ) {
        const id = target.id;
        target = await withStepUp(() => adminEditIntegration(id, fields));
      }
      setInstalled(target);
      await runCheck(target);
    });
  }

  function handleTrust(fingerprint: string) {
    if (installed === null) return;
    const id = installed.id;
    return step('trust', async () => {
      const target = await withStepUp(() => adminEditIntegration(id, { cert_fingerprint: fingerprint }));
      setInstalled(target);
      await runCheck(target);
    });
  }

  async function handleFinish() {
    if (installed === null || check === null) return;
    const id = installed.id;
    if (check.auth !== 'none' && method === 'oauth') {
      await step('finish', async () => {
        await withStepUp(() =>
          adminEditIntegration(id, { credential_model: signIn, auth_method: 'oauth', token_header: null }),
        );
        const { authorize_url } =
          signIn === 'shared' ? await withStepUp(() => adminStartSharedSignIn(id)) : await startMySignIn(id);
        router.replace({ pathname: '/settings/integrations/manage/[integrationId]', params: { integrationId: id } });
        await openSignIn(authorize_url);
      });
      return;
    }
    const ok = await step('finish', async () => {
      if (check.auth !== 'none') {
        await withStepUp(() =>
          adminEditIntegration(id, {
            credential_model: signIn,
            auth_method: 'token',
            token_header: header.trim() || null,
          }),
        );
        if (signIn === 'shared') await withStepUp(() => adminSetSharedCredential(id, secret.trim()));
        else await connectIntegration(id, secret.trim());
        setSecret('');
      }
      await withStepUp(() => adminRefreshIntegration(id));
    });
    if (ok) {
      router.replace({ pathname: '/settings/integrations/manage/[integrationId]', params: { integrationId: id } });
    }
  }

  async function handleCancel() {
    if (installed !== null) {
      const id = installed.id;
      if (!(await step('cancel', () => withStepUp(() => adminUninstallIntegration(id))))) return;
    }
    router.back();
  }

  const reachable = check?.reachable === true;
  const needsSignIn = reachable && check.auth !== 'none';
  const signsIn = needsSignIn && method === 'oauth';
  const needsToken = needsSignIn && !signsIn;

  return (
    <SettingsFrame title="Add integration" backTo="/settings/integrations/manage" testID="settings-add-integration-screen">
      <SectionTitle>Server</SectionTitle>
      <Card>
        <View style={settingsStyles.cardBody}>
          <AuthField
            label="Name"
            value={name}
            onChangeText={setName}
            autoCapitalize="words"
            maxLength={80}
            placeholder="Home Assistant"
            testID="add-integration-name"
          />
          <AuthField
            label="Short name the agent uses"
            value={effectiveSlug}
            onChangeText={(text) => {
              setSlugEdited(true);
              setSlug(text.toLowerCase());
            }}
            maxLength={40}
            placeholder="home-assistant"
            testID="add-integration-slug"
          />
          <AuthField
            label="MCP address"
            value={url}
            onChangeText={(text) => {
              setUrl(text);
              setCheck(null);
            }}
            keyboardType="url"
            maxLength={2000}
            placeholder="http://192.168.0.20:8123/api/mcp"
            testID="add-integration-url"
          />
          <AddressHints facts={facts} url={url} />
          {exceptionNeeded && facts.refused === null ? (
            <Notice testID="add-integration-private">
              <Text style={noticeTextStyle}>
                {facts.kind === 'name'
                  ? `If ${facts.host} is on your home network, HomeAI needs your permission to reach it.`
                  : `${facts.host} is on your home network.`}{' '}
                Allowing it opens exactly {facts.host}:{facts.port} to the integrations service, and nothing else on
                that device. If it&apos;s this HomeAI server itself, make sure that port isn&apos;t one of HomeAI&apos;s
                own services.
              </Text>
              <View style={styles.switchRow}>
                <Text style={[settingsStyles.rowTitle, styles.switchLabel]}>
                  Allow {facts.host}:{facts.port}
                </Text>
                <Switch
                  value={allowPrivate}
                  onValueChange={(value) => {
                    setAllowPrivate(value);
                    setCheck(null);
                  }}
                  accessibilityLabel={`Allow ${facts.host}:${facts.port}`}
                  testID="add-integration-allow-private"
                />
              </View>
            </Notice>
          ) : null}
          {facts.scheme === 'http' && facts.refused === null ? <PlainHttpWarning /> : null}
          <ActionButton
            label={installed ? 'Check again' : 'Check server'}
            variant={check ? 'secondary' : 'primary'}
            busy={busy === 'check'}
            disabled={!canCheck || busy !== null}
            onPress={handleCheck}
            testID="add-integration-check"
          />
        </View>
      </Card>

      {check ? <CheckResult check={check} facts={facts} busy={busy} onTrust={handleTrust} /> : null}

      {reachable ? (
        <>
          <SectionTitle>Sign-in</SectionTitle>
          <Card>
            <View style={settingsStyles.cardBody}>
              {needsSignIn ? (
                <>
                  {oauthUsable(check) ? (
                    <Segmented<Method>
                      value={method}
                      onChange={setMethod}
                      options={[
                        { value: 'oauth', label: 'Sign in', testID: 'add-integration-method-oauth' },
                        { value: 'token', label: 'Token', testID: 'add-integration-method-token' },
                      ]}
                    />
                  ) : null}
                  <Text style={settingsStyles.muted}>Who signs in to {name.trim() || 'it'}?</Text>
                  <Segmented<SignIn>
                    value={signIn}
                    onChange={setSignIn}
                    options={[
                      { value: 'per_user', label: 'Each person', testID: 'add-integration-signin-per-user' },
                      { value: 'shared', label: 'One for everyone', testID: 'add-integration-signin-shared' },
                    ]}
                  />
                  <Text style={settingsStyles.muted}>
                    {signIn === 'per_user'
                      ? `Everyone connects their own account, so the server's own permissions apply to each person. ${signsIn ? 'Sign in with yours now' : 'Enter yours now'} so HomeAI can find the tools.`
                      : signsIn
                        ? 'One household sign-in, used for everyone. The server sees every request as the same account.'
                        : 'One household token, used for everyone. The server sees every request as the same account.'}
                  </Text>
                  {signsIn ? (
                    <Text style={settingsStyles.muted} testID="add-integration-oauth-next">
                      {oauthHostsLine(check.oauth ?? {})} The sign-in page opens next; when you come back, HomeAI has
                      found the tools.
                    </Text>
                  ) : null}
                </>
              ) : null}
              {needsToken ? (
                <>
                  <AuthField
                    label={signIn === 'per_user' ? 'Your token' : 'Household token'}
                    value={secret}
                    onChangeText={setSecret}
                    secureTextEntry
                    autoComplete="off"
                    placeholder="Paste it here"
                    testID="add-integration-secret"
                  />
                  <AuthField
                    label="Header (optional)"
                    value={header}
                    onChangeText={setHeader}
                    maxLength={64}
                    placeholder="Authorization: Bearer"
                    testID="add-integration-header"
                  />
                  <Text style={settingsStyles.muted}>
                    Leave the header empty unless the server wants the key somewhere else, such as X-Api-Key.
                  </Text>
                </>
              ) : needsSignIn ? null : (
                <Text style={settingsStyles.muted} testID="add-integration-no-signin">
                  This server answers without signing in, so anyone here can use it.
                </Text>
              )}
              <ActionButton
                label={signsIn ? 'Sign in' : 'Find tools'}
                variant="primary"
                busy={busy === 'finish'}
                disabled={busy !== null || (needsToken && !secret.trim())}
                onPress={handleFinish}
                testID="add-integration-finish"
              />
              <Text style={settingsStyles.muted}>
                Tools it finds start off. You choose which to turn on next.
              </Text>
            </View>
          </Card>
        </>
      ) : null}

      <ErrorText testID="add-integration-error">{error}</ErrorText>
      <ActionButton
        label="Cancel"
        busy={busy === 'cancel'}
        disabled={busy !== null}
        onPress={handleCancel}
        testID="add-integration-cancel"
      />
    </SettingsFrame>
  );
}

function AddressHints({ facts, url }: { facts: ReturnType<typeof addressFacts>; url: string }) {
  if (!url.trim() || facts.refused === null) return null;
  if (facts.refused === 'invalid_url') {
    if (!/^https?:\/\//i.test(url.trim()) && url.trim().length < 8) return null;
  }
  return <ErrorText testID="add-integration-url-hint">{errorCodeMessage(facts.refused)}</ErrorText>;
}

function CheckResult({
  check,
  facts,
  busy,
  onTrust,
}: {
  check: ServerCheck;
  facts: AddressFacts;
  busy: string | null;
  onTrust: (fingerprint: string) => void;
}) {
  const noticeTextStyle = useNoticeTextStyle();
  const integrationStyles = useIntegrationStyles();
  const settingsStyles = useSettingsStyles();
  if (check.reachable) {
    const server = check.server ?? {};
    const found = typeof server.name === 'string' ? `${server.name}${typeof server.version === 'string' ? ` ${server.version}` : ''}` : null;
    return (
      <Notice tone="info" testID="add-integration-check-ok">
        <Text style={noticeTextStyle}>
          {found ? `Found ${found}. ` : 'The server answered. '}
          {check.auth === 'none'
            ? 'It needs no sign-in.'
            : check.auth === 'oauth'
              ? oauthUsable(check)
                ? `It signs in with OAuth. ${oauthHostsLine(check.oauth ?? {})} If it also takes a token or API key (Home Assistant: a long-lived access token from your profile page), you can use that instead.`
                : `It offers OAuth sign-in, but HomeAI can't use it${check.oauth?.problem ? ` (${errorCodeMessage(check.oauth.problem)})` : ''}. If it also takes a token or API key (Home Assistant: a long-lived access token from your profile page), use that below.`
              : 'It asks for a token or API key (Home Assistant: a long-lived access token from your profile page).'}
        </Text>
      </Notice>
    );
  }
  if ((check.problem === 'certificate_untrusted' || check.problem === 'certificate_changed') && check.certificate) {
    return (
      <Notice testID="add-integration-certificate">
        <Text style={noticeTextStyle}>
          {check.problem === 'certificate_changed'
            ? 'The server now shows a different certificate from the one you confirmed.'
            : "The server's certificate isn't from a known authority. That's normal for a home device with a self-signed certificate."}{' '}
          Trust it only if this SHA-256 fingerprint matches the one the server shows:
        </Text>
        <Text style={integrationStyles.mono} selectable testID="add-integration-fingerprint">
          {formatFingerprint(check.certificate)}
        </Text>
        <ActionButton
          label="Trust this certificate"
          busy={busy === 'trust'}
          disabled={busy !== null}
          onPress={() => onTrust(check.certificate as string)}
          testID="add-integration-trust"
        />
      </Notice>
    );
  }
  const message =
    check.problem === 'address_refused'
      ? 'That name points to an address on your home network. Allow it above, then check again.'
      : errorCodeMessage(check.problem ?? 'unreachable');
  const blockedHere =
    (check.problem === 'unreachable' || check.problem === 'timeout') &&
    check.this_machine !== false &&
    (facts.kind === 'private' || check.this_machine === true);
  return (
    <Notice testID="add-integration-check-failed">
      <Text style={noticeTextStyle}>{message}</Text>
      {check.detail ? <Text style={settingsStyles.muted}>{check.detail}</Text> : null}
      {blockedHere ? (
        <View style={styles.sameMachine} testID="add-integration-same-machine">
          <Text style={noticeTextStyle}>
            {check.this_machine
              ? `${facts.host} is this HomeAI machine, and its firewall`
              : 'If this server runs on the HomeAI machine itself, that machine’s firewall'}{' '}
            stops HomeAI from reaching other services on it until you allow the port. Run this on the HomeAI
            machine, then check again:
          </Text>
          <Text style={integrationStyles.mono} selectable testID="add-integration-firewall-command">
            {sameMachineFirewallCommand(facts.host, facts.port)}
          </Text>
        </View>
      ) : null}
    </Notice>
  );
}

const styles = StyleSheet.create({
  switchRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
  },
  switchLabel: {
    flex: 1,
  },
  sameMachine: {
    gap: 8,
  },
});
