import { act, type ReactTestRenderer } from 'react-test-renderer';
import { Platform } from 'react-native';

import type { Space } from '@/lib/platform';
import type { Routine, RoutineRun } from '@/lib/routines';

import { exists, mockFetchRoutes, press, render, requestsTo, textOf, type } from '../../../../../test-utils/screen';

const mockPush = jest.fn();
const mockBack = jest.fn();
const mockReplace = jest.fn();
const mockNavigate = jest.fn();
const mockDismissTo = jest.fn();
const mockShowPage = jest.fn();
let mockParams: Record<string, string> = {};
jest.mock('expo-router', () => ({
  useRouter: () => ({ back: mockBack, push: mockPush, replace: mockReplace, navigate: mockNavigate, dismissTo: mockDismissTo, canGoBack: () => true }),
  useLocalSearchParams: () => mockParams,
  useFocusEffect: jest.fn(),
}));

const mockOpenChat = jest.fn();
jest.mock('@/lib/currentPage', () => ({
  showPage: (...args: unknown[]) => mockShowPage(...args),
}));
jest.mock('@/lib/currentChat', () => ({
  openChat: (...args: unknown[]) => mockOpenChat(...args),
}));

// eslint-disable-next-line import/first -- must follow the jest.mock call above
import RoutinesScreen from '../index';
// eslint-disable-next-line import/first -- must follow the jest.mock call above
import RoutineDetailScreen from '../[routineId]';
// eslint-disable-next-line import/first -- must follow the jest.mock call above
import RoutineRunsScreen from '../[routineId]/runs';
// eslint-disable-next-line import/first -- must follow the jest.mock call above
import RoutineEditScreen from '../edit';

function routine(overrides: Partial<Routine> = {}): Routine {
  return {
    id: 'r1',
    name: 'Morning brief',
    prompt: 'Summarize my notes.',
    space: '/personal',
    schedule: { kind: 'weekdays', time: '07:00:00' },
    timezone: 'America/Los_Angeles',
    enabled: true,
    next_run_at: '2026-10-06T14:00:00Z',
    last_run_at: null,
    created_at: '2026-10-01T00:00:00Z',
    updated_at: '2026-10-01T00:00:00Z',
    last_run: null,
    ...overrides,
  };
}

function runRecord(overrides: Partial<RoutineRun>): RoutineRun {
  return {
    id: 'run-1',
    trigger: 'schedule',
    status: 'succeeded',
    detail: null,
    thread_id: 'thread-1',
    due_at: null,
    started_at: '2026-10-05T14:00:00Z',
    finished_at: '2026-10-05T14:01:00Z',
    created_at: '2026-10-05T14:00:00Z',
    ...overrides,
  };
}

const SPACES: Space[] = [
  { id: 's1', slug: 'alice', name: 'Alice', kind: 'personal', gid: 1, owner_user_id: 'u1', role: 'owner', created_at: '', archived_at: null },
  { id: 's2', slug: 'family', name: 'Family', kind: 'shared', gid: 2, owner_user_id: null, role: 'editor', created_at: '', archived_at: null },
  { id: 's3', slug: 'work', name: 'Work', kind: 'shared', gid: 3, owner_user_id: null, role: 'viewer', created_at: '', archived_at: null },
];

let renderer: ReactTestRenderer | null = null;
const originalConfirm = window.confirm;
beforeEach(() => {
  mockPush.mockReset();
  mockBack.mockReset();
  mockReplace.mockReset();
  mockParams = {};
  Platform.OS = 'web';
  window.confirm = jest.fn().mockReturnValue(true);
});
afterEach(() => {
  window.confirm = originalConfirm;
  act(() => renderer?.unmount());
  renderer = null;
});

describe('RoutinesScreen', () => {
  it('lists routines with their schedule in words, next run and last result', async () => {
    mockFetchRoutes({
      'GET /api/routines': {
        body: [
          routine({ last_run: { id: 'run-1', status: 'failed', finished_at: '2026-10-05T14:01:00Z', thread_id: 't' } }),
          routine({ id: 'r2', name: 'Bills', schedule: { kind: 'monthly', day: 1, time: '09:00:00' }, enabled: false, next_run_at: null }),
        ],
      },
    });
    renderer = await render(RoutinesScreen);

    expect(textOf(renderer)).toContain('Morning brief');
    expect(textOf(renderer)).toContain('Weekdays at 7:00');
    expect(textOf(renderer)).toContain('Next: Tue, Oct 6, 7:00 AM');
    expect(textOf(renderer)).toContain('Monthly on the 1st at 9:00');
    expect(textOf(renderer)).toContain('Next: Off');
    expect(exists(renderer, 'routine-last-r1')).toBe(true);
    expect(exists(renderer, 'routine-last-r2')).toBe(false);

    await press(renderer, 'routine-row-r2');
    expect(mockPush).toHaveBeenCalledWith({ pathname: '/routines/[routineId]', params: { routineId: 'r2' } });
  });

  it('turns a routine on and off from the list', async () => {
    const fetchMock = mockFetchRoutes({
      'GET /api/routines': { body: [routine()] },
      'PATCH /api/routines/r1': { body: routine({ enabled: false, next_run_at: null }) },
    });
    renderer = await render(RoutinesScreen);

    await act(async () => {
      renderer!.root.find((n) => n.props.testID === 'routine-toggle-r1' && n.props.onValueChange).props.onValueChange(false);
    });

    expect(requestsTo(fetchMock, 'PATCH', '/api/routines/r1')).toEqual([{ enabled: false }]);
    expect(textOf(renderer)).toContain('Next: Off');
  });

  it('explains routines when there are none, and starts a new one', async () => {
    mockFetchRoutes({ 'GET /api/routines': { body: [] } });
    renderer = await render(RoutinesScreen);

    expect(exists(renderer, 'routines-empty')).toBe(true);
    await press(renderer, 'routines-new');
    expect(mockPush).toHaveBeenCalledWith('/routines/edit');
  });
});

describe('RoutineEditScreen', () => {
  it('creates a weekly routine in a shared space', async () => {
    const fetchMock = mockFetchRoutes({
      'GET /api/platform/spaces': { body: { spaces: SPACES } },
      'POST /api/routines': { status: 201, body: routine({ id: 'new' }) },
    });
    renderer = await render(RoutineEditScreen);

    expect(exists(renderer, 'routine-space-alice')).toBe(true);
    expect(exists(renderer, 'routine-space-family')).toBe(true);
    expect(exists(renderer, 'routine-space-work')).toBe(false); // viewer only

    await type(renderer, 'routine-name', 'Plan meals');
    await type(renderer, 'routine-prompt', 'Plan dinners for the week.');
    await press(renderer, 'routine-space-family');
    await press(renderer, 'routine-kind-weekly');
    await press(renderer, 'routine-day-sun');
    await press(renderer, 'routine-day-mon');
    await type(renderer, 'routine-time', '17:30');
    await type(renderer, 'routine-timezone', 'Europe/Berlin');
    expect(textOf(renderer)).toContain('Sundays at 17:30');
    expect(textOf(renderer)).toContain('It uses your Agent permissions');

    await press(renderer, 'routine-save');

    expect(requestsTo(fetchMock, 'POST', '/api/routines')).toEqual([
      {
        name: 'Plan meals',
        prompt: 'Plan dinners for the week.',
        space: '/spaces/family',
        schedule: { kind: 'weekly', days: ['sun'], time: '17:30' },
        timezone: 'Europe/Berlin',
        enabled: true,
      },
    ]);
    expect(mockReplace).toHaveBeenCalledWith({ pathname: '/routines/[routineId]', params: { routineId: 'new' } });
  });

  it('shows what is wrong instead of saving', async () => {
    const fetchMock = mockFetchRoutes({ 'GET /api/platform/spaces': { body: { spaces: SPACES } } });
    renderer = await render(RoutineEditScreen);

    await type(renderer, 'routine-name', 'Plan meals');
    await type(renderer, 'routine-prompt', 'Plan dinners.');
    await type(renderer, 'routine-time', 'soon');
    await press(renderer, 'routine-save');

    expect(textOf(renderer)).toContain('Enter the time as HH:MM');
    expect(requestsTo(fetchMock, 'POST', '/api/routines')).toEqual([]);
  });

  it('edits a routine, sending only what changed', async () => {
    mockParams = { routineId: 'r1' };
    const fetchMock = mockFetchRoutes({
      'GET /api/routines/r1': { body: routine() },
      'GET /api/platform/spaces': { body: { spaces: SPACES } },
      'PATCH /api/routines/r1': { body: routine({ name: 'Brief' }) },
    });
    renderer = await render(RoutineEditScreen);

    await type(renderer, 'routine-name', 'Brief');
    await press(renderer, 'routine-save');

    expect(requestsTo(fetchMock, 'PATCH', '/api/routines/r1')).toEqual([
      { schedule: { kind: 'weekdays', time: '07:00' }, name: 'Brief' },
    ]);
    expect(mockBack).toHaveBeenCalled();
  });

  it('shows the server’s refusal', async () => {
    const fetchMock = mockFetchRoutes({
      'GET /api/platform/spaces': { body: { spaces: SPACES } },
      'POST /api/routines': { status: 422, body: { detail: 'the schedule has no future run' } },
    });
    renderer = await render(RoutineEditScreen);
    await type(renderer, 'routine-name', 'Old');
    await type(renderer, 'routine-prompt', 'Do it.');
    await press(renderer, 'routine-kind-once');
    await type(renderer, 'routine-date', '2020-01-01');
    await press(renderer, 'routine-save');

    expect(requestsTo(fetchMock, 'POST', '/api/routines')).toHaveLength(1);
    expect(textOf(renderer)).toContain('the schedule has no future run');
    expect(mockReplace).not.toHaveBeenCalled();
  });
});

describe('RoutineDetailScreen', () => {
  const routes = (extra = {}) => ({
    'GET /api/routines/r1': { body: routine({ space: '/spaces/family' }) },
    'GET /api/routines/r1/runs': {
      body: [
        runRecord({ id: 'run-2', status: 'waiting_approval', thread_id: 'thread-2', detail: 'waiting for an approval in its chat' }),
        runRecord({ id: 'run-1' }),
        runRecord({ id: 'run-0', status: 'missed', thread_id: null, detail: 'not started within 1 h of its time' }),
      ],
    },
    'GET /api/platform/spaces': { body: { spaces: SPACES } },
    ...extra,
  });

  beforeEach(() => {
    mockParams = { routineId: 'r1' };
  });

  it('shows the routine and its runs; a run opens its chat', async () => {
    mockFetchRoutes(routes());
    renderer = await render(RoutineDetailScreen);

    const text = textOf(renderer);
    expect(text).toContain('Weekdays at 7:00');
    expect(text).toContain('Family');
    expect(text).toContain('Summarize my notes.');
    expect(text).toContain('Needs approval');
    expect(text).toContain('Missed');

    await press(renderer, 'routine-run-run-2');
    expect(mockOpenChat).toHaveBeenCalledWith('thread-2');
    expect(mockShowPage).toHaveBeenCalledWith('chat');
    expect(mockDismissTo).toHaveBeenCalledWith('/');
  });

  it('runs it now and opens the new run’s chat', async () => {
    const fetchMock = mockFetchRoutes(
      routes({ 'POST /api/routines/r1/run': { status: 202, body: runRecord({ id: 'run-3', trigger: 'manual', status: 'running', thread_id: 'thread-3' }) } }),
    );
    renderer = await render(RoutineDetailScreen);

    await press(renderer, 'routine-run-now');

    expect(requestsTo(fetchMock, 'POST', '/api/routines/r1/run')).toHaveLength(1);
    expect(mockOpenChat).toHaveBeenCalledWith('thread-3');
    expect(mockShowPage).toHaveBeenCalledWith('chat');
    expect(mockDismissTo).toHaveBeenCalledWith('/');
  });

  it('opens the editor', async () => {
    mockFetchRoutes(routes());
    renderer = await render(RoutineDetailScreen);
    await press(renderer, 'routine-edit');
    expect(mockPush).toHaveBeenCalledWith({ pathname: '/routines/edit', params: { routineId: 'r1' } });
  });

  it('deletes after confirming', async () => {
    const fetchMock = mockFetchRoutes(routes({ 'DELETE /api/routines/r1': { status: 204 } }));
    renderer = await render(RoutineDetailScreen);

    (window.confirm as jest.Mock).mockReturnValueOnce(false);
    await press(renderer, 'routine-delete');
    expect(requestsTo(fetchMock, 'DELETE', '/api/routines/r1')).toHaveLength(0);

    await press(renderer, 'routine-delete');
    expect(requestsTo(fetchMock, 'DELETE', '/api/routines/r1')).toHaveLength(1);
    expect(mockBack).toHaveBeenCalled();
  });
});

describe('recent runs and More…', () => {
  const manyRuns = Array.from({ length: 7 }, (_, i) => runRecord({ id: `run-${i}`, thread_id: `thread-${i}` }));
  const routes = {
    'GET /api/routines/r1': { body: routine() },
    'GET /api/routines/r1/runs': { body: manyRuns },
    'GET /api/platform/spaces': { body: { spaces: SPACES } },
  };

  beforeEach(() => {
    mockParams = { routineId: 'r1' };
  });

  it('shows the last 5 runs and links to the rest', async () => {
    mockFetchRoutes(routes);
    renderer = await render(RoutineDetailScreen);

    expect(exists(renderer, 'routine-run-run-4')).toBe(true);
    expect(exists(renderer, 'routine-run-run-5')).toBe(false);
    await press(renderer, 'routine-runs-more');
    expect(mockPush).toHaveBeenCalledWith({ pathname: '/routines/[routineId]/runs', params: { routineId: 'r1' } });
  });

  it('has no More… with 5 runs or fewer', async () => {
    mockFetchRoutes({ ...routes, 'GET /api/routines/r1/runs': { body: manyRuns.slice(0, 5) } });
    renderer = await render(RoutineDetailScreen);
    expect(exists(renderer, 'routine-runs-more')).toBe(false);
  });

  it('lists every run on the runs page; a run opens its chat', async () => {
    mockFetchRoutes(routes);
    renderer = await render(RoutineRunsScreen);

    expect(textOf(renderer)).toContain('Morning brief: runs');
    expect(exists(renderer, 'routine-run-run-6')).toBe(true);
    await press(renderer, 'routine-run-run-6');
    expect(mockOpenChat).toHaveBeenCalledWith('thread-6');
    expect(mockShowPage).toHaveBeenCalledWith('chat');
    expect(mockDismissTo).toHaveBeenCalledWith('/');
  });
});

describe('a delivered routine', () => {
  const memory = (overrides: Partial<Routine> = {}) =>
    routine({
      id: 'm1',
      name: 'Memory',
      prompt: 'Keep notes on what matters to me.',
      schedule: { kind: 'daily', time: '03:11:00' },
      delivered: true,
      fixed_prompt: 'Read your chats since the last run and update /personal/Memory.',
      prompt_is_default: false,
      unavailable_tools: [
        { name: 'web_search', label: 'Search the web', group: 'Web' },
        { name: 'web_fetch', label: 'Read web pages', group: 'Web' },
        { name: 'open_integration', label: 'Look at integrations', group: 'Integrations' },
        { name: 'use_integration', label: 'Use integrations', group: 'Integrations' },
        { name: 'read_integration_resource', label: 'Read integration resources', group: 'Integrations' },
        { name: 'get_integration_prompt', label: 'Use integration prompts', group: 'Integrations' },
      ],
      ...overrides,
    });
  const detailRoutes = (body: Routine) => ({
    'GET /api/routines/m1': { body },
    'GET /api/routines/m1/runs': { body: [runRecord({ id: 'run-q' })] },
    'GET /api/platform/spaces': { body: { spaces: SPACES } },
  });

  beforeEach(() => {
    mockParams = { routineId: 'm1' };
  });

  it('is marked built in on the list', async () => {
    mockFetchRoutes({ 'GET /api/routines': { body: [memory(), routine()] } });
    renderer = await render(RoutinesScreen);
    expect(exists(renderer, 'routine-builtin-m1')).toBe(true);
    expect(exists(renderer, 'routine-builtin-r1')).toBe(false);
  });

  it('has no Delete, says what it can’t use, and keeps the fixed prompt collapsed', async () => {
    mockFetchRoutes(detailRoutes(memory()));
    renderer = await render(RoutineDetailScreen);

    expect(exists(renderer, 'routine-delete')).toBe(false);
    expect(textOf(renderer)).toContain("This routine can't use: Search the web, Read web pages, Integrations");
    expect(textOf(renderer)).toContain('Keep notes on what matters to me.');
    expect(exists(renderer, 'routine-fixed-prompt')).toBe(false);
    expect(exists(renderer, 'routine-run-run-q')).toBe(true);

    await press(renderer, 'routine-fixed-toggle');
    expect(textOf(renderer)).toContain('Read your chats since the last run');
  });

  it('shows the admin’s switch and locks its own', async () => {
    mockFetchRoutes(detailRoutes(memory({ disabled_by_admin: true })));
    renderer = await render(RoutineDetailScreen);
    expect(textOf(renderer)).toContain('Turned off by your admin');
    expect(textOf(renderer)).toContain('Next: Off');
    const runNow = renderer.root.find((n) => n.props.testID === 'routine-run-now' && n.props.accessibilityState);
    expect(runNow.props.accessibilityState.disabled).toBe(true);

    act(() => renderer!.unmount());
    mockFetchRoutes({ 'GET /api/routines': { body: [memory({ disabled_by_admin: true })] } });
    renderer = await render(RoutinesScreen);
    expect(exists(renderer, 'routine-admin-off-m1')).toBe(true);
    const toggle = renderer.root.find((n) => n.props.testID === 'routine-toggle-m1' && n.props.onValueChange);
    expect(toggle.props.disabled).toBe(true);
  });

  it('locks the name and space, and resets the prompt to the default', async () => {
    const fetchMock = mockFetchRoutes({
      'GET /api/routines/m1': { body: memory({ disabled_by_admin: true }) },
      'GET /api/platform/spaces': { body: { spaces: SPACES } },
      'PATCH /api/routines/m1': { body: memory({ prompt_is_default: true }) },
    });
    renderer = await render(RoutineEditScreen);

    const name = renderer.root.find((n) => n.props.testID === 'routine-name' && n.props.onChangeText);
    expect(name.props.editable).toBe(false);
    expect(exists(renderer, 'routine-space-alice')).toBe(true);
    expect(exists(renderer, 'routine-space-family')).toBe(false);
    const enabled = renderer.root.find((n) => n.props.testID === 'routine-enabled' && n.props.onValueChange);
    expect(enabled.props.disabled).toBe(true);

    await press(renderer, 'routine-prompt-reset');
    expect(exists(renderer, 'routine-prompt-reset-note')).toBe(true);
    await press(renderer, 'routine-save');

    expect(requestsTo(fetchMock, 'PATCH', '/api/routines/m1')).toEqual([
      { schedule: { kind: 'daily', time: '03:11' }, prompt: '' },
    ]);
  });

  it('has no Reset when the prompt is already the default', async () => {
    mockFetchRoutes({
      'GET /api/routines/m1': { body: memory({ prompt_is_default: true }) },
      'GET /api/platform/spaces': { body: { spaces: SPACES } },
    });
    renderer = await render(RoutineEditScreen);
    expect(exists(renderer, 'routine-prompt-reset')).toBe(false);
  });

  it('leaves ordinary routines as they were', async () => {
    mockParams = { routineId: 'r1' };
    mockFetchRoutes({
      'GET /api/routines/r1': { body: routine() },
      'GET /api/routines/r1/runs': { body: [] },
      'GET /api/platform/spaces': { body: { spaces: SPACES } },
    });
    renderer = await render(RoutineDetailScreen);
    expect(exists(renderer, 'routine-delete')).toBe(true);
    expect(exists(renderer, 'routine-delivered')).toBe(false);

    act(() => renderer!.unmount());
    renderer = await render(RoutineEditScreen);
    const name = renderer.root.find((n) => n.props.testID === 'routine-name' && n.props.onChangeText);
    expect(name.props.editable).toBe(true);
    expect(exists(renderer, 'routine-space-family')).toBe(true);
    expect(exists(renderer, 'routine-prompt-reset')).toBe(false);
  });
});
