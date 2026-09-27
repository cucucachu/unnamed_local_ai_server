import { createElement } from 'react';
import { Text as RNText } from 'react-native';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';

import { ApiError } from '@/lib/api';

const mockReplace = jest.fn();
let mockToken: string | undefined = 'hi_token';
jest.mock('expo-router', () => ({
  useRouter: () => ({ replace: mockReplace }),
  useLocalSearchParams: () => ({ token: mockToken }),
}));

const mockAcceptInvite = jest.fn();
jest.mock('@/components/AuthProvider', () => ({
  useAuth: () => ({ acceptInvite: mockAcceptInvite }),
}));

// eslint-disable-next-line import/first -- must follow the jest.mock calls above
import InviteScreen from '../invite';

let renderer: ReactTestRenderer | null = null;

async function render() {
  await act(async () => {
    renderer = create(createElement(InviteScreen));
  });
}

function text(): string {
  return renderer!.root
    .findAllByType(RNText)
    .map((node) => String(node.props.children ?? ''))
    .join(' | ');
}

async function type(testID: string, value: string) {
  const input = renderer!.root.find((node) => node.props.testID === testID && typeof node.props.onChangeText === 'function');
  await act(async () => input.props.onChangeText(value));
}

async function fillAndSubmit() {
  await type('auth-username', 'bob');
  await type('auth-display-name', 'Bob');
  await type('auth-password', 'correct horse');
  await type('auth-password-confirm', 'correct horse');
  const button = renderer!.root.find((node) => node.props.testID === 'auth-submit' && typeof node.props.onPress === 'function');
  await act(async () => button.props.onPress());
}

beforeEach(() => {
  mockToken = 'hi_token';
  mockReplace.mockReset();
  mockAcceptInvite.mockReset();
});

afterEach(() => {
  act(() => renderer?.unmount());
  renderer = null;
});

describe('InviteScreen', () => {
  it('accepts the invite with the URL token and goes to the app', async () => {
    mockAcceptInvite.mockResolvedValue(undefined);
    await render();
    await fillAndSubmit();

    expect(mockAcceptInvite).toHaveBeenCalledWith({
      token: 'hi_token',
      username: 'bob',
      displayName: 'Bob',
      password: 'correct horse',
    });
    expect(mockReplace).toHaveBeenCalledWith('/');
  });

  it('shows invalid_invite and stays put', async () => {
    mockAcceptInvite.mockRejectedValue(new ApiError(401, 'invalid_invite'));
    await render();
    await fillAndSubmit();

    expect(text()).toContain('This invite link is invalid');
    expect(mockReplace).not.toHaveBeenCalled();
  });

  it('explains a link without a token and does not submit', async () => {
    mockToken = undefined;
    await render();
    await fillAndSubmit();

    expect(text()).toContain('missing its token');
    expect(mockAcceptInvite).not.toHaveBeenCalled();
  });
});
