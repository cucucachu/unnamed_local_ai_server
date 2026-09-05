import { createElement, type ComponentProps, type ReactElement, type ReactNode } from 'react';
import { Linking, Platform, ScrollView, StyleSheet, Text, View } from 'react-native';
import MarkdownDisplay, { MarkdownIt, type ASTNode } from '@ronradtke/react-native-markdown-display';

import { normalizeFileLink, workspacePathFromHref } from '@/lib/fileLink';
import { monospaceFontFamily, theme } from '@/lib/theme';

export interface MarkdownProps {
  children: string;
  /** Workspace-relative path from a `file:` link (already normalized). */
  onFileLink?: (path: string) => void;
}

/** `markdown-it` does not treat `- [ ]` / `- [x]` as task items. Rewrite the
 * checkbox marker to a unicode box so the default list renderer shows them
 * without a second plugin dependency. Only the leading text of an inline
 * token is touched, so `[ ]` inside a fenced code block is left alone. */
function taskListUnicodePlugin(md: InstanceType<typeof MarkdownIt>): void {
  md.core.ruler.after('inline', 'task-list-unicode', (state) => {
    for (const token of state.tokens) {
      if (token.type !== 'inline' || token.children == null) continue;
      for (const child of token.children) {
        if (child.type !== 'text') continue;
        child.content = child.content.replace(/^\[ \]\s+/, '☐ ').replace(/^\[[xX]\]\s+/, '☑ ');
      }
    }
  });
}

const markdownIt = new MarkdownIt({ typographer: true }).use(taskListUnicodePlugin);
const defaultValidateLink = markdownIt.validateLink.bind(markdownIt);
markdownIt.validateLink = (url: string) => isFileUrl(url) || defaultValidateLink(url);

function SelectableText(props: ComponentProps<typeof Text>): ReactElement {
  return <Text {...props} selectable />;
}

function trimFenceContent(content: string | undefined): string {
  if (typeof content !== 'string') return '';
  return content.endsWith('\n') ? content.slice(0, -1) : content;
}

function webBoxStyle(extra: Record<string, string | number> = {}): Record<string, string | number> {
  return extra;
}

function FenceBlock({ nodeKey, content }: { nodeKey: string; content: string }): ReactElement {
  if (Platform.OS === 'web') {
    return createElement(
      'pre',
      {
        key: nodeKey,
        testID: 'markdown-code-block',
        style: webBoxStyle({
          fontFamily: monospaceFontFamily ?? 'monospace',
          fontSize: 13,
          lineHeight: 18,
          backgroundColor: theme.bg,
          color: theme.text,
          padding: 8,
          borderRadius: 6,
          overflowX: 'auto',
          margin: 0,
        }),
      },
      content,
    );
  }
  return (
    <ScrollView key={nodeKey} horizontal style={styles.fenceScroll} testID="markdown-code-block">
      <Text selectable style={styles.fenceText}>
        {content}
      </Text>
    </ScrollView>
  );
}

function TableWrap({ nodeKey, children }: { nodeKey: string; children: ReactNode }): ReactElement {
  if (Platform.OS === 'web') {
    return createElement(
      'div',
      {
        key: nodeKey,
        style: webBoxStyle({ overflowX: 'auto', width: '100%' }),
      },
      createElement(
        'table',
        {
          style: webBoxStyle({
            borderCollapse: 'collapse',
            width: '100%',
            border: `1px solid ${theme.border}`,
          }),
        },
        children,
      ),
    );
  }
  return (
    <ScrollView key={nodeKey} horizontal style={styles.tableScroll}>
      <View style={styles.table}>{children}</View>
    </ScrollView>
  );
}

function isFileUrl(url: string): boolean {
  return url.startsWith('file:');
}

/**
 * Themed markdown renderer for finished assistant bubbles (M9-01).
 * Images render as alt text; `file:` links call `onFileLink` with a
 * workspace-relative path (M9-03).
 */
/** Unwrap `` `[label](file:path)` `` so a code-spanned file link still
 * renders as a tappable `file:` link (the model often copies the prompt
 * example including its backticks). */
export function unwrapFileLinkCodeSpans(source: string): string {
  return source.replace(/`(\[[^\]]+\]\(file:[^)]+\))`/g, '$1');
}

export function Markdown({ children, onFileLink }: MarkdownProps): ReactElement {
  const rules = {
    // Default `textgroup` is a `<Text>` wrapper; Pressable `file:` links
    // nested inside it are flattened away on web (no `data-testid`).
    textgroup: (node: ASTNode, children: ReactNode[]) => (
      <View key={node.key} style={styles.textGroup}>
        {children}
      </View>
    ),
    link: (node: ASTNode, children: ReactNode[]) => {
      const href = typeof node.attributes?.href === 'string' ? node.attributes.href : '';
      const filePath = workspacePathFromHref(href);
      const open = () => {
        if (filePath !== null) {
          onFileLink?.(filePath);
          return;
        }
        if (href) void Linking.openURL(href);
      };
      // Real `<a>` on web so Playwright can find `data-testid="file-link"`.
      // RN `Text`/`Pressable` wrappers get flattened and lose the test id.
      if (Platform.OS === 'web') {
        return createElement(
          'a',
          {
            key: node.key,
            href: filePath !== null ? `#file:${filePath}` : href,
            'data-testid': filePath !== null ? 'file-link' : undefined,
            'data-file-path': filePath ?? undefined,
            style: {
              color: theme.accent,
              textDecoration: 'underline',
              cursor: 'pointer',
            },
            onClick: (event: { preventDefault?: () => void }) => {
              event.preventDefault?.();
              open();
            },
          },
          children,
        );
      }
      return (
        <Text
          key={node.key}
          selectable
          style={markdownStyles.link}
          accessibilityRole="link"
          accessibilityLabel={filePath ?? href}
          testID={filePath !== null ? 'file-link' : undefined}
          {...(filePath !== null ? { dataSet: { filePath } } : {})}
          onPress={open}
        >
          {children}
        </Text>
      );
    },
    image: (node: ASTNode) => {
      const alt =
        typeof node.attributes?.alt === 'string' && node.attributes.alt.length > 0
          ? node.attributes.alt
          : 'image';
      return (
        <Text key={node.key} selectable style={styles.imageAlt} testID="markdown-image-alt">
          {alt}
        </Text>
      );
    },
    fence: (node: ASTNode) => (
      <FenceBlock key={node.key} nodeKey={node.key} content={trimFenceContent(node.content)} />
    ),
    code_block: (node: ASTNode) => (
      <FenceBlock key={node.key} nodeKey={node.key} content={trimFenceContent(node.content)} />
    ),
    table: (node: ASTNode, tableChildren: ReactNode[]) => (
      <TableWrap key={node.key} nodeKey={node.key}>
        {tableChildren}
      </TableWrap>
    ),
    thead: (node: ASTNode, theadChildren: ReactNode[]) => {
      if (Platform.OS === 'web') {
        return createElement('thead', { key: node.key }, theadChildren);
      }
      return (
        <View key={node.key} style={styles.thead}>
          {theadChildren}
        </View>
      );
    },
    tbody: (node: ASTNode, tbodyChildren: ReactNode[]) => {
      if (Platform.OS === 'web') {
        return createElement('tbody', { key: node.key }, tbodyChildren);
      }
      return (
        <View key={node.key} style={styles.tbody}>
          {tbodyChildren}
        </View>
      );
    },
    tr: (node: ASTNode, rowChildren: ReactNode[]) => {
      if (Platform.OS === 'web') {
        return createElement('tr', { key: node.key }, rowChildren);
      }
      return (
        <View key={node.key} style={styles.tr}>
          {rowChildren}
        </View>
      );
    },
    th: (node: ASTNode, cellChildren: ReactNode[]) => {
      if (Platform.OS === 'web') {
        return createElement(
          'th',
          {
            key: node.key,
            style: webBoxStyle({
              border: `1px solid ${theme.border}`,
              padding: 6,
              textAlign: 'left',
              color: theme.text,
              fontWeight: 600,
            }),
          },
          cellChildren,
        );
      }
      return (
        <View key={node.key} style={styles.th}>
          {cellChildren}
        </View>
      );
    },
    td: (node: ASTNode, cellChildren: ReactNode[]) => {
      if (Platform.OS === 'web') {
        return createElement(
          'td',
          {
            key: node.key,
            style: webBoxStyle({
              border: `1px solid ${theme.border}`,
              padding: 6,
              textAlign: 'left',
              color: theme.text,
            }),
          },
          cellChildren,
        );
      }
      return (
        <View key={node.key} style={styles.td}>
          {cellChildren}
        </View>
      );
    },
  };

  return (
    <View testID="markdown">
      <MarkdownDisplay
        markdownit={markdownIt}
        style={markdownStyles}
        mergeStyle
        rules={rules}
        onLinkPress={(url: string) => {
          const path = workspacePathFromHref(url);
          if (path !== null) {
            onFileLink?.(path);
            return false;
          }
          return true;
        }}
        {...{
          textcomponent: SelectableText,
          allowedImageHandlers: [] as string[],
          defaultImageHandler: null,
        }}
      >
        {unwrapFileLinkCodeSpans(children)}
      </MarkdownDisplay>
    </View>
  );
}

const markdownStyles = {
  body: {
    color: theme.text,
    fontSize: 15,
    lineHeight: 20,
  },
  heading1: {
    color: theme.text,
    fontSize: 22,
    lineHeight: 28,
    fontWeight: '700' as const,
    marginTop: 4,
    marginBottom: 6,
  },
  heading2: {
    color: theme.text,
    fontSize: 19,
    lineHeight: 24,
    fontWeight: '700' as const,
    marginTop: 4,
    marginBottom: 4,
  },
  heading3: {
    color: theme.text,
    fontSize: 17,
    lineHeight: 22,
    fontWeight: '600' as const,
    marginTop: 4,
    marginBottom: 4,
  },
  heading4: {
    color: theme.text,
    fontSize: 16,
    lineHeight: 21,
    fontWeight: '600' as const,
    marginTop: 2,
    marginBottom: 2,
  },
  heading5: {
    color: theme.text,
    fontSize: 15,
    lineHeight: 20,
    fontWeight: '600' as const,
  },
  heading6: {
    color: theme.textMuted,
    fontSize: 13,
    lineHeight: 18,
    fontWeight: '600' as const,
  },
  paragraph: {
    marginTop: 4,
    marginBottom: 4,
    flexWrap: 'wrap' as const,
    flexDirection: 'row' as const,
    width: '100%' as const,
  },
  link: {
    color: theme.accent,
    textDecorationLine: 'underline' as const,
  },
  blockquote: {
    backgroundColor: theme.bg,
    borderColor: theme.border,
    borderLeftWidth: 3,
    marginLeft: 0,
    paddingHorizontal: 8,
    paddingVertical: 4,
  },
  code_inline: {
    fontFamily: monospaceFontFamily,
    fontSize: 13,
    backgroundColor: theme.bg,
    color: theme.text,
    borderWidth: 0,
    borderRadius: 4,
    paddingHorizontal: 4,
    paddingVertical: 1,
  },
  code_block: {
    fontFamily: monospaceFontFamily,
    fontSize: 13,
    backgroundColor: theme.bg,
    color: theme.text,
    borderWidth: 0,
    borderRadius: 6,
    padding: 8,
  },
  fence: {
    fontFamily: monospaceFontFamily,
    fontSize: 13,
    backgroundColor: theme.bg,
    color: theme.text,
    borderWidth: 0,
    borderRadius: 6,
    padding: 8,
  },
  table: {
    borderWidth: 1,
    borderColor: theme.border,
    borderRadius: 4,
  },
  tr: {
    borderBottomWidth: 1,
    borderColor: theme.border,
    flexDirection: 'row' as const,
  },
  th: {
    flex: 1,
    padding: 6,
  },
  td: {
    flex: 1,
    padding: 6,
  },
  hr: {
    backgroundColor: theme.border,
    height: 1,
    marginVertical: 8,
  },
  bullet_list: {
    marginTop: 2,
    marginBottom: 2,
  },
  ordered_list: {
    marginTop: 2,
    marginBottom: 2,
  },
  list_item: {
    flexDirection: 'row' as const,
    justifyContent: 'flex-start' as const,
  },
};

const styles = StyleSheet.create({
  fenceScroll: {
    backgroundColor: theme.bg,
    borderRadius: 6,
    marginVertical: 4,
  },
  fenceText: {
    fontFamily: monospaceFontFamily,
    fontSize: 13,
    lineHeight: 18,
    color: theme.text,
    padding: 8,
  },
  tableScroll: {
    marginVertical: 4,
  },
  table: {
    borderWidth: 1,
    borderColor: theme.border,
    borderRadius: 4,
  },
  thead: {},
  tbody: {},
  tr: {
    borderBottomWidth: 1,
    borderColor: theme.border,
    flexDirection: 'row',
  },
  th: {
    padding: 6,
    minWidth: 72,
    borderRightWidth: 1,
    borderRightColor: theme.border,
  },
  td: {
    padding: 6,
    minWidth: 72,
    borderRightWidth: 1,
    borderRightColor: theme.border,
  },
  imageAlt: {
    color: theme.textMuted,
    fontSize: 13,
    fontStyle: 'italic',
  },
  textGroup: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    alignItems: 'center',
  },
});
