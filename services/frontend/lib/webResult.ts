/**
 * Pure parsers for the `web_search` / `web_fetch` tools' `result_preview`
 * text — the exact formats `services/agent-server/app/agent/web_tools.py`
 * produces (`_format_search_results` / the `web_fetch` closure body),
 * mirroring `lib/execResult.ts`'s "parse the known plain-text shape,
 * tolerate anything that doesn't match it" pattern for `execute_code`.
 *
 * `web_search` result text: a numbered list, one result per entry, each
 * entry exactly:
 *
 *   {n}. {title}
 *      {url}
 *      {snippet}
 *
 * (3 lines, 3-space indent on lines 2-3, entries joined by `\n`, no
 * trailing newline). Missing title -> the literal `"(untitled)"`; missing
 * snippet -> `""`. Zero results -> the literal `"No results found."` (no
 * list at all). Any error (bad request, egress-denied, transport failure,
 * ...) -> the *whole* string starts with `"Error: "` instead.
 *
 * `web_fetch` result text is exactly:
 *
 *   Title: {title}
 *   URL: {final_url}
 *
 *   {text}
 *
 * (2-line header, one blank line, then the body — note the blank line is
 * NOT optional even when `text` is empty). Missing title -> `"(untitled)"`.
 * If the tool's own `web_fetch_tool_max_chars` cap truncated `text`, the
 * body ends with the literal `"\n[content truncated]"`. Errors: same
 * `"Error: "`-prefixed whole-string convention as `web_search`.
 *
 * Both formats above are the tool's *own* output — but what a `ToolItemCard`
 * actually renders is `tool_end.result_preview`, which `chat_ws.py`
 * additionally truncates to `_RESULT_PREVIEW_TRUNCATE_LEN` (2000) chars
 * *on top of* whatever the tool itself already did. That second truncation
 * is a dumb char-count slice with no knowledge of the text format
 * underneath it, so it can land anywhere — mid-entry in a search list,
 * mid-line in a fetch header, mid-sentence in fetched body text. Every
 * function here is written to degrade gracefully at exactly those cut
 * points (never throw, never produce garbage/`undefined`-ish output) rather
 * than assume a preview it receives is always a complete, well-formed
 * instance of the format above — see each function's own doc for exactly
 * how it handles a cut in each position.
 */

export interface ParsedSearchResult {
  title: string;
  url: string;
  snippet: string;
}

export interface ParsedSearchResults {
  /** `true` iff `preview` starts with `"Error: "` — `results`/`resultCount`
   * are always empty/0 in that case. */
  isError: boolean;
  /** The text after the `"Error: "` prefix; `undefined` when `isError` is
   * `false`. */
  errorMessage?: string;
  /** Fully-parsed results (title + a non-empty url + snippet), in order.
   * May be shorter than `resultCount` — see that field's doc. */
  results: ParsedSearchResult[];
  /** How many numbered entries (`"{n}. "` at a line start, in the expected
   * 1, 2, 3, ... sequence) were STARTED in `preview`, regardless of
   * whether enough of that entry survived the preview's 2000-char cut to
   * make it into `results` — this is what the collapsed chip's "N results"
   * count should read, since the tool really did find (and start
   * reporting) that many even if the last one got cut off mid-way. */
  resultCount: number;
}

const NO_RESULTS_TEXT = 'No results found.';
const ERROR_PREFIX = 'Error: ';
/** Only a header at the exact next expected number (starting at 1) counts
 * — guards against a snippet's own text coincidentally starting with
 * something that looks like `"N. "` at the start of a wrapped line. */
const ENTRY_HEADER = /^(\d+)\. (.*)$/;
const INDENT = '   ';

/**
 * Parses one `web_search` `result_preview` string (see module doc for the
 * exact format, and for how a preview cut mid-entry by the 2000-char
 * `result_preview` cap is handled: any entry whose url line didn't survive
 * the cut is dropped from `results` but still counted in `resultCount`).
 */
export function parseSearchResults(preview: string): ParsedSearchResults {
  if (preview.startsWith(ERROR_PREFIX)) {
    return {
      isError: true,
      errorMessage: preview.slice(ERROR_PREFIX.length),
      results: [],
      resultCount: 0,
    };
  }

  if (preview === NO_RESULTS_TEXT) {
    return { isError: false, results: [], resultCount: 0 };
  }

  const lines = preview.split('\n');
  const results: ParsedSearchResult[] = [];
  let resultCount = 0;
  let expectedNext = 1;

  for (let i = 0; i < lines.length; i += 1) {
    const headerMatch = ENTRY_HEADER.exec(lines[i]);
    if (headerMatch === null || Number.parseInt(headerMatch[1], 10) !== expectedNext) continue;

    expectedNext += 1;
    resultCount += 1;

    const title = headerMatch[2].length > 0 ? headerMatch[2] : '(untitled)';
    const urlLine = lines[i + 1];
    if (urlLine === undefined || !urlLine.startsWith(INDENT)) {
      // Cut off before the url line ever arrived — nothing usable to show
      // for this entry (a result with no url can't be tapped/linked to).
      continue;
    }
    const url = urlLine.slice(INDENT.length);
    if (url.length === 0) continue;

    const snippetLine = lines[i + 2];
    const snippet = snippetLine !== undefined && snippetLine.startsWith(INDENT) ? snippetLine.slice(INDENT.length) : '';

    results.push({ title, url, snippet });
  }

  return { isError: false, results, resultCount };
}

export interface ParsedFetchResult {
  /** `true` iff `preview` starts with `"Error: "`. */
  isError: boolean;
  /** The text after the `"Error: "` prefix; `undefined` when `isError` is
   * `false`. */
  errorMessage?: string;
  /** The `Title:` line's value, or `null` if `preview` was cut before that
   * whole line survived (or didn't start with `"Title: "` at all). */
  title: string | null;
  /** The `URL:` line's value, or `null` if `preview` was cut before the
   * `URL: ` prefix itself ever appeared. If the prefix appeared but the
   * rest of the line got cut short, whatever text survived is used as-is
   * (see the function doc — there's no reliable way to tell a genuinely
   * short url apart from a truncated one). */
  url: string | null;
  /** The extracted page text (the part after the blank line), with any
   * trailing `"\n[content truncated]"` marker stripped out into
   * `truncatedByTool` instead. `""` if the preview was cut before any body
   * text arrived (including the case where `title`/`url` themselves are
   * `null`). */
  text: string;
  /** `true` iff the *tool itself* (not `chat_ws.py`'s preview cap) capped
   * `text` at `web_fetch_tool_max_chars` and appended its own
   * `"[content truncated]"` marker. */
  truncatedByTool: boolean;
}

const TITLE_PREFIX = 'Title: ';
const URL_PREFIX = 'URL: ';
const TOOL_TRUNCATED_MARKER = '\n[content truncated]';

/**
 * Parses one `web_fetch` `result_preview` string (see module doc for the
 * exact format). Degrades field-by-field as `preview` gets shorter: a cut
 * mid-`Title:`-line (or before any `URL:` line ever appears) yields
 * `title: null, url: null, text: ''`; a cut anywhere in/after the `URL:`
 * line yields a real `title`, a (possibly truncated, but non-`null`) `url`,
 * and `text: ''` until a body actually starts; a cut mid-body yields real
 * `title`/`url` and whatever body text survived.
 */
export function parseFetchResult(preview: string): ParsedFetchResult {
  if (preview.startsWith(ERROR_PREFIX)) {
    return {
      isError: true,
      errorMessage: preview.slice(ERROR_PREFIX.length),
      title: null,
      url: null,
      text: '',
      truncatedByTool: false,
    };
  }

  if (!preview.startsWith(TITLE_PREFIX)) {
    return { isError: false, title: null, url: null, text: '', truncatedByTool: false };
  }

  const firstNewline = preview.indexOf('\n');
  if (firstNewline === -1) {
    // Cut mid-`Title:` line — not even the title survived intact.
    return { isError: false, title: null, url: null, text: '', truncatedByTool: false };
  }
  const title = preview.slice(TITLE_PREFIX.length, firstNewline);

  const afterTitle = preview.slice(firstNewline + 1);
  if (!afterTitle.startsWith(URL_PREFIX)) {
    return { isError: false, title: null, url: null, text: '', truncatedByTool: false };
  }

  // Same lenient treatment as a mid-cut `web_search` result url (see that
  // function's doc): whatever text survived after the `URL: ` prefix, up
  // to the next newline (or end of string if the cut landed inside/right
  // at the end of this line), is used as-is — there's no reliable way to
  // tell "the url is genuinely this short" apart from "the url got cut
  // off here", so this never nulls out a `url` that has SOME content.
  const secondNewline = afterTitle.indexOf('\n');
  const url =
    secondNewline === -1 ? afterTitle.slice(URL_PREFIX.length) : afterTitle.slice(URL_PREFIX.length, secondNewline);

  // Body only exists once a third newline (the blank-line separator)
  // arrived; if the cut landed exactly at/inside the `URL:` line itself,
  // there's no body to extract yet.
  let body = secondNewline === -1 ? '' : afterTitle.slice(secondNewline + 1);
  if (body.startsWith('\n')) {
    body = body.slice(1);
  }

  const truncatedByTool = body.endsWith(TOOL_TRUNCATED_MARKER);
  if (truncatedByTool) {
    body = body.slice(0, -TOOL_TRUNCATED_MARKER.length);
  }

  return { isError: false, title, url, text: body, truncatedByTool };
}

/**
 * Hostname (no scheme, no userinfo, no port, no path) for a URL string —
 * used for both `web_search`'s per-result hostname and `web_fetch`'s
 * collapsed-header hostname. Hand-rolled with a regex rather than the
 * `URL` global: it needs to run identically in Jest's Node test
 * environment and RN/RN-Web at runtime without relying on a global whose
 * availability varies across those (unlike `new URL(...)`, which isn't
 * guaranteed present in React Native's JS engine the way it is on web).
 * Falls back to the input string itself (never throws) if it doesn't look
 * like an absolute URL with a scheme.
 */
export function hostnameFromUrl(url: string): string {
  const match = /^[a-zA-Z][a-zA-Z0-9+.-]*:\/\/(?:[^/?#]*@)?([^/?#:]+)/.exec(url);
  if (match) return match[1];
  return url.split(/[/?#]/)[0];
}
