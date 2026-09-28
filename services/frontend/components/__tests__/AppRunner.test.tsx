import { act, create, type ReactTestRenderer } from 'react-test-renderer';

import type { Space } from '@/lib/platform';

import { exists, flush, press, textOf } from '../../test-utils/screen';

const mockSandboxes: Record<string, any>[] = [];
jest.mock('@/components/AppSandbox', () => {
  const React = jest.requireActual('react');
  const { View } = jest.requireActual('react-native');
  return {
    AppSandbox: (props: Record<string, any>) => {
      // One entry per mounted sandbox (a Reload remounts it).
      const [registered] = React.useState(() => mockSandboxes.push(props));
      void registered;
      return React.createElement(View, { testID: 'app-sandbox' });
    },
  };
});

const mockLoadDocument = jest.fn();
const mockLoadCode = jest.fn();
jest.mock('@/lib/appHost', () => ({
  ...jest.requireActual('@/lib/appHost'),
  loadInstanceDocument: (...args: unknown[]) => mockLoadDocument(...args),
  loadInstanceCode: (...args: unknown[]) => mockLoadCode(...args),
}));

const mockSockets: { onFrame: (frame: string) => void; close: jest.Mock }[] = [];
jest.mock('@/lib/platformEvents', () => ({
  openPlatformEvents: (onFrame: (frame: string) => void) => {
    const socket = { onFrame, close: jest.fn() };
    mockSockets.push(socket);
    return socket;
  },
}));

// eslint-disable-next-line import/first -- must follow the jest.mock calls above
import { AppRunner } from '../AppRunner';

const INSTANCE = '11111111-1111-4111-8111-111111111111';
const space = (role: Space['role']): Space => ({
  id: 's1',
  slug: 'home',
  name: 'Home',
  kind: 'shared',
  gid: 3000,
  owner_user_id: null,
  role,
  created_at: '',
  archived_at: null,
});

function fakeHost() {
  return { receive: jest.fn(), event: jest.fn(), dbChanged: jest.fn(), loadBundle: jest.fn(), setSpace: jest.fn(), close: jest.fn() };
}

let renderer: ReactTestRenderer | null = null;
async function mount(role: Space['role'] = 'owner') {
  const s = space(role);
  await act(async () => {
    renderer = create(<AppRunner instanceId={INSTANCE} space={s} />);
  });
  await flush();
  return renderer!;
}

beforeEach(() => {
  mockSandboxes.length = 0;
  mockSockets.length = 0;
  mockLoadDocument.mockReset().mockResolvedValue({ appId: 'app-1', version: '1.0.0', html: '<html>v1</html>' });
  mockLoadCode.mockReset().mockResolvedValue('__homeai_define(v2)');
});

afterEach(() => {
  act(() => renderer?.unmount());
  renderer = null;
});

describe('AppRunner', () => {
  it('opens the fixed instance, relays its events, and hot-reloads its app’s builds', async () => {
    const r = await mount();
    expect(mockLoadDocument).toHaveBeenCalledWith(INSTANCE, expect.objectContaining({ id: 's1' }));
    expect(mockSandboxes).toHaveLength(1);
    expect(mockSandboxes[0]).toMatchObject({ instanceId: INSTANCE, html: '<html>v1</html>', readOnly: false });
    expect(exists(r, 'app-runner-read-only')).toBe(false);

    const host = fakeHost();
    await act(async () => mockSandboxes[0].onHost(host));
    expect(mockSockets).toHaveLength(1);
    const { onFrame } = mockSockets[0];

    await act(async () => {
      onFrame('{"type":"ready"}');
      onFrame(JSON.stringify({ type: 'db_changed', instance_id: 'someone-else' }));
      onFrame(JSON.stringify({ type: 'db_changed', instance_id: INSTANCE }));
      onFrame(JSON.stringify({ type: 'app_built', app_id: 'other-app', version: '9' }));
      onFrame(JSON.stringify({ type: 'app_built', app_id: 'app-1', version: '1.0.1' }));
    });
    await flush();
    expect(host.dbChanged).toHaveBeenCalledTimes(2);
    expect(mockLoadCode).toHaveBeenCalledWith(INSTANCE);
    expect(host.loadBundle).toHaveBeenCalledWith('__homeai_define(v2)');

    act(() => renderer?.unmount());
    renderer = null;
    expect(mockSockets[0].close).toHaveBeenCalled();
  });

  it('covers a crashed app with an error overlay whose Reload starts a fresh sandbox', async () => {
    const r = await mount();
    await act(async () => mockSandboxes[0].onEvent('runtime.error', { message: 'boom: items is undefined', stack: '', componentStack: '' }));
    expect(exists(r, 'app-error-overlay')).toBe(true);
    expect(textOf(r)).toContain('boom: items is undefined');

    mockLoadDocument.mockResolvedValue({ appId: 'app-1', version: '1.0.1', html: '<html>v2</html>' });
    await press(r, 'app-error-reload');
    expect(exists(r, 'app-error-overlay')).toBe(false);
    expect(mockLoadDocument).toHaveBeenCalledTimes(2);
    expect(mockSandboxes).toHaveLength(2);
    expect(mockSandboxes[1].html).toBe('<html>v2</html>');

    // A hot reload that renders again clears it too.
    await act(async () => mockSandboxes[1].onEvent('runtime.error', { message: 'again', stack: '', componentStack: '' }));
    expect(exists(r, 'app-error-overlay')).toBe(true);
    await act(async () => mockSandboxes[1].onEvent('runtime.ready', { version: 2, renderMs: 3 }));
    expect(exists(r, 'app-error-overlay')).toBe(false);
  });

  it('shows the overlay when the web frame is killed for navigating', async () => {
    const r = await mount();
    await act(async () => mockSandboxes[0].onKilled());
    expect(textOf(r)).toContain('This app was stopped');
  });

  it('opens viewers read-only', async () => {
    const r = await mount('viewer');
    expect(mockSandboxes[0].readOnly).toBe(true);
    expect(exists(r, 'app-runner-read-only')).toBe(true);
  });

  it('says so when the app has never been built, and retries', async () => {
    mockLoadDocument.mockRejectedValueOnce(Object.assign(new Error('no_bundle'), { code: 'no_bundle' }));
    const r = await mount();
    expect(textOf(r)).toContain("This app hasn't been built yet.");
    expect(mockSandboxes).toHaveLength(0);
    await press(r, 'app-runner-retry');
    expect(mockSandboxes).toHaveLength(1);
  });
});
