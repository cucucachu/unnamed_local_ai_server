import { createElement } from 'react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';

const mockReplace = jest.fn();
jest.mock('expo-router', () => ({
  useRouter: () => ({ replace: mockReplace }),
  useLocalSearchParams: () => ({ token: 'hd_a+b', challenge: 'ch_1' }),
}));

let mockUser: { id: string } | null = null;
jest.mock('@/components/AuthProvider', () => ({
  useAuth: () => ({ state: { phase: 'ready', user: mockUser } }),
}));

const mockLoginScreen = jest.fn((_props: { pairPayload?: string }) => null);
jest.mock('@/components/LoginScreen', () => ({
  LoginScreen: (props: { pairPayload?: string }) => mockLoginScreen(props),
}));

// eslint-disable-next-line import/first -- must follow the jest.mock calls above
import PairRoute from '../pair';
// eslint-disable-next-line import/first -- must follow the jest.mock calls above
import { parsePairingPayload } from '@/lib/auth';

let renderer: ReactTestRenderer | null = null;

afterEach(() => {
  act(() => renderer?.unmount());
  renderer = null;
  mockReplace.mockReset();
  mockLoginScreen.mockClear();
  mockUser = null;
});

describe('pair link route', () => {
  it('opens the pairing form with the link token and challenge', async () => {
    await act(async () => {
      renderer = create(createElement(PairRoute));
    });
    const payload = mockLoginScreen.mock.calls[0][0].pairPayload!;
    expect(parsePairingPayload(payload)).toEqual({ token: 'hd_a+b', challenge: 'ch_1' });
    expect(mockReplace).not.toHaveBeenCalled();
  });

  it('goes home once pairing signs in', async () => {
    await act(async () => {
      renderer = create(createElement(PairRoute));
    });
    mockUser = { id: 'u1' };
    await act(async () => {
      renderer!.update(createElement(PairRoute));
    });
    expect(mockReplace).toHaveBeenCalledWith('/');
  });
});
