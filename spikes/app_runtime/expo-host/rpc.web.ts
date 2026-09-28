// On web the host forwards to the platform stand-in (host/server.mjs) with its own session cookie.
import { INSTANCE } from './bridgeHost';

export async function rpc(method: string, params: unknown) {
  const r = await fetch(`/api/rpc/${INSTANCE}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ method, params }),
  });
  const body = await r.json();
  if (!body.ok) throw Object.assign(new Error(body.error?.message), { code: body.error?.code });
  return body.result;
}
