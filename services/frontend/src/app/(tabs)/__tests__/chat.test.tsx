import { createElement } from 'react';
import { act, create } from 'react-test-renderer';

import type { UseChatResult } from '@/lib/useChat';

// Per the ticket's own text ("one shallow render of the screen"): mock
// `useChat` entirely rather than exercising the real WebSocket layer, so
// this stays a true shallow render (no socket, no timers, no fake server).
const mockUseChat = jest.fn<UseChatResult, unknown[]>();
jest.mock('@/lib/useChat', () => ({
  useChat: (...args: unknown[]) => mockUseChat(...args),
}));

// eslint-disable-next-line import/first -- must follow the jest.mock call above
import ChatScreen from '../chat';

function setUseChatResult(overrides: Partial<UseChatResult> = {}): void {
  mockUseChat.mockReturnValue({
    items: [],
    sendMessage: jest.fn(),
    busy: false,
    connectionState: 'open',
    ...overrides,
  });
}

describe('ChatScreen', () => {
  beforeEach(() => {
    mockUseChat.mockReset();
  });

  it('renders without crashing with an empty item list', () => {
    setUseChatResult();

    expect(() => {
      act(() => {
        create(createElement(ChatScreen));
      });
    }).not.toThrow();
  });

  it('renders user, assistant, tool, and error items without crashing', () => {
    setUseChatResult({
      connectionState: 'reconnecting',
      items: [
        { id: 'u-1', kind: 'user', text: 'hello' },
        { id: 'a-1', kind: 'assistant', text: 'hi there', streaming: true },
        {
          id: 't-1',
          kind: 'tool',
          toolCallId: 'call-1',
          name: 'read_file',
          category: 'file',
          status: 'running',
          args: { path: '/tmp/x' },
        },
        { id: 'e-1', kind: 'error', message: 'something went wrong' },
      ],
    });

    let renderer: ReturnType<typeof create> | undefined;
    expect(() => {
      act(() => {
        renderer = create(createElement(ChatScreen));
      });
    }).not.toThrow();

    const text = renderer?.toJSON();
    expect(text).toBeTruthy();
  });
});
