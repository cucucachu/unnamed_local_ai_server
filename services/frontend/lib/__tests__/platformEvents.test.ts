import { EVENTS_RECONNECT_DELAYS_MS, openPlatformEvents } from '../platformEvents';
import { setSessionToken } from '../session';
import type { WebSocketCtor } from '../chatSocket';

jest.mock('../api', () => ({
  ...jest.requireActual('../api'),
  probeSession: jest.fn(async () => {}),
}));
// eslint-disable-next-line import/first -- must follow the jest.mock call above
import { probeSession } from '../api';

class FakeSocket {
  static all: FakeSocket[] = [];
  onopen: ((event: unknown) => void) | null = null;
  onmessage: ((event: { data: unknown }) => void) | null = null;
  onerror: ((event: unknown) => void) | null = null;
  onclose: ((event: unknown) => void) | null = null;
  closed = false;
  constructor(
    readonly url: string,
    readonly protocols?: string | string[],
    readonly options?: { headers: Record<string, string> },
  ) {
    FakeSocket.all.push(this);
  }
  send() {}
  close() {
    this.closed = true;
  }
}
const Ctor = FakeSocket as unknown as WebSocketCtor;

beforeEach(() => {
  jest.useFakeTimers();
  FakeSocket.all = [];
  (probeSession as jest.Mock).mockClear();
});
afterEach(() => {
  jest.useRealTimers();
  setSessionToken(null);
});

describe('openPlatformEvents', () => {
  it('connects with the native bearer and hands on text frames', () => {
    setSessionToken('hs_abc');
    const frames: string[] = [];
    openPlatformEvents((f) => frames.push(f), Ctor);
    const [ws] = FakeSocket.all;
    expect(ws.url).toBe('ws://homeai.local/ws/platform/events');
    expect(ws.options).toEqual({ headers: { Authorization: 'Bearer hs_abc' } });
    ws.onopen?.({});
    ws.onmessage?.({ data: '{"type":"ready"}' });
    ws.onmessage?.({ data: new ArrayBuffer(2) });
    expect(frames).toEqual(['{"type":"ready"}']);
  });

  it('reconnects with backoff, probes the session on 4401, and stops when closed', () => {
    const events = openPlatformEvents(() => {}, Ctor);
    FakeSocket.all[0].onopen?.({});
    FakeSocket.all[0].onclose?.({ code: 4401 });
    expect(probeSession).toHaveBeenCalledTimes(1);
    jest.advanceTimersByTime(EVENTS_RECONNECT_DELAYS_MS[0]);
    expect(FakeSocket.all).toHaveLength(2);

    FakeSocket.all[1].onclose?.({ code: 1006 });
    expect(probeSession).toHaveBeenCalledTimes(2);
    jest.advanceTimersByTime(EVENTS_RECONNECT_DELAYS_MS[1] - 1);
    expect(FakeSocket.all).toHaveLength(2);
    jest.advanceTimersByTime(1);
    expect(FakeSocket.all).toHaveLength(3);

    events.close();
    expect(FakeSocket.all[2].closed).toBe(true);
    FakeSocket.all[2].onclose?.({ code: 1000 });
    jest.advanceTimersByTime(60000);
    expect(FakeSocket.all).toHaveLength(3);
  });
});
