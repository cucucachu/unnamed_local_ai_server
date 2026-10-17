import { AppState, Linking } from 'react-native';
import { act, type ReactTestRenderer } from 'react-test-renderer';

import { addressFacts, needsException, type AdminIntegration, type Integration, type IntegrationTool } from '@/lib/integrations';

import { exists, isDisabled, mockFetchRoutes, press, render, requestsTo, textOf, type } from '../../../../../test-utils/screen';

const mockPush = jest.fn();
const mockBack = jest.fn();
const mockReplace = jest.fn();
let mockIntegrationId = 'int-1';
let mockRole: 'admin' | 'member' = 'member';
jest.mock('expo-router', () => ({
  useRouter: () => ({ back: mockBack, push: mockPush, replace: mockReplace, canGoBack: () => true }),
  useLocalSearchParams: () => ({ integrationId: mockIntegrationId }),
  useFocusEffect: jest.fn(),
}));

jest.mock('@/components/AuthProvider', () => ({
  useAuth: () => ({
    state: { phase: 'ready', setupRequired: false, user: { id: 'u1', username: 'alice', role: mockRole } },
  }),
}));

const mockWithStepUp = jest.fn((fn: () => Promise<unknown>) => fn());
jest.mock('@/components/StepUpProvider', () => ({
  useStepUp: () => ({ withStepUp: mockWithStepUp }),
}));

// eslint-disable-next-line import/first -- must follow the jest.mock calls above
import IntegrationsScreen from '../integrations';
// eslint-disable-next-line import/first -- must follow the jest.mock calls above
import IntegrationScreen from '../integrations/[integrationId]';
// eslint-disable-next-line import/first -- must follow the jest.mock calls above
import AddIntegrationScreen from '../integrations/manage/add';
// eslint-disable-next-line import/first -- must follow the jest.mock calls above
import ManageIntegrationScreen from '../integrations/manage/[integrationId]';

const MINE = '/api/platform/integrations';
const ADMIN = '/api/platform/admin/integrations';
const TOKEN = 'llat-very-secret-token-value';

function tool(name: string, overrides: Partial<IntegrationTool> = {}): IntegrationTool {
  return {
    name,
    title: null,
    description: `About ${name}`,
    annotations: {},
    enabled: true,
    permission: 'ask',
    enabled_locked: false,
    permission_locked: false,
    ...overrides,
  };
}

function mine(overrides: Partial<Integration> = {}): Integration {
  return {
    id: 'int-1',
    slug: 'home',
    name: 'Home Assistant',
    description: 'Lights and switches',
    credential_model: 'per_user',
    auth_method: 'token',
    plain_http: false,
    status: 'not_connected',
    tools: [],
    ...overrides,
  };
}

function adminView(overrides: Partial<AdminIntegration> = {}): AdminIntegration {
  return {
    id: 'int-1',
    slug: 'home',
    name: 'Home Assistant',
    description: 'Lights and switches',
    credential_model: 'per_user',
    auth_method: 'token',
    url: 'http://192.168.0.20:8123/api/mcp',
    plain_http: true,
    cert_fingerprint: null,
    private_address: true,
    token_header: null,
    server_info: { name: 'home-assistant', version: '2026.10' },
    protocol_version: '2025-11-25',
    discovered_at: '2026-10-12T00:00:00Z',
    installed_by: 'u1',
    created_at: '2026-10-12T00:00:00Z',
    shared_credential: null,
    connections: [],
    tools: [],
    resources: [],
    prompts: [],
    ...overrides,
  };
}

async function toggle(renderer: ReactTestRenderer, testID: string, value: boolean): Promise<void> {
  await act(async () => {
    renderer.root.find((n) => n.props.testID === testID && typeof n.props.onValueChange === 'function').props.onValueChange(value);
  });
}

/** Every string a rendered node carries: text, input values, labels. */
function everything(renderer: ReactTestRenderer): string {
  return renderer.root
    .findAll(() => true)
    .flatMap((n) => [n.props.value, n.props.children, n.props.accessibilityLabel, n.props.placeholder])
    .filter((v): v is string => typeof v === 'string')
    .join(' ');
}

let renderer: ReactTestRenderer | null = null;

beforeEach(() => {
  mockPush.mockReset();
  mockBack.mockReset();
  mockReplace.mockReset();
  mockWithStepUp.mockReset();
  mockWithStepUp.mockImplementation((fn) => fn());
  mockIntegrationId = 'int-1';
  mockRole = 'member';
});

afterEach(() => {
  act(() => renderer?.unmount());
  renderer = null;
});

describe('addressFacts', () => {
  it.each([
    ['http://homeassistant.local:8123/api/mcp', 'mdns_name', false],
    ['http://127.0.0.1:8123/api/mcp', 'loopback_refused', false],
    ['http://localhost/api/mcp', 'loopback_refused', false],
    ['http://8.8.8.8/mcp', 'https_required', false],
    ['192.168.0.20:8123', 'invalid_url', false],
    ['http://192.168.0.20:8123/api/mcp', null, true],
    ['http://ha.lan:8123/api/mcp', null, true],
    ['https://ha.example.com/mcp', null, false],
    ['https://[fd00::5]/mcp', null, true],
  ])('%s', (url, refused, exception) => {
    const facts = addressFacts(url);
    expect(facts.refused).toBe(refused);
    expect(needsException(facts)).toBe(exception);
  });

  it('knows the port it would open', () => {
    expect(addressFacts('http://192.168.0.20:8123/api/mcp')).toMatchObject({ host: '192.168.0.20', port: 8123 });
    expect(addressFacts('https://ha.example.com/mcp').port).toBe(443);
  });
});

describe('IntegrationsScreen', () => {
  it('lists integrations with their status; Manage is for admins only', async () => {
    mockFetchRoutes({
      [`GET ${MINE}`]: { body: { integrations: [mine({ status: 'needs_reconnecting' })] } },
    });
    renderer = await render(IntegrationsScreen);
    expect(textOf(renderer)).toContain('Home Assistant');
    expect(textOf(renderer)).toContain('Needs reconnecting');
    expect(exists(renderer, 'integrations-nav-manage')).toBe(false);

    act(() => renderer?.unmount());
    mockRole = 'admin';
    renderer = await render(IntegrationsScreen);
    await press(renderer, 'integrations-nav-manage');
    expect(mockPush).toHaveBeenCalledWith('/settings/integrations/manage');
  });
});

describe('IntegrationScreen', () => {
  it('connects with a token that is sent once and never shown again', async () => {
    let integration = mine({ plain_http: true });
    const fetchMock = mockFetchRoutes({
      [`GET ${MINE}`]: () => ({ body: { integrations: [integration] } }),
      [`PUT ${MINE}/int-1/connection`]: () => {
        integration = { ...integration, status: 'connected' };
        return { body: { status: 'connected', checked: true, problem: null } };
      },
      [`DELETE ${MINE}/int-1/connection`]: () => {
        integration = { ...integration, status: 'not_connected' };
        return { status: 204 };
      },
    });
    renderer = await render(IntegrationScreen);
    expect(exists(renderer, 'integration-plain-http')).toBe(true);
    expect(exists(renderer, 'integration-disconnect')).toBe(false);

    await type(renderer, 'integration-token-input', `  ${TOKEN}  `);
    await press(renderer, 'integration-token-submit');
    expect(requestsTo(fetchMock, 'PUT', `${MINE}/int-1/connection`)).toEqual([{ secret: TOKEN }]);
    expect(textOf(renderer)).toContain('Connected');
    expect(everything(renderer)).not.toContain(TOKEN);

    await press(renderer, 'integration-disconnect');
    expect(requestsTo(fetchMock, 'DELETE', `${MINE}/int-1/connection`)).toHaveLength(1);
    expect(textOf(renderer)).toContain('Not connected');
  });

  it('says so when the token could be stored but not checked', async () => {
    mockFetchRoutes({
      [`GET ${MINE}`]: { body: { integrations: [mine()] } },
      [`PUT ${MINE}/int-1/connection`]: { body: { status: 'connected', checked: false, problem: 'unreachable' } },
    });
    renderer = await render(IntegrationScreen);
    await type(renderer, 'integration-token-input', TOKEN);
    await press(renderer, 'integration-token-submit');
    expect(textOf(renderer)).toContain("couldn't be asked to check it");
  });

  it('shows a rejected token and keeps the field', async () => {
    mockFetchRoutes({
      [`GET ${MINE}`]: { body: { integrations: [mine()] } },
      [`PUT ${MINE}/int-1/connection`]: { status: 422, body: { detail: 'credential_rejected' } },
    });
    renderer = await render(IntegrationScreen);
    await type(renderer, 'integration-token-input', TOKEN);
    await press(renderer, 'integration-token-submit');
    expect(textOf(renderer)).toContain('The server turned that token down.');
    const input = renderer.root.find(
      (n) => n.props.testID === 'integration-token-input' && typeof n.props.onChangeText === 'function',
    );
    expect(input.props.value).toBe(TOKEN);
  });

  it("follows the admin's limits, and allows all the rest at once", async () => {
    let integration = mine({
      status: 'connected',
      tools: [
        tool('get_state', { title: 'Get state', annotations: { readOnlyHint: true } }),
        tool('turn_on'),
        tool('unlock_door', { permission_locked: true }),
        tool('set_alarm', { enabled: false, enabled_locked: true }),
      ],
    });
    const fetchMock = mockFetchRoutes({
      [`GET ${MINE}`]: () => ({ body: { integrations: [integration] } }),
      [`PATCH ${MINE}/int-1/tools`]: (body) => {
        const changes = (body as { changes: { name: string; permission?: 'ask' | 'allow' }[] }).changes;
        integration = {
          ...integration,
          tools: integration.tools.map((t) => {
            const change = changes.find((c) => c.name === t.name);
            return change ? { ...t, ...change } : t;
          }),
        };
        return { body: { integrations: [integration] } };
      },
    });
    renderer = await render(IntegrationScreen);

    expect(textOf(renderer)).toContain('Get state');
    expect(textOf(renderer)).toContain('Says: read-only');
    expect(isDisabled(renderer, 'integration-tool-permission-unlock_door-allow')).toBe(true);
    expect(exists(renderer, 'integration-tool-locked-unlock_door')).toBe(true);
    expect(exists(renderer, 'integration-tool-set_alarm')).toBe(false);
    expect(textOf(renderer)).toContain('1 more is turned off by an admin.');

    await press(renderer, 'integration-allow-all');
    expect(requestsTo(fetchMock, 'PATCH', `${MINE}/int-1/tools`)).toEqual([
      {
        changes: [
          { name: 'get_state', permission: 'allow' },
          { name: 'turn_on', permission: 'allow' },
        ],
      },
    ]);
    expect(exists(renderer, 'integration-allow-all')).toBe(false);
  });

  it('turns every tool it may on or off at once, and says when an admin turned it off', async () => {
    let integration = mine({
      status: 'connected',
      turned_off: true,
      tools: [
        tool('get_state'),
        tool('turn_on', { enabled: false }),
        tool('unlock_door', { permission: 'allow' }),
        tool('set_alarm', { enabled: false, enabled_locked: true }),
      ],
    });
    const fetchMock = mockFetchRoutes({
      [`GET ${MINE}`]: () => ({ body: { integrations: [integration] } }),
      [`PATCH ${MINE}/int-1/tools`]: (body) => {
        const changes = (body as { changes: { name: string; enabled?: boolean }[] }).changes;
        integration = {
          ...integration,
          tools: integration.tools.map((t) => ({ ...t, ...changes.find((c) => c.name === t.name) })),
        };
        return { body: { integrations: [integration] } };
      },
    });
    renderer = await render(IntegrationScreen);
    expect(exists(renderer, 'integration-turned-off')).toBe(true);
    expect(textOf(renderer)).toContain('Turned off');

    await press(renderer, 'integration-all-off');
    await press(renderer, 'integration-all-on');
    expect(requestsTo(fetchMock, 'PATCH', `${MINE}/int-1/tools`)).toEqual([
      {
        changes: [
          { name: 'get_state', enabled: false },
          { name: 'unlock_door', enabled: false },
        ],
      },
      {
        changes: [
          { name: 'get_state', enabled: true },
          { name: 'turn_on', enabled: true },
          { name: 'unlock_door', enabled: true },
        ],
      },
    ]);
    expect(exists(renderer, 'integration-all-on')).toBe(false);
    await press(renderer, 'integration-all-ask');
    expect(requestsTo(fetchMock, 'PATCH', `${MINE}/int-1/tools`)[2]).toEqual({
      changes: [{ name: 'unlock_door', permission: 'ask' }],
    });
  });
});

describe('AddIntegrationScreen', () => {
  it('hints at .local names and needs the private-address exception for a home IP', async () => {
    mockFetchRoutes({});
    renderer = await render(AddIntegrationScreen);
    await type(renderer, 'add-integration-name', 'Home Assistant');
    await type(renderer, 'add-integration-url', 'http://homeassistant.local:8123/api/mcp');
    expect(textOf(renderer)).toContain('.local');
    expect(isDisabled(renderer, 'add-integration-check')).toBe(true);

    await type(renderer, 'add-integration-url', 'http://192.168.0.20:8123/api/mcp');
    expect(textOf(renderer)).toContain('Allow 192.168.0.20:8123');
    expect(exists(renderer, 'integration-plain-http')).toBe(true);
    expect(isDisabled(renderer, 'add-integration-check')).toBe(true);
    await toggle(renderer, 'add-integration-allow-private', true);
    expect(isDisabled(renderer, 'add-integration-check')).toBe(false);
  });

  it('installs, checks, trusts a certificate, then signs in and finds tools', async () => {
    let integration = adminView({ credential_model: 'none', auth_method: 'none', discovered_at: null, url: 'https://192.168.0.20:8123/api/mcp', plain_http: false });
    let checks = 0;
    const fingerprint = 'ab'.repeat(32);
    const fetchMock = mockFetchRoutes({
      [`POST ${ADMIN}`]: () => ({ status: 201, body: integration }),
      [`POST ${ADMIN}/int-1/check`]: () => {
        checks += 1;
        return {
          body:
            checks === 1
              ? { reachable: false, auth: null, problem: 'certificate_untrusted', detail: null, certificate: fingerprint, protocol_version: null, server: null }
              : { reachable: true, auth: 'token', problem: null, detail: null, certificate: null, protocol_version: null, server: null },
        };
      },
      [`PATCH ${ADMIN}/int-1`]: (body) => {
        integration = { ...integration, ...(body as Partial<AdminIntegration>) };
        return { body: integration };
      },
      [`PUT ${ADMIN}/int-1/shared-credential`]: { body: { status: 'connected', checked: true, problem: null } },
      [`POST ${ADMIN}/int-1/refresh`]: () => ({ body: { ...integration, discovered_at: '2026-10-12T00:00:00Z' } }),
    });
    renderer = await render(AddIntegrationScreen);
    await type(renderer, 'add-integration-name', 'Home Assistant');
    await type(renderer, 'add-integration-url', 'https://192.168.0.20:8123/api/mcp');
    await toggle(renderer, 'add-integration-allow-private', true);
    await press(renderer, 'add-integration-check');

    expect(requestsTo(fetchMock, 'POST', ADMIN)).toEqual([
      {
        name: 'Home Assistant',
        slug: 'home-assistant',
        url: 'https://192.168.0.20:8123/api/mcp',
        private_address: true,
        credential_model: 'none',
        auth_method: 'none',
      },
    ]);
    expect(textOf(renderer)).toContain('AB:AB:AB');
    expect(exists(renderer, 'add-integration-finish')).toBe(false);

    await press(renderer, 'add-integration-trust');
    expect(requestsTo(fetchMock, 'PATCH', `${ADMIN}/int-1`)).toEqual([{ cert_fingerprint: fingerprint }]);
    expect(checks).toBe(2);
    expect(textOf(renderer)).toContain('asks for a token');

    await press(renderer, 'add-integration-signin-shared');
    expect(isDisabled(renderer, 'add-integration-finish')).toBe(true);
    await type(renderer, 'add-integration-secret', TOKEN);
    await press(renderer, 'add-integration-finish');

    expect(requestsTo(fetchMock, 'PATCH', `${ADMIN}/int-1`)[1]).toEqual({
      credential_model: 'shared',
      auth_method: 'token',
      token_header: null,
    });
    expect(requestsTo(fetchMock, 'PUT', `${ADMIN}/int-1/shared-credential`)).toEqual([{ secret: TOKEN }]);
    expect(requestsTo(fetchMock, 'POST', `${ADMIN}/int-1/refresh`)).toHaveLength(1);
    expect(mockReplace).toHaveBeenCalledWith({
      pathname: '/settings/integrations/manage/[integrationId]',
      params: { integrationId: 'int-1' },
    });
  });

  it("a server that needs no sign-in goes straight to discovery; cancel uninstalls what it installed", async () => {
    const fetchMock = mockFetchRoutes({
      [`POST ${ADMIN}`]: { status: 201, body: adminView({ credential_model: 'none', auth_method: 'none', url: 'https://tools.example.com/mcp' }) },
      [`POST ${ADMIN}/int-1/check`]: {
        body: { reachable: true, auth: 'none', problem: null, detail: null, certificate: null, protocol_version: '2026-07-28', server: { name: 'fake', version: '1' } },
      },
      [`DELETE ${ADMIN}/int-1`]: { status: 204 },
    });
    renderer = await render(AddIntegrationScreen);
    await type(renderer, 'add-integration-name', 'Tools');
    await type(renderer, 'add-integration-url', 'https://tools.example.com/mcp');
    expect(exists(renderer, 'add-integration-allow-private')).toBe(false);
    await press(renderer, 'add-integration-check');
    expect(textOf(renderer)).toContain('Found fake 1');
    expect(exists(renderer, 'add-integration-no-signin')).toBe(true);
    expect(exists(renderer, 'add-integration-secret')).toBe(false);

    await press(renderer, 'add-integration-cancel');
    expect(requestsTo(fetchMock, 'DELETE', `${ADMIN}/int-1`)).toHaveLength(1);
    expect(mockBack).toHaveBeenCalled();
  });

  it('a name that points home gets the exception after the check says so', async () => {
    mockFetchRoutes({
      [`POST ${ADMIN}`]: { status: 201, body: adminView({ url: 'https://ha.home.example/mcp', private_address: false }) },
      [`POST ${ADMIN}/int-1/check`]: {
        body: { reachable: false, auth: null, problem: 'address_refused', detail: null, certificate: null, protocol_version: null, server: null },
      },
    });
    renderer = await render(AddIntegrationScreen);
    await type(renderer, 'add-integration-name', 'Home');
    await type(renderer, 'add-integration-url', 'https://ha.home.example/mcp');
    await press(renderer, 'add-integration-check');
    expect(textOf(renderer)).toContain('points to an address on your home network');
    expect(exists(renderer, 'add-integration-allow-private')).toBe(true);
  });

  it("an unreachable server on this machine gets the firewall command with its own host and port", async () => {
    let thisMachine: boolean | null = true;
    const installed = adminView({ url: 'http://192.168.0.108:8123/api/mcp', private_address: true });
    mockFetchRoutes({
      [`POST ${ADMIN}`]: { status: 201, body: installed },
      [`PATCH ${ADMIN}/int-1`]: (body) => ({ body: { ...installed, ...(body as Partial<AdminIntegration>) } }),
      [`POST ${ADMIN}/int-1/check`]: () => ({
        body: { reachable: false, this_machine: thisMachine, auth: null, problem: 'timeout', detail: null, certificate: null, protocol_version: null, server: null },
      }),
    });
    renderer = await render(AddIntegrationScreen);
    await type(renderer, 'add-integration-name', 'Home Assistant');
    await type(renderer, 'add-integration-url', 'http://192.168.0.108:8123/api/mcp');
    await toggle(renderer, 'add-integration-allow-private', true);
    await press(renderer, 'add-integration-check');
    expect(textOf(renderer)).toContain('192.168.0.108 is this HomeAI machine');
    const command = textOf(renderer);
    expect(command).toContain('docker network inspect homeai_homeai-net');
    expect(command).toContain('to 192.168.0.108 port 8123 proto tcp');

    thisMachine = null;
    await press(renderer, 'add-integration-check');
    expect(textOf(renderer)).toContain('If this server runs on the HomeAI machine itself');

    thisMachine = false;
    await press(renderer, 'add-integration-check');
    expect(exists(renderer, 'add-integration-check-failed')).toBe(true);
    expect(exists(renderer, 'add-integration-same-machine')).toBe(false);
  });
});

describe('ManageIntegrationScreen', () => {
  it('sets tools for everyone, shows hints as information, and lists connections by status only', async () => {
    let integration = adminView({
      tools: [
        { name: 'turn_on', title: 'Turn on', description: '', annotations: { destructiveHint: true }, enabled: false, ceiling: 'allow' },
      ],
      connections: [
        { user_id: 'u2', username: 'bob', display_name: 'Bob', status: 'needs_reconnecting', updated_at: '2026-10-12T00:00:00Z' },
      ],
    });
    const fetchMock = mockFetchRoutes({
      [`GET ${ADMIN}`]: () => ({ body: { integrations: [integration] } }),
      [`PATCH ${ADMIN}/int-1/tools`]: (body) => {
        const [change] = (body as { changes: { name: string; enabled?: boolean; ceiling?: 'ask' | 'allow' }[] }).changes;
        integration = { ...integration, tools: integration.tools.map((t) => ({ ...t, ...change })) };
        return { body: integration };
      },
      [`DELETE ${ADMIN}/int-1/connections/u2`]: () => {
        integration = { ...integration, connections: [] };
        return { status: 204 };
      },
      [`DELETE ${ADMIN}/int-1`]: { status: 204 },
    });
    renderer = await render(ManageIntegrationScreen);
    expect(mockWithStepUp).toHaveBeenCalled();
    expect(textOf(renderer)).toContain('Says: can delete or overwrite');
    expect(textOf(renderer)).toContain('Off for everyone.');
    expect(exists(renderer, 'integration-plain-http')).toBe(true);
    expect(textOf(renderer)).toContain('Needs reconnecting');

    await toggle(renderer, 'manage-integration-tool-enabled-turn_on', true);
    await press(renderer, 'manage-integration-tool-ceiling-turn_on-ask');
    expect(requestsTo(fetchMock, 'PATCH', `${ADMIN}/int-1/tools`)).toEqual([
      { changes: [{ name: 'turn_on', enabled: true }] },
      { changes: [{ name: 'turn_on', ceiling: 'ask' }] },
    ]);

    await press(renderer, 'manage-integration-disconnect-bob');
    expect(requestsTo(fetchMock, 'DELETE', `${ADMIN}/int-1/connections/u2`)).toHaveLength(1);
    expect(exists(renderer, 'manage-integration-no-connections')).toBe(true);

    await press(renderer, 'manage-integration-uninstall');
    expect(requestsTo(fetchMock, 'DELETE', `${ADMIN}/int-1`)).toHaveLength(0);
    expect(textOf(renderer)).toContain('deletes its tool settings and every stored token');
    await press(renderer, 'manage-integration-uninstall-confirm-button');
    expect(requestsTo(fetchMock, 'DELETE', `${ADMIN}/int-1`)).toHaveLength(1);
    expect(mockBack).toHaveBeenCalled();
  });

  it('turns all tools on or off and sets every limit at once, and turns the integration off and on', async () => {
    let integration = adminView({
      tools: [
        { name: 'get_state', title: null, description: '', annotations: {}, enabled: true, ceiling: 'allow' },
        { name: 'turn_on', title: null, description: '', annotations: {}, enabled: false, ceiling: 'allow' },
      ],
    });
    const fetchMock = mockFetchRoutes({
      [`GET ${ADMIN}`]: () => ({ body: { integrations: [integration] } }),
      [`PATCH ${ADMIN}/int-1/tools`]: (body) => {
        const changes = (body as { changes: { name: string }[] }).changes;
        integration = {
          ...integration,
          tools: integration.tools.map((t) => ({ ...t, ...changes.find((c) => c.name === t.name) })),
        };
        return { body: integration };
      },
      [`PATCH ${ADMIN}/int-1`]: (body) => {
        integration = { ...integration, ...(body as { turned_off: boolean }) };
        return { body: integration };
      },
    });
    renderer = await render(ManageIntegrationScreen);
    await press(renderer, 'manage-integration-all-on');
    await press(renderer, 'manage-integration-all-ask');
    expect(requestsTo(fetchMock, 'PATCH', `${ADMIN}/int-1/tools`)).toEqual([
      { changes: [{ name: 'turn_on', enabled: true }] },
      {
        changes: [
          { name: 'get_state', ceiling: 'ask' },
          { name: 'turn_on', ceiling: 'ask' },
        ],
      },
    ]);
    expect(exists(renderer, 'manage-integration-all-ask')).toBe(false);
    expect(exists(renderer, 'manage-integration-all-choose')).toBe(true);

    expect(exists(renderer, 'manage-integration-turned-off')).toBe(false);
    await press(renderer, 'manage-integration-turn');
    expect(requestsTo(fetchMock, 'PATCH', `${ADMIN}/int-1`)).toEqual([{ turned_off: true }]);
    expect(exists(renderer, 'manage-integration-turned-off')).toBe(true);
    await press(renderer, 'manage-integration-turn');
    expect(requestsTo(fetchMock, 'PATCH', `${ADMIN}/int-1`)[1]).toEqual({ turned_off: false });
    expect(mockWithStepUp).toHaveBeenCalled();
  });

  it('sets the shared token without ever showing it', async () => {
    let integration = adminView({ credential_model: 'shared', shared_credential: 'not_connected' });
    const fetchMock = mockFetchRoutes({
      [`GET ${ADMIN}`]: () => ({ body: { integrations: [integration] } }),
      [`PUT ${ADMIN}/int-1/shared-credential`]: () => {
        integration = { ...integration, shared_credential: 'connected' };
        return { body: { status: 'connected', checked: true, problem: null } };
      },
    });
    renderer = await render(ManageIntegrationScreen);
    expect(textOf(renderer)).toContain('Not set');
    await type(renderer, 'manage-integration-shared-input', TOKEN);
    await press(renderer, 'manage-integration-shared-submit');
    expect(requestsTo(fetchMock, 'PUT', `${ADMIN}/int-1/shared-credential`)).toEqual([{ secret: TOKEN }]);
    expect(exists(renderer, 'manage-integration-shared-clear')).toBe(true);
    expect(everything(renderer)).not.toContain(TOKEN);
  });
});

describe('OAuth sign-in', () => {
  const OAUTH = {
    issuer: 'https://agent.example/mcp/trading',
    sign_in_host: 'example.com',
    token_host: 'api.example.com',
    registration: 'dynamic' as const,
    revocable: false,
  };
  const AUTHORIZE = 'https://example.com/oauth?state=s1';
  let openURL: jest.SpyInstance;
  let appState: jest.SpyInstance;
  let listeners: ((state: string) => void)[] = [];

  beforeEach(() => {
    openURL = jest.spyOn(Linking, 'openURL').mockResolvedValue(true);
    listeners = [];
    appState = jest.spyOn(AppState, 'addEventListener').mockImplementation((_event, listener) => {
      listeners.push(listener as (state: string) => void);
      return { remove: jest.fn() } as unknown as ReturnType<typeof AppState.addEventListener>;
    });
  });

  afterEach(() => {
    openURL.mockRestore();
    appState.mockRestore();
  });

  it('signs in at the provider, and says disconnecting leaves HomeAI signed in there', async () => {
    let integration = mine({ auth_method: 'oauth', oauth: OAUTH, slug: 'trading', name: 'Trading' });
    const fetchMock = mockFetchRoutes({
      [`GET ${MINE}`]: () => ({ body: { integrations: [integration] } }),
      [`POST ${MINE}/int-1/oauth/start?app=true`]: { body: { authorize_url: AUTHORIZE } },
      [`DELETE ${MINE}/int-1/connection`]: { status: 204 },
    });
    renderer = await render(IntegrationScreen);
    expect(exists(renderer, 'integration-token-input')).toBe(false);
    expect(textOf(renderer)).toContain('Signs in on example.com; tokens from api.example.com.');
    expect(textOf(renderer)).toContain('also remove HomeAI in your example.com settings');
    await press(renderer, 'integration-sign-in');
    expect(requestsTo(fetchMock, 'POST', `${MINE}/int-1/oauth/start?app=true`)).toHaveLength(1);
    expect(openURL).toHaveBeenCalledWith(AUTHORIZE);

    integration = { ...integration, status: 'connected' };
    await act(async () => listeners.forEach((listener) => listener('active')));
    expect(textOf(renderer)).toContain('Signed in with your account');
    await press(renderer, 'integration-disconnect');
    expect(requestsTo(fetchMock, 'DELETE', `${MINE}/int-1/connection`)).toHaveLength(1);
  });

  it('the admin adds an OAuth server: sign in, then the manage screen', async () => {
    const installed = adminView({ url: 'https://agent.example/mcp/trading', private_address: false, plain_http: false });
    const fetchMock = mockFetchRoutes({
      [`POST ${ADMIN}`]: { status: 201, body: installed },
      [`PATCH ${ADMIN}/int-1`]: (body) => ({ body: { ...installed, ...(body as Partial<AdminIntegration>) } }),
      [`POST ${ADMIN}/int-1/check`]: {
        body: { reachable: true, auth: 'oauth', problem: null, detail: null, certificate: null, protocol_version: null, server: null, oauth: OAUTH },
      },
      [`POST ${MINE}/int-1/oauth/start?app=true`]: { body: { authorize_url: AUTHORIZE } },
    });
    renderer = await render(AddIntegrationScreen);
    await type(renderer, 'add-integration-name', 'Trading');
    await type(renderer, 'add-integration-url', 'https://agent.example/mcp/trading');
    await press(renderer, 'add-integration-check');
    expect(textOf(renderer)).toContain('It signs in with OAuth. Signs in on example.com');
    expect(exists(renderer, 'add-integration-secret')).toBe(false);
    expect(exists(renderer, 'add-integration-no-signin')).toBe(false);
    expect(isDisabled(renderer, 'add-integration-finish')).toBe(false);
    await press(renderer, 'add-integration-finish');
    expect(requestsTo(fetchMock, 'PATCH', `${ADMIN}/int-1`)).toEqual([
      { credential_model: 'per_user', auth_method: 'oauth', token_header: null },
    ]);
    expect(requestsTo(fetchMock, 'POST', `${ADMIN}/int-1/refresh`)).toHaveLength(0);
    expect(mockReplace).toHaveBeenCalledWith({
      pathname: '/settings/integrations/manage/[integrationId]',
      params: { integrationId: 'int-1' },
    });
    expect(openURL).toHaveBeenCalledWith(AUTHORIZE);
  });

  it("the admin can still use a token, and an OAuth sign-in HomeAI can't use says why", async () => {
    let oauth: Record<string, unknown> = OAUTH;
    const installed = adminView({ url: 'https://ha.example.com/mcp', private_address: false });
    mockFetchRoutes({
      [`POST ${ADMIN}`]: { status: 201, body: installed },
      [`PATCH ${ADMIN}/int-1`]: (body) => ({ body: { ...installed, ...(body as Partial<AdminIntegration>) } }),
      [`POST ${ADMIN}/int-1/check`]: () => ({
        body: { reachable: true, auth: 'oauth', problem: null, detail: null, certificate: null, protocol_version: null, server: null, oauth },
      }),
    });
    renderer = await render(AddIntegrationScreen);
    await type(renderer, 'add-integration-name', 'Home Assistant');
    await type(renderer, 'add-integration-url', 'https://ha.example.com/mcp');
    await press(renderer, 'add-integration-check');
    await press(renderer, 'add-integration-method-token');
    expect(exists(renderer, 'add-integration-secret')).toBe(true);
    expect(isDisabled(renderer, 'add-integration-finish')).toBe(true);

    oauth = { problem: 'oauth_unsupported', detail: 'No PKCE.' };
    await press(renderer, 'add-integration-check');
    expect(textOf(renderer)).toContain("It offers OAuth sign-in, but HomeAI can't use it");
    expect(exists(renderer, 'add-integration-method-oauth')).toBe(false);
    expect(exists(renderer, 'add-integration-secret')).toBe(true);
  });

  it('the household sign-in is started by the admin', async () => {
    const fetchMock = mockFetchRoutes({
      [`GET ${ADMIN}`]: {
        body: {
          integrations: [
            adminView({ credential_model: 'shared', auth_method: 'oauth', shared_credential: 'not_connected', oauth: { ...OAUTH, revocable: true } }),
          ],
        },
      },
      [`POST ${ADMIN}/int-1/oauth/start?app=true`]: { body: { authorize_url: AUTHORIZE } },
    });
    renderer = await render(ManageIntegrationScreen);
    expect(textOf(renderer)).toContain('One household sign-in, used for everyone.');
    expect(textOf(renderer)).toContain('Disconnecting signs HomeAI out there too.');
    expect(textOf(renderer)).toContain('with HomeAI open at http://localhost');
    expect(exists(renderer, 'manage-integration-shared-input')).toBe(false);
    await press(renderer, 'manage-integration-shared-sign-in');
    expect(requestsTo(fetchMock, 'POST', `${ADMIN}/int-1/oauth/start?app=true`)).toHaveLength(1);
    expect(mockWithStepUp).toHaveBeenCalled();
    expect(openURL).toHaveBeenCalledWith(AUTHORIZE);
  });
});
