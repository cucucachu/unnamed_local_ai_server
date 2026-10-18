/**
 * `file:` href → virtual files path (M9-03, M11-01).
 *
 * `/personal/...` and `/spaces/<slug>/...` are kept as they are, and so are
 * their exec-shell spellings, which the model sometimes echoes into a link:
 * `/files/personal/...` and `/files/spaces/<slug>/...` (`file:///files/...`
 * too). Anything else is a pre-spaces link, relative to what used to be the
 * single files root, which is now the user's personal space:
 * `file:notes.txt`, `file:/notes.txt` and `file:/files/notes.txt` all open
 * `/personal/notes.txt`.
 */
export function filePathFromHref(href: string): string | null {
  if (href.startsWith('http:') || href.startsWith('https:') || href.startsWith('mailto:')) {
    return null;
  }
  if (href === '' || href.startsWith('#')) return null;
  return normalizeFileLink(href.startsWith('file:') ? href : `file:${href}`);
}

const SPACE_ROOTS = ['personal', 'spaces'];

export function normalizeFileLink(href: string): string {
  let path = href.startsWith('file:') ? href.slice('file:'.length) : href;

  try {
    path = decodeURIComponent(path);
  } catch {
    // keep the raw path if it isn't valid URI encoding
  }

  let stripped = path.replace(/^\/+/, '').replace(/\/+$/, '');
  if (stripped === 'files') stripped = '';
  else if (stripped.startsWith('files/')) stripped = stripped.slice('files/'.length);
  if (SPACE_ROOTS.some((root) => stripped === root || stripped.startsWith(`${root}/`))) {
    return `/${stripped}`;
  }
  return stripped ? `/personal/${stripped}` : '/personal';
}

/** A markdown link in a file under `baseDir` as a virtual path: relative hrefs
 * (`b.md`, `../notes/c.md`) resolve against the file's folder, never above
 * its space; `file:` and absolute paths go through `filePathFromHref`; web
 * links (`https:`, `mailto:` and other schemes) and `#anchors` give null. */
export function linkPathFrom(href: string, baseDir?: string): string | null {
  if (href.startsWith('file:') || href.startsWith('/')) return filePathFromHref(href);
  if (baseDir === undefined) return filePathFromHref(href);
  if (href === '' || href.startsWith('#') || /^[a-z][a-z0-9+.-]*:/i.test(href)) return null;
  let rel = href.split('#')[0].split('?')[0];
  try {
    rel = decodeURIComponent(rel);
  } catch {
    // keep the raw path if it isn't valid URI encoding
  }
  const parts = baseDir.split('/').filter(Boolean);
  const floor = parts[0] === 'spaces' ? 2 : 1;
  for (const segment of rel.split('/')) {
    if (segment === '..') {
      if (parts.length > floor) parts.pop();
    } else if (segment !== '.' && segment !== '') {
      parts.push(segment);
    }
  }
  return `/${parts.join('/')}`;
}
