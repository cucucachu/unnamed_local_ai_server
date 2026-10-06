type CurrentChatModule = typeof import('../currentChat');
type CurrentChat = import('../currentChat').CurrentChat;

function memoryStorage(): Storage {
  const data = new Map<string, string>();
  return {
    get length() {
      return data.size;
    },
    clear: () => data.clear(),
    getItem: (key) => data.get(key) ?? null,
    key: (index) => [...data.keys()][index] ?? null,
    removeItem: (key) => {
      data.delete(key);
    },
    setItem: (key, value) => {
      data.set(key, value);
    },
  };
}

/** A fresh copy of the module (a cold launch or a page reload) and its current value. */
function launch(os: 'android' | 'web' = 'android'): { chat: CurrentChatModule; now: () => CurrentChat } {
  let chat!: CurrentChatModule;
  jest.isolateModules(() => {
    jest.doMock('react-native', () => ({ Platform: { OS: os } }));
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- a fresh copy per launch
    chat = require('../currentChat');
  });
  return { chat, now: () => chat.getCurrentChat() };
}

const globals = globalThis as { sessionStorage?: Storage };

afterEach(() => {
  delete globals.sessionStorage;
});

describe('currentChat (M19-02)', () => {
  it('starts on a new chat; switching changes the key, a new chat getting its thread does not', () => {
    const { chat, now } = launch();
    expect(now()).toEqual({ threadId: null, key: 0 });

    chat.adoptCreatedThread('a');
    expect(now()).toEqual({ threadId: 'a', key: 0 });

    chat.openChat('a');
    expect(now().key).toBe(0);

    chat.openChat('b');
    expect(now()).toEqual({ threadId: 'b', key: 1 });

    chat.openChat(null);
    expect(now()).toEqual({ threadId: null, key: 2 });
  });

  it('native keeps nothing across a cold launch', () => {
    const first = launch();
    first.chat.openChat('thread-1');
    expect(launch().now().threadId).toBeNull();
  });

  it('on web, a reload in the same tab keeps the chat; sign-out forgets it', () => {
    globals.sessionStorage = memoryStorage();
    const first = launch('web');
    first.chat.openChat('thread-1');

    const reloaded = launch('web');
    expect(reloaded.now().threadId).toBe('thread-1');

    reloaded.chat.resetCurrentChat();
    expect(launch('web').now().threadId).toBeNull();
  });
});
