/**
 * `file:` href → file-tool-root-relative path (M9-03).
 *
 * Absolute `/files/...` forms (including `file:///files/...`) are
 * stripped to the relative remainder, since the model is occasionally
 * asked to describe both roots (file tools at `/`, `execute_code`'s
 * shell at `/files`) and may echo the `/files` prefix into a link by
 * mistake even though the prompt tells it not to. Other leading slashes
 * are dropped so `file:/notes/x.md` is treated as root-relative
 * `notes/x.md`.
 */
export function filePathFromHref(href: string): string | null {
  if (href.startsWith('http:') || href.startsWith('https:') || href.startsWith('mailto:')) {
    return null;
  }
  if (href === '' || href.startsWith('#')) return null;
  return normalizeFileLink(href.startsWith('file:') ? href : `file:${href}`);
}

export function normalizeFileLink(href: string): string {
  let path = href.startsWith('file:') ? href.slice('file:'.length) : href;

  try {
    path = decodeURIComponent(path);
  } catch {
    // keep the raw path if it isn't valid URI encoding
  }

  const stripped = path.replace(/^\/+/, '');
  if (stripped === 'files') return '';
  if (stripped.startsWith('files/')) {
    return stripped.slice('files/'.length);
  }
  return stripped;
}
