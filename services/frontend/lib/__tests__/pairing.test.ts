import { ApiError } from '../api';
import { pairThisDevice, parsePairingPayload } from '../auth';
import { pairingQrValue } from '../platform';

const mockStore = new Map<string, string>();
jest.mock('expo-secure-store', () => ({
  getItemAsync: jest.fn(async (key: string) => mockStore.get(key) ?? null),
  setItemAsync: jest.fn(async (key: string, value: string) => {
    mockStore.set(key, value);
  }),
  deleteItemAsync: jest.fn(async (key: string) => {
    mockStore.delete(key);
  }),
}));

const mockDeleteKey = jest.fn();
jest.mock('../deviceKey', () => ({
  generateDeviceKey: jest.fn(async () => 'pub'),
  signChallenge: jest.fn(async () => 'sig'),
  deleteDeviceKey: () => mockDeleteKey(),
}));
jest.mock('../localAuth', () => ({ confirmDevicePresence: jest.fn(async () => true) }));

const BEGIN = {
  v: 1,
  kind: 'homeai-host-pair' as const,
  token: 'hd_a+b/c',
  challenge: 'ch_A-z_9',
  user: 'cody',
  expires_at: '2026-10-05T00:00:00+00:00',
};

const originalFetch = global.fetch;

afterEach(() => {
  global.fetch = originalFetch;
  mockStore.clear();
  mockDeleteKey.mockReset();
});

describe('pairing QR', () => {
  it('is a homeai://pair link the app parses back', () => {
    const link = pairingQrValue(BEGIN);
    expect(link.startsWith('homeai://pair?')).toBe(true);
    expect(parsePairingPayload(link)).toEqual({ token: 'hd_a+b/c', challenge: 'ch_A-z_9' });
  });

  it('still accepts the pasted JSON payload', () => {
    expect(parsePairingPayload(JSON.stringify(BEGIN))).toEqual({ token: 'hd_a+b/c', challenge: 'ch_A-z_9' });
  });

  it('rejects anything else', () => {
    expect(parsePairingPayload('hello')).toBeNull();
    expect(parsePairingPayload('{"kind":"other","token":"t","challenge":"c"}')).toBeNull();
    expect(parsePairingPayload('homeai://pair?token=t')).toBeNull();
  });
});

describe('pairThisDevice', () => {
  it('forgets the old pair when enroll fails after the key was replaced', async () => {
    mockStore.set('homeai_host_device_id', 'old-device');
    global.fetch = jest.fn(async () => ({
      ok: false,
      status: 409,
      statusText: '',
      headers: { get: () => null },
      json: async () => ({ detail: 'already_used' }),
    })) as unknown as typeof fetch;

    await expect(pairThisDevice(pairingQrValue(BEGIN))).rejects.toEqual(new ApiError(409, 'already_used'));
    expect(mockStore.has('homeai_host_device_id')).toBe(false);
    expect(mockDeleteKey).toHaveBeenCalled();
  });
});
