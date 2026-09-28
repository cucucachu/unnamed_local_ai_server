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
  fetchMock = jest.fn((url: string) => {
    if (String(url).includes('/api/auth/status')) {
      return respond(200, { setup_required: false, authenticated: true, webauthn: { origin_ok: false } });
    }
    return Promise.reject(new Error(`unexpected ${url}`));
  });
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
    const request = jest.fn().mockRejectedValueOnce(new ApiError(403, 'step_up_required')).mockResolvedValueOnce('done');
    let result: Promise<unknown> = Promise.resolve();
    act(() => {
      result = withStepUp(request);
    });
    await flush();
    expect(renderer!.root.findAll((node) => node.props.testID === 'step-up-modal').length).toBeGreaterThan(0);

    fetchMock.mockReturnValueOnce(respond(200, { stepped_up_until: '2026-01-01T00:05:00Z' }));
    await act(async () => byTestId('step-up-password').props.onChangeText('hunter22'));
    await act(async () => byTestId('step-up-submit').props.onPress());
    await flush();

    await expect(result).resolves.toBe('done');
    const stepUpCall = fetchMock.mock.calls.find(([url]) => String(url).includes('/api/auth/step-up'));
    expect(stepUpCall).toBeDefined();
    const [url, init] = stepUpCall!;
    expect(url).toContain('/api/auth/step-up');
    expect(JSON.parse(init.body)).toEqual({ password: 'hunter22' });
    expect(request).toHaveBeenCalledTimes(2);
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
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/api/auth/step-up'))).toBe(false);
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
    expect(fetchMock.mock.calls.filter(([url]) => String(url).includes('/api/auth/step-up')).length).toBe(1);
  });

  it('offers a passkey button and retries after navigator.credentials.get', async () => {
    const { Platform } = require('react-native');
    Platform.OS = 'web';
    const get = jest.fn().mockResolvedValue({
      id: 'cred',
      rawId: new Uint8Array([1]).buffer,
      type: 'public-key',
      response: {
        clientDataJSON: new Uint8Array([2]).buffer,
        authenticatorData: new Uint8Array([3]).buffer,
        signature: new Uint8Array([4]).buffer,
        userHandle: null,
      },
      getClientExtensionResults: () => ({}),
    });
    Object.defineProperty(navigator, 'credentials', {
      configurable: true,
      value: { create: jest.fn(), get },
    });
    fetchMock.mockImplementation((url: string) => {
      if (String(url).includes('/api/auth/status')) {
        return respond(200, {
          setup_required: false,
          authenticated: true,
          webauthn: { rp_id: 'localhost', origin_ok: true },
        });
      }
      if (String(url).includes('/api/auth/passkey/step-up/begin')) {
        return respond(200, { challenge: 'Y2hhbGxlbmdl', rpId: 'localhost', allowCredentials: [] });
      }
      if (String(url).includes('/api/auth/passkey/step-up/finish')) {
        return respond(200, { stepped_up_until: '2026-01-01T00:05:00Z' });
      }
      return Promise.reject(new Error(`unexpected ${url}`));
    });

    const request = jest.fn().mockRejectedValueOnce(new ApiError(403, 'step_up_required')).mockResolvedValueOnce('done');
    let result: Promise<unknown> = Promise.resolve();
    act(() => {
      result = withStepUp(request);
    });
    await flush();
    expect(renderer!.root.findAll((node) => node.props.testID === 'step-up-passkey').length).toBeGreaterThan(0);

    await act(async () => byTestId('step-up-passkey').props.onPress());
    await flush();

    await expect(result).resolves.toBe('done');
    expect(get).toHaveBeenCalled();
    expect(request).toHaveBeenCalledTimes(2);
  });
});
