import { createElement, useEffect } from 'react';
import { Text as RNText } from 'react-native';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';

import { ApiError } from '@/lib/api';
import { StepUpCancelledError } from '@/lib/stepUp';

import { StepUpProvider, useStepUp } from '../StepUpProvider';

const originalFetch = global.fetch;
let fetchMock: jest.Mock;
let renderer: ReactTestRenderer | null = null;
let withStepUp: ReturnType<typeof useStepUp>['withStepUp'];

function Harness({ onValue }: { onValue: (value: ReturnType<typeof useStepUp>) => void }) {
  const value = useStepUp();
  useEffect(() => onValue(value), [value, onValue]);
  return null;
}

function respond(status: number, body: unknown) {
  return Promise.resolve({ ok: status < 300, status, statusText: '', headers: { get: () => null }, json: async () => body });
}

function byTestId(testID: string) {
  return renderer!.root.find((node) => node.props.testID === testID && (node.props.onPress || node.props.onChangeText));
}

function text(): string {
  return renderer!.root
    .findAllByType(RNText)
    .map((node) => String(node.props.children ?? ''))
    .join(' | ');
}

const flush = () => act(async () => new Promise((resolve) => setTimeout(resolve, 0)));

beforeEach(async () => {
  fetchMock = jest.fn();
  global.fetch = fetchMock as unknown as typeof fetch;
  await act(async () => {
    renderer = create(
      createElement(
        StepUpProvider,
        null,
        createElement(Harness, {
          onValue: (value) => {
            withStepUp = value.withStepUp;
          },
        }),
      ),
    );
  });
});

afterEach(() => {
  act(() => renderer?.unmount());
  renderer = null;
  global.fetch = originalFetch;
});

describe('StepUpProvider', () => {
  it('shows no prompt until a call needs step-up', () => {
    expect(renderer!.root.findAll((node) => node.props.testID === 'step-up-modal')).toHaveLength(0);
  });

  it('prompts for the password, POSTs /api/auth/step-up, and retries the call', async () => {
    const call = jest.fn().mockRejectedValueOnce(new ApiError(403, 'step_up_required')).mockResolvedValueOnce('done');
    let result: Promise<unknown> = Promise.resolve();
    act(() => {
      result = withStepUp(call);
    });
    await flush();
    expect(renderer!.root.findAll((node) => node.props.testID === 'step-up-modal').length).toBeGreaterThan(0);

    fetchMock.mockReturnValueOnce(respond(200, { stepped_up_until: '2026-01-01T00:05:00Z' }));
    await act(async () => byTestId('step-up-password').props.onChangeText('hunter22'));
    await act(async () => byTestId('step-up-submit').props.onPress());
    await flush();

    await expect(result).resolves.toBe('done');
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain('/api/auth/step-up');
    expect(JSON.parse(init.body)).toEqual({ password: 'hunter22' });
    expect(call).toHaveBeenCalledTimes(2);
    expect(renderer!.root.findAll((node) => node.props.testID === 'step-up-modal')).toHaveLength(0);
  });

  it('keeps the prompt open with a message on a wrong password', async () => {
    act(() => {
      void withStepUp(() => Promise.reject(new ApiError(403, 'step_up_required'))).catch(() => {});
    });
    await flush();

    fetchMock.mockReturnValueOnce(respond(403, { detail: 'invalid_password' }));
    await act(async () => byTestId('step-up-password').props.onChangeText('wrong'));
    await act(async () => byTestId('step-up-submit').props.onPress());
    await flush();

    expect(text()).toContain('Incorrect password.');
    expect(renderer!.root.findAll((node) => node.props.testID === 'step-up-modal').length).toBeGreaterThan(0);
  });

  it('Cancel rejects the call with StepUpCancelledError', async () => {
    let result: Promise<unknown> = Promise.resolve();
    act(() => {
      result = withStepUp(() => Promise.reject(new ApiError(403, 'step_up_required'))).catch((error) => error);
    });
    await flush();
    await act(async () => byTestId('step-up-cancel').props.onPress());

    await expect(result).resolves.toBeInstanceOf(StepUpCancelledError);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('concurrent calls share one prompt', async () => {
    const a = jest.fn().mockRejectedValueOnce(new ApiError(403, 'step_up_required')).mockResolvedValueOnce('a');
    const b = jest.fn().mockRejectedValueOnce(new ApiError(403, 'step_up_required')).mockResolvedValueOnce('b');
    let results: Promise<unknown[]> = Promise.resolve([]);
    act(() => {
      results = Promise.all([withStepUp(a), withStepUp(b)]);
    });
    await flush();

    fetchMock.mockReturnValueOnce(respond(200, { stepped_up_until: 'x' }));
    await act(async () => byTestId('step-up-password').props.onChangeText('pw'));
    await act(async () => byTestId('step-up-submit').props.onPress());
    await flush();

    await expect(results).resolves.toEqual(['a', 'b']);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
