import {
  createBridgeHost,
  fetchBundle,
  fetchRuntime,
  platformForward,
  sandboxDocument,
  type BridgeHost,
  type BridgeHostOptions,
  type FetchLike,
  type Forward,
  type PlatformOptions,
  type Space as SandboxSpace,
} from '@homeai/sdk/host';

import { apiBase } from './api';
import { isReadOnly, type Space } from './platform';
import { authHeaders, notifyUnauthorized } from './session';

export { isReadOnly };

/**
 * The app host's use of `@homeai/sdk/host` (`docs/PLATFORM.md` §7 "Runtime
 * and bridge"). The runner fixes one instance id when it opens a sandbox;
 * every bridge request goes to that instance's RPC endpoint with the host's
 * own credentials (the cookie on web, the bearer on native), and nothing the
 * sandbox sends can change where.
 */

/** Like `apiFetch`, a `401` means the session is gone. */
const hostFetch: FetchLike = async (url, init) => {
  const response = await fetch(url, init);
  if (response.status === 401) notifyUnauthorized();
  return response;
};

function platformOptions(): PlatformOptions {
  return { baseUrl: apiBase(), headers: authHeaders(), fetch: hostFetch };
}

/** The bridge's `forward`, bound to `instanceId`. */
export function instanceForward(instanceId: string): Forward {
  return platformForward(instanceId, platformOptions());
}

/** A bridge host for `instanceId` (native: `send` injects into the WebView). */
export function instanceBridge(
  instanceId: string,
  options: Omit<BridgeHostOptions, 'forward'>,
): BridgeHost {
  return createBridgeHost({ ...options, forward: instanceForward(instanceId) });
}

/** `components/AppSandbox` (the iframe on web, the WebView on native). */
export interface AppSandboxProps {
  /** Fixed for the sandbox's lifetime: a different id is a new sandbox. */
  instanceId: string;
  html: string;
  readOnly: boolean;
  onEvent: NonNullable<BridgeHostOptions['onEvent']>;
  /** The live bridge host, then `null` once the sandbox is gone. */
  onHost: (host: BridgeHost | null) => void;
  /** Web: the frame navigated itself and was removed. (Native refuses the
   * navigation before it starts.) */
  onKilled: () => void;
  /** `agent.ask` from the sandbox (`askAgent`); the runner opens the panel. */
  onAskAgent?: (prompt: string) => void;
}

export function sandboxSpace(space: Space): SandboxSpace {
  return { id: space.id, slug: space.slug, name: space.name, role: isReadOnly(space) ? 'viewer' : space.role! };
}

export interface InstanceDocument {
  appId: string;
  version: string;
  html: string;
}

/** The instance's current bundle and its SDK's runtime, as one sandbox document. */
export async function loadInstanceDocument(instanceId: string, space: Space): Promise<InstanceDocument> {
  const options = platformOptions();
  const bundle = await fetchBundle(instanceId, options);
  const runtime = await fetchRuntime(bundle.sdk, options);
  const html = sandboxDocument({ runtime, app: bundle.code, config: { initialPath: '/', space: sandboxSpace(space) } });
  return { appId: bundle.app_id, version: bundle.version, html };
}

/** The newest bundle's code, for hot reload. */
export async function loadInstanceCode(instanceId: string): Promise<string> {
  return (await fetchBundle(instanceId, platformOptions())).code;
}
