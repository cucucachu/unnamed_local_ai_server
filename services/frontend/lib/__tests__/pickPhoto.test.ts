import * as ImagePicker from 'expo-image-picker';

import { MAX_PHOTO_BYTES, base64Bytes, fitWithin, pickPhoto } from '../pickPhoto';

const mockSaves: { base64: string; width: number; height: number }[] = [];
const mockResizes: unknown[] = [];

jest.mock('expo-image-picker', () => ({
  requestCameraPermissionsAsync: jest.fn(async () => ({ granted: true })),
  launchCameraAsync: jest.fn(),
  launchImageLibraryAsync: jest.fn(),
}));
jest.mock('expo-image-manipulator', () => ({
  SaveFormat: { JPEG: 'jpeg' },
  ImageManipulator: {
    manipulate: () => {
      const context = {
        resize: (size: unknown) => (mockResizes.push(size), context),
        renderAsync: async () => ({ saveAsync: async () => mockSaves.shift() }),
      };
      return context;
    },
  },
}));

const picker = ImagePicker as jest.Mocked<typeof ImagePicker>;
const asset = (width: number, height: number) => ({ canceled: false, assets: [{ uri: 'file:///p.jpg', width, height }] }) as never;

beforeEach(() => {
  mockSaves.length = 0;
  mockResizes.length = 0;
});

it('fits within the long edge without enlarging', () => {
  expect(fitWithin(4000, 3000, 1280)).toEqual({ width: 1280, height: 960 });
  expect(fitWithin(600, 800, 1280)).toEqual({ width: 600, height: 800 });
  expect(base64Bytes('AAAA')).toBe(3);
  expect(base64Bytes('AA==')).toBe(1);
});

it('shrinks to 1280 px and compresses harder until the JPEG fits', async () => {
  picker.launchImageLibraryAsync.mockResolvedValueOnce(asset(4000, 3000));
  const big = 'A'.repeat(Math.ceil((MAX_PHOTO_BYTES * 4) / 3) + 8);
  mockSaves.push({ base64: big, width: 1280, height: 960 }, { base64: 'data:image/jpeg;base64,AAAA', width: 1280, height: 960 });
  expect(await pickPhoto('library')).toEqual({ base64: 'AAAA', width: 1280, height: 960 });
  expect(mockResizes).toEqual([{ width: 1280, height: 960 }, { width: 1280, height: 960 }]);
});

it('answers null when the user cancels, and refuses without camera access', async () => {
  picker.launchImageLibraryAsync.mockResolvedValueOnce({ canceled: true, assets: null } as never);
  expect(await pickPhoto('library')).toBeNull();
  picker.requestCameraPermissionsAsync.mockResolvedValueOnce({ granted: false } as never);
  await expect(pickPhoto('camera')).rejects.toMatchObject({ code: 'permission_denied' });
  expect(picker.launchCameraAsync).not.toHaveBeenCalled();
});
