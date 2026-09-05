import {
  extractTurnMetaFromHistory,
  formatDuration,
  groupItemsIntoTurns,
  runningStatusLine,
} from '../chatTurns';
import type { ChatItem } from '../useChat';
import type { ThreadMessage } from '../threads';

const user = (id: string, text: string): ChatItem => ({ id, kind: 'user', text });
const assistant = (id: string, text: string, extra: Partial<Extract<ChatItem, { kind: 'assistant' }>> = {}): ChatItem => ({
  id,
  kind: 'assistant',
  text,
  streaming: false,
  ...extra,
});
const tool = (id: string, name: string, extra: Partial<Extract<ChatItem, { kind: 'tool' }>> = {}): ChatItem => ({
  id,
  kind: 'tool',
  toolCallId: id,
  name,
  category: 'file',
  status: 'success',
  args: {},
  ...extra,
});

describe('formatDuration', () => {
  it('renders seconds under 60s and Xm Ys at or above', () => {
    expect(formatDuration(0)).toBe('0s');
    expect(formatDuration(42000)).toBe('42s');
    expect(formatDuration(59000)).toBe('59s');
    expect(formatDuration(60000)).toBe('1m 0s');
    expect(formatDuration(65000)).toBe('1m 5s');
  });
});

describe('runningStatusLine', () => {
  it('is Thinking… when empty or only an empty assistant', () => {
    expect(runningStatusLine([])).toBe('Thinking…');
    expect(runningStatusLine([assistant('a', '', { streaming: true })])).toBe('Thinking…');
  });

  it('is Thinking… while a reasoning item is the latest activity (M8-07)', () => {
    const reasoning: ChatItem = { id: 'r1', kind: 'reasoning', text: 'The user asked 2+2.' };
    expect(runningStatusLine([reasoning])).toBe('Thinking…');
    expect(runningStatusLine([assistant('a', '', { streaming: true }), reasoning])).toBe('Thinking…');
  });

  it('is Writing… when the latest event is assistant text', () => {
    expect(runningStatusLine([assistant('a', 'hello', { streaming: true })])).toBe('Writing…');
  });

  it('derives a line from the latest tool_start', () => {
    expect(
      runningStatusLine([
        tool('t1', 'read_file', { status: 'running', args: { file_path: '/notes.md' } }),
      ]),
    ).toBe('Reading `notes.md`');
    expect(
      runningStatusLine([tool('t2', 'edit_file', { status: 'running', args: { file_path: '/x.py' } })]),
    ).toBe('Editing `x.py`');
    expect(
      runningStatusLine([tool('t3', 'execute_code', { category: 'exec', status: 'running', args: {} })]),
    ).toBe('Running command…');
    expect(
      runningStatusLine([tool('t4', 'web_search', { category: 'web', status: 'running', args: {} })]),
    ).toBe('Searching the web…');
    expect(
      runningStatusLine([
        tool('t5', 'web_fetch', { category: 'web', status: 'running', args: { url: 'https://example.com/docs' } }),
      ]),
    ).toBe('Reading `example.com`');
  });
});

describe('groupItemsIntoTurns', () => {
  it('promotes the last text segment on a completed text-tool-text turn', () => {
    const turns = groupItemsIntoTurns(
      [
        user('u1', 'look this up'),
        assistant('a1', 'let me read'),
        tool('t1', 'read_file', { args: { file_path: '/notes.md' } }),
        assistant('a2', 'here is the answer'),
      ],
      { u1: { status: 'completed', durationMs: 42000 } },
    );

    expect(turns).toHaveLength(1);
    expect(turns[0].user?.text).toBe('look this up');
    expect(turns[0].final?.text).toBe('here is the answer');
    expect(turns[0].activity.map((i) => i.kind)).toEqual(['assistant', 'tool']);
    expect(turns[0].status).toBe('completed');
    expect(turns[0].durationMs).toBe(42000);
  });

  it('keeps a tool-only completed turn with no final', () => {
    const turns = groupItemsIntoTurns(
      [user('u1', 'list files'), tool('t1', 'ls')],
      { u1: { status: 'completed', durationMs: 8000 } },
    );

    expect(turns[0].final).toBeNull();
    expect(turns[0].activity).toHaveLength(1);
    expect(turns[0].activity[0].kind).toBe('tool');
  });

  it('keeps cancelled partial text in activity (not final)', () => {
    const turns = groupItemsIntoTurns(
      [user('u1', 'count'), assistant('a1', 'one two three', { stopped: true })],
      { u1: { status: 'cancelled', durationMs: 12000 } },
    );

    expect(turns[0].final).toBeNull();
    expect(turns[0].status).toBe('cancelled');
    expect(turns[0].activity[0]).toMatchObject({ kind: 'assistant', text: 'one two three', stopped: true });
  });

  it('keeps a running turn entirely in activity', () => {
    const turns = groupItemsIntoTurns(
      [user('u1', 'hi'), assistant('a1', 'hel', { streaming: true })],
      { u1: { status: 'running' } },
    );

    expect(turns[0].final).toBeNull();
    expect(turns[0].status).toBe('running');
    expect(turns[0].activity[0]).toMatchObject({ kind: 'assistant', streaming: true });
  });

  it('hydrates the same grouping from history rows with turn metadata', () => {
    const messages: ThreadMessage[] = [
      { id: 'u1', role: 'user', content: 'read it', tool_name: null, tool_calls: null },
      {
        id: 'a-empty',
        role: 'assistant',
        content: '',
        tool_name: null,
        tool_calls: [{ id: 'c1', name: 'read_file', args: { file_path: '/notes.md' } }],
      },
      {
        id: 't1',
        role: 'tool',
        content: 'file body',
        tool_name: 'read_file',
        tool_calls: null,
        tool_call_id: 'c1',
      },
      {
        id: 'a-final',
        role: 'assistant',
        content: 'done',
        tool_name: null,
        tool_calls: null,
        turn: { status: 'completed', duration_ms: 15000 },
      },
    ];

    const { mapHistoryToItems } = require('../useChat') as typeof import('../useChat');
    const items = mapHistoryToItems(messages);
    const metas = extractTurnMetaFromHistory(messages);
    const turns = groupItemsIntoTurns(items, metas);

    expect(metas.u1).toEqual({ status: 'completed', durationMs: 15000 });
    expect(turns[0].final?.text).toBe('done');
    expect(turns[0].activity.map((i) => i.kind)).toEqual(['tool']);
    expect(turns[0].durationMs).toBe(15000);
  });

  it('hydrates without turn metadata as a completed turn with no duration', () => {
    const messages: ThreadMessage[] = [
      { id: 'u1', role: 'user', content: 'hi', tool_name: null, tool_calls: null },
      { id: 'a1', role: 'assistant', content: 'hello', tool_name: null, tool_calls: null },
    ];
    const { mapHistoryToItems } = require('../useChat') as typeof import('../useChat');
    const items = mapHistoryToItems(messages);
    const turns = groupItemsIntoTurns(items, extractTurnMetaFromHistory(messages));

    expect(turns[0].status).toBe('completed');
    expect(turns[0].durationMs).toBeUndefined();
    expect(turns[0].final?.text).toBe('hello');
    expect(turns[0].activity).toEqual([]);
  });
});
