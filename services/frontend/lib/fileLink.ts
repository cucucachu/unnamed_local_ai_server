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
