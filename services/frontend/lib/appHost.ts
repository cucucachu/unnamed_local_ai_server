import {
  createBridgeHost,
  fetchBundle,
  fetchRuntime,
  platformForward,
  platformImages,
  sandboxDocument,
  type BridgeHost,
  type BridgeHostOptions,
  type FetchLike,
  type Forward,
  type ImageHost,
  type PlatformOptions,
  type Member as SandboxMember,
  type Space as SandboxSpace,
  type User as SandboxUser,
} from '@homeai/sdk/host';

import { apiBase } from './api';
import { pickPhoto } from './pickPhoto';
import { getMe, isReadOnly, listMembers, type Space } from './platform';
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

let picking = false;

/** The bridge's photo store (`image.*`), bound to `instanceId`: one picker open at a time. */
export function instanceImages(instanceId: string): ImageHost {
  const store = platformImages(instanceId, platformOptions());
  return {
    async pick(source) {
      if (picking) throw Object.assign(new Error('a photo picker is already open'), { code: 'busy' });
      picking = true;
      try {
        const photo = await pickPhoto(source);
        if (!photo) return null;
        return { id: await store.upload(photo.base64), width: photo.width, height: photo.height };
      } finally {
        picking = false;
      }
    },
    get: store.dataUrl,
    remove: store.remove,
  };
}

/** A bridge host for `instanceId` (native: `send` injects into the WebView). */
export function instanceBridge(
  instanceId: string,
  options: Omit<BridgeHostOptions, 'forward' | 'images'>,
): BridgeHost {
  return createBridgeHost({ ...options, forward: instanceForward(instanceId), images: instanceImages(instanceId) });
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

/** Who the app shows as the user and the space's members (`useUser` / `useMembers`):
 * display only, so a failure leaves them empty rather than failing the app. */
async function sandboxPeople(space: Space): Promise<{ user: SandboxUser | null; members: SandboxMember[] }> {
  const [me, members] = await Promise.all([getMe().catch(() => null), listMembers(space.id).catch(() => [])]);
  return {
    user: me && { id: me.id, username: me.username, name: me.display_name || me.username },
    members: members.map((m) => ({ id: m.user_id, username: m.username, name: m.display_name || m.username, role: m.role })),
  };
}

/** The instance's current bundle and its SDK's runtime, as one sandbox document. `colorScheme` is
 * the device's, which apps follow unless their app.json pins a `userInterfaceStyle`. */
export async function loadInstanceDocument(
  instanceId: string,
  space: Space,
  colorScheme: 'light' | 'dark' = 'light',
): Promise<InstanceDocument> {
  const options = platformOptions();
  const [bundle, people] = await Promise.all([fetchBundle(instanceId, options), sandboxPeople(space)]);
  const runtime = await fetchRuntime(bundle.sdk, options);
  const html = sandboxDocument({ runtime, app: bundle.code, config: { initialPath: '/', space: sandboxSpace(space), ...people, colorScheme } });
  return { appId: bundle.app_id, version: bundle.version, html };
}

/** The newest bundle's code, for hot reload. */
export async function loadInstanceCode(instanceId: string): Promise<string> {
  return (await fetchBundle(instanceId, platformOptions())).code;
}
