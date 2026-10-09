import { Platform } from 'react-native';

const mockCopy = jest.fn();
const mockPick = jest.fn();
const mockStore = new Map<string, string>();
const mockShare = jest.fn();

jest.mock('expo-file-system', () => {
  class Directory {
    uri: string;
    constructor(...parts: unknown[]) {
      this.uri = parts.map((p) => (typeof p === 'string' ? p : (p as { uri: string }).uri)).join('/');
    }
    static pickDirectoryAsync = (...args: unknown[]) => mockPick(...args);
  }
  class File {
    uri: string;
    constructor(dir: { uri: string }, name: string) {
      this.uri = `${dir.uri}/${name}`;
    }
    copy(destination: { uri: string }, options: unknown) {
      return mockCopy(destination.uri, options);
    }
    static downloadFileAsync = async (_url: string, destination: File) => destination;
  }
  return { Directory, File, Paths: { cache: 'file:///cache' }, UploadType: {} };
});
jest.mock('expo-secure-store', () => ({
  getItemAsync: async (key: string) => mockStore.get(key) ?? null,
  setItemAsync: async (key: string, value: string) => {
    mockStore.set(key, value);
  },
  deleteItemAsync: async (key: string) => {
    mockStore.delete(key);
  },
}));
jest.mock('expo-sharing', () => ({
  isAvailableAsync: async () => true,
  shareAsync: (...args: unknown[]) => mockShare(...args),
}));

import { downloadFile, folderLabel } from '../files';

const TREE = 'content://com.android.externalstorage.documents/tree/primary%3ADownload%2FHomeAI';
const originalOS = Platform.OS;

describe('downloadFile on Android', () => {
  beforeEach(() => {
    Object.defineProperty(Platform, 'OS', { value: 'android', configurable: true });
    mockCopy.mockReset().mockResolvedValue(undefined);
    mockPick.mockReset();
    mockShare.mockReset();
    mockStore.clear();
  });

  afterAll(() => {
    Object.defineProperty(Platform, 'OS', { value: originalOS, configurable: true });
  });

  it('asks for a folder once, saves there, and reuses it without the share sheet', async () => {
    mockPick.mockResolvedValue({ uri: TREE });

    expect(await downloadFile('/personal/HomeAI.apk')).toBe('Download/HomeAI');
    expect(mockCopy).toHaveBeenCalledWith(TREE, { overwrite: true });

    expect(await downloadFile('/personal/HomeAI.apk')).toBe('Download/HomeAI');
    expect(mockPick).toHaveBeenCalledTimes(1);
    expect(mockCopy).toHaveBeenCalledTimes(2);
    expect(mockShare).not.toHaveBeenCalled();
  });

  it('asks again when the remembered folder no longer works', async () => {
    mockStore.set('homeai_download_dir', 'content://gone/tree/primary%3AOld');
    mockCopy.mockRejectedValueOnce(new Error('permission denied'));
    mockPick.mockResolvedValue({ uri: TREE });

    expect(await downloadFile('/personal/a.txt')).toBe('Download/HomeAI');
    expect(mockPick).toHaveBeenCalledTimes(1);
    expect(mockStore.get('homeai_download_dir')).toBe(TREE);
  });

  it('resolves null and remembers nothing when the picker is cancelled', async () => {
    mockPick.mockRejectedValue(Object.assign(new Error('The file picker was cancelled by the user'), {
      code: 'ERR_PICKER_CANCELLED',
    }));

    expect(await downloadFile('/personal/a.txt')).toBeNull();
    expect(mockCopy).not.toHaveBeenCalled();
    expect(mockStore.size).toBe(0);
  });
});

describe('folderLabel', () => {
  it('shows the path inside the storage volume', () => {
    expect(folderLabel(TREE)).toBe('Download/HomeAI');
    expect(folderLabel('content://x/tree/primary%3A')).toBe('primary:');
    expect(folderLabel('file:///somewhere')).toBe('the chosen folder');
  });
});
