import { ExpoSpeechRecognitionModule } from 'expo-speech-recognition';

/**
 * Native half of the M9-06 speech split: the platform recognizer (Android's
 * SpeechRecognizer, usually Google's) through `expo-speech-recognition`,
 * which needs the custom build (`scripts/build_host_app_android.sh`), not
 * Expo Go. Same contract as the Web Speech API half in `speech.web.ts`.
 */

export interface StartListeningOptions {
  lang?: string;
  onInterim?: (text: string) => void;
  onFinal?: (text: string) => void;
  onError?: (error: string) => void;
  onEnd?: () => void;
}

let subscriptions: ReturnType<typeof ExpoSpeechRecognitionModule.addListener>[] = [];
let session = 0;
let active = false;

function unsubscribe(): void {
  for (const subscription of subscriptions) subscription.remove();
  subscriptions = [];
}

export function isSpeechSupported(): boolean {
  try {
    return ExpoSpeechRecognitionModule.isRecognitionAvailable();
  } catch {
    return false;
  }
}

export function stopListening(): void {
  session += 1;
  if (!active) return;
  active = false;
  try {
    ExpoSpeechRecognitionModule.stop();
  } catch {
    // already stopped
  }
}

export function startListening(options: StartListeningOptions): () => void {
  stopListening();
  unsubscribe();
  const mine = ++session;
  const finish = () => {
    if (mine !== session) return;
    active = false;
    unsubscribe();
    options.onEnd?.();
  };

  subscriptions = [
    ExpoSpeechRecognitionModule.addListener('result', (event) => {
      const text = event.results[0]?.transcript ?? '';
      if (!text) return;
      if (event.isFinal) options.onFinal?.(text);
      else options.onInterim?.(text);
    }),
    ExpoSpeechRecognitionModule.addListener('error', (event) => options.onError?.(event.error)),
    ExpoSpeechRecognitionModule.addListener('end', finish),
  ];

  ExpoSpeechRecognitionModule.requestPermissionsAsync()
    .then((permission) => {
      if (mine !== session) return;
      if (!permission.granted) {
        options.onError?.('not-allowed');
        finish();
        return;
      }
      active = true;
      ExpoSpeechRecognitionModule.start({ lang: options.lang ?? 'en-US', interimResults: true, continuous: false });
    })
    .catch(() => {
      if (mine !== session) return;
      options.onError?.('audio-capture');
      finish();
    });

  return () => stopListening();
}
