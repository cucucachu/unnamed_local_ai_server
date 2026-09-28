import { act, create, type ReactTestRenderer } from 'react-test-renderer';

import type { AppContext } from '@/lib/appAgent';
import type { Space } from '@/lib/platform';

import { exists, flush, press, textOf, type as typeInto } from '../../test-utils/screen';

const mockSend = jest.fn();
const mockCreateThread = jest.fn();
const mockLoadContext = jest.fn();

jest.mock('@/lib/useChat', () => ({
  useChat: () => ({
    turns: [],
    sendMessage: mockSend,
    busy: false,
    hydrationState: 'done',
    pendingApproval: null,
    respondToApproval: jest.fn(),
  }),
}));

jest.mock('@/lib/threads', () => ({
  createThread: (...args: unknown[]) => mockCreateThread(...args),
}));

jest.mock('@/lib/appAgent', () => {
  const actual = jest.requireActual('@/lib/appAgent') as typeof import('@/lib/appAgent');
  return {
    ...actual,
    loadAppContext: (...args: unknown[]) => mockLoadContext(...args),
  };
});

// eslint-disable-next-line import/first -- must follow the jest.mock calls above
import { AppAgentPanel } from '../AppAgentPanel';
import { seedUserMessage } from '@/lib/appAgent';

const INSTANCE = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const APP = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
const space: Space = {
  id: 's1',
  slug: 'family',
  name: 'Family',
  kind: 'shared',
  gid: 3000,
  owner_user_id: null,
  role: 'owner',
  created_at: '',
  archived_at: null,
};

const ctx: AppContext = {
  instanceId: INSTANCE,
  space: { id: space.id, slug: space.slug, name: space.name, role: space.role },
  appId: APP,
  appName: 'Runtime check',
  sourcePath: '/spaces/family/Apps/runtime-check',
  files: {
    'app.json': '{"name":"Runtime check","slug":"runtime-check"}',
    'AGENT.md': '# Runtime check\nA list of items.',
    'schema.sql': 'CREATE TABLE items (id INTEGER PRIMARY KEY);',
  },
};

let renderer: ReactTestRenderer | null = null;
let n = 0;

async function mount(prompt?: string) {
  n += 1;
  const instanceId = `${INSTANCE.slice(0, -1)}${n.toString(16)}`;
  const loaded = { ...ctx, instanceId };
  mockLoadContext.mockResolvedValue(loaded);
  mockCreateThread.mockResolvedValue({ id: `thread-${n}`, title: 'Ask: Runtime check', created_at: '', updated_at: '' });
  await act(async () => {
    renderer = create(
      <AppAgentPanel
        instanceId={instanceId}
        space={space}
        appId={APP}
        appName="Runtime check"
        initialPrompt={prompt ?? null}
        onClose={jest.fn()}
      />,
    );
  });
  await flush();
  await flush();
  return { renderer: renderer!, instanceId, ctx: loaded };
}

beforeEach(() => {
  mockSend.mockReset();
  mockCreateThread.mockReset();
  mockLoadContext.mockReset();
});

afterEach(() => {
  act(() => renderer?.unmount());
  renderer = null;
});

describe('AppAgentPanel', () => {
  it('shows the instance, space, and source files', async () => {
    const { renderer: r } = await mount();
    expect(exists(r, 'app-agent-panel')).toBe(true);
    expect(textOf(r)).toContain('family');
    expect(textOf(r)).toContain('Family');
    expect(textOf(r)).toContain('# Runtime check');
    expect(textOf(r)).toContain('CREATE TABLE items');
    expect(textOf(r)).toContain('runtime-check');
    expect(mockCreateThread).toHaveBeenCalledWith('Ask: Runtime check');
  });

  it('sends the first prompt pre-seeded with app context', async () => {
    const { renderer: r, ctx: loaded } = await mount();
    await typeInto(r, 'app-agent-composer', 'Add Milk via app_sql');
    await press(r, 'app-agent-send');
    expect(mockSend).toHaveBeenCalledTimes(1);
    const wire = mockSend.mock.calls[0][0] as string;
    expect(wire).toBe(seedUserMessage(loaded, 'Add Milk via app_sql'));
  });

  it('auto-sends askAgent’s prompt once the thread is ready', async () => {
    await mount('Add Milk via app_sql');
    expect(mockSend).toHaveBeenCalledTimes(1);
    expect(mockSend.mock.calls[0][0]).toContain('Add Milk via app_sql');
    expect(mockSend.mock.calls[0][0]).toContain('schema.sql');
  });
});
