/**
 * Native no-op half of the M9-06 speech split. Expo Go has no bundled
 * speech-recognition module and this project has no custom native builds,
 * so in-app dictation is unsupported here — the phone keyboard's own mic
 * (Gboard / iOS dictation) is the supported path. Web implements the
 * Web Speech API in `speech.web.ts`.
 */

export interface StartListeningOptions {
  lang?: string;
  onInterim?: (text: string) => void;
  onFinal?: (text: string) => void;
  onError?: (error: string) => void;
  onEnd?: () => void;
}

export function isSpeechSupported(): boolean {
  return false;
}

export function startListening(_options: StartListeningOptions): () => void {
  return () => {};
}

export function stopListening(): void {}
