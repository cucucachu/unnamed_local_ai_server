/**
 * Web half of the M9-06 speech split — the browser's Web Speech API
 * (`SpeechRecognition` / `webkitSpeechRecognition`). Requires a secure
 * context (https://homeai.local or localhost). Audio is handled by the
 * browser vendor, not this stack.
 */

export interface StartListeningOptions {
  lang?: string;
  onInterim?: (text: string) => void;
  onFinal?: (text: string) => void;
  onError?: (error: string) => void;
  onEnd?: () => void;
}

interface SpeechRecognitionAlternativeLike {
  transcript: string;
}

interface SpeechRecognitionResultLike {
  isFinal: boolean;
  0?: SpeechRecognitionAlternativeLike;
  length: number;
}

interface SpeechRecognitionEventLike {
  resultIndex: number;
  results: ArrayLike<SpeechRecognitionResultLike>;
}

interface SpeechRecognitionLike {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onerror: ((event: { error: string }) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
  abort(): void;
}

type SpeechRecognitionCtor = new () => SpeechRecognitionLike;

function getSpeechRecognitionCtor(): SpeechRecognitionCtor | null {
  const w = window as Window & {
    SpeechRecognition?: SpeechRecognitionCtor;
    webkitSpeechRecognition?: SpeechRecognitionCtor;
  };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

export function isSpeechSupported(): boolean {
  return getSpeechRecognitionCtor() !== null && window.isSecureContext === true;
}

let active: SpeechRecognitionLike | null = null;

export function stopListening(): void {
  if (active === null) return;
  const recognition = active;
  active = null;
  try {
    recognition.stop();
  } catch {
    // already stopped
  }
}

export function startListening(options: StartListeningOptions): () => void {
  stopListening();

  const Ctor = getSpeechRecognitionCtor();
  if (Ctor === null || window.isSecureContext !== true) {
    options.onError?.('not-supported');
    return () => {};
  }

  const recognition = new Ctor();
  recognition.continuous = false;
  recognition.interimResults = true;
  recognition.lang = options.lang ?? navigator.language ?? 'en-US';

  recognition.onresult = (event) => {
    let interim = '';
    let finalText = '';
    for (let i = event.resultIndex; i < event.results.length; i += 1) {
      const result = event.results[i];
      const transcript = result[0]?.transcript ?? '';
      if (result.isFinal) {
        finalText += transcript;
      } else {
        interim += transcript;
      }
    }
    if (interim) options.onInterim?.(interim);
    if (finalText) options.onFinal?.(finalText);
  };

  recognition.onerror = (event) => {
    options.onError?.(event.error);
  };

  recognition.onend = () => {
    if (active === recognition) active = null;
    options.onEnd?.();
  };

  active = recognition;
  try {
    recognition.start();
  } catch {
    active = null;
    options.onError?.('audio-capture');
  }

  return () => stopListening();
}
