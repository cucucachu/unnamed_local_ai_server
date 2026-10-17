import { act, type ReactTestRenderer } from 'react-test-renderer';

import type { AdminTool, DeliveredRoutine, MyTool } from '@/lib/platform';

import { exists, flush, isDisabled, mockFetchRoutes, press, render, requestsTo, textOf } from '../../../../../test-utils/screen';

jest.mock('expo-router', () => ({
  useRouter: () => ({ back: jest.fn(), push: jest.fn(), replace: jest.fn(), canGoBack: () => true }),
}));

const mockWithStepUp = jest.fn((fn: () => Promise<unknown>) => fn());
jest.mock('@/components/StepUpProvider', () => ({
  useStepUp: () => ({ withStepUp: mockWithStepUp }),
}));

// eslint-disable-next-line import/first -- must follow the jest.mock calls above
import AgentToolsScreen from '../agent-tools';
// eslint-disable-next-line import/first -- must follow the jest.mock calls above
import PermissionsScreen from '../permissions';

function mine(name: string, overrides: Partial<MyTool> = {}): MyTool {
  return {
    name,
    label: name,
    description: `About ${name}`,
    group: 'Files',
    read_only: false,
    enabled: true,
    permission: 'ask',
    enabled_locked: false,
    permission_locked: false,
    ...overrides,
  };
}

function admin(name: string, overrides: Partial<AdminTool> = {}): AdminTool {
  return {
    name,
    label: name,
    description: `About ${name}`,
    group: 'Files',
    read_only: false,
    enabled: true,
    ceiling: 'allow',
    ...overrides,
  };
}

async function toggle(renderer: ReactTestRenderer, testID: string, value: boolean): Promise<void> {
  await act(async () => {
    renderer.root.find((node) => node.props.testID === testID && typeof node.props.onValueChange === 'function').props.onValueChange(value);
  });
  await flush();
}

function switchOf(renderer: ReactTestRenderer, testID: string) {
  return renderer.root.find((node) => node.props.testID === testID && typeof node.props.onValueChange === 'function').props;
}

let renderer: ReactTestRenderer | null = null;

beforeEach(() => {
  mockWithStepUp.mockReset();
  mockWithStepUp.mockImplementation((fn) => fn());
});

afterEach(() => {
  act(() => renderer?.unmount());
  renderer = null;
});

describe('PermissionsScreen', () => {
  it('lists tools by group and changes one', async () => {
    let tools = [mine('ls', { read_only: true, permission: 'allow' }), mine('write_file'), mine('web_search', { group: 'Web' })];
    const fetchMock = mockFetchRoutes({
      'GET /api/platform/tools': () => ({ body: { tools } }),
      'PATCH /api/platform/tools': (body) => {
        const [change] = (body as { changes: { name: string; permission?: 'ask' | 'allow'; enabled?: boolean }[] }).changes;
        tools = tools.map((t) => (t.name === change.name ? { ...t, ...change } : t));
        return { body: { tools } };
      },
    });
    renderer = await render(PermissionsScreen);

    expect(textOf(renderer)).toContain('Files');
    expect(textOf(renderer)).toContain('Web');
    expect(textOf(renderer)).toContain('About write_file');

    await press(renderer, 'tool-permission-write_file-allow');
    expect(requestsTo(fetchMock, 'PATCH', '/api/platform/tools')).toEqual([
      { changes: [{ name: 'write_file', permission: 'allow' }] },
    ]);

    await toggle(renderer, 'tool-enabled-web_search', false);
    expect(requestsTo(fetchMock, 'PATCH', '/api/platform/tools')[1]).toEqual({
      changes: [{ name: 'web_search', enabled: false }],
    });
    expect(exists(renderer, 'tool-permission-web_search-ask')).toBe(false);
  });

  it("shows what an admin decided, and can't change it", async () => {
    mockFetchRoutes({
      'GET /api/platform/tools': {
        body: {
          tools: [
            mine('execute_code', { enabled: false, enabled_locked: true }),
            mine('delete', { permission_locked: true }),
          ],
        },
      },
    });
    renderer = await render(PermissionsScreen);

    expect(switchOf(renderer, 'tool-enabled-execute_code').disabled).toBe(true);
    expect(exists(renderer, 'tool-enabled-locked-execute_code')).toBe(true);
    expect(exists(renderer, 'tool-permission-execute_code-ask')).toBe(false);
    expect(textOf(renderer)).toContain('Turned off by an admin.');

    expect(isDisabled(renderer, 'tool-permission-delete-allow')).toBe(true);
    expect(textOf(renderer)).toContain('An admin requires asking first.');
  });

  it('shows the refusal when the admin changed it meanwhile', async () => {
    mockFetchRoutes({
      'GET /api/platform/tools': { body: { tools: [mine('write_file')] } },
      'PATCH /api/platform/tools': { status: 422, body: { detail: 'locked_by_admin' } },
    });
    renderer = await render(PermissionsScreen);
    await press(renderer, 'tool-permission-write_file-allow');
    expect(textOf(renderer)).toContain('An admin has set this for everyone.');
  });
});

describe('AgentToolsScreen', () => {
  it('loads through step-up and sets a tool for everyone', async () => {
    let tools = [admin('execute_code'), admin('delete')];
    const fetchMock = mockFetchRoutes({
      'GET /api/platform/admin/tools': () => ({ body: { tools } }),
      'GET /api/platform/delivered-routines': { body: { routines: [] } },
      'PATCH /api/platform/admin/tools': (body) => {
        const [change] = (body as { changes: { name: string; ceiling?: 'ask' | 'allow'; enabled?: boolean }[] }).changes;
        tools = tools.map((t) => (t.name === change.name ? { ...t, ...change } : t));
        return { body: { tools } };
      },
    });
    renderer = await render(AgentToolsScreen);
    expect(mockWithStepUp).toHaveBeenCalled();

    await press(renderer, 'admin-tool-ceiling-delete-ask');
    expect(requestsTo(fetchMock, 'PATCH', '/api/platform/admin/tools')).toEqual([
      { changes: [{ name: 'delete', ceiling: 'ask' }] },
    ]);

    await toggle(renderer, 'admin-tool-enabled-execute_code', false);
    expect(requestsTo(fetchMock, 'PATCH', '/api/platform/admin/tools')[1]).toEqual({
      changes: [{ name: 'execute_code', enabled: false }],
    });
    expect(textOf(renderer)).toContain('Off for everyone.');
    expect(mockWithStepUp).toHaveBeenCalledTimes(3);
  });

  it('turns a delivered routine off for everyone', async () => {
    const memory: DeliveredRoutine = {
      key: 'memory',
      name: 'Memory',
      description: 'Keeps notes from your chats each night.',
      enabled_by_admin: true,
    };
    const fetchMock = mockFetchRoutes({
      'GET /api/platform/admin/tools': { body: { tools: [admin('read_file')] } },
      'GET /api/platform/delivered-routines': { body: { routines: [memory] } },
      'PUT /api/platform/admin/delivered-routines/memory': (body) => ({
        body: { ...memory, enabled_by_admin: (body as { enabled: boolean }).enabled },
      }),
    });
    renderer = await render(AgentToolsScreen);
    expect(textOf(renderer)).toContain('Delivered routines');
    expect(textOf(renderer)).toContain('Keeps notes from your chats each night.');
    expect(switchOf(renderer, 'admin-delivered-enabled-memory').value).toBe(true);

    await toggle(renderer, 'admin-delivered-enabled-memory', false);
    expect(requestsTo(fetchMock, 'PUT', '/api/platform/admin/delivered-routines/memory')).toEqual([
      { enabled: false },
    ]);
    expect(switchOf(renderer, 'admin-delivered-enabled-memory').value).toBe(false);
    expect(textOf(renderer)).toContain('Off for everyone.');
  });

  it('shows no section when nothing is delivered', async () => {
    mockFetchRoutes({
      'GET /api/platform/admin/tools': { body: { tools: [admin('read_file')] } },
      'GET /api/platform/delivered-routines': { body: { routines: [] } },
    });
    renderer = await render(AgentToolsScreen);
    expect(exists(renderer, 'admin-delivered-routines')).toBe(false);
  });
});
