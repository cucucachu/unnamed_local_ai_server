import { createElement } from 'react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';

import { exists, flush, mockFetchRoutes, press, requestsTo, textOf } from '../../test-utils/screen';

const mockPush = jest.fn();
jest.mock('expo-router', () => ({
  useRouter: () => ({ push: mockPush }),
}));

// eslint-disable-next-line import/first -- must follow the jest.mock call above
import { RoutineInbox, inboxShown } from '../RoutineInbox';
// eslint-disable-next-line import/first -- must follow the jest.mock call above
import { useInboxUnread, type Inbox, type InboxItem } from '@/lib/inbox';

function item(id: string, status: InboxItem['status'], unread: boolean, extra: Partial<InboxItem> = {}): InboxItem {
  return {
    id,
    routine_id: 'r1',
    routine_name: `Routine ${id}`,
    trigger: 'schedule',
    status,
    detail: null,
    thread_id: `thread-${id}`,
    due_at: null,
    started_at: null,
    finished_at: '2026-10-05T15:30:00Z',
    created_at: '2026-10-05T15:30:00Z',
    unread,
    ...extra,
  };
}

const INBOX: Inbox = {
  unread: 2,
  items: [
    item('done', 'succeeded', true),
    item('old', 'succeeded', false),
    item('paused', 'waiting_approval', false, { detail: 'waiting for an approval in its chat' }),
    item('broke', 'failed', true, { detail: 'the model fell over' }),
  ],
};
const READ: Inbox = { unread: 0, items: INBOX.items.map((i) => ({ ...i, unread: false })) };

let renderer: ReactTestRenderer | null = null;
beforeEach(() => mockPush.mockReset());
afterEach(() => {
  act(() => renderer?.unmount());
  renderer = null;
});

async function renderInbox(): Promise<ReactTestRenderer> {
  await act(async () => {
    renderer = create(createElement(RoutineInbox));
  });
  await flush();
  return renderer!;
}

function Unread() {
  const { Text } = jest.requireActual('react-native');
  return createElement(Text, { testID: 'unread' }, String(useInboxUnread(60_000)));
}

describe('RoutineInbox', () => {
  it('puts runs needing approval first, then unread ones, and skips what was read', () => {
    expect(inboxShown(INBOX).map((i) => i.id)).toEqual(['paused', 'done', 'broke']);
  });

  it('lists needing-approval, finished and failed runs with their status', async () => {
    mockFetchRoutes({ 'GET /api/inbox': { body: INBOX } });
    const tree = await renderInbox();

    const text = textOf(tree);
    expect(text).toContain('Routines · 2 new');
    expect(text).toContain('Needs approval');
    expect(text).toContain('Finished');
    expect(text).toContain('Failed');
    expect(text).toContain('the model fell over');
    expect(exists(tree, 'home-inbox-old')).toBe(false);
  });

  it('opens a run in its chat and marks it read', async () => {
    const fetchMock = mockFetchRoutes({
      'GET /api/inbox': { body: INBOX },
      'POST /api/inbox/read': { body: { ...INBOX, unread: 1 } },
    });
    const tree = await renderInbox();

    await press(tree, 'home-inbox-done');

    expect(mockPush).toHaveBeenCalledWith({ pathname: '/chat/[threadId]', params: { threadId: 'thread-done' } });
    expect(requestsTo(fetchMock, 'POST', '/api/inbox/read')).toEqual([{ run_ids: ['done'] }]);
    expect(textOf(tree)).toContain('Routines · 1 new');
  });

  it('opens a paused run without marking anything when it was already read', async () => {
    const fetchMock = mockFetchRoutes({ 'GET /api/inbox': { body: INBOX } });
    const tree = await renderInbox();

    await press(tree, 'home-inbox-paused');

    expect(mockPush).toHaveBeenCalledWith({ pathname: '/chat/[threadId]', params: { threadId: 'thread-paused' } });
    expect(requestsTo(fetchMock, 'POST', '/api/inbox/read')).toEqual([]);
  });

  it('marks everything read, keeping a paused run listed', async () => {
    const fetchMock = mockFetchRoutes({
      'GET /api/inbox': { body: INBOX },
      'POST /api/inbox/read': { body: READ },
    });
    const tree = await renderInbox();

    await press(tree, 'home-inbox-read-all');

    expect(requestsTo(fetchMock, 'POST', '/api/inbox/read')).toEqual([{}]);
    expect(exists(tree, 'home-inbox-read-all')).toBe(false);
    expect(exists(tree, 'home-inbox-paused')).toBe(true);
    expect(exists(tree, 'home-inbox-done')).toBe(false);
  });

  it('renders nothing when there is nothing new or the inbox fails to load', async () => {
    mockFetchRoutes({ 'GET /api/inbox': { body: { unread: 0, items: [item('old', 'succeeded', false)] } } });
    expect(exists(await renderInbox(), 'home-inbox')).toBe(false);
    act(() => renderer?.unmount());

    mockFetchRoutes({ 'GET /api/inbox': { status: 500, body: { detail: 'down' } } });
    expect(exists(await renderInbox(), 'home-inbox')).toBe(false);
  });

  it('shares the unread count with the tab badge', async () => {
    mockFetchRoutes({
      'GET /api/inbox': { body: INBOX },
      'POST /api/inbox/read': { body: READ },
    });
    let badge!: ReactTestRenderer;
    await act(async () => {
      badge = create(createElement(Unread));
    });
    await flush();
    expect(textOf(badge)).toBe('2');

    const tree = await renderInbox();
    await press(tree, 'home-inbox-read-all');
    expect(textOf(badge)).toBe('0');
    act(() => badge.unmount());
  });
});
