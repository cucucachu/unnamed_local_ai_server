import { normalizeFileLink, workspacePathFromHref } from '../fileLink';

describe('workspacePathFromHref', () => {
  it('accepts a bare workspace-relative href the model sometimes emits', () => {
    expect(workspacePathFromHref('notes/link-test.md')).toBe('notes/link-test.md');
  });

  it('ignores http(s) and mailto links', () => {
    expect(workspacePathFromHref('https://example.com/x.md')).toBeNull();
    expect(workspacePathFromHref('mailto:a@b.c')).toBeNull();
  });
});

describe('normalizeFileLink', () => {
  const cases: { href: string; expected: string }[] = [
    { href: 'file:notes/x.md', expected: 'notes/x.md' },
    { href: 'file:/workspace/notes/x.md', expected: 'notes/x.md' },
    { href: 'file:///workspace/notes/x.md', expected: 'notes/x.md' },
    { href: 'file://workspace/notes/x.md', expected: 'notes/x.md' },
    { href: 'file:/notes/x.md', expected: 'notes/x.md' },
    { href: 'file:notes/hello%20world.md', expected: 'notes/hello world.md' },
    { href: 'file:', expected: '' },
    { href: 'file:///workspace', expected: '' },
  ];

  it.each(cases)('normalizes $href to $expected', ({ href, expected }) => {
    expect(normalizeFileLink(href)).toBe(expected);
  });
});
