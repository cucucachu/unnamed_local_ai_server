import { createElement } from 'react';
import { Text } from 'react-native';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';

import { publishThreads, readThread, useChatAttention } from '../chatAttention';
import type { Thread } from '../threads';

function thread(id: string, extra: Partial<Thread> = {}): Thread {
  return { id, title: id, created_at: '2026-10-05T07:00:00Z', updated_at: '2026-10-05T07:00:00Z', ...extra };
}

let fetchMock: jest.Mock;
let listed: Thread[] = [];

beforeEach(() => {
  fetchMock = jest.fn(async (url: string, init?: RequestInit) => ({
    ok: true,
    status: init?.method === 'POST' ? 204 : 200,
    statusText: 'OK',
    json: async () => (url.endsWith('/api/threads') ? listed : undefined),
  }));
  global.fetch = fetchMock as unknown as typeof fetch;
});

function Count() {
  return createElement(Text, { testID: 'count' }, String(useChatAttention(60_000)));
}

function shown(renderer: ReactTestRenderer): string {
  return String(renderer.root.findByProps({ testID: 'count' }).props.children);
}

describe('chat attention (M17-10)', () => {
  it('counts chats waiting on an approval or unread, once each, and follows reads', async () => {
    listed = [
      thread('a', { needs_approval: true, unread: true }),
      thread('b', { unread: true }),
      thread('c'),
    ];
    let renderer!: ReactTestRenderer;
    await act(async () => {
      renderer = create(createElement(Count));
    });
    expect(shown(renderer)).toBe('2');

    await act(async () => {
      await readThread('b');
    });
    expect(shown(renderer)).toBe('1');
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/api/threads/b/read'),
      expect.objectContaining({ method: 'POST' }),
    );

    // Still waiting on its approval after being read.
    await act(async () => {
      await readThread('a');
    });
    expect(shown(renderer)).toBe('1');

    act(() => publishThreads([thread('c')]));
    expect(shown(renderer)).toBe('0');
    act(() => renderer.unmount());
  });
});
