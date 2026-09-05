import { getSettings, updateSettings } from '../settings';

describe('lib/settings', () => {
  const originalFetch = global.fetch;

  afterEach(() => {
    global.fetch = originalFetch;
    jest.clearAllMocks();
  });

  it('getSettings() GETs /api/settings and resolves with the document', async () => {
    const fetchMock = jest.fn().mockResolvedValue({
      ok: true,
      status: 200,
      statusText: 'OK',
      json: async () => ({ hitl_enabled: true, thinking_enabled: false, edit_mode_default: 'truncate' }),
    });
    global.fetch = fetchMock as unknown as typeof fetch;

    const result = await getSettings();

    expect(result).toEqual({ hitl_enabled: true, thinking_enabled: false, edit_mode_default: 'truncate' });
    const [calledPath, calledInit] = fetchMock.mock.calls[0];
    expect(calledPath).toContain('/api/settings');
    expect(calledInit).toBeUndefined();
  });

  it('updateSettings(partial) PUTs the partial body and resolves with the merged document', async () => {
    const fetchMock = jest.fn().mockResolvedValue({
      ok: true,
      status: 200,
      statusText: 'OK',
      json: async () => ({ hitl_enabled: false, thinking_enabled: false, edit_mode_default: 'truncate' }),
    });
    global.fetch = fetchMock as unknown as typeof fetch;

    const result = await updateSettings({ hitl_enabled: false });

    expect(result).toEqual({ hitl_enabled: false, thinking_enabled: false, edit_mode_default: 'truncate' });
    const [calledPath, calledInit] = fetchMock.mock.calls[0];
    expect(calledPath).toContain('/api/settings');
    expect(calledInit).toMatchObject({ method: 'PUT' });
    expect(JSON.parse(calledInit.body as string)).toEqual({ hitl_enabled: false });
  });

  it('updateSettings() rejects with ApiError on a 422', async () => {
    const fetchMock = jest.fn().mockResolvedValue({
      ok: false,
      status: 422,
      statusText: 'Unprocessable Entity',
      json: async () => ({ detail: 'validation error' }),
    });
    global.fetch = fetchMock as unknown as typeof fetch;

    const error = await updateSettings({ hitl_enabled: false }).catch((e) => e);

    expect(error).toMatchObject({ status: 422, detail: 'validation error' });
  });
});
