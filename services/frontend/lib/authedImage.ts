import { useEffect, useState } from 'react';
import { Platform } from 'react-native';
import { Directory, File, Paths } from 'expo-file-system';

import { authHeaders, notifyUnauthorized } from './session';

/** Where a downloaded image lives on the phone, per URL, for this run. */
const cached = new Map<string, string>();
const pending = new Map<string, Promise<string>>();
let counter = 0;
// The cache map is per run, so the last run's files are only clutter.
let cleared = false;

async function download(url: string): Promise<string> {
  const response = await fetch(url, { headers: authHeaders() });
  if (response.status === 401) notifyUnauthorized();
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const bytes = new Uint8Array(await response.arrayBuffer());
  const folder = new Directory(Paths.cache, 'images');
  if (!cleared && folder.exists) folder.delete();
  cleared = true;
  if (!folder.exists) folder.create({ intermediates: true });
  counter += 1;
  const file = new File(folder, `${Date.now()}-${counter}`);
  file.write(bytes);
  return file.uri;
}

function load(url: string): Promise<string> {
  let promise = pending.get(url);
  if (!promise) {
    promise = download(url).then(
      (uri) => {
        cached.set(url, uri);
        pending.delete(url);
        return uri;
      },
      (error: unknown) => {
        pending.delete(url);
        throw error;
      },
    );
    pending.set(url, promise);
  }
  return promise;
}

/**
 * An `Image` source for a URL behind sign-in. On web the session cookie
 * rides along, so it's the URL itself. On the phone `Image` doesn't send
 * the bearer header the platform needs (every request got Caddy's 401), so
 * the image is fetched with it into the cache and shown from there.
 * `uri` is null while that runs; `failed` when it couldn't be fetched.
 */
export function useAuthedImage(url: string): { uri: string | null; failed: boolean } {
  const web = Platform.OS === 'web';
  const [result, setResult] = useState<{ url: string; uri: string | null; failed: boolean } | null>(null);
  const hit = web ? url : cached.get(url);

  useEffect(() => {
    if (hit) return;
    let live = true;
    load(url).then(
      (uri) => live && setResult({ url, uri, failed: false }),
      () => live && setResult({ url, uri: null, failed: true }),
    );
    return () => {
      live = false;
    };
  }, [url, hit]);

  if (hit) return { uri: hit, failed: false };
  return result?.url === url ? { uri: result.uri, failed: result.failed } : { uri: null, failed: false };
}
