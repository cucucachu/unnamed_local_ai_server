import { ExpoSpeechRecognitionModule } from 'expo-speech-recognition';

import { isSpeechSupported, startListening, stopListening } from '../speech';

const module = ExpoSpeechRecognitionModule as unknown as {
  isRecognitionAvailable: jest.Mock;
  requestPermissionsAsync: jest.Mock;
  start: jest.Mock;
  stop: jest.Mock;
  addListener: jest.Mock;
};

type Listener = (event: unknown) => void;

function listeners(): Record<string, Listener> {
  return Object.fromEntries(module.addListener.mock.calls.map(([name, fn]) => [name, fn]));
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

beforeEach(() => {
  jest.clearAllMocks();
  module.requestPermissionsAsync.mockResolvedValue({ granted: true });
});

afterEach(() => stopListening());

describe('native speech', () => {
  it('is supported when the platform recognizer is available', () => {
    module.isRecognitionAvailable.mockReturnValueOnce(true);
    expect(isSpeechSupported()).toBe(true);
    module.isRecognitionAvailable.mockImplementationOnce(() => {
      throw new Error('no module');
    });
    expect(isSpeechSupported()).toBe(false);
  });

  it('asks for the microphone, then streams interim and final text, then ends', async () => {
    const onInterim = jest.fn();
    const onFinal = jest.fn();
    const onEnd = jest.fn();
    startListening({ onInterim, onFinal, onEnd });
    await flush();
    expect(module.start).toHaveBeenCalledWith({ lang: 'en-US', interimResults: true, continuous: false });

    const on = listeners();
    on.result({ isFinal: false, results: [{ transcript: 'hello' }] });
    on.result({ isFinal: true, results: [{ transcript: 'hello world' }] });
    on.end(null);
    expect(onInterim).toHaveBeenCalledWith('hello');
    expect(onFinal).toHaveBeenCalledWith('hello world');
    expect(onEnd).toHaveBeenCalledTimes(1);
  });

  it('reports not-allowed when the microphone is refused, and never starts', async () => {
    module.requestPermissionsAsync.mockResolvedValueOnce({ granted: false });
    const onError = jest.fn();
    const onEnd = jest.fn();
    startListening({ onError, onEnd });
    await flush();
    expect(onError).toHaveBeenCalledWith('not-allowed');
    expect(onEnd).toHaveBeenCalledTimes(1);
    expect(module.start).not.toHaveBeenCalled();
  });

  it('stopping before the permission answer means it never starts', async () => {
    startListening({});
    stopListening();
    await flush();
    expect(module.start).not.toHaveBeenCalled();
  });
});
