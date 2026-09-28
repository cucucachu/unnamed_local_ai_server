import { probeSession, wsUrl } from './api';
import { WS_CLOSE_UNAUTHORIZED, type WebSocketCtor, type WebSocketLike } from './chatSocket';
import { authHeaders } from './session';

/**
 * `/ws/platform/events` (`docs/ARCHITECTURE.md` §3 "App data" → "Events"):
 * `ready` on every (re)connect, then `db_changed` / `app_built` hints.
 * Nothing is replayed after a reconnect, which is why each `ready` is
 * handed on too (the app host's relay re-runs queries on it). Reconnects
 * with backoff until closed; a rejected or `4401`-closed socket asks the
 * platform whether the session still holds.
 */

export const EVENTS_RECONNECT_DELAYS_MS = [1000, 2000, 5000, 10000, 30000];

export interface PlatformEvents {
  close(): void;
}

function defaultWebSocketCtor(): WebSocketCtor {
  return (globalThis as { WebSocket?: WebSocketCtor }).WebSocket as WebSocketCtor;
}

export function openPlatformEvents(
  onFrame: (frame: string) => void,
  WebSocketImpl: WebSocketCtor = defaultWebSocketCtor(),
): PlatformEvents {
  const url = wsUrl('/ws/platform/events');
  let socket: WebSocketLike | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let failures = 0;
  let closed = false;

  function connect() {
    const headers = authHeaders();
    const ws =
      Object.keys(headers).length > 0 ? new WebSocketImpl(url, undefined, { headers }) : new WebSocketImpl(url);
    socket = ws;
    let opened = false;
    ws.onopen = () => {
      opened = true;
      failures = 0;
    };
    ws.onmessage = (event) => {
      if (typeof event.data === 'string') onFrame(event.data);
    };
    ws.onerror = () => {};
    ws.onclose = (event) => {
      if (closed || socket !== ws) return;
      socket = null;
      if (!opened || (event as { code?: unknown } | null)?.code === WS_CLOSE_UNAUTHORIZED) void probeSession();
      const delay = EVENTS_RECONNECT_DELAYS_MS[Math.min(failures, EVENTS_RECONNECT_DELAYS_MS.length - 1)];
      failures += 1;
      timer = setTimeout(() => {
        timer = null;
        if (!closed) connect();
      }, delay);
    };
  }

  connect();
  return {
    close() {
      closed = true;
      if (timer !== null) clearTimeout(timer);
      socket?.close();
      socket = null;
    },
  };
}
