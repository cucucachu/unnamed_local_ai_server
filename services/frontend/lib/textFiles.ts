import { ApiError, apiBase, apiFetch, detailFromBody } from './api';
import { joinPath, parentPath, statPath } from './files';
import { authHeaders, notifyUnauthorized } from './session';

/**
 * Text files in the Files viewer/editor (M22-10, docs/APP_TEMPLATES.md §8):
 * which names open as text, loading one with its `version`, and saving with
 * the platform's `if_version` check (`409 changed` when someone else wrote it
 * since).
 */

/** Same limit as the search index. */
export const TEXT_MAX_BYTES = 1024 * 1024;

const TEXT_EXTENSIONS = new Set([
  'txt', 'md', 'markdown', 'mdx', 'json', 'jsonc', 'yaml', 'yml', 'toml', 'ini', 'cfg', 'conf',
  'env', 'log', 'csv', 'tsv', 'xml', 'html', 'htm', 'css', 'scss', 'js', 'jsx', 'mjs', 'cjs',
  'ts', 'tsx', 'py', 'rb', 'go', 'rs', 'java', 'kt', 'swift', 'c', 'h', 'cpp', 'hpp', 'cs',
  'php', 'sh', 'bash', 'zsh', 'sql', 'graphql', 'proto', 'lua', 'r', 'vue', 'svelte',
  'gitignore', 'dockerignore', 'editorconfig', 'properties', 'srt', 'vtt', 'tex', 'rst', 'org',
]);

function extensionOf(name: string): string | null {
  const dot = name.lastIndexOf('.');
  return dot === -1 ? null : name.slice(dot + 1).toLowerCase();
}

/** Whether `name` opens in the text viewer: a known text extension, or none
 * at all (README, Makefile); the content's UTF-8 check decides the rest. */
export function isTextFileName(name: string): boolean {
  const ext = extensionOf(name);
  return ext === null || TEXT_EXTENSIONS.has(ext);
}

export function isMarkdownName(name: string): boolean {
  const ext = extensionOf(name);
  return ext === 'md' || ext === 'markdown' || ext === 'mdx';
}

const PROSE_EXTENSIONS = new Set(['txt', 'log', 'srt', 'vtt', 'tex', 'rst', 'org']);

/** How the viewer shows a text file (M22-11): markdown rendered, CSV/TSV as
 * a table, prose as plain text, everything else as code with line numbers. */
export type TextView = 'markdown' | 'csv' | 'text' | 'code';

export function textViewOf(name: string): TextView {
  const ext = extensionOf(name);
  if (isMarkdownName(name)) return 'markdown';
  if (ext === 'csv' || ext === 'tsv') return 'csv';
  if (ext === null || PROSE_EXTENSIONS.has(ext)) return 'text';
  return 'code';
}

export function isPdfName(name: string): boolean {
  return extensionOf(name) === 'pdf';
}

/** Rows of a CSV file (RFC 4180 quoting, CRLF or LF). A trailing newline
 * doesn't make an empty last row. */
export function parseCsv(text: string, delimiter: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = '';
  let quoted = false;
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"' && text[i + 1] === '"') {
        field += '"';
        i += 1;
      } else if (ch === '"') {
        quoted = false;
      } else {
        field += ch;
      }
    } else if (ch === '"' && field === '') {
      quoted = true;
    } else if (ch === delimiter) {
      row.push(field);
      field = '';
    } else if (ch === '\n' || ch === '\r') {
      if (ch === '\r' && text[i + 1] === '\n') i += 1;
      row.push(field);
      rows.push(row);
      row = [];
      field = '';
    } else {
      field += ch;
    }
  }
  if (field !== '' || row.length > 0) {
    row.push(field);
    rows.push(row);
  }
  return rows;
}

/** Tab for `.tsv`; otherwise `;` when the header line has more of them than commas. */
export function csvDelimiterFor(name: string, text: string): string {
  if (extensionOf(name) === 'tsv') return '\t';
  const header = text.slice(0, text.search(/\r?\n|$/));
  const count = (ch: string) => header.split(ch).length - 1;
  return count(';') > count(',') ? ';' : ',';
}

export class NotTextError extends Error {
  readonly reason: 'too_large' | 'binary';

  constructor(reason: 'too_large' | 'binary') {
    super(reason);
    this.name = 'NotTextError';
    this.reason = reason;
  }
}

export interface TextFile {
  path: string;
  text: string;
  /** What a save sends as `if_version`; null from a platform without it. */
  version: string | null;
  writable: boolean;
}

function decodeUtf8(bytes: ArrayBuffer): string | null {
  try {
    const text = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
    return text.includes('\u0000') ? null : text;
  } catch {
    return null;
  }
}

/** Stat (for `version` and `writable`), then the whole file. Stat goes first,
 * so a write in between makes the version older, never newer, than the text. */
export async function loadTextFile(path: string): Promise<TextFile> {
  const { entry, writable } = await statPath(path);
  if (entry.type !== 'file') throw new ApiError(404, 'not_found');
  if (entry.size > TEXT_MAX_BYTES) throw new NotTextError('too_large');
  const response = await fetch(`${apiBase()}/api/platform/files/stream?path=${encodeURIComponent(entry.path)}`, {
    credentials: 'include',
    headers: authHeaders(),
  });
  if (response.status === 401) notifyUnauthorized();
  if (!response.ok) {
    let body: unknown;
    try {
      body = await response.json();
    } catch {
      body = undefined;
    }
    throw new ApiError(response.status, detailFromBody(body, response.statusText || 'Request failed'));
  }
  const text = decodeUtf8(await response.arrayBuffer());
  if (text === null) throw new NotTextError('binary');
  return { path: entry.path, text, version: entry.version ?? null, writable };
}

/** `POST /files/write`; with `ifVersion`, `ApiError 409 changed` if the file
 * moved on. Returns the new version. */
export async function saveTextFile(path: string, text: string, ifVersion: string | null): Promise<string | null> {
  const out = await apiFetch<{ path: string; version?: string }>('/api/platform/files/write', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path, content: text, ...(ifVersion ? { if_version: ifVersion } : {}) }),
  });
  return out.version ?? null;
}

/** `notes (copy).md`, or `notes (copy 2).md` and so on, whichever is free. */
export async function copyPathFor(path: string): Promise<string> {
  const name = path.split('/').pop() ?? path;
  const dot = name.lastIndexOf('.');
  const [stem, ext] = dot > 0 ? [name.slice(0, dot), name.slice(dot)] : [name, ''];
  for (let n = 1; n < 100; n += 1) {
    const candidate = joinPath(parentPath(path), `${stem} (copy${n === 1 ? '' : ` ${n}`})${ext}`);
    try {
      await statPath(candidate);
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) return candidate;
      throw error;
    }
  }
  throw new ApiError(409, 'already_exists');
}
