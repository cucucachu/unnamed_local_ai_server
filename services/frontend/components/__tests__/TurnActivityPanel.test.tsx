import { createElement } from 'react';
import { Text } from 'react-native';
import { act, create } from 'react-test-renderer';

import { TurnActivityPanel } from '../TurnActivityPanel';
import type { ChatTurn } from '@/lib/useChat';

function runningTurn(overrides: Partial<ChatTurn> = {}): ChatTurn {
  return {
    id: 'u1',
    user: { id: 'u1', kind: 'user', text: 'hi' },
    activity: [{ id: 'a1', kind: 'assistant', text: 'partial', streaming: true }],
    final: null,
    status: 'running',
    ...overrides,
  };
}

function finishedTurn(overrides: Partial<ChatTurn> = {}): ChatTurn {
  return {
    id: 'u1',
    user: { id: 'u1', kind: 'user', text: 'hi' },
    activity: [
      {
        id: 't1',
        kind: 'tool',
        toolCallId: 'c1',
        name: 'read_file',
        category: 'file',
        status: 'success',
        args: { file_path: '/notes.md' },
      },
    ],
    final: { id: 'a1', kind: 'assistant', text: 'done', streaming: false },
    status: 'completed',
    durationMs: 42000,
    ...overrides,
  };
}

function renderedText(renderer: ReturnType<typeof create>): string {
  return JSON.stringify(renderer.toJSON());
}

describe('TurnActivityPanel', () => {
  it('running with reasoning: collapsed status is Thinking…', () => {
    let renderer: ReturnType<typeof create> | undefined;
    act(() => {
      renderer = create(
        createElement(TurnActivityPanel, {
          turn: runningTurn({
            activity: [{ id: 'r1', kind: 'reasoning', text: 'Let me work this out.' }],
          }),
        }),
      );
    });

    expect(renderer!.root.findByProps({ testID: 'turn-activity-status' }).props.children).toBe(
      'Thinking…',
    );
  });

  it('running: shows a spinner and Writing…, hides activity until expanded', () => {
    let renderer: ReturnType<typeof create> | undefined;
    act(() => {
      renderer = create(
        createElement(
          TurnActivityPanel,
          { turn: runningTurn() },
          createElement(Text, { testID: 'secret-activity' }, 'partial'),
        ),
      );
    });

    expect(renderer!.root.findByProps({ testID: 'turn-activity-spinner' })).toBeTruthy();
    expect(renderer!.root.findByProps({ testID: 'turn-activity-status' }).props.children).toBe('Writing…');
    expect(() => renderer!.root.findByProps({ testID: 'secret-activity' })).toThrow();
    expect(renderedText(renderer!)).not.toContain('partial');

    act(() => {
      (renderer!.root.findByProps({ testID: 'turn-activity-header' }).props as { onPress: () => void }).onPress();
    });
    expect(renderer!.root.findByProps({ testID: 'secret-activity' })).toBeTruthy();
  });

  it('finished: shows Worked for 42s and reveals activity on toggle', () => {
    let renderer: ReturnType<typeof create> | undefined;
    act(() => {
      renderer = create(
        createElement(
          TurnActivityPanel,
          { turn: finishedTurn() },
          createElement(Text, { testID: 'tool-child' }, 'read_file'),
        ),
      );
    });

    expect(renderer!.root.findByProps({ testID: 'turn-activity-duration' }).props.children).toBe(
      'Worked for 42s',
    );
    expect(() => renderer!.root.findByProps({ testID: 'tool-child' })).toThrow();

    act(() => {
      (renderer!.root.findByProps({ testID: 'turn-activity-header' }).props as { onPress: () => void }).onPress();
    });
    expect(renderer!.root.findByProps({ testID: 'tool-child' })).toBeTruthy();
  });

  it('cancelled: uses Stopped after Xs and the stopped caption testID', () => {
    let renderer: ReturnType<typeof create> | undefined;
    act(() => {
      renderer = create(
        createElement(TurnActivityPanel, {
          turn: finishedTurn({ status: 'cancelled', durationMs: 12000, final: null }),
        }),
      );
    });

    const caption = renderer!.root.findByProps({ testID: 'chat-item-stopped-caption' });
    expect(caption.props.children).toBe('Stopped after 12s');
  });

  it('error: uses Failed after Xs', () => {
    let renderer: ReturnType<typeof create> | undefined;
    act(() => {
      renderer = create(
        createElement(TurnActivityPanel, {
          turn: finishedTurn({ status: 'error', durationMs: 5000, final: null }),
        }),
      );
    });

    expect(renderedText(renderer!)).toContain('Failed after 5s');
  });
});
