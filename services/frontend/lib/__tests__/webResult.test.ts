import {
  hostnameFromUrl,
  parseFetchResult,
  parseSearchResults,
  type ParsedFetchResult,
  type ParsedSearchResults,
} from '../webResult';

describe('parseSearchResults', () => {
  const cases: { name: string; input: string; expected: ParsedSearchResults }[] = [
    {
      name: 'happy path — two full results',
      input:
        '1. ggml-org/llama.cpp\n' +
        '   https://github.com/ggml-org/llama.cpp\n' +
        '   LLM inference in C/C++\n' +
        '2. llama.cpp docs\n' +
        '   https://example.com/docs\n' +
        '   Documentation site',
      expected: {
        isError: false,
        resultCount: 2,
        results: [
          { title: 'ggml-org/llama.cpp', url: 'https://github.com/ggml-org/llama.cpp', snippet: 'LLM inference in C/C++' },
          { title: 'llama.cpp docs', url: 'https://example.com/docs', snippet: 'Documentation site' },
        ],
      },
    },
    {
      name: 'missing snippet renders as empty string (tool substitutes "" server-side)',
      input: '1. Some Title\n   https://example.com/\n   ',
      expected: {
        isError: false,
        resultCount: 1,
        results: [{ title: 'Some Title', url: 'https://example.com/', snippet: '' }],
      },
    },
    {
      name: 'literal "No results found." (no list) parses to zero results, not an error',
      input: 'No results found.',
      expected: { isError: false, results: [], resultCount: 0 },
    },
    {
      name: 'error string (whole preview starts with "Error: ")',
      input: 'Error: web_search failed: ConnectError(\'boom\')',
      expected: {
        isError: true,
        errorMessage: "web_search failed: ConnectError('boom')",
        results: [],
        resultCount: 0,
      },
    },
    {
      name: 'preview truncated (chat_ws.py 2000-char cap) mid-way through the 3rd entry\'s snippet line — first 2 results still parse',
      input:
        '1. First\n   https://a.example/\n   snippet a\n' +
        '2. Second\n   https://b.example/\n   snippet b\n' +
        '3. Third result title\n   https://c.example/\n   this snippet got cut off mid-sen',
      expected: {
        isError: false,
        resultCount: 3,
        results: [
          { title: 'First', url: 'https://a.example/', snippet: 'snippet a' },
          { title: 'Second', url: 'https://b.example/', snippet: 'snippet b' },
          { title: 'Third result title', url: 'https://c.example/', snippet: 'this snippet got cut off mid-sen' },
        ],
      },
    },
    {
      name: 'preview truncated mid-way through the url line of the last entry — the (truncated) url is still usable, so the entry is kept with an empty snippet rather than dropped entirely',
      input: '1. First\n   https://a.example/\n   snippet a\n2. Second\n   https://b.exam',
      expected: {
        isError: false,
        resultCount: 2,
        results: [
          { title: 'First', url: 'https://a.example/', snippet: 'snippet a' },
          { title: 'Second', url: 'https://b.exam', snippet: '' },
        ],
      },
    },
    {
      name: 'preview truncated immediately after the last entry\'s number+title line (no url line at all yet) — dropped from results, still counted',
      input: '1. First\n   https://a.example/\n   snippet a\n2. Second title only, cut r',
      expected: {
        isError: false,
        resultCount: 2,
        results: [{ title: 'First', url: 'https://a.example/', snippet: 'snippet a' }],
      },
    },
    {
      name: 'preview truncated mid-way through the number itself of what would be entry 2 (e.g. cut right at "\\n2") — not counted (doesn\'t match the header pattern)',
      input: '1. First\n   https://a.example/\n   snippet a\n2',
      expected: {
        isError: false,
        resultCount: 1,
        results: [{ title: 'First', url: 'https://a.example/', snippet: 'snippet a' }],
      },
    },
    {
      name: 'a snippet line that coincidentally starts with "N. " text is not mistaken for a new entry, since it is out of sequence',
      input: '1. First\n   https://a.example/\n   9. this looks like a header but is just snippet text',
      expected: {
        isError: false,
        resultCount: 1,
        results: [
          {
            title: 'First',
            url: 'https://a.example/',
            snippet: '9. this looks like a header but is just snippet text',
          },
        ],
      },
    },
    {
      name: 'empty string is not "No results found." and has no headers — parses to zero results',
      input: '',
      expected: { isError: false, results: [], resultCount: 0 },
    },
  ];

  for (const { name, input, expected } of cases) {
    it(name, () => {
      expect(parseSearchResults(input)).toEqual(expected);
    });
  }
});

describe('parseFetchResult', () => {
  const cases: { name: string; input: string; expected: ParsedFetchResult }[] = [
    {
      name: 'happy path',
      input: 'Title: Example Domain\nURL: https://example.com/\n\nThis is an example page.',
      expected: {
        isError: false,
        title: 'Example Domain',
        url: 'https://example.com/',
        text: 'This is an example page.',
        truncatedByTool: false,
      },
    },
    {
      name: 'missing title falls back to the tool\'s own "(untitled)" substitution — treated as ordinary body text, not re-substituted',
      input: 'Title: (untitled)\nURL: http://example.com/x\n\nhi',
      expected: {
        isError: false,
        title: '(untitled)',
        url: 'http://example.com/x',
        text: 'hi',
        truncatedByTool: false,
      },
    },
    {
      name: 'empty body (blank line still present per the exact format)',
      input: 'Title: Empty Page\nURL: https://example.com/empty\n\n',
      expected: {
        isError: false,
        title: 'Empty Page',
        url: 'https://example.com/empty',
        text: '',
        truncatedByTool: false,
      },
    },
    {
      name: 'tool-side truncation marker is extracted into truncatedByTool, stripped from text',
      input: 'Title: Long Page\nURL: http://example.com/x\n\n' + 'a'.repeat(10) + '\n[content truncated]',
      expected: {
        isError: false,
        title: 'Long Page',
        url: 'http://example.com/x',
        text: 'a'.repeat(10),
        truncatedByTool: true,
      },
    },
    {
      name: 'error string (whole preview starts with "Error: ")',
      input: 'Error: destination not allowed by egress policy',
      expected: {
        isError: true,
        errorMessage: 'destination not allowed by egress policy',
        title: null,
        url: null,
        text: '',
        truncatedByTool: false,
      },
    },
    {
      name: 'malformed input (garbage string, not this tool\'s format at all) falls back to title/url: null, text: ""',
      input: 'web_fetch failed: 502 Bad Gateway',
      expected: { isError: false, title: null, url: null, text: '', truncatedByTool: false },
    },
    {
      name: 'empty string falls back to title/url: null, text: ""',
      input: '',
      expected: { isError: false, title: null, url: null, text: '', truncatedByTool: false },
    },
    {
      name: 'preview (chat_ws.py 2000-char cap) truncated mid-way through the Title: line itself — nothing usable survived',
      input: 'Title: Some very long title that got cut off right in the mid',
      expected: { isError: false, title: null, url: null, text: '', truncatedByTool: false },
    },
    {
      name: 'preview truncated exactly at the end of the Title: line (no URL: line at all yet)',
      input: 'Title: Example Domain',
      expected: { isError: false, title: null, url: null, text: '', truncatedByTool: false },
    },
    {
      name: 'preview truncated mid-way through the URL: line — the (truncated) url is still extracted as-is, same lenient treatment as a mid-cut search-result url',
      input: 'Title: Example Domain\nURL: https://example.com/some-long-path-that-got-cu',
      expected: {
        isError: false,
        title: 'Example Domain',
        url: 'https://example.com/some-long-path-that-got-cu',
        text: '',
        truncatedByTool: false,
      },
    },
    {
      name: 'preview truncated exactly at the end of the URL: line (blank-line separator never arrived)',
      input: 'Title: Example Domain\nURL: https://example.com/',
      expected: { isError: false, title: 'Example Domain', url: 'https://example.com/', text: '', truncatedByTool: false },
    },
    {
      name: 'preview truncated mid-sentence in the body text (the common case: a long fetched page cut by the 2000-char preview cap)',
      input: 'Title: Example Domain\nURL: https://example.com/\n\nThis page has a lot of text that gets cut off mid-sen',
      expected: {
        isError: false,
        title: 'Example Domain',
        url: 'https://example.com/',
        text: 'This page has a lot of text that gets cut off mid-sen',
        truncatedByTool: false,
      },
    },
  ];

  for (const { name, input, expected } of cases) {
    it(name, () => {
      expect(parseFetchResult(input)).toEqual(expected);
    });
  }
});

describe('hostnameFromUrl', () => {
  const cases: { name: string; input: string; expected: string }[] = [
    { name: 'plain https URL', input: 'https://example.com/path?query=1#frag', expected: 'example.com' },
    { name: 'http URL with a port', input: 'http://example.com:8080/path', expected: 'example.com' },
    { name: 'URL with userinfo (rare, but the format allows it)', input: 'https://user:pass@example.com/x', expected: 'example.com' },
    { name: 'bare domain, no path', input: 'https://example.com', expected: 'example.com' },
    { name: 'subdomain', input: 'https://docs.github.com/en/x', expected: 'docs.github.com' },
    { name: 'not an absolute URL (defensive) — falls back to the input up to the first path-like delimiter', input: 'example.com/x', expected: 'example.com' },
    { name: 'empty string (defensive) — no crash', input: '', expected: '' },
  ];

  for (const { name, input, expected } of cases) {
    it(name, () => {
      expect(hostnameFromUrl(input)).toBe(expected);
    });
  }
});
