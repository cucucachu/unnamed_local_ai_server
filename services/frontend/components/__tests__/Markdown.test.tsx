import { createElement } from 'react';
import { Linking, Text as RNText } from 'react-native';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';

import { Markdown } from '../Markdown';

const FIXTURE = `# Heading One

A paragraph with a [link](https://example.com) and \`inline code\`.

- list item one
- list item two

| Planet | Type |
| --- | --- |
| Earth | Terrestrial |

> A quoted line

- [ ] unchecked task
- [x] checked task

\`\`\`python
print("hello")
\`\`\`

![diagram of mars](https://example.com/mars.png)

See the [workspace file](file:///tmp/notes.txt).
`;

function renderMarkdown(source: string = FIXTURE, onFileLink?: (url: string) => void): ReactTestRenderer {
  let renderer!: ReactTestRenderer;
  act(() => {
    renderer = create(createElement(Markdown, { children: source, onFileLink }));
  });
  return renderer;
}

function textOf(renderer: ReactTestRenderer): string {
  return renderer.root
    .findAllByType(RNText)
    .map((node) => {
      const children = node.props.children;
      return Array.isArray(children) ? children.join('') : String(children ?? '');
    })
    .join(' | ');
}

describe('Markdown', () => {
  it('renders headings, lists, tables, links, and a code block from a fixture document', () => {
    const renderer = renderMarkdown();
    const text = textOf(renderer);

    expect(text).toContain('Heading One');
    expect(text).toContain('list item one');
    expect(text).toContain('Earth');
    expect(text).toContain('Terrestrial');
    expect(text).toContain('print("hello")');
    expect(text).toContain('link');

    expect(renderer.root.findByProps({ testID: 'markdown-code-block' })).toBeTruthy();
    expect(renderer.root.findByProps({ testID: 'markdown' })).toBeTruthy();
  });

  it('snapshots the fenced code block style', () => {
    const renderer = renderMarkdown();
    const codeBlock = renderer.root.findByProps({ testID: 'markdown-code-block' });
    expect(codeBlock.props.style).toMatchSnapshot();
  });

  it('renders image alt text instead of loading the image', () => {
    const renderer = renderMarkdown();
    const alt = renderer.root.findByProps({ testID: 'markdown-image-alt' });
    expect(textOf(renderer)).toContain('diagram of mars');
    expect(alt).toBeTruthy();
    expect(JSON.stringify(renderer.toJSON())).not.toContain('https://example.com/mars.png');
  });

  it('opens http(s) links via Linking.openURL', () => {
    const openURL = jest.spyOn(Linking, 'openURL').mockResolvedValue(undefined);
    const renderer = renderMarkdown('[docs](https://example.com)');
    const pressable = renderer.root.findAll(
      (node) => typeof (node.props as { onPress?: unknown }).onPress === 'function',
    )[0];
    act(() => {
      (pressable.props as { onPress: () => void }).onPress();
    });
    expect(openURL).toHaveBeenCalledWith('https://example.com');
    openURL.mockRestore();
  });

  it('keeps a file: link tappable when it sits mid-sentence', () => {
    const onFileLink = jest.fn();
    const renderer = renderMarkdown(
      'I saved it at [notes/link-test.md](file:notes/link-test.md).',
      onFileLink,
    );
    const link = renderer.root
      .findAllByProps({ testID: 'file-link' })
      .find((node) => typeof (node.props as { onPress?: unknown }).onPress === 'function');
    expect(link).toBeTruthy();
    act(() => {
      (link?.props as { onPress: () => void }).onPress();
    });
    expect(onFileLink).toHaveBeenCalledWith('notes/link-test.md');
  });

  it('unwraps a file: link the model put inside a code span', () => {
    const onFileLink = jest.fn();
    const renderer = renderMarkdown(
      'Saved at `[link-test.md](file:notes/link-test.md)`.',
      onFileLink,
    );
    const link = renderer.root
      .findAllByProps({ testID: 'file-link' })
      .find((node) => typeof (node.props as { onPress?: unknown }).onPress === 'function');
    expect(link).toBeTruthy();
    act(() => {
      (link?.props as { onPress: () => void }).onPress();
    });
    expect(onFileLink).toHaveBeenCalledWith('notes/link-test.md');
  });

  it('treats a bare workspace-relative href as a file link', () => {
    const onFileLink = jest.fn();
    const renderer = renderMarkdown('[link-test.md](notes/link-test.md)', onFileLink);
    const link = renderer.root
      .findAllByProps({ testID: 'file-link' })
      .find((node) => typeof (node.props as { onPress?: unknown }).onPress === 'function');
    act(() => {
      (link?.props as { onPress: () => void }).onPress();
    });
    expect(onFileLink).toHaveBeenCalledWith('notes/link-test.md');
  });

  it('treats file:relative/path (no slashes after the scheme) as a file link', () => {
    const onFileLink = jest.fn();
    const renderer = renderMarkdown('[link-test.md](file:notes/link-test.md)', onFileLink);
    const link = renderer.root
      .findAllByProps({ testID: 'file-link' })
      .find((node) => typeof (node.props as { onPress?: unknown }).onPress === 'function');
    act(() => {
      (link?.props as { onPress: () => void }).onPress();
    });
    expect(onFileLink).toHaveBeenCalledWith('notes/link-test.md');
  });

  it('routes file: links to onFileLink and does not call Linking.openURL', () => {
    const onFileLink = jest.fn();
    const openURL = jest.spyOn(Linking, 'openURL').mockResolvedValue(undefined);
    const renderer = renderMarkdown('[notes](file:///tmp/notes.txt)', onFileLink);
    const pressable = renderer.root.findAll(
      (node) => typeof (node.props as { onPress?: unknown }).onPress === 'function',
    )[0];
    act(() => {
      (pressable.props as { onPress: () => void }).onPress();
    });
    expect(onFileLink).toHaveBeenCalledWith('tmp/notes.txt');
    expect(renderer.root.findByProps({ testID: 'file-link' })).toBeTruthy();
    expect(openURL).not.toHaveBeenCalled();
    openURL.mockRestore();
  });

  it('rewrites task-list markers to unicode checkboxes', () => {
    const renderer = renderMarkdown();
    const text = textOf(renderer);
    expect(text).toContain('☐');
    expect(text).toContain('☑');
    expect(text).toContain('unchecked task');
    expect(text).toContain('checked task');
  });
});
