declare module 'markdown-it' {
  interface MarkdownItToken {
    type: string;
    content: string;
    children: MarkdownItToken[] | null;
  }

  interface MarkdownItState {
    tokens: MarkdownItToken[];
  }

  class MarkdownIt {
    constructor(options?: { typographer?: boolean; html?: boolean; linkify?: boolean });
    use(plugin: (md: MarkdownIt) => void): this;
    validateLink: (url: string) => boolean;
    core: {
      ruler: {
        after: (name: string, rule: string, fn: (state: MarkdownItState) => void) => void;
      };
    };
  }

  export default MarkdownIt;
}

declare module 'markdown-it/lib/token' {
  export default class Token {
    type: string;
    content: string;
    children: Token[] | null;
  }
}
