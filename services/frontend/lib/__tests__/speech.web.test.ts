import { isSpeechSupported, startListening, stopListening } from '../speech.web';

interface FakeHandlers {
  onresult: ((event: unknown) => void) | null;
  onerror: ((event: { error: string }) => void) | null;
  onend: (() => void) | null;
}

class FakeSpeechRecognition implements FakeHandlers {
  continuous = false;
  interimResults = false;
  lang = '';
  onresult: FakeHandlers['onresult'] = null;
  onerror: FakeHandlers['onerror'] = null;
  onend: FakeHandlers['onend'] = null;
  started = false;
  stopped = false;

  start(): void {
    this.started = true;
  }

  stop(): void {
    this.stopped = true;
    this.onend?.();
  }

  abort(): void {
    this.stopped = true;
    this.onend?.();
  }

  emitInterim(transcript: string): void {
    this.onresult?.(resultEvent(transcript, false));
  }

  emitFinal(transcript: string): void {
    this.onresult?.(resultEvent(transcript, true));
  }

  emitError(error: string): void {
    this.onerror?.({ error });
  }
}

function resultEvent(transcript: string, isFinal: boolean) {
  const result: { isFinal: boolean; 0: { transcript: string }; length: number } = {
    isFinal,
    0: { transcript },
    length: 1,
  };
  return { resultIndex: 0, results: [result] };
}

let lastInstance: FakeSpeechRecognition | null = null;

function installWindow(overrides: { secure?: boolean; recognition?: boolean } = {}): void {
  const secure = overrides.secure ?? true;
  const recognition = overrides.recognition ?? true;
  lastInstance = null;
  const Ctor = recognition
    ? class extends FakeSpeechRecognition {
        constructor() {
          super();
          lastInstance = this;
        }
      }
    : undefined;

  const w = (typeof globalThis.window === 'object' && globalThis.window !== null
    ? globalThis.window
    : globalThis) as typeof globalThis & {
    isSecureContext?: boolean;
    SpeechRecognition?: unknown;
    webkitSpeechRecognition?: unknown;
  };
  if (typeof globalThis.window === 'undefined') {
    (globalThis as { window?: unknown }).window = w;
  }
  Object.defineProperty(w, 'isSecureContext', { configurable: true, writable: true, value: secure });
  w.SpeechRecognition = Ctor;
  w.webkitSpeechRecognition = Ctor;

  const nav = (typeof globalThis.navigator === 'object' && globalThis.navigator !== null
    ? globalThis.navigator
    : {}) as { language?: string };
  Object.defineProperty(nav, 'language', { configurable: true, writable: true, value: 'en-US' });
  if (typeof globalThis.navigator === 'undefined') {
    (globalThis as { navigator?: unknown }).navigator = nav;
  }
}

describe('speech.web', () => {
  afterEach(() => {
    stopListening();
  });

  it('is unsupported when SpeechRecognition is missing', () => {
    installWindow({ recognition: false });
    expect(isSpeechSupported()).toBe(false);
  });

  it('is unsupported in an insecure context even when SpeechRecognition exists', () => {
    installWindow({ secure: false });
    expect(isSpeechSupported()).toBe(false);
  });

  it('is supported when SpeechRecognition exists in a secure context', () => {
    installWindow();
    expect(isSpeechSupported()).toBe(true);
  });

  it('starts with continuous=false, interimResults=true, and lang from navigator', () => {
    installWindow();
    startListening({});
    expect(lastInstance).not.toBeNull();
    expect(lastInstance?.started).toBe(true);
    expect(lastInstance?.continuous).toBe(false);
    expect(lastInstance?.interimResults).toBe(true);
    expect(lastInstance?.lang).toBe('en-US');
  });

  it('forwards interim then final transcripts', () => {
    installWindow();
    const onInterim = jest.fn();
    const onFinal = jest.fn();
    startListening({ onInterim, onFinal });
    lastInstance?.emitInterim('hello');
    lastInstance?.emitFinal('hello world');
    expect(onInterim).toHaveBeenCalledWith('hello');
    expect(onFinal).toHaveBeenCalledWith('hello world');
  });

  it('forwards not-allowed / audio-capture errors', () => {
    installWindow();
    const onError = jest.fn();
    startListening({ onError });
    lastInstance?.emitError('not-allowed');
    expect(onError).toHaveBeenCalledWith('not-allowed');
    lastInstance?.emitError('audio-capture');
    expect(onError).toHaveBeenCalledWith('audio-capture');
  });

  it('stopListening stops the active recognition', () => {
    installWindow();
    const onEnd = jest.fn();
    startListening({ onEnd });
    stopListening();
    expect(lastInstance?.stopped).toBe(true);
    expect(onEnd).toHaveBeenCalled();
  });
});
