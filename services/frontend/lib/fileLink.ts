/**
 * `file:` href → workspace-relative path (M9-03).
 *
 * Absolute `/workspace/...` forms (including `file:///workspace/...`) are
 * stripped to the relative remainder. Other leading slashes are dropped so
 * `file:/notes/x.md` is treated as workspace-relative `notes/x.md`.
 */
export function workspacePathFromHref(href: string): string | null {
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
  if (stripped === 'workspace') return '';
  if (stripped.startsWith('workspace/')) {
    return stripped.slice('workspace/'.length);
  }
  return stripped;
}
